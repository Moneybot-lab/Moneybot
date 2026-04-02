#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moneybot.services.deterministic_model import (
    BaselineModelArtifact,
    classify,
    save_artifact,
    summarize_binary_predictions,
    train_logistic_baseline,
)
from moneybot.services.model_metadata import append_artifact_history, build_artifact_metadata, save_artifact_metadata

RESERVED_COLUMNS = {
    "ts",
    "symbol",
    "endpoint",
    "decision_source",
    "recommendation",
    "probability_up",
    "model_version",
    "return_1d",
    "return_5d",
    "outcome_1d",
    "outcome_5d",
}


def _load_jsonl(path: str) -> pd.DataFrame:
    rows: list[dict] = []
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


def _select_feature_columns(df: pd.DataFrame) -> list[str]:
    cols: list[str] = []
    for col in df.columns:
        if col in RESERVED_COLUMNS:
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            cols.append(str(col))
    return sorted(cols)


def _chronological_split(df: pd.DataFrame, train_ratio: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    pivot = int(len(df) * train_ratio)
    if pivot <= 0 or pivot >= len(df):
        raise ValueError("train_ratio creates an empty train or test split")
    return df.iloc[:pivot].copy(), df.iloc[pivot:].copy()


def _build_artifact_with_features(base: BaselineModelArtifact, feature_columns: list[str]) -> BaselineModelArtifact:
    return BaselineModelArtifact(
        version="candidate-logreg-v1",
        feature_columns=list(feature_columns),
        means=base.means,
        stds=base.stds,
        weights=base.weights,
        bias=base.bias,
        decision_threshold=base.decision_threshold,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a candidate model from logged decision outcomes.")
    parser.add_argument("--input", default="data/decision_training_snapshot.jsonl")
    parser.add_argument("--output-model", default="data/candidate_model.json")
    parser.add_argument("--train-ratio", type=float, default=0.8)
    parser.add_argument("--min-rows", type=int, default=200)
    args = parser.parse_args()

    df = _load_jsonl(args.input)
    if df.empty:
        raise SystemExit("No rows available in input dataset")

    if "ts" in df.columns:
        df = df.sort_values("ts").reset_index(drop=True)

    feature_columns = _select_feature_columns(df)
    if not feature_columns:
        raise SystemExit("No numeric feature columns found in decision dataset")

    clean = df.dropna(subset=feature_columns + ["return_5d"]).copy()
    clean["label_up_5d"] = (clean["return_5d"].astype(float) > 0.0).astype(float)

    if len(clean) < max(1, args.min_rows):
        raise SystemExit(f"Not enough rows to train candidate model (have={len(clean)}, need={args.min_rows})")

    train_df, test_df = _chronological_split(clean, args.train_ratio)

    X_train = train_df[feature_columns].to_numpy(dtype=float)
    y_train = train_df["label_up_5d"].to_numpy(dtype=float)
    base_artifact = train_logistic_baseline(X_train, y_train)
    artifact = _build_artifact_with_features(base_artifact, feature_columns)

    X_test = test_df[feature_columns].to_numpy(dtype=float)
    y_test = test_df["label_up_5d"].to_numpy(dtype=float)
    y_pred = classify(artifact, X_test)
    metrics = summarize_binary_predictions(y_test, y_pred)
    metrics.update(
        {
            "avg_return_1d": round(float(test_df["return_1d"].dropna().mean()), 4) if test_df["return_1d"].notna().any() else None,
            "avg_return_5d": round(float(test_df["return_5d"].dropna().mean()), 4) if test_df["return_5d"].notna().any() else None,
        }
    )

    save_artifact(artifact, args.output_model)
    metadata = build_artifact_metadata(
        model_path=args.output_model,
        model_version=artifact.version,
        input_path=args.input,
        train_rows=len(train_df),
        test_rows=len(test_df),
        metrics=metrics,
        train_ratio=args.train_ratio,
        horizon_days=5,
        target_return=0.0,
    )
    metadata_path = save_artifact_metadata(args.output_model, metadata)
    history_path = append_artifact_history(args.output_model, metadata)

    print(f"Saved candidate model -> {args.output_model}")
    print(f"Saved metadata -> {metadata_path}")
    print(f"Updated history -> {history_path}")
    print(json.dumps({"rows": len(clean), "feature_columns": feature_columns, "metrics": metrics}, sort_keys=True))


if __name__ == "__main__":
    main()
