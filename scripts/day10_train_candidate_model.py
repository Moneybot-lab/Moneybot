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
FLAT_OPTIMUM_ABSOLUTE_TOLERANCE = 0.005
FLAT_OPTIMUM_RELATIVE_TOLERANCE = 0.02
UTILITY_BIG_GAIN_WEIGHT = 0.10
UTILITY_DOWNSIDE_WEIGHT = 1.0
UTILITY_BIG_LOSS_WEIGHT = 1.0
CALIBRATION_FRACTION_OF_DEVELOPMENT = 0.20
THRESHOLD_FRACTION_OF_DEVELOPMENT = 0.20
LABEL_HORIZON_DAYS = 5
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


def _chronological_training_periods_with_diagnostics(df: pd.DataFrame, train_ratio: float) -> tuple[list[pd.DataFrame], list[dict[str, Any]]]:
    """Split chronologically and return purged/embargoed periods plus diagnostics."""
    dated_periods = _date_based_training_periods(df, train_ratio)
    if dated_periods is not None:
        fit, calibration, threshold, final_test = dated_periods
    else:
        development, final_test = _chronological_split(df, train_ratio)
        calibration_rows = max(1, int(len(development) * CALIBRATION_FRACTION_OF_DEVELOPMENT))
        threshold_rows = max(1, int(len(development) * THRESHOLD_FRACTION_OF_DEVELOPMENT))
        fit_rows = len(development) - calibration_rows - threshold_rows
        if fit_rows < 1:
            raise ValueError("train_ratio leaves insufficient rows for separate fit, calibration, and threshold periods")
        fit = development.iloc[:fit_rows].copy()
        calibration = development.iloc[fit_rows:fit_rows + calibration_rows].copy()
        threshold = development.iloc[fit_rows + calibration_rows:].copy()
    periods, boundaries = purge_embargo_periods(
        [fit, calibration, threshold, final_test],
        horizon_days=LABEL_HORIZON_DAYS,
        embargo_days=EMBARGO_DAYS,
    )
    if any(period.empty for period in periods):
        if dated_periods is None and _event_dates(df) is None:
            # Preserve support for tiny synthetic/legacy snapshots that have no
            # real event timestamps. They can be trained for smoke testing, but
            # diagnostics explicitly prevent them from certifying split hygiene.
            raw_periods = [fit, calibration, threshold, final_test]
            unavailable = {
                "method": "unavailable_no_event_time",
                "horizon_days": LABEL_HORIZON_DAYS,
                "embargo_days": EMBARGO_DAYS,
                "label_horizon_gap_passed": False,
                "date_overlap_count": 0,
                "symbol_date_overlap_count": 0,
            }
            return raw_periods, [
                {**unavailable, "left_period_index": index, "right_period_index": index + 1}
                for index in range(len(raw_periods) - 1)
            ]
        raise ValueError("purging/embargo leaves an empty fit, calibration, threshold, or final-test period")
    return periods, boundaries


def _event_dates(df: pd.DataFrame) -> pd.Series | None:
    if "event_date" in df.columns:
        times = pd.to_datetime(df["event_date"], utc=True, errors="coerce")
    elif "ts" in df.columns:
        numeric = pd.to_numeric(df["ts"], errors="coerce")
        if numeric.notna().all() and numeric.abs().median() >= 100_000_000:
            times = pd.to_datetime(numeric, unit="s", utc=True, errors="coerce")
        else:
            return None
    else:
        return None
    if times.isna().any():
        return None
    return times.dt.normalize()


def _date_based_training_periods(df: pd.DataFrame, train_ratio: float) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame] | None:
    """Allocate whole event dates so dense dates cannot collapse tuning periods."""
    dates = _event_dates(df)
    if dates is None:
        return None
    unique_dates = sorted(dates.unique())
    # Middle periods lose rows on both sides: the preceding boundary embargoes
    # their first dates and the following boundary purges their last horizon.
    # Reserve enough distinct dates up front rather than relying on row counts;
    # a single busy trading date can contain hundreds of rows.
    minimum_period_dates = LABEL_HORIZON_DAYS + EMBARGO_DAYS + 1
    minimum_dates = minimum_period_dates * 4
    if len(unique_dates) < minimum_dates:
        return None
    requested_development_dates = int(len(unique_dates) * train_ratio)
    development_date_count = min(
        max(requested_development_dates, minimum_period_dates * 3),
        len(unique_dates) - minimum_period_dates,
    )
    development_dates = unique_dates[:development_date_count]
    calibration_date_count = max(
        minimum_period_dates,
        int(len(development_dates) * CALIBRATION_FRACTION_OF_DEVELOPMENT),
    )
    threshold_date_count = max(
        minimum_period_dates,
        int(len(development_dates) * THRESHOLD_FRACTION_OF_DEVELOPMENT),
    )
    fit_date_count = len(development_dates) - calibration_date_count - threshold_date_count
    if fit_date_count < minimum_period_dates:
        return None
    threshold_start = fit_date_count + calibration_date_count
    date_sets = (
        set(development_dates[:fit_date_count]),
        set(development_dates[fit_date_count:threshold_start]),
        set(development_dates[threshold_start:]),
        set(unique_dates[development_date_count:]),
    )
    periods = tuple(df.loc[dates.isin(date_set)].copy() for date_set in date_sets)
    if any(period.empty for period in periods):
        return None
    return periods


def _chronological_training_periods(df: pd.DataFrame, train_ratio: float) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split chronologically into disjoint fit, calibration, threshold, and test periods."""
    periods, _ = _chronological_training_periods_with_diagnostics(df, train_ratio)
    return periods[0], periods[1], periods[2], periods[3]


def _period_window(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"rows": 0, "start": None, "end": None}
    if "event_date" in frame.columns:
        values = frame["event_date"].fillna("").astype(str)
    elif "ts" in frame.columns:
        numeric = pd.to_numeric(frame["ts"], errors="coerce")
        values = pd.to_datetime(numeric, unit="s", utc=True, errors="coerce").astype(str)
    else:
        values = pd.Series(frame.index.astype(str), index=frame.index)
    return {"rows": int(len(frame)), "start": str(values.iloc[0]), "end": str(values.iloc[-1])}


def _flat_optimum_threshold(candidates: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Select the center of the broadest near-optimal utility plateau."""
    peak = max(candidates, key=lambda item: float(item["utility_score"]))
    peak_utility = float(peak["utility_score"])
    tolerance = max(FLAT_OPTIMUM_ABSOLUTE_TOLERANCE, abs(peak_utility) * FLAT_OPTIMUM_RELATIVE_TOLERANCE)
    near_optimal = sorted(
        [item for item in candidates if float(item["utility_score"]) >= peak_utility - tolerance],
        key=lambda item: float(item["threshold"]),
    )
    grid_indexes = {float(value): index for index, value in enumerate(THRESHOLD_SEARCH_VALUES)}
    plateaus: list[list[dict[str, Any]]] = []
    for item in near_optimal:
        if not plateaus:
            plateaus.append([item])
            continue
        previous = plateaus[-1][-1]
        previous_index = grid_indexes.get(float(previous["threshold"]))
        current_index = grid_indexes.get(float(item["threshold"]))
        if previous_index is not None and current_index == previous_index + 1:
            plateaus[-1].append(item)
        else:
            plateaus.append([item])
    plateau = max(
        plateaus,
        key=lambda group: (
            len(group),
            max(float(item["utility_score"]) for item in group),
            -min(float(item.get("big_loss_prediction_rate") or 0.0) for item in group),
        ),
    )
    selected = plateau[(len(plateau) - 1) // 2]
    return selected, {
        "policy": "center_of_broadest_near_optimal_plateau",
        "peak_threshold": float(peak["threshold"]),
        "peak_utility_score": round(peak_utility, 6),
        "utility_tolerance": round(tolerance, 6),
        "near_optimal_thresholds": [float(item["threshold"]) for item in near_optimal],
        "selected_plateau_thresholds": [float(item["threshold"]) for item in plateau],
        "selected_threshold": float(selected["threshold"]),
    }


def _threshold_selection_support(
    frame: pd.DataFrame,
    scored: list[dict[str, Any]],
    *,
    min_positive_predictions: int,
    min_big_gain_examples: int,
    min_independent_dates: int,
    min_unique_symbols: int,
) -> dict[str, Any]:
    positive_counts = [int(item.get("positive_predictions") or 0) for item in scored]
    dates = _event_dates(frame)
    independent_dates = int(dates.nunique()) if dates is not None else 0
    unique_symbols = int(frame["symbol"].fillna("").astype(str).str.upper().nunique()) if "symbol" in frame.columns else 0
    big_gain_examples = int((frame.get("return_bin_5d", pd.Series("", index=frame.index)).fillna("").astype(str) == "big_gain").sum())
    checks = {
        "positive_predictions_passed": max(positive_counts, default=0) >= min_positive_predictions,
        "big_gain_examples_passed": big_gain_examples >= min_big_gain_examples,
        "independent_dates_passed": independent_dates >= min_independent_dates,
        "unique_symbols_passed": unique_symbols >= min_unique_symbols,
    }
    max_positive = max(positive_counts, default=0)
    return {
        "passed": all(checks.values()),
        "rows": int(len(frame)),
        "minimum_positive_predictions": int(min_positive_predictions),
        "maximum_positive_predictions": max_positive,
        "positive_predictions": max_positive,
        "minimum_big_gain_examples": int(min_big_gain_examples),
        "big_gain_examples": big_gain_examples,
        "big_gain_rows": big_gain_examples,
        "minimum_independent_dates": int(min_independent_dates),
        "independent_dates": independent_dates,
        "minimum_unique_symbols": int(min_unique_symbols),
        "unique_symbols": unique_symbols,
        "checks": checks,
    }


def _select_profit_threshold(
    frame: pd.DataFrame,
    probs: np.ndarray,
    *,
    current_threshold: float = DEFAULT_DECISION_THRESHOLD,
    min_positive_predictions: int = MIN_THRESHOLD_SELECTION_POSITIVE_PREDICTIONS,
    min_big_gain_examples: int = MIN_THRESHOLD_SELECTION_BIG_GAIN_EXAMPLES,
    min_independent_dates: int = MIN_THRESHOLD_SELECTION_INDEPENDENT_DATES,
    min_unique_symbols: int = MIN_THRESHOLD_SELECTION_UNIQUE_SYMBOLS,
) -> dict[str, Any]:
    scored: list[dict[str, Any]] = []
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

    support = _threshold_selection_support(
        frame,
        scored,
        min_positive_predictions=min_positive_predictions,
        min_big_gain_examples=min_big_gain_examples,
        min_independent_dates=min_independent_dates,
        min_unique_symbols=min_unique_symbols,
    )
    if not support["passed"]:
        return {
            "threshold": float(current_threshold),
            "utility_score": None,
            "positive_predictions": 0,
            "avg_signal_return": None,
            "big_loss_guardrail": "threshold_selection_support_insufficient",
            "threshold_selection_support": support,
            "threshold_selection_sufficient": False,
            "selection_status": "insufficient_support_keep_current_threshold",
            "search": scored,
        }

    viable = [item for item in scored if isinstance(item.get("utility_score"), (int, float)) and int(item.get("positive_predictions") or 0) >= min_positive_predictions]
    if not viable:
        return {"threshold": float(current_threshold), "utility_score": None, "positive_predictions": 0, "avg_signal_return": None, "big_loss_guardrail": "no_supported_positive_thresholds", "threshold_selection_support": support, "threshold_selection_sufficient": False, "selection_status": "insufficient_positive_thresholds_keep_current_threshold", "search": scored}

    zero_big_loss_viable = [item for item in viable if int(item.get("big_loss_predictions") or 0) == 0]
    guarded = zero_big_loss_viable or viable
    best, flat_optimum = _flat_optimum_threshold(guarded)
    guardrail = "zero_big_loss_predictions" if zero_big_loss_viable else "minimize_big_loss_rate"
    return {**best, "big_loss_guardrail": guardrail, "flat_optimum": flat_optimum, "threshold_selection_support": support, "threshold_selection_sufficient": True, "selection_status": "selected_supported_threshold", "search": scored}


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
    args = parser.parse_args()

    df = _load_jsonl(args.input)
    if df.empty:
        raise SystemExit("No rows available in input dataset")

    training_source = _training_input_manifest(args.input, df)
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
        _future_safe_feature_columns(_select_feature_columns(filtered_target)),
        persisted_feature_columns,
    )
    if not feature_columns:
        raise SystemExit("No numeric feature columns found in decision dataset")

    periods, purge_embargo_boundaries = _chronological_training_periods_with_diagnostics(filtered_target, args.train_ratio)
    fit_raw, calibration_raw, threshold_raw, test_raw = periods
    fit_df, feature_fill_values = _fill_feature_gaps(fit_raw, feature_columns)
    calibration_df = _apply_feature_fill_values(calibration_raw, feature_columns, feature_fill_values)
    threshold_df = _apply_feature_fill_values(threshold_raw, feature_columns, feature_fill_values)
    test_df = _apply_feature_fill_values(test_raw, feature_columns, feature_fill_values)
    clean = pd.concat([fit_df, calibration_df, threshold_df, test_df]).sort_index()
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

    replica = train_logistic_baseline(X_fit, y_fit, sample_weight=sample_weight)
    replica_calibration_artifact = _build_artifact_with_features(replica, feature_columns, version="calibration-reproduction")
    replica_raw_probs = predict_proba(replica_calibration_artifact, calibration_df[feature_columns].to_numpy(dtype=float))
    replica_calibration = fit_probability_calibration(replica_raw_probs, calibration_df[target_column].to_numpy(dtype=float))
    replica.calibration_slope = float(replica_calibration["slope"])
    replica.calibration_intercept = float(replica_calibration["intercept"])
    replica_threshold_probs = predict_proba(_build_artifact_with_features(replica, feature_columns, version="threshold-reproduction"), X_threshold)
    replica_threshold = _select_profit_threshold(threshold_df, replica_threshold_probs)
    replica.decision_threshold = float(replica_threshold.get("threshold") or replica.decision_threshold)
    recipe_parameter_delta = _artifact_parameter_delta(base_artifact, replica)
    recipe_reproduction = {
        "passed": recipe_parameter_delta <= 1e-12,
        "maximum_parameter_delta": recipe_parameter_delta,
        "first_threshold": float(base_artifact.decision_threshold),
        "rerun_threshold": float(replica.decision_threshold),
        "first_calibration": calibration,
        "rerun_calibration": replica_calibration,
    }

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
            "calibration": calibration,
            "recipe_reproduction": recipe_reproduction,
            "training_source": training_source,
            "feature_preference_policy": {
                "preferred_families": ["lagged_returns", "technical", "volume_liquidity", "market_regime", "spy_relative"],
                "app_signal_features_ranked_after_massive_market_features": True,
            },
            "feature_availability_report": _feature_availability_report(clean, feature_columns),
            "training_periods": {
                "fit_rows": len(fit_df),
                "calibration_rows": len(calibration_df),
                "threshold_selection_rows": len(threshold_df),
                "final_test_rows": len(test_df),
                "purged_embargoed": True,
                "label_horizon_days": LABEL_HORIZON_DAYS,
                "embargo_days": EMBARGO_DAYS,
                "windows": {
                    "fit": _period_window(fit_df),
                    "calibration": _period_window(calibration_df),
                    "threshold_selection": _period_window(threshold_df),
                    "final_test": _period_window(test_df),
                },
                "purge_embargo_boundaries": purge_embargo_boundaries,
            },
        }
    )

    save_artifact(artifact, args.output_model)
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
                "selected_decision_threshold": artifact.decision_threshold,
                "threshold_selection": threshold_selection,
                "calibration": calibration,
                "recipe_reproduction": recipe_reproduction,
                "training_source": training_source,
                "feature_preference_policy": metrics["feature_preference_policy"],
                "feature_availability_report": metrics["feature_availability_report"],
                "training_periods": metrics["training_periods"],
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
