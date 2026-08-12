#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
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
    fit_probability_calibration,
    predict_proba,
    save_artifact,
    summarize_binary_predictions,
    train_logistic_baseline,
)
from moneybot.services.model_metadata import append_artifact_history, build_artifact_metadata, save_artifact_metadata
from moneybot.services.temporal_validation import purge_embargo_periods
from moneybot.services.decision_target import (
    HORIZON_DAYS,
    POSITIVE_RETURN_BUCKETS,
    RETURN_BIN_EDGES,
    TARGET_NAME,
    target_metadata,
)

TARGET_GAIN_BUCKETS = set(POSITIVE_RETURN_BUCKETS)
RETURN_BIN_SAMPLE_WEIGHTS = {
    "big_loss": 3.0,
    "loss": 1.5,
    "flat": 0.5,
    "gain": 1.25,
    "big_gain": 4.0,
}
THRESHOLD_SEARCH_VALUES = (0.55, 0.575, 0.60, 0.625, 0.65, 0.675, 0.70)
FLAT_OPTIMUM_ABSOLUTE_TOLERANCE = 0.005
FLAT_OPTIMUM_RELATIVE_TOLERANCE = 0.02
UTILITY_BIG_GAIN_WEIGHT = 0.10
UTILITY_DOWNSIDE_WEIGHT = 1.0
UTILITY_BIG_LOSS_WEIGHT = 1.0
CALIBRATION_FRACTION_OF_DEVELOPMENT = 0.20
THRESHOLD_FRACTION_OF_DEVELOPMENT = 0.20
LABEL_HORIZON_DAYS = HORIZON_DAYS
EMBARGO_DAYS = 1
DEFAULT_DECISION_THRESHOLD = 0.55
MIN_THRESHOLD_SELECTION_POSITIVE_PREDICTIONS = 10
MIN_THRESHOLD_SELECTION_BIG_GAIN_EXAMPLES = 10
MIN_THRESHOLD_SELECTION_INDEPENDENT_DATES = 5
MIN_THRESHOLD_SELECTION_UNIQUE_SYMBOLS = 5

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

RETURN_BIN_EDGES = (-0.03, -0.005, 0.005, 0.03)
TARGET_GAIN_BUCKETS = {"gain", "big_gain"}
RETURN_BIN_SAMPLE_WEIGHTS = {
    "big_loss": 3.0,
    "loss": 1.5,
    "flat": 0.5,
    "gain": 1.25,
    "big_gain": 4.0,
}

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

RETURN_BIN_EDGES = (-0.03, -0.005, 0.005, 0.03)
TARGET_GAIN_BUCKETS = {"gain", "big_gain"}
RETURN_BIN_SAMPLE_WEIGHTS = {
    "big_loss": 3.0,
    "loss": 1.5,
    "flat": 0.5,
    "gain": 1.25,
    "big_gain": 4.0,
}

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


MASSIVE_PREFERRED_FEATURE_TOKENS = (
    "_lagged",
    "sma",
    "ema",
    "rsi",
    "macd",
    "atr",
    "trend",
    "momentum",
    "volatility",
    "volume",
    "liquidity",
    "vwap",
    "dollar_volume",
    "market_regime",
    "market_volatility",
    "spy",
    "relative",
    "beta",
    "symbol_minus_spy",
    "sector_relative",
)


def _feature_preference_rank(column: str) -> tuple[int, str]:
    lowered = column.lower()
    if any(token in lowered for token in MASSIVE_PREFERRED_FEATURE_TOKENS):
        return (0, column)
    if lowered in APP_SIGNAL_FEATURE_COLUMNS:
        return (2, column)
    return (1, column)


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
    return sorted(cols, key=_feature_preference_rank)


FUTURE_LEAKAGE_FEATURE_EXACT = {"feature_return_1d", "feature_return_5d"}
FUTURE_LEAKAGE_FEATURE_FRAGMENTS = ("forward_return", "future_return", "realized_return", "outcome_", "label_")


def _future_safe_feature_columns(feature_columns: list[str]) -> list[str]:
    """Reject labels, outcomes, and unlagged/future returns from model inputs."""
    safe: list[str] = []
    for column in feature_columns:
        lowered = column.lower()
        if lowered in FUTURE_LEAKAGE_FEATURE_EXACT:
            continue
        if any(fragment in lowered for fragment in FUTURE_LEAKAGE_FEATURE_FRAGMENTS):
            continue
        safe.append(column)
    return safe


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


def _apply_feature_fill_values(df: pd.DataFrame, feature_columns: list[str], fill_values: dict[str, float]) -> pd.DataFrame:
    """Apply fit-period feature medians without learning from later periods."""
    out = df.copy()
    for col in feature_columns:
        numeric = pd.to_numeric(out[col], errors="coerce").replace([np.inf, -np.inf], np.nan)
        out[col] = numeric.fillna(float(fill_values.get(col, 0.0))).astype(float)
    return out


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
        calibration_slope=base.calibration_slope,
        calibration_intercept=base.calibration_intercept,
        lineage=base.lineage,
    )


def _candidate_lineage(artifact: BaselineModelArtifact, feature_columns: list[str]) -> dict[str, Any]:
    recipe = {
        "model_family": "logistic_regression",
        "feature_subset": list(feature_columns),
        "sample_weight_policy": RETURN_BIN_SAMPLE_WEIGHTS,
        "calibration": {"method": "identity" if artifact.calibration_slope == 1.0 and artifact.calibration_intercept == 0.0 else "platt", "slope": artifact.calibration_slope, "intercept": artifact.calibration_intercept},
        "decision_threshold": artifact.decision_threshold,
        "abstention": {"enabled": False, "margin": 0.0},
    }
    recipe_hash = hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {
        "schema_version": "moneybot-challenger-lineage.v1",
        "lineage_id": f"recipe-{recipe_hash[:16]}",
        "recipe_hash": recipe_hash,
        "generation": 1,
        "parent_lineage_ids": [],
        "recipe": recipe,
    }


def _average_optional_return(frame: pd.DataFrame, column: str) -> float | None:
    if column not in frame.columns:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    if values.empty:
        return None
    return round(float(values.mean()), 4)


def _feature_availability_report(frame: pd.DataFrame, feature_columns: list[str]) -> dict[str, Any]:
    rows = int(len(frame))
    features: dict[str, Any] = {}
    for feature in feature_columns:
        if feature in frame.columns:
            non_null = int(pd.to_numeric(frame[feature], errors="coerce").notna().sum())
        else:
            non_null = 0
        fill_count = max(0, rows - non_null)
        features[feature] = {
            "non_null_count": non_null,
            "fill_count": fill_count,
            "fill_rate": round(fill_count / rows, 6) if rows else 0.0,
            "availability_rate": round(non_null / rows, 6) if rows else 0.0,
        }
    return {"rows": rows, "feature_count": len(feature_columns), "features": features}


def _training_input_manifest(input_path: str, frame: pd.DataFrame | None = None) -> dict[str, Any]:
    path = Path(input_path)
    manifest_path = path.with_suffix(path.suffix + ".manifest.json")
    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {"manifest_error": "invalid_json"}
    leakage_guards: list[str] = []
    if frame is not None and "leakage_guard" in frame.columns:
        leakage_guards = sorted(str(value) for value in frame["leakage_guard"].dropna().astype(str).unique())
    return {
        "input_path": str(path),
        "canonical_training_input": "data/track_b/decision_training_snapshot_massive.jsonl",
        "uses_massive_canonical_input": path.name == "decision_training_snapshot_massive.jsonl",
        "manifest_path": str(manifest_path),
        "manifest_loaded": bool(manifest),
        "schema_version": manifest.get("schema_version"),
        "leakage_safe": bool(manifest.get("leakage_safe")),
        "join_policy": manifest.get("join_policy"),
        "leakage_guard_values": leakage_guards,
        "massive_manifest": manifest,
    }


def _artifact_parameter_delta(left: BaselineModelArtifact, right: BaselineModelArtifact) -> float:
    values = [
        abs(float(left.bias) - float(right.bias)),
        abs(float(left.decision_threshold) - float(right.decision_threshold)),
        abs(float(left.calibration_slope) - float(right.calibration_slope)),
        abs(float(left.calibration_intercept) - float(right.calibration_intercept)),
    ]
    for left_values, right_values in ((left.means, right.means), (left.stds, right.stds), (left.weights, right.weights)):
        values.extend(abs(float(a) - float(b)) for a, b in zip(left_values, right_values))
    return max(values, default=0.0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a candidate model from logged decision outcomes.")
    parser.add_argument("--input", default="data/decision_training_snapshot.jsonl")
    parser.add_argument("--output-model", default="data/candidate_model.json")
    parser.add_argument("--train-ratio", type=float, default=0.8)
    parser.add_argument("--min-rows", type=int, default=200)
    parser.add_argument("--cleaned-train")
    parser.add_argument("--cleaned-test")
    parser.add_argument("--cleaned-all")
    parser.add_argument("--model-version", default="candidate_market_no_echo_v1")
    args = parser.parse_args()

    cleaned_paths = (args.cleaned_train, args.cleaned_test, args.cleaned_all)
    if any(cleaned_paths):
        if not all(cleaned_paths):
            raise SystemExit("--cleaned-train, --cleaned-test, and --cleaned-all must be provided together")
        from scripts.train_massive_baseline_model import train_massive_market_model

        report = train_massive_market_model(
            Path(args.cleaned_train),
            Path(args.cleaned_test),
            Path(args.cleaned_all),
            Path(args.output_model),
            model_version=args.model_version,
            report_prefix="candidate_market_no_echo_v1",
        )
        print(json.dumps(report, indent=2))
        return

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

    X_fit = fit_df[feature_columns].to_numpy(dtype=float)
    y_fit = fit_df[target_column].to_numpy(dtype=float)
    sample_weight = _bucket_sample_weights(fit_df).to_numpy(dtype=float)
    base_artifact = train_logistic_baseline(X_fit, y_fit, sample_weight=sample_weight)

    calibration_artifact = _build_artifact_with_features(base_artifact, feature_columns, version="calibration-fit")
    raw_calibration_probs = predict_proba(calibration_artifact, calibration_df[feature_columns].to_numpy(dtype=float))
    calibration = fit_probability_calibration(raw_calibration_probs, calibration_df[target_column].to_numpy(dtype=float))
    base_artifact.calibration_slope = float(calibration["slope"])
    base_artifact.calibration_intercept = float(calibration["intercept"])

    X_threshold = threshold_df[feature_columns].to_numpy(dtype=float)
    threshold_probs = predict_proba(_build_artifact_with_features(base_artifact, feature_columns, version="threshold-search"), X_threshold)
    threshold_selection = _select_profit_threshold(threshold_df, threshold_probs)
    threshold_selection["selection_rows"] = int(len(threshold_df))
    base_artifact.decision_threshold = float(threshold_selection.get("threshold") or base_artifact.decision_threshold)
    candidate_version = f"candidate-logreg-v1-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    artifact = _build_artifact_with_features(base_artifact, feature_columns, version=candidate_version)
    artifact.lineage = _candidate_lineage(artifact, feature_columns)

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
    artifact_payload = json.loads(Path(args.output_model).read_text(encoding="utf-8"))
    artifact_payload.update(target_metadata())
    Path(args.output_model).write_text(
        json.dumps(artifact_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    metadata = build_artifact_metadata(
        model_path=args.output_model,
        model_version=artifact.version,
        input_path=args.input,
        train_rows=len(fit_df) + len(calibration_df) + len(threshold_df),
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
