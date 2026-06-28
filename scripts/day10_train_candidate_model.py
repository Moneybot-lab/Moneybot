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

RETURN_BIN_EDGES = (-0.03, -0.005, 0.005, 0.03)
TARGET_GAIN_BUCKETS = {"gain", "big_gain"}
RETURN_BIN_SAMPLE_WEIGHTS = {"big_loss": 2.0, "loss": 1.25, "flat": 0.75, "gain": 1.0, "big_gain": 2.0}

RESERVED_COLUMNS = {
    "ts",
    "symbol",
    "endpoint",
    "decision_source",
    "recommendation",
    "model_version",
    "return_5d",
    "outcome_1d",
    "outcome_5d",
    "return_bin_5d",
    "label_up_5d",
    "label_gain_5d",
}


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


def _ensure_return_bucket_labels(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "return_bin_5d" not in out.columns:
        returns = pd.to_numeric(out.get("return_5d"), errors="coerce")
        out["return_bin_5d"] = [_return_bin(value) if pd.notna(value) else None for value in returns]
    bins = out["return_bin_5d"].fillna("").astype(str)
    out["label_gain_5d"] = bins.isin(TARGET_GAIN_BUCKETS).astype(float)
    return out


def _bucket_sample_weights(df: pd.DataFrame) -> pd.Series:
    bins = df.get("return_bin_5d", pd.Series("", index=df.index)).fillna("").astype(str)
    return bins.map(RETURN_BIN_SAMPLE_WEIGHTS).fillna(1.0).astype(float)


def _bucket_counts(df: pd.DataFrame) -> dict[str, int]:
    if "return_bin_5d" not in df.columns:
        return {}
    counts = df["return_bin_5d"].fillna("unknown").astype(str).value_counts().to_dict()
    return {str(key): int(value) for key, value in sorted(counts.items())}


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
        if not str(col).startswith("feature_"):
            continue
        numeric = pd.to_numeric(df[col], errors="coerce")
        if numeric.notna().any():
            cols.append(str(col))
    return sorted(cols)


def _fill_feature_gaps(df: pd.DataFrame, feature_columns: list[str]) -> tuple[pd.DataFrame, dict[str, float]]:
    """Coerce sparse feature columns to numeric and median-fill missing values.

    Decision logs come from multiple endpoints and app versions, so feature maps are
    naturally sparse. Requiring every selected feature to be present on the same
    row can drop an otherwise large labeled dataset to zero rows.
    """
    out = df.copy()
    fill_values: dict[str, float] = {}
    for col in feature_columns:
        numeric = pd.to_numeric(out[col], errors="coerce").replace([np.inf, -np.inf], np.nan)
        median = numeric.median(skipna=True)
        fill_value = float(median) if pd.notna(median) else 0.0
        out[col] = numeric.fillna(fill_value).astype(float)
        fill_values[col] = fill_value
    return out, fill_values


def _prepare_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    recommendation = out["recommendation"] if "recommendation" in out.columns else pd.Series("", index=out.index)
    recommendation = recommendation.fillna("").astype(str).str.upper()
    out["rec_buy"] = (recommendation == "BUY").astype(float)
    out["rec_sell"] = (recommendation == "SELL").astype(float)
    out["rec_hold"] = recommendation.isin({"HOLD", "HOLD OFF FOR NOW"}).astype(float)
    prob = out["probability_up"] if "probability_up" in out.columns else pd.Series(np.nan, index=out.index)
    out["probability_up_filled"] = pd.to_numeric(prob, errors="coerce").fillna(0.5)
    return out


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
    df = _prepare_frame(df)

    target_column = "label_up_5d"
    rows_loaded = len(df)
    if target_column not in df.columns:
        if "return_5d" in df.columns:
            df[target_column] = (pd.to_numeric(df["return_5d"], errors="coerce") > 0.0).astype(float)
        else:
            raise SystemExit("Missing target column label_up_5d and unable to derive from return_5d")

    df = _ensure_return_bucket_labels(df)
    target_column = "label_gain_5d"
    df[target_column] = pd.to_numeric(df[target_column], errors="coerce")
    filtered_target = df.dropna(subset=[target_column]).copy()
    rows_after_target_filter = len(filtered_target)

    feature_columns = _select_feature_columns(filtered_target)
    if not feature_columns:
        raise SystemExit("No numeric feature columns found in decision dataset")

    clean, feature_fill_values = _fill_feature_gaps(filtered_target, feature_columns)
    rows_after_feature_filter = len(clean)

    if len(clean) < max(1, args.min_rows):
        raise SystemExit(f"Not enough rows to train candidate model (have={len(clean)}, need={args.min_rows})")

    train_df, test_df = _chronological_split(clean, args.train_ratio)

    X_train = train_df[feature_columns].to_numpy(dtype=float)
    y_train = train_df[target_column].to_numpy(dtype=float)
    sample_weight = _bucket_sample_weights(train_df).to_numpy(dtype=float)
    base_artifact = train_logistic_baseline(X_train, y_train, sample_weight=sample_weight)
    artifact = _build_artifact_with_features(base_artifact, feature_columns)

    X_test = test_df[feature_columns].to_numpy(dtype=float)
    y_test = test_df[target_column].to_numpy(dtype=float)
    y_pred = classify(artifact, X_test)
    metrics = summarize_binary_predictions(y_test, y_pred)
    metrics.update(
        {
            "avg_return_1d": round(float(test_df["return_1d"].dropna().mean()), 4) if test_df["return_1d"].notna().any() else None,
            "avg_return_5d": round(float(test_df["return_5d"].dropna().mean()), 4) if test_df["return_5d"].notna().any() else None,
            "return_bin_counts": _bucket_counts(test_df),
            "return_bin_sample_weights": RETURN_BIN_SAMPLE_WEIGHTS,
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

    print(
        json.dumps(
            {
                "rows_loaded": rows_loaded,
                "rows_after_target_filter": rows_after_target_filter,
                "rows_after_feature_filter": rows_after_feature_filter,
                "selected_feature_columns": feature_columns,
                "feature_fill_values": feature_fill_values,
                "target_column": target_column,
                "return_bin_counts": _bucket_counts(clean),
                "return_bin_sample_weights": RETURN_BIN_SAMPLE_WEIGHTS,
            },
            sort_keys=True,
        )
    )
    print(f"Saved candidate model -> {args.output_model}")
    print(f"Saved metadata -> {metadata_path}")
    print(f"Updated history -> {history_path}")
    print(json.dumps({"rows": len(clean), "feature_columns": feature_columns, "metrics": metrics}, sort_keys=True))


if __name__ == "__main__":
    main()
