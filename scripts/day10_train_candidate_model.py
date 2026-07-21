#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moneybot.services.deterministic_model import (
    BaselineModelArtifact,
    classify,
    predict_proba,
    save_artifact,
    summarize_binary_predictions,
    train_logistic_baseline,
)
from moneybot.services.model_metadata import append_artifact_history, build_artifact_metadata, save_artifact_metadata

RETURN_BIN_EDGES = (-0.03, -0.005, 0.005, 0.03)
TARGET_GAIN_BUCKETS = {"gain", "big_gain"}
RETURN_BIN_SAMPLE_WEIGHTS = {
    "big_loss": 3.0,
    "loss": 1.5,
    "flat": 0.5,
    "gain": 1.25,
    "big_gain": 4.0,
}
THRESHOLD_SEARCH_VALUES = (0.55, 0.575, 0.60, 0.625, 0.65, 0.675, 0.70)
UTILITY_BIG_GAIN_WEIGHT = 0.10
UTILITY_DOWNSIDE_WEIGHT = 1.0
UTILITY_BIG_LOSS_WEIGHT = 1.0

APP_SIGNAL_FEATURE_COLUMNS = {
    "feature_endpoint_hot_momentum_buys",
    "feature_endpoint_quick_ask",
    "feature_endpoint_user_watchlist",
    "feature_probability_up",
    "feature_rec_buy",
    "feature_rec_hold",
    "feature_rec_hold_off_for_now",
    "feature_rec_negative",
    "feature_rec_positive",
    "feature_rec_sell",
    "feature_rec_strong_buy",
    "feature_source_ai_enhanced",
    "feature_source_deterministic_model",
    "feature_source_rule_based",
}

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


def _profit_utility_score(frame: pd.DataFrame, preds: np.ndarray) -> float | None:
    signal_returns = pd.to_numeric(frame.loc[preds == 1, "return_5d"], errors="coerce").dropna()
    if signal_returns.empty:
        return None

    bins = frame["return_bin_5d"].fillna("").astype(str)
    big_loss = bins == "big_loss"
    big_gain = bins == "big_gain"
    avg_return = float(signal_returns.mean())
    negative_signal_returns = signal_returns[signal_returns < 0.0]
    downside = 0.0 if negative_signal_returns.empty else float(abs(negative_signal_returns.mean()))
    big_loss_rate = float((preds[big_loss.to_numpy()] == 1).sum() / int(big_loss.sum())) if int(big_loss.sum()) else 0.0
    big_gain_rate = float((preds[big_gain.to_numpy()] == 1).sum() / int(big_gain.sum())) if int(big_gain.sum()) else 0.0
    return (
        avg_return
        - (UTILITY_DOWNSIDE_WEIGHT * downside)
        - (UTILITY_BIG_LOSS_WEIGHT * big_loss_rate)
        + (UTILITY_BIG_GAIN_WEIGHT * big_gain_rate)
    )


def _threshold_selection_frame(train_df: pd.DataFrame) -> pd.DataFrame:
    validation_rows = int(len(train_df) * 0.25)
    if validation_rows >= 20 and validation_rows < len(train_df):
        return train_df.tail(validation_rows).copy()
    return train_df.copy()


def _select_profit_threshold(frame: pd.DataFrame, probs: np.ndarray) -> dict[str, Any]:
    scored: list[dict[str, float | int | None | bool]] = []
    bins = frame["return_bin_5d"].fillna("").astype(str)
    big_loss = (bins == "big_loss").to_numpy()
    big_gain = (bins == "big_gain").to_numpy()
    big_loss_rows = int(big_loss.sum())
    big_gain_rows = int(big_gain.sum())
    for threshold in THRESHOLD_SEARCH_VALUES:
        preds = (probs >= threshold).astype(int)
        utility = _profit_utility_score(frame, preds)
        signal_returns = pd.to_numeric(frame.loc[preds == 1, "return_5d"], errors="coerce").dropna()
        big_loss_predictions = int((preds[big_loss] == 1).sum()) if big_loss_rows else 0
        big_gain_predictions = int((preds[big_gain] == 1).sum()) if big_gain_rows else 0
        scored.append(
            {
                "threshold": float(threshold),
                "utility_score": round(float(utility), 6) if utility is not None else None,
                "positive_predictions": int((preds == 1).sum()),
                "avg_signal_return": round(float(signal_returns.mean()), 6) if not signal_returns.empty else None,
                "big_loss_rows": big_loss_rows,
                "big_loss_predictions": big_loss_predictions,
                "big_loss_prediction_rate": round(big_loss_predictions / big_loss_rows, 6) if big_loss_rows else None,
                "big_gain_rows": big_gain_rows,
                "big_gain_predictions": big_gain_predictions,
                "big_gain_capture_rate": round(big_gain_predictions / big_gain_rows, 6) if big_gain_rows else None,
            }
        )

    viable = [item for item in scored if isinstance(item.get("utility_score"), (int, float)) and int(item.get("positive_predictions") or 0) > 0]
    if not viable:
        return {"threshold": 0.55, "utility_score": None, "positive_predictions": 0, "avg_signal_return": None, "big_loss_guardrail": "no_positive_thresholds", "search": scored}

    zero_big_loss_viable = [item for item in viable if int(item.get("big_loss_predictions") or 0) == 0]
    guarded = zero_big_loss_viable or viable
    best = max(
        guarded,
        key=lambda item: (
            float(item["utility_score"] or 0.0),
            -float(item.get("big_loss_prediction_rate") or 0.0),
            float(item.get("avg_signal_return") or 0.0),
            -abs(float(item["threshold"] or 0.55) - 0.55),
        ),
    )
    guardrail = "zero_big_loss_predictions" if zero_big_loss_viable else "minimize_big_loss_rate"
    return {**best, "big_loss_guardrail": guardrail, "search": scored}


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


def _backtest_compatible_feature_columns(feature_columns: list[str], persisted_feature_columns: set[str]) -> list[str]:
    """Keep derived app-signal features only when they are persisted upstream.

    Day 10 can derive app-signal columns from raw row fields for local
    experiments, but downstream Track B backtests often read the persisted flat
    feature store directly. If an artifact is trained on derived columns that
    were not written to that store, the backtest step raises a KeyError when it
    indexes the frame by artifact feature names.
    """

    return [
        col
        for col in feature_columns
        if col not in APP_SIGNAL_FEATURE_COLUMNS or col in persisted_feature_columns
    ]


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
    out["feature_rec_buy"] = (recommendation == "BUY").astype(float)
    out["feature_rec_sell"] = (recommendation == "SELL").astype(float)
    out["feature_rec_hold"] = (recommendation == "HOLD").astype(float)
    out["feature_rec_hold_off_for_now"] = (recommendation == "HOLD OFF FOR NOW").astype(float)
    out["feature_rec_strong_buy"] = (recommendation == "STRONG BUY").astype(float)
    out["feature_rec_positive"] = recommendation.isin({"BUY", "STRONG BUY"}).astype(float)
    out["feature_rec_negative"] = recommendation.isin({"SELL", "HOLD OFF FOR NOW"}).astype(float)
    prob = out["probability_up"] if "probability_up" in out.columns else pd.Series(np.nan, index=out.index)
    prob_numeric = pd.to_numeric(prob, errors="coerce")
    if "feature_probability_up" in out.columns:
        existing_prob = pd.to_numeric(out["feature_probability_up"], errors="coerce")
        prob_numeric = existing_prob.combine_first(prob_numeric)
    out["feature_probability_up"] = prob_numeric.fillna(0.5).astype(float)
    endpoint = out["endpoint"] if "endpoint" in out.columns else pd.Series("", index=out.index)
    endpoint = endpoint.fillna("").astype(str).str.lower()
    out["feature_endpoint_quick_ask"] = (endpoint == "quick_ask").astype(float)
    out["feature_endpoint_hot_momentum_buys"] = (endpoint == "hot_momentum_buys").astype(float)
    out["feature_endpoint_user_watchlist"] = (endpoint == "user_watchlist").astype(float)
    source = out["decision_source"] if "decision_source" in out.columns else pd.Series("", index=out.index)
    source = source.fillna("").astype(str).str.lower()
    out["feature_source_ai_enhanced"] = (source == "ai_enhanced").astype(float)
    out["feature_source_deterministic_model"] = (source == "deterministic_model").astype(float)
    out["feature_source_rule_based"] = (source == "rule_based").astype(float)
    return out


def _chronological_split(df: pd.DataFrame, train_ratio: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    pivot = int(len(df) * train_ratio)
    if pivot <= 0 or pivot >= len(df):
        raise ValueError("train_ratio creates an empty train or test split")
    return df.iloc[:pivot].copy(), df.iloc[pivot:].copy()


def _build_artifact_with_features(base: BaselineModelArtifact, feature_columns: list[str], *, version: str) -> BaselineModelArtifact:
    return BaselineModelArtifact(
        version=version,
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

    persisted_feature_columns = {str(col) for col in df.columns if str(col).startswith("feature_")}

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

    feature_columns = _backtest_compatible_feature_columns(
        _select_feature_columns(filtered_target),
        persisted_feature_columns,
    )
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
    threshold_df = _threshold_selection_frame(train_df)
    X_threshold = threshold_df[feature_columns].to_numpy(dtype=float)
    train_probs = predict_proba(_build_artifact_with_features(base_artifact, feature_columns, version="threshold-search"), X_threshold)
    threshold_selection = _select_profit_threshold(threshold_df, train_probs)
    threshold_selection["selection_rows"] = int(len(threshold_df))
    base_artifact.decision_threshold = float(threshold_selection.get("threshold") or base_artifact.decision_threshold)
    candidate_version = f"candidate-logreg-v1-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    artifact = _build_artifact_with_features(base_artifact, feature_columns, version=candidate_version)

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
            "selected_decision_threshold": artifact.decision_threshold,
            "threshold_selection": threshold_selection,
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
                "selected_decision_threshold": artifact.decision_threshold,
                "threshold_selection": threshold_selection,
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
