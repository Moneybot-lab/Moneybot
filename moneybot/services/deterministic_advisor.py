from __future__ import annotations

import logging
import math
import hashlib
from pathlib import Path
from typing import Any, Dict

import numpy as np

from .deterministic_model import BaselineModelArtifact, default_baseline_artifact, load_artifact, predict_proba

LIVE_SUPPLIED_FEATURE_COLUMNS = frozenset({"return_1d", "return_5d", "rsi_14", "macd_hist", "vol_ratio_20d"})


class DeterministicQuickAdvisor:
    """Serve quick-ask predictions from the Day-1 deterministic model artifact."""

    def __init__(
        self,
        *,
        enabled: bool,
        artifact_path: str,
        quick_buy_threshold: float | None = None,
        quick_strong_buy_threshold: float = 0.70,
        portfolio_buy_prob_threshold: float = 0.62,
        portfolio_sell_prob_threshold: float = 0.45,
        portfolio_buy_dip_threshold_pct: float = -4.0,
        portfolio_sell_profit_threshold_pct: float = 6.0,
        calibration_enabled: bool = False,
        calibration_slope: float = 1.0,
        calibration_intercept: float = 0.0,
        rollout_percentage: float = 100.0,
        rollout_seed: str = "moneybot",
        rollout_allowlist: set[str] | None = None,
        rollout_blocklist: set[str] | None = None,
        rollout_dry_run: bool = False,
        portfolio_rollout_percentage: float | None = None,
    ):
        self.enabled = bool(enabled)
        self.artifact_path = artifact_path
        self.artifact: BaselineModelArtifact | None = None
        self.load_error: str | None = None

        self.quick_buy_threshold = quick_buy_threshold
        self.quick_strong_buy_threshold = float(quick_strong_buy_threshold)
        self.portfolio_buy_prob_threshold = float(portfolio_buy_prob_threshold)
        self.portfolio_sell_prob_threshold = float(portfolio_sell_prob_threshold)
        self.portfolio_buy_dip_threshold_pct = float(portfolio_buy_dip_threshold_pct)
        self.portfolio_sell_profit_threshold_pct = float(portfolio_sell_profit_threshold_pct)
        self.calibration_enabled = bool(calibration_enabled)
        self.calibration_slope = float(calibration_slope)
        self.calibration_intercept = float(calibration_intercept)
        self.rollout_percentage = max(0.0, min(100.0, float(rollout_percentage)))
        self.rollout_seed = str(rollout_seed or "moneybot")
        self.rollout_allowlist = {s.strip().upper() for s in (rollout_allowlist or set()) if str(s).strip()}
        self.rollout_blocklist = {s.strip().upper() for s in (rollout_blocklist or set()) if str(s).strip()}
        self.rollout_dry_run = bool(rollout_dry_run)
        self.portfolio_rollout_percentage = max(
            0.0,
            min(
                100.0,
                float(portfolio_rollout_percentage)
                if portfolio_rollout_percentage is not None
                else self.rollout_percentage,
            ),
        )

        if self.enabled:
            self._load_artifact()

    def reload_artifact(self) -> bool:
        """Reload the model artifact from disk and report whether it is usable."""
        self._load_artifact()
        return self.artifact is not None

    def _load_artifact(self) -> None:
        try:
            self.artifact = load_artifact(self.artifact_path)
            self.load_error = None
        except FileNotFoundError:
            self.artifact = default_baseline_artifact()
            self.load_error = (
                f"Artifact file not found at {self.artifact_path}. "
                "Using built-in deterministic fallback artifact."
            )
            logging.warning("%s", self.load_error)
        except Exception as exc:  # noqa: BLE001
            self.artifact = None
            self.load_error = str(exc)
            logging.warning(
                "Deterministic quick advisor disabled: unable to load artifact %s (%s)",
                self.artifact_path,
                exc,
            )

    @staticmethod
    def _num(value: Any) -> float | None:
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            return float(value)
        return None

    def _build_feature_row(self, signal_data: Dict[str, Any], quote_data: Dict[str, Any]) -> tuple[np.ndarray, list[str]]:
        assert self.artifact is not None

        technical = signal_data.get("technical") or {}

        # Day-1 model features: return_1d, return_5d, rsi_14, macd_hist, vol_ratio_20d
        return_1d = self._num(quote_data.get("change_percent"))
        return_1d = (return_1d / 100.0) if return_1d is not None else None
        return_5d = self._num(signal_data.get("return_5d"))
        if return_5d is None and return_1d is not None:
            return_5d = return_1d * 5.0

        raw_values = {
            "return_1d": return_1d,
            "return_5d": return_5d,
            "rsi_14": self._num(technical.get("rsi")),
            "macd_hist": self._num(technical.get("macd_histogram") or signal_data.get("macd_hist")),
            "vol_ratio_20d": self._num(signal_data.get("volume_ratio")),
        }

        means = np.asarray(self.artifact.means, dtype=float)
        row = np.zeros(len(self.artifact.feature_columns), dtype=float)
        imputed: list[str] = []
        for idx, col in enumerate(self.artifact.feature_columns):
            val = raw_values.get(col)
            if val is None:
                row[idx] = float(means[idx])
                imputed.append(col)
            else:
                row[idx] = float(val)

        return row, imputed

    def _is_in_rollout(self, symbol: str | None) -> bool:
        return self._is_in_rollout_with_percentage(symbol, self.rollout_percentage)

    def _is_in_rollout_with_percentage(self, symbol: str | None, rollout_percentage: float) -> bool:
        normalized = str(symbol or "").strip().upper()
        if normalized and normalized in self.rollout_allowlist:
            return True
        if normalized and normalized in self.rollout_blocklist:
            return False
        if rollout_percentage >= 100.0:
            return True
        if rollout_percentage <= 0.0:
            return False
        key = f"{self.rollout_seed}:{normalized or '*'}".encode("utf-8")
        bucket = int(hashlib.sha256(key).hexdigest()[:8], 16) / 0xFFFFFFFF
        return bucket < (rollout_percentage / 100.0)

    def _is_in_portfolio_rollout(self, symbol: str | None) -> bool:
        return self._is_in_rollout_with_percentage(symbol, self.portfolio_rollout_percentage)

    @staticmethod
    def _sigmoid(value: float) -> float:
        clipped = max(min(value, 35.0), -35.0)
        return 1.0 / (1.0 + math.exp(-clipped))

    def _calibrate_probability(self, prob_up: float) -> float:
        if not self.calibration_enabled:
            return prob_up
        p = min(max(float(prob_up), 1e-6), 1.0 - 1e-6)
        logit = math.log(p / (1.0 - p))
        calibrated_logit = (self.calibration_slope * logit) + self.calibration_intercept
        return self._sigmoid(calibrated_logit)

    def _predict_quick_decision_internal(
        self,
        *,
        signal_data: Dict[str, Any],
        quote_data: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        if not self.enabled:
            return None
        if self.artifact is None:
            return None

        row, imputed = self._build_feature_row(signal_data, quote_data)
        raw_prob_up = float(predict_proba(self.artifact, row)[0])
        prob_up = self._calibrate_probability(raw_prob_up)
        threshold = float(self.quick_buy_threshold if self.quick_buy_threshold is not None else self.artifact.decision_threshold)
        strong_threshold = max(threshold + 0.15, self.quick_strong_buy_threshold)

        if prob_up >= strong_threshold:
            recommendation = "STRONG BUY"
        elif prob_up >= threshold:
            recommendation = "BUY"
        else:
            recommendation = "HOLD OFF FOR NOW"

        confidence = round(max(prob_up, 1.0 - prob_up) * 100.0, 1)
        rationale = (
            f"Alpha Atlas v1 probability-up={prob_up:.2f} vs threshold={threshold:.2f}."
        )
        if self.calibration_enabled:
            rationale += (
                f" Calibrated from raw={raw_prob_up:.2f} with slope={self.calibration_slope:.2f}"
                f" intercept={self.calibration_intercept:.2f}."
            )
        if imputed:
            rationale += f" Missing live features were imputed: {', '.join(imputed)}."

        return {
            "recommendation": recommendation,
            "rationale": rationale,
            "current_price": quote_data.get("price"),
            "change_percent": quote_data.get("change_percent"),
            "quote_source": quote_data.get("quote_source"),
            "quote_diagnostics": quote_data.get("diagnostics"),
            "decision_source": "deterministic_model",
            "model_version": self.artifact.version,
            "raw_probability_up": round(raw_prob_up, 4),
            "probability_up": round(prob_up, 4),
            "decision_threshold": threshold,
            "confidence": confidence,
            "imputed_features": imputed,
            "rollout_percentage": self.rollout_percentage,
            "calibration_enabled": self.calibration_enabled,
        }

    def predict_quick_decision(
        self,
        *,
        signal_data: Dict[str, Any],
        quote_data: Dict[str, Any],
        symbol: str | None = None,
    ) -> Dict[str, Any] | None:
        if not self._is_in_rollout(symbol):
            return None
        return self._predict_quick_decision_internal(signal_data=signal_data, quote_data=quote_data)

    def predict_shadow_decision(
        self,
        *,
        signal_data: Dict[str, Any],
        quote_data: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        return self._predict_quick_decision_internal(signal_data=signal_data, quote_data=quote_data)

    def predict_portfolio_position(
        self,
        *,
        symbol: str,
        entry_price: float | None,
        current_price: float | None,
        shares: float,
        signal_data: Dict[str, Any],
        quote_data: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        quick = None
        if self._is_in_portfolio_rollout(symbol):
            quick = self.predict_quick_decision(signal_data=signal_data, quote_data=quote_data, symbol=symbol)
        if quick is None and self.rollout_dry_run:
            quick = self.predict_shadow_decision(signal_data=signal_data, quote_data=quote_data)
        if quick is None:
            return None

        prob_up = float(quick.get("probability_up") or 0.0)
        confidence = float(quick.get("confidence") or 50.0)

        pnl_percent = None
        if isinstance(entry_price, (int, float)) and isinstance(current_price, (int, float)) and float(entry_price) > 0:
            pnl_percent = ((float(current_price) - float(entry_price)) / float(entry_price)) * 100.0

        advice = "HOLD"
        if pnl_percent is not None:
            if prob_up >= self.portfolio_buy_prob_threshold and pnl_percent <= self.portfolio_buy_dip_threshold_pct:
                advice = "BUY"
            elif prob_up <= self.portfolio_sell_prob_threshold and pnl_percent >= self.portfolio_sell_profit_threshold_pct:
                advice = "SELL"

        reason = (
            f"Alpha Atlas v1 portfolio rule using probability_up={prob_up:.2f}"
            f" and pnl_percent={pnl_percent:.2f}."
            if pnl_percent is not None
            else "Alpha Atlas v1 portfolio rule holding due to missing entry/current price context."
        )

        return {
            "mode": "deterministic_model",
            "symbol": symbol,
            "advice": advice,
            "advice_reason": reason,
            "decision_source": "deterministic_model",
            "model_version": quick.get("model_version"),
            "probability_up": round(prob_up, 4),
            "confidence": confidence,
            "position_shares": float(shares),
            "pnl_percent": round(pnl_percent, 2) if pnl_percent is not None else None,
        }
