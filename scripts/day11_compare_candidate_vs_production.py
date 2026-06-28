#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moneybot.services.deterministic_model import load_artifact, predict_proba

RETURN_BIN_EDGES = (-0.03, -0.005, 0.005, 0.03)
TARGET_GAIN_BUCKETS = {"gain", "big_gain"}


def _load_jsonl(path: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            raw = line.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return pd.DataFrame(rows)


def _chronological_split(df: pd.DataFrame, train_ratio: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    if "ts" in df.columns:
        df = df.sort_values("ts").reset_index(drop=True)
    pivot = int(len(df) * train_ratio)
    if pivot <= 0 or pivot >= len(df):
        raise ValueError("train_ratio creates empty split")
    return df.iloc[:pivot].copy(), df.iloc[pivot:].copy()


def _brier_score(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    return float(np.mean((y_prob - y_true) ** 2))


def _return_bin(value: float | None) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    ret = float(value)
    if ret < RETURN_BIN_EDGES[0]:
        return "big_loss"
    if ret < RETURN_BIN_EDGES[1]:
        return "loss"
    if ret <= RETURN_BIN_EDGES[2]:
        return "flat"
    if ret <= RETURN_BIN_EDGES[3]:
        return "gain"
    return "big_gain"


def _ensure_return_bins(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "return_bin_5d" not in out.columns:
        returns = pd.to_numeric(out.get("return_5d"), errors="coerce")
        out["return_bin_5d"] = [_return_bin(value) if pd.notna(value) else None for value in returns]
    return out


def _bucket_metrics(usable: pd.DataFrame, preds: np.ndarray, probs: np.ndarray) -> dict[str, dict[str, float | int | None]]:
    out: dict[str, dict[str, float | int | None]] = {}
    work = usable.copy()
    work["_pred"] = preds
    work["_prob"] = probs
    for bucket, group in work.groupby("return_bin_5d", dropna=False):
        key = str(bucket or "unknown")
        returns = pd.to_numeric(group["return_5d"], errors="coerce")
        out[key] = {
            "rows": int(len(group)),
            "positive_predictions": int((group["_pred"] == 1).sum()),
            "avg_probability": round(float(group["_prob"].mean()), 4) if len(group) else None,
            "avg_return": round(float(returns.mean()), 4) if returns.notna().any() else None,
        }
    return dict(sorted(out.items()))


def _evaluate(artifact_path: str, test_df: pd.DataFrame) -> dict[str, Any]:
    if not Path(artifact_path).exists():
        return {"accuracy": None, "avg_return": None, "brier_score": None, "downside_risk": None, "positive_predictions": 0, "rows": 0}
    artifact = load_artifact(artifact_path)
    usable = test_df.copy()
    for idx, col in enumerate(artifact.feature_columns):
        if col not in usable.columns:
            usable[col] = np.nan
        numeric = pd.to_numeric(usable[col], errors="coerce").replace([np.inf, -np.inf], np.nan)
        fallback = float(artifact.means[idx]) if idx < len(artifact.means) else 0.0
        usable[col] = numeric.fillna(fallback).astype(float)
    usable["return_5d"] = pd.to_numeric(usable.get("return_5d"), errors="coerce")
    usable = usable.dropna(subset=["return_5d"]).copy()
    usable = _ensure_return_bins(usable)
    if usable.empty:
        return {"accuracy": None, "avg_return": None, "brier_score": None, "downside_risk": None, "positive_predictions": 0, "rows": 0}

    X = usable[artifact.feature_columns].to_numpy(dtype=float)
    y = usable["return_bin_5d"].fillna("").astype(str).isin(TARGET_GAIN_BUCKETS).astype(int).to_numpy()
    probs = predict_proba(artifact, X)
    preds = (probs >= artifact.decision_threshold).astype(int)
    accuracy = float((preds == y).mean())
    signal_returns = usable.loc[preds == 1, "return_5d"].astype(float)
    if signal_returns.empty:
        avg_return = None
        downside_risk = None
    else:
        avg_return = float(signal_returns.mean())
        negative_signal_returns = signal_returns[signal_returns < 0.0]
        downside_risk = 0.0 if negative_signal_returns.empty else float(abs(negative_signal_returns.mean()))
    brier = _brier_score(y.astype(float), probs.astype(float))
    return {
        "accuracy": round(accuracy, 4),
        "avg_return": round(avg_return, 4) if avg_return is not None else None,
        "brier_score": round(brier, 4),
        "downside_risk": round(downside_risk, 4) if downside_risk is not None else None,
        "positive_predictions": int((preds == 1).sum()),
        "return_bin_counts": {str(k): int(v) for k, v in sorted(usable["return_bin_5d"].fillna("unknown").astype(str).value_counts().to_dict().items())},
        "bucket_metrics": _bucket_metrics(usable, preds, probs),
        "rows": int(len(usable)),
    }


def _numeric_metric(metrics: dict[str, Any], key: str) -> float | None:
    value = metrics.get(key)
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if np.isfinite(numeric) else None


def _decide(candidate: dict[str, Any], production: dict[str, Any], *, min_rows: int = 200) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    rows = int(candidate.get("rows") or 0)
    if rows < min_rows:
        reasons.append(f"candidate rows below minimum ({rows} < {min_rows})")
        return False, reasons

    c_acc = _numeric_metric(candidate, "accuracy")
    p_acc = _numeric_metric(production, "accuracy")
    c_brier = _numeric_metric(candidate, "brier_score")
    p_brier = _numeric_metric(production, "brier_score")
    c_return = _numeric_metric(candidate, "avg_return")
    p_return = _numeric_metric(production, "avg_return")
    c_downside = _numeric_metric(candidate, "downside_risk")
    p_downside = _numeric_metric(production, "downside_risk")
    if None in {c_acc, p_acc, c_brier, p_brier, c_return, p_return, c_downside, p_downside}:
        reasons.append("insufficient comparable accuracy, brier, return, or downside metrics")
        return False, reasons

    accuracy_ok = c_acc > p_acc
    brier_ok = c_brier < p_brier
    return_ok = c_return >= p_return
    downside_ok = c_downside <= p_downside

    if not accuracy_ok:
        reasons.append("candidate accuracy does not exceed production")
    if not brier_ok:
        reasons.append("candidate brier score does not improve production")
    if not (return_ok or downside_ok):
        reasons.append("candidate avg_return is lower and downside_risk is higher than production")

    if accuracy_ok and brier_ok and (return_ok or downside_ok):
        reasons.append("candidate improves accuracy and brier with acceptable return/downside")
        return True, reasons

    reasons.append("candidate did not satisfy profit-aware promotion thresholds")
    return False, reasons


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare candidate model against production model on same holdout.")
    parser.add_argument("--input", default="data/decision_training_snapshot.jsonl")
    parser.add_argument("--production-model", default="data/day1_baseline_model.json")
    parser.add_argument("--candidate-model", default="data/candidate_model.json")
    parser.add_argument("--output", default="data/model_comparison_report.json")
    parser.add_argument("--train-ratio", type=float, default=0.8)
    parser.add_argument("--min-rows", type=int, default=200)
    args = parser.parse_args()

    df = _load_jsonl(args.input)
    if df.empty:
        raise SystemExit("No rows available for model comparison")

    _, test_df = _chronological_split(df, args.train_ratio)
    candidate_metrics = _evaluate(args.candidate_model, test_df.copy())
    production_metrics = _evaluate(args.production_model, test_df.copy())

    candidate_win, reasons = _decide(candidate_metrics, production_metrics, min_rows=max(1, args.min_rows))

    report = {
        "candidate_metrics": candidate_metrics,
        "production_metrics": production_metrics,
        "candidate_win": candidate_win,
        "reasons": reasons,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
