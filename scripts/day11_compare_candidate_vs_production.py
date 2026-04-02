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


def _evaluate(artifact_path: str, test_df: pd.DataFrame) -> dict[str, Any]:
    if not Path(artifact_path).exists():
        return {"accuracy": None, "avg_return": None, "brier_score": None, "rows": 0}
    artifact = load_artifact(artifact_path)
    for col in artifact.feature_columns:
        if col not in test_df.columns:
            test_df[col] = np.nan
    usable = test_df.dropna(subset=artifact.feature_columns + ["return_5d"]).copy()
    if usable.empty:
        return {"accuracy": None, "avg_return": None, "brier_score": None, "rows": 0}

    X = usable[artifact.feature_columns].to_numpy(dtype=float)
    y = (usable["return_5d"].astype(float) > 0.0).astype(int).to_numpy()
    probs = predict_proba(artifact, X)
    preds = (probs >= artifact.decision_threshold).astype(int)
    accuracy = float((preds == y).mean())
    avg_return = float(usable["return_5d"].mean())
    brier = _brier_score(y.astype(float), probs.astype(float))
    return {
        "accuracy": round(accuracy, 4),
        "avg_return": round(avg_return, 4),
        "brier_score": round(brier, 4),
        "rows": int(len(usable)),
    }


def _decide(candidate: dict[str, Any], production: dict[str, Any], *, min_rows: int = 200) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    rows = int(candidate.get("rows") or 0)
    if rows < min_rows:
        reasons.append(f"candidate rows below minimum ({rows} < {min_rows})")
        return False, reasons

    c_acc = candidate.get("accuracy")
    p_acc = production.get("accuracy")
    c_brier = candidate.get("brier_score")
    p_brier = production.get("brier_score")
    if None in {c_acc, p_acc, c_brier, p_brier}:
        reasons.append("insufficient comparable metrics")
        return False, reasons

    if c_acc >= p_acc + 0.02:
        reasons.append("candidate accuracy exceeds production by at least 0.02")
        return True, reasons

    if c_acc >= p_acc and c_brier <= p_brier - 0.01:
        reasons.append("candidate matches/exceeds accuracy and improves brier by at least 0.01")
        return True, reasons

    reasons.append("candidate did not satisfy promotion thresholds")
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
