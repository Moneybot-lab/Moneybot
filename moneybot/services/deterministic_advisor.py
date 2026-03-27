from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, Dict

import numpy as np

from .deterministic_model import BaselineModelArtifact, load_artifact, predict_proba


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

        if self.enabled:
            self._load_artifact()

    def _load_artifact(self) -> None:
        try:
            self.artifact = load_artifact(self.artifact_path)
            self.load_error = None
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

    def predict_quick_decision(self, *, signal_data: Dict[str, Any], quote_data: Dict[str, Any]) -> Dict[str, Any] | None:
        if not self.enabled:
            return None
        if self.artifact is None:
            return None

        row, imputed = self._build_feature_row(signal_data, quote_data)
        prob_up = float(predict_proba(self.artifact, row)[0])
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
            f"Deterministic model ({self.artifact.version}) probability-up={prob_up:.2f} "
            f"vs threshold={threshold:.2f}."
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
            "probability_up": round(prob_up, 4),
            "decision_threshold": threshold,
            "confidence": confidence,
            "imputed_features": imputed,
        }

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
        quick = self.predict_quick_decision(signal_data=signal_data, quote_data=quote_data)
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
            f"Deterministic portfolio rule ({quick.get('model_version')}) using probability_up={prob_up:.2f}"
            f" and pnl_percent={pnl_percent:.2f}."
            if pnl_percent is not None
            else f"Deterministic portfolio rule ({quick.get('model_version')}) holding due to missing entry/current price context."
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
