#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BACKTEST_SCHEMA_VERSION = "moneybot-challenger-backtest.v1"


def _load_jsonl(path: Path) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return pd.DataFrame(rows)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -35.0, 35.0)))


def _return_column(df: pd.DataFrame, horizon_days: int) -> str:
    preferred = f"return_{horizon_days}d"
    if preferred in df.columns:
        return preferred
    for col in ("return_5d", "forward_return_5d", "return_3d", "return_1d"):
        if col in df.columns:
            return col
    raise ValueError(f"No return column found for horizon_days={horizon_days}")


def _feature_columns(df: pd.DataFrame, suite_manifest: dict[str, Any]) -> list[str]:
    cols = [str(col) for col in suite_manifest.get("feature_columns") or [] if str(col) in df.columns]
    if cols:
        return cols
    return sorted(str(col) for col in df.columns if str(col).startswith("feature_"))


def _prepare_features(df: pd.DataFrame, feature_columns: list[str], fill_values: dict[str, Any]) -> pd.DataFrame:
    out = df.copy()
    for col in feature_columns:
        fill = fill_values.get(col, 0.0) if isinstance(fill_values, dict) else 0.0
        try:
            fill = float(fill)
        except (TypeError, ValueError):
            fill = 0.0
        out[col] = pd.to_numeric(out[col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(fill)
    return out


def _predict(artifact: dict[str, Any], frame: pd.DataFrame, feature_columns: list[str]) -> tuple[np.ndarray, np.ndarray]:
    model_type = str(artifact.get("model_type") or "logistic_regression")
    if model_type == "logistic_regression":
        artifact_features = [str(col) for col in artifact.get("feature_columns") or feature_columns]
        X = frame[artifact_features].to_numpy(dtype=float)
        means = np.asarray(artifact.get("means"), dtype=float)
        stds = np.asarray(artifact.get("stds"), dtype=float)
        stds = np.where(stds == 0.0, 1.0, stds)
        weights = np.asarray(artifact.get("weights"), dtype=float)
        probs = _sigmoid(((X - means) / stds) @ weights + float(artifact.get("bias", 0.0)))
        preds = (probs >= float(artifact.get("decision_threshold", 0.5))).astype(int)
        return probs, preds
    if model_type == "decision_stump":
        values = frame[str(artifact["feature"])].to_numpy(dtype=float)
        threshold = float(artifact["threshold"])
        if artifact.get("direction") == "gte_positive":
            preds = (values >= threshold).astype(int)
        else:
            preds = (values < threshold).astype(int)
        return preds.astype(float), preds
    if model_type == "baseline_classifier":
        spec = artifact.get("training_spec") if isinstance(artifact.get("training_spec"), dict) else {}
        if "always-down" in str(artifact.get("version")):
            pred = 0
        elif "always-up" in str(artifact.get("version")):
            pred = 1
        else:
            pred = int(spec.get("majority_class", 1))
        preds = np.full(len(frame), pred, dtype=int)
        return preds.astype(float), preds
    raise ValueError(f"Unsupported challenger model_type={model_type}")


def _max_drawdown(equity: np.ndarray) -> float:
    if equity.size == 0:
        return 0.0
    peaks = np.maximum.accumulate(equity)
    drawdowns = (equity / np.where(peaks == 0, 1.0, peaks)) - 1.0
    return round(float(drawdowns.min()), 6)


def _calibration(probs: np.ndarray, labels: np.ndarray, bins: int = 5) -> dict[str, Any]:
    if len(probs) == 0:
        return {"brier_score": None, "ece": None, "bins": []}
    brier = float(np.mean((probs - labels) ** 2))
    bin_rows: list[dict[str, Any]] = []
    ece = 0.0
    for idx in range(bins):
        lo = idx / bins
        hi = (idx + 1) / bins
        mask = (probs >= lo) & (probs <= hi if idx == bins - 1 else probs < hi)
        if not mask.any():
            continue
        avg_conf = float(probs[mask].mean())
        observed = float(labels[mask].mean())
        weight = float(mask.mean())
        ece += weight * abs(avg_conf - observed)
        bin_rows.append({"lower": lo, "upper": hi, "rows": int(mask.sum()), "avg_probability": round(avg_conf, 6), "observed_rate": round(observed, 6)})
    return {"brier_score": round(brier, 6), "ece": round(ece, 6), "bins": bin_rows}


def _drift(frame: pd.DataFrame, feature_columns: list[str]) -> dict[str, Any]:
    midpoint = len(frame) // 2
    if midpoint <= 0:
        return {"max_mean_shift": 0.0, "feature_shifts": {}}
    first = frame.iloc[:midpoint]
    second = frame.iloc[midpoint:]
    shifts: dict[str, float] = {}
    for col in feature_columns:
        std = float(first[col].std(ddof=0) or 0.0)
        denom = std if std > 1e-12 else 1.0
        shifts[col] = round(abs(float(second[col].mean()) - float(first[col].mean())) / denom, 6)
    return {"max_mean_shift": max(shifts.values()) if shifts else 0.0, "feature_shifts": shifts}


def _promotion_gates(metrics: dict[str, Any], benchmark: dict[str, Any], *, min_rows: int, max_drawdown: float, max_ece: float, min_excess_return: float, max_drift_shift: float) -> dict[str, Any]:
    failures: list[str] = []
    if metrics["rows"] < min_rows:
        failures.append("insufficient_rows")
    if metrics["total_return_net"] < benchmark["buy_and_hold_return"] + min_excess_return:
        failures.append("underperforms_buy_and_hold_after_costs")
    if metrics["max_drawdown"] < -abs(max_drawdown):
        failures.append("drawdown_gate_failed")
    if metrics["calibration"]["ece"] is not None and metrics["calibration"]["ece"] > max_ece:
        failures.append("calibration_gate_failed")
    if metrics["drift"]["max_mean_shift"] > max_drift_shift:
        failures.append("drift_gate_failed")
    return {"promotion_ready": not failures, "failed_gates": failures, "objective_gates": {"min_rows": min_rows, "max_drawdown": max_drawdown, "max_ece": max_ece, "min_excess_return": min_excess_return, "max_drift_shift": max_drift_shift}}


def backtest_challenger_suite(
    *,
    suite_manifest_path: Path,
    feature_store_path: Path,
    output_path: Path,
    horizon_days: int = 5,
    transaction_cost_bps: float = 5.0,
    slippage_bps: float = 5.0,
    min_rows: int = 20,
    max_drawdown: float = 0.20,
    max_ece: float = 0.20,
    min_excess_return: float = 0.0,
    max_drift_shift: float = 3.0,
) -> dict[str, Any]:
    suite = _load_json(suite_manifest_path)
    raw = _load_jsonl(feature_store_path)
    if "ts" in raw.columns:
        raw = raw.sort_values("ts")
    raw = raw.reset_index(drop=True)
    return_col = _return_column(raw, horizon_days)
    label_col = f"label_up_{horizon_days}d" if f"label_up_{horizon_days}d" in raw.columns else "label_up_5d"
    features = _feature_columns(raw, suite)
    frame = _prepare_features(raw.dropna(subset=[return_col, label_col]).copy(), features, suite.get("feature_fill_values") or {})
    labels = pd.to_numeric(frame[label_col], errors="coerce").fillna(0).to_numpy(dtype=float)
    returns = pd.to_numeric(frame[return_col], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    benchmark = {
        "buy_and_hold_return": round(float(np.prod(1.0 + returns) - 1.0), 6),
        "cash_return": 0.0,
        "equal_weight_long_cash_return": round(float(np.prod(1.0 + (returns * 0.5)) - 1.0), 6),
    }
    cost_rate = (float(transaction_cost_bps) + float(slippage_bps)) / 10_000.0
    challengers: list[dict[str, Any]] = []
    for challenger in suite.get("challengers") or []:
        artifact = _load_json(Path(challenger["model_path"]))
        probs, preds = _predict(artifact, frame, features)
        position_changes = np.abs(np.diff(np.concatenate([[0], preds.astype(float)])))
        strategy_returns = (preds * returns) - (position_changes * cost_rate)
        equity = np.cumprod(1.0 + strategy_returns)
        metrics = {
            "rows": int(len(frame)),
            "accuracy": round(float((preds == labels).mean()), 6) if len(frame) else 0.0,
            "positive_rate": round(float(preds.mean()), 6) if len(frame) else 0.0,
            "total_return_net": round(float(equity[-1] - 1.0), 6) if len(equity) else 0.0,
            "avg_return_net": round(float(strategy_returns.mean()), 6) if len(strategy_returns) else 0.0,
            "turnover": round(float(position_changes.sum()), 6),
            "transaction_cost_bps": transaction_cost_bps,
            "slippage_bps": slippage_bps,
            "max_drawdown": _max_drawdown(equity),
            "calibration": _calibration(probs, labels),
            "drift": _drift(frame, features),
        }
        gates = _promotion_gates(metrics, benchmark, min_rows=min_rows, max_drawdown=max_drawdown, max_ece=max_ece, min_excess_return=min_excess_return, max_drift_shift=max_drift_shift)
        challengers.append({**challenger, "backtest_metrics": metrics, "promotion_gates": gates, "shadow_logging_recommended": gates["promotion_ready"], "routing_allowed": False})
    ranked = sorted(challengers, key=lambda item: (item["promotion_gates"]["promotion_ready"], item["backtest_metrics"]["total_return_net"], -item["backtest_metrics"]["calibration"].get("ece", 1.0)), reverse=True)
    report = {
        "schema_version": BACKTEST_SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "suite_manifest_path": str(suite_manifest_path),
        "feature_store_path": str(feature_store_path),
        "rows": int(len(frame)),
        "horizon_days": horizon_days,
        "benchmark": benchmark,
        "challengers": challengers,
        "ranked_model_versions": [item["model_version"] for item in ranked],
        "shadow_candidates": [item["model_version"] for item in ranked if item["shadow_logging_recommended"]],
        "routing_policy": "shadow-log first; user-facing routing remains disabled until gates pass and human promotion occurs",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Chronologically backtest every offline challenger with costs, slippage, drawdown, calibration, benchmarks, gates, and drift checks.")
    parser.add_argument("--suite-manifest", default="data/challenger_suite/challenger_suite_manifest.json")
    parser.add_argument("--feature-store", default="data/flat_feature_store/test.jsonl")
    parser.add_argument("--output", default="data/challenger_suite/backtest_report.json")
    parser.add_argument("--horizon-days", type=int, default=5)
    parser.add_argument("--transaction-cost-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--min-rows", type=int, default=20)
    args = parser.parse_args()
    report = backtest_challenger_suite(
        suite_manifest_path=Path(args.suite_manifest),
        feature_store_path=Path(args.feature_store),
        output_path=Path(args.output),
        horizon_days=args.horizon_days,
        transaction_cost_bps=args.transaction_cost_bps,
        slippage_bps=args.slippage_bps,
        min_rows=args.min_rows,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
