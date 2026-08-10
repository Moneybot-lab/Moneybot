#!/usr/bin/env python3
"""Train the leakage-safe Massive market-only baseline comparator."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moneybot.services.deterministic_model import (
    BaselineModelArtifact,
    fit_probability_calibration,
    predict_proba,
    save_artifact,
    train_logistic_baseline,
)
from moneybot.services.temporal_validation import purge_embargo_periods
from moneybot.services.alpha_atlas_v3_features import (
    ALPHA_ATLAS_V3_FEATURES,
    validate_v3_feature_columns,
)
from moneybot.services.decision_target import (
    RETURN_COLUMN,
    TARGET_NAME,
    target_metadata,
)

VERSION = "massive_baseline_model_v1"
TARGET_COLUMN = TARGET_NAME
THRESHOLDS = (0.50, 0.525, 0.55, 0.575, 0.60, 0.625, 0.65, 0.675, 0.70)
MIN_POSITIVES = 10
MIN_SYMBOLS = 5
MIN_DATES = 5
MIN_BIG_GAINS = 10
MAX_CONCENTRATION = 0.50
GROUP_COLUMNS = ("symbol", "event_date", "endpoint", "decision_source")
MODEL_ECHO_FEATURES = {
    "feature_probability_up",
    "feature_probability_up_delta_from_last_signal",
    "feature_previous_recommendation_buy",
    "feature_recommendation_changed",
    "feature_symbol_buy_count_7d",
    "feature_symbol_sell_count_7d",
    "feature_symbol_signal_count_7d",
    "feature_days_since_last_signal",
}
FORBIDDEN_EXACT = {
    "return_5d",
    "label_up_5d",
    "label_asof_date",
    "feature_return_5d",
    "probability_up",
    "model_version",
    "recommendation",
}
FORBIDDEN_FRAGMENTS = ("future_", "forward_", "outcome_", "realized_return", "label_")
MARKET_FEATURE_TOKENS = (
    "lagged",
    "return",
    "rsi",
    "macd",
    "sma",
    "ema",
    "vwap",
    "trend",
    "volume",
    "liquidity",
    "volatility",
    "atr",
    "regime",
    "spy",
    "sector",
    "relative",
    "price_to_",
    "moving_average",
    "beta",
)


def _load_jsonl(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"Required cleaned Massive input does not exist: {path}"
        )
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return pd.DataFrame(rows)


def _market_feature_columns(frame: pd.DataFrame) -> list[str]:
    selected: list[str] = []
    for raw_name in frame.columns:
        name = str(raw_name)
        lowered = name.lower()
        if (
            not lowered.startswith("feature_")
            or name in MODEL_ECHO_FEATURES
            or name in FORBIDDEN_EXACT
        ):
            continue
        if any(fragment in lowered for fragment in FORBIDDEN_FRAGMENTS):
            continue
        if (
            lowered.startswith("feature_endpoint_")
            or lowered.startswith("feature_source_")
            or lowered.startswith("feature_rec_")
        ):
            continue
        if not any(token in lowered for token in MARKET_FEATURE_TOKENS):
            continue
        if pd.to_numeric(frame[name], errors="coerce").notna().any():
            selected.append(name)
    return sorted(selected)


def _event_dates(frame: pd.DataFrame) -> pd.Series:
    if "event_date" in frame.columns:
        return pd.to_datetime(
            frame["event_date"], utc=True, errors="coerce"
        ).dt.normalize()
    return pd.to_datetime(
        pd.to_numeric(frame["ts"], errors="coerce"), unit="s", utc=True, errors="coerce"
    ).dt.normalize()


def _temporal_train_periods(
    frame: pd.DataFrame,
) -> tuple[list[pd.DataFrame], list[dict[str, Any]]]:
    ordered = (
        frame.assign(_event_date=_event_dates(frame))
        .dropna(subset=["_event_date"])
        .sort_values("_event_date")
    )
    dates = sorted(ordered["_event_date"].unique())
    if len(dates) < 21:
        raise ValueError(
            "Massive baseline training requires at least 21 independent dates"
        )
    fit_end = max(7, int(len(dates) * 0.60))
    calibration_end = max(fit_end + 7, int(len(dates) * 0.80))
    calibration_end = min(calibration_end, len(dates) - 7)
    date_sets = (
        set(dates[:fit_end]),
        set(dates[fit_end:calibration_end]),
        set(dates[calibration_end:]),
    )
    periods = [
        ordered.loc[ordered["_event_date"].isin(values)]
        .drop(columns="_event_date")
        .copy()
        for values in date_sets
    ]
    cleaned, diagnostics = purge_embargo_periods(
        periods, horizon_days=5, embargo_days=1
    )
    if any(period.empty for period in cleaned):
        raise ValueError(
            "purge/embargo leaves an empty Massive fit, calibration, or threshold period"
        )
    return cleaned, diagnostics


def _duplicate_weights(frame: pd.DataFrame) -> np.ndarray:
    keys = pd.DataFrame(index=frame.index)
    for column in GROUP_COLUMNS:
        keys[column] = (
            frame[column].fillna("unknown").astype(str)
            if column in frame.columns
            else "unknown"
        )
    counts = (
        keys.groupby(list(GROUP_COLUMNS), dropna=False)[GROUP_COLUMNS[0]]
        .transform("size")
        .astype(float)
    )
    return (1.0 / counts).to_numpy(dtype=float)


def _fill_from_fit(
    fit: pd.DataFrame, others: list[pd.DataFrame], features: list[str]
) -> tuple[pd.DataFrame, list[pd.DataFrame], dict[str, float]]:
    fills: dict[str, float] = {}
    fit_out = fit.copy()
    for feature in features:
        numeric = pd.to_numeric(fit_out[feature], errors="coerce").replace(
            [np.inf, -np.inf], np.nan
        )
        fills[feature] = float(numeric.median()) if numeric.notna().any() else 0.0
        fit_out[feature] = numeric.fillna(fills[feature])
    outputs: list[pd.DataFrame] = []
    for frame in others:
        out = frame.copy()
        for feature in features:
            values = pd.to_numeric(out.get(feature), errors="coerce").replace(
                [np.inf, -np.inf], np.nan
            )
            out[feature] = values.fillna(fills[feature])
        outputs.append(out)
    return fit_out, outputs, fills


def _artifact(
    base: BaselineModelArtifact,
    features: list[str],
    *,
    version: str = VERSION,
    threshold: float | None = None,
) -> BaselineModelArtifact:
    return BaselineModelArtifact(
        version=version,
        feature_columns=features,
        means=base.means,
        stds=base.stds,
        weights=base.weights,
        bias=base.bias,
        decision_threshold=float(
            base.decision_threshold if threshold is None else threshold
        ),
        calibration_slope=base.calibration_slope,
        calibration_intercept=base.calibration_intercept,
        lineage=base.lineage,
        scaler_type=base.scaler_type,
        scaler_version=base.scaler_version,
        clip_lower=base.clip_lower,
        clip_upper=base.clip_upper,
    )


def _score(
    frame: pd.DataFrame,
    probs: np.ndarray,
    threshold: float,
    weights: np.ndarray | None = None,
) -> dict[str, Any]:
    labels = pd.to_numeric(frame[TARGET_COLUMN], errors="coerce").to_numpy(dtype=float)
    returns = pd.to_numeric(frame[RETURN_COLUMN], errors="coerce").to_numpy(dtype=float)
    preds = probs >= threshold
    row_weights = (
        np.ones(len(frame), dtype=float)
        if weights is None
        else np.asarray(weights, dtype=float)
    )
    row_weights = row_weights / row_weights.sum()
    selected_weights = row_weights[preds]
    selected_returns = returns[preds]
    selected_weight_total = float(selected_weights.sum())
    big_gains = returns >= 0.03
    big_losses = returns < -0.03
    symbols = (
        frame["symbol"].fillna("unknown").astype(str)
        if "symbol" in frame.columns
        else pd.Series("unknown", index=frame.index)
    )
    dates = _event_dates(frame).dt.strftime("%Y-%m-%d").fillna("unknown")
    selected_groups = pd.DataFrame(
        {"symbol": symbols[preds], "date": dates[preds], "weight": selected_weights}
    )
    if len(selected_groups) and selected_weight_total > 0:
        grouped_weights = selected_groups.groupby(["symbol", "date"], dropna=False)[
            "weight"
        ].sum()
        top_concentration = float(grouped_weights.max() / grouped_weights.sum())
    else:
        top_concentration = 0.0
    avg_return = (
        float(np.average(selected_returns, weights=selected_weights))
        if selected_weight_total > 0
        else None
    )
    return {
        "rows": int(len(frame)),
        "accuracy": round(float(np.sum(row_weights * (preds == labels))), 6),
        "brier_score": round(float(np.sum(row_weights * ((probs - labels) ** 2))), 6),
        "positive_predictions": int(preds.sum()),
        "positive_rate": round(float(preds.mean()), 6),
        "avg_selected_return": round(avg_return, 6) if avg_return is not None else None,
        "big_gain_rows": int(big_gains.sum()),
        "big_gain_predictions": int((preds & big_gains).sum()),
        "big_gain_capture_rate": (
            round(float((preds & big_gains).sum() / big_gains.sum()), 6)
            if big_gains.any()
            else None
        ),
        "big_loss_rows": int(big_losses.sum()),
        "big_loss_predictions": int((preds & big_losses).sum()),
        "big_loss_false_positive_rate": (
            round(float((preds & big_losses).sum() / big_losses.sum()), 6)
            if big_losses.any()
            else None
        ),
        "selected_unique_symbols": int(symbols[preds].nunique()),
        "selected_unique_dates": int(dates[preds].nunique()),
        "selected_unique_symbol_dates": int(
            len(selected_groups[["symbol", "date"]].drop_duplicates())
        ),
        "top_symbol_date_concentration": round(top_concentration, 6),
    }


def _select_threshold(frame: pd.DataFrame, probs: np.ndarray) -> dict[str, Any]:
    results = []
    duplicate_weights = _duplicate_weights(frame)
    for threshold in THRESHOLDS:
        metrics = _score(frame, probs, threshold, duplicate_weights)
        utility = metrics["avg_selected_return"]
        passed = bool(
            metrics["positive_predictions"] >= MIN_POSITIVES
            and metrics["selected_unique_symbols"] >= MIN_SYMBOLS
            and metrics["selected_unique_dates"] >= MIN_DATES
            and metrics["big_gain_rows"] >= MIN_BIG_GAINS
            and metrics["top_symbol_date_concentration"] <= MAX_CONCENTRATION
            and metrics["big_gain_predictions"] > 0
            and utility is not None
            and utility >= 0.0
        )
        results.append({"threshold": threshold, "support_passed": passed, **metrics})
    viable = [item for item in results if item["support_passed"]]
    selected = (
        max(
            viable,
            key=lambda item: (
                item["avg_selected_return"],
                item["big_gain_capture_rate"],
            ),
        )
        if viable
        else None
    )
    return {
        "selected_threshold": float(selected["threshold"]) if selected else 0.55,
        "threshold_selection_sufficient": bool(selected),
        "selection_status": (
            "selected_supported_threshold"
            if selected
            else "insufficient_support_keep_default_threshold"
        ),
        "support_requirements": {
            "minimum_positive_predictions": MIN_POSITIVES,
            "minimum_unique_symbols": MIN_SYMBOLS,
            "minimum_unique_dates": MIN_DATES,
            "minimum_big_gain_examples": MIN_BIG_GAINS,
            "maximum_symbol_date_concentration": MAX_CONCENTRATION,
            "minimum_avg_selected_return": 0.0,
        },
        "selected_metrics": selected,
        "evaluation_weighting_policy": "1 / count(symbol, event_date, endpoint, decision_source)",
        "search": results,
    }


def train_massive_market_model(
    train_path: Path,
    test_path: Path,
    all_path: Path,
    output_path: Path,
    *,
    model_version: str = VERSION,
    report_prefix: str = "massive_baseline",
    feature_allowlist: tuple[str, ...] | list[str] | None = None,
    scaler_type: str = "legacy_standard",
    winsor_quantiles: tuple[float, float] | None = None,
    l2: float = 1e-3,
) -> dict[str, Any]:
    train = _load_jsonl(train_path)
    test = _load_jsonl(test_path)
    all_cleaned = _load_jsonl(all_path)
    for name, frame in (("train", train), ("test", test)):
        missing = {TARGET_COLUMN, RETURN_COLUMN}.difference(frame.columns)
        if missing:
            raise ValueError(
                f"{name} input is missing required columns: {sorted(missing)}"
            )
    features = _market_feature_columns(train)
    if feature_allowlist is not None:
        requested = [str(feature) for feature in feature_allowlist]
        forbidden = validate_v3_feature_columns(requested)
        if forbidden:
            raise ValueError(
                f"V3 feature allowlist contains unsupported or unsafe fields: {forbidden}"
            )
        missing = [feature for feature in requested if feature not in features]
        if missing:
            raise ValueError(
                f"Canonical Massive input is missing V3 features: {missing}"
            )
        features = requested
    if not features:
        raise ValueError("No leakage-safe Massive market features are available")
    periods, boundaries = _temporal_train_periods(train)
    fit_raw, calibration_raw, threshold_raw = periods
    fit, [calibration, threshold, holdout], fills = _fill_from_fit(
        fit_raw, [calibration_raw, threshold_raw, test], features
    )
    fit_weights = _duplicate_weights(fit)
    base = train_logistic_baseline(
        fit[features].to_numpy(dtype=float),
        pd.to_numeric(fit[TARGET_COLUMN], errors="coerce").to_numpy(dtype=float),
        sample_weight=fit_weights,
        scaler_type=scaler_type,
        winsor_quantiles=winsor_quantiles,
        l2=l2,
    )
    model = _artifact(base, features, version=model_version)
    model.forecast_horizon = "5d"
    raw_calibration_probs = predict_proba(
        model, calibration[features].to_numpy(dtype=float)
    )
    calibration_report = fit_probability_calibration(
        raw_calibration_probs,
        pd.to_numeric(calibration[TARGET_COLUMN], errors="coerce").to_numpy(
            dtype=float
        ),
    )
    model.calibration_slope = float(calibration_report["slope"])
    model.calibration_intercept = float(calibration_report["intercept"])
    threshold_probs = predict_proba(model, threshold[features].to_numpy(dtype=float))
    threshold_report = _select_threshold(threshold, threshold_probs)
    model.decision_threshold = float(threshold_report["selected_threshold"])
    deployable_recipe = {
        "model_family": "logistic_regression",
        "feature_subset": features,
        "sample_weight_policy": "1 / count(symbol, event_date, endpoint, decision_source)",
        "scaler_type": model.scaler_type,
        "scaler_version": model.scaler_version,
        "winsor_quantiles": list(winsor_quantiles) if winsor_quantiles else None,
        "l2": float(l2),
        "calibration": calibration_report,
        "forecast_horizon": "5d",
        "feature_fill_values": fills,
        "decision_threshold": float(model.decision_threshold),
        "abstention": {"enabled": False, "margin": 0.0},
        "target_column": TARGET_COLUMN,
        "evaluation_return_column": RETURN_COLUMN,
        "horizon_days": 5,
        "decision_target": target_metadata(),
    }
    recipe_hash = hashlib.sha256(
        json.dumps(deployable_recipe, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    model.lineage = {
        "schema_version": "moneybot-challenger-lineage.v1",
        "lineage_id": f"recipe-{recipe_hash[:16]}",
        "recipe_hash": recipe_hash,
        "recipe": deployable_recipe,
        "training_source": "cleaned_massive_training_quality",
        "train_path": str(train_path),
        "test_path": str(test_path),
        "all_cleaned_path": str(all_path),
        "target_column": TARGET_COLUMN,
        "decision_target": target_metadata(),
        "evaluation_return_column": RETURN_COLUMN,
        "horizon_days": 5,
        "feature_policy": "massive_market_only_no_model_echo_v1",
        "sample_weight_policy": "1 / count(symbol, event_date, endpoint, decision_source)",
        "calibration": calibration_report,
        "feature_fill_values": fills,
        "threshold_selection": threshold_report,
    }
    save_artifact(model, output_path)
    # Keep deployment-critical configuration visible without requiring lineage
    # interpretation. The deterministic artifact loader safely ignores these
    # additive fields while Day11 reads them for schema matching.
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    payload.update(
        {
            "model_type": "logistic_regression",
            "candidate_lane": "decision",
            "model_version": model_version,
            "target_column": TARGET_COLUMN,
            "target_name": TARGET_COLUMN,
            "target_definition": target_metadata()["target_definition"],
            "positive_return_buckets": target_metadata()["positive_return_buckets"],
            "decision_target": target_metadata(),
            "evaluation_return_column": RETURN_COLUMN,
            "horizon_days": 5,
            "training_inputs": {
                "train": str(train_path),
                "test": str(test_path),
                "all_cleaned": str(all_path),
            },
            "feature_policy": "massive_market_only_no_model_echo_v1",
            "sample_weight_policy": model.lineage["sample_weight_policy"],
            "duplicate_weighting_applied": True,
            "scaler_type": model.scaler_type,
            "scaler_version": model.scaler_version,
            "clip_lower": model.clip_lower,
            "clip_upper": model.clip_upper,
            "threshold_selection_sufficient": bool(
                threshold_report["threshold_selection_sufficient"]
            ),
            "selected_threshold": float(model.decision_threshold),
            "calibration": calibration_report,
            "forecast_horizon": "5d",
            "feature_fill_values": fills,
        }
    )
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    holdout_probs = predict_proba(model, holdout[features].to_numpy(dtype=float))
    raw_metrics = _score(holdout, holdout_probs, model.decision_threshold)
    weighted_metrics = _score(
        holdout, holdout_probs, model.decision_threshold, _duplicate_weights(holdout)
    )
    feature_coverage = {
        "model_version": model_version,
        "rows": int(len(all_cleaned)),
        "features": {},
        "future_outcome_fields_excluded": True,
        "model_echo_fields_excluded": True,
    }
    for feature in features:
        values = (
            pd.to_numeric(all_cleaned[feature], errors="coerce")
            if feature in all_cleaned.columns
            else pd.Series(np.nan, index=all_cleaned.index)
        )
        feature_coverage["features"][feature] = {
            "non_null_count": int(values.notna().sum()),
            "availability_rate": (
                round(float(values.notna().mean()), 6) if len(values) else 0.0
            ),
            "fill_value": fills[feature],
        }
    report = {
        "available": True,
        "model_version": model_version,
        "model_path": str(output_path),
        "target_column": TARGET_COLUMN,
        "decision_target": target_metadata(),
        "evaluation_return_column": RETURN_COLUMN,
        "feature_columns": features,
        "feature_fill_values": fills,
        "sample_weight_policy": model.lineage["sample_weight_policy"],
        "duplicate_weighting_applied": True,
        "training_inputs": {
            "train": str(train_path),
            "test": str(test_path),
            "all_cleaned": str(all_path),
        },
        "row_counts": {
            "train": int(len(train)),
            "test": int(len(test)),
            "all_cleaned": int(len(all_cleaned)),
            "fit": int(len(fit)),
            "calibration": int(len(calibration)),
            "threshold": int(len(threshold)),
        },
        "temporal_validation": {
            "cleaned_test_untouched_for_final_holdout": True,
            "boundaries": boundaries,
        },
        "calibration": calibration_report,
        "threshold_selection": threshold_report,
        "raw_row_metrics": raw_metrics,
        "duplicate_weighted_metrics": weighted_metrics,
        "promotion_ready": False,
        "routing_allowed": False,
        "usage_scope": "massive_baseline_comparator",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    (output_path.parent / f"{report_prefix}_model_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    (output_path.parent / f"{report_prefix}_feature_coverage_report.json").write_text(
        json.dumps(feature_coverage, indent=2), encoding="utf-8"
    )
    (output_path.parent / f"{report_prefix}_backtest_report.json").write_text(
        json.dumps(
            {
                "raw_row_metrics": raw_metrics,
                "duplicate_weighted_metrics": weighted_metrics,
                "threshold_selection": threshold_report,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return report


def train_massive_baseline(
    train_path: Path, test_path: Path, all_path: Path, output_path: Path
) -> dict[str, Any]:
    return train_massive_market_model(train_path, test_path, all_path, output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--train", default="data/track_b/training_quality/cleaned_train.jsonl"
    )
    parser.add_argument(
        "--test", default="data/track_b/training_quality/cleaned_test.jsonl"
    )
    parser.add_argument(
        "--all-cleaned", default="data/track_b/training_quality/cleaned_all.jsonl"
    )
    parser.add_argument(
        "--output", default="data/track_b/massive_baseline_model_v1.json"
    )
    args = parser.parse_args()
    report = train_massive_baseline(
        Path(args.train), Path(args.test), Path(args.all_cleaned), Path(args.output)
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
