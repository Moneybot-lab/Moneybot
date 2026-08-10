#!/usr/bin/env python3
"""Train Alpha Atlas V3.1 from canonical Massive rows without promoting it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moneybot.services.alpha_atlas_v31_quantitative import (
    V31_CANDIDATE_VERSION,
    V31_L2_VALUES,
    V31_SCALER_RECIPES,
    calibration_is_stable,
    extreme_return_audit,
    feature_distribution_audit,
)
from moneybot.services.alpha_atlas_v3_features import (
    ALPHA_ATLAS_V3_FEATURES,
    FEATURE_CONTRACT_VERSION,
    FEATURE_ENGINE_VERSION,
    FORECAST_HORIZON,
    build_alpha_atlas_v3_features,
    v3_feature_declarations,
)
from moneybot.services.decision_target import (
    RETURN_COLUMN,
    TARGET_NAME,
    target_metadata,
)
from moneybot.services.deterministic_model import (
    BaselineModelArtifact,
    fit_probability_calibration,
    load_artifact,
    predict_proba,
    save_artifact,
    train_logistic_baseline,
)
from moneybot.services.market_data import MarketDataService
from moneybot.services.production_servability import certify_candidate
from moneybot.services.temporal_validation import purge_embargo_periods
from scripts.train_massive_baseline_model import (
    _artifact,
    _duplicate_weights,
    _event_dates,
    _fill_from_fit,
    _load_jsonl,
    _score,
    _select_threshold,
    _temporal_train_periods,
)

REPRESENTATIVE_SYMBOLS = (
    "AAPL",
    "SPY",
    "APLD",
    "UMAC",
    "ASPI",
    "ACHR",
    "JOBY",
    "LASE",
    "ONDS",
    "SQQQ",
)
SAMPLE_WEIGHT_POLICY = "1 / count(symbol, event_date, endpoint, decision_source)"
CALIBRATION_MAX_FOLD_REGRESSION = 0.0025
ABLATIONS = (
    ("all_features", tuple(ALPHA_ATLAS_V3_FEATURES)),
    (
        "without_return_20d",
        tuple(f for f in ALPHA_ATLAS_V3_FEATURES if f != "feature_return_20d_lagged"),
    ),
    (
        "without_market_regime",
        tuple(
            f for f in ALPHA_ATLAS_V3_FEATURES if f != "feature_market_regime_risk_on"
        ),
    ),
    (
        "without_volatility",
        tuple(f for f in ALPHA_ATLAS_V3_FEATURES if f != "feature_volatility_20d"),
    ),
    (
        "without_spy_return",
        tuple(f for f in ALPHA_ATLAS_V3_FEATURES if f != "feature_spy_return_5d"),
    ),
)


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _require_inputs(paths: list[Path]) -> None:
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Canonical Massive cleaned input is required; missing: "
            + ", ".join(missing)
        )


def _walk_forward_folds(
    train: pd.DataFrame,
) -> list[tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]]:
    ordered = (
        train.assign(_date=_event_dates(train))
        .dropna(subset=["_date"])
        .sort_values("_date")
    )
    dates = sorted(ordered["_date"].unique())
    if len(dates) < 30:
        raise ValueError(
            "V3.1 walk-forward selection requires at least 30 independent dates"
        )
    folds = []
    for fit_fraction, cal_fraction, val_fraction in (
        (0.45, 0.10, 0.10),
        (0.60, 0.10, 0.10),
        (0.70, 0.10, 0.10),
    ):
        fit_end = int(len(dates) * fit_fraction)
        cal_end = min(len(dates) - 2, fit_end + max(2, int(len(dates) * cal_fraction)))
        val_end = min(len(dates), cal_end + max(2, int(len(dates) * val_fraction)))
        periods = []
        for selected in (
            dates[:fit_end],
            dates[fit_end:cal_end],
            dates[cal_end:val_end],
        ):
            periods.append(
                ordered.loc[ordered["_date"].isin(selected)]
                .drop(columns="_date")
                .copy()
            )
        cleaned, _ = purge_embargo_periods(periods, horizon_days=5, embargo_days=1)
        if all(not frame.empty for frame in cleaned):
            folds.append(tuple(cleaned))
    if len(folds) < 2:
        raise ValueError("V3.1 requires at least two healthy purged walk-forward folds")
    return folds


def _log_loss(probs: np.ndarray, labels: np.ndarray, weights: np.ndarray) -> float:
    probs = np.clip(probs, 1e-9, 1 - 1e-9)
    weights = weights / weights.sum()
    return float(
        -np.sum(weights * (labels * np.log(probs) + (1 - labels) * np.log(1 - probs)))
    )


def _fit_recipe(
    fit: pd.DataFrame,
    calibration: pd.DataFrame,
    validation: pd.DataFrame,
    features: tuple[str, ...],
    recipe: dict[str, Any],
    l2: float,
) -> tuple[dict[str, Any], BaselineModelArtifact]:
    fit_filled, [cal_filled, val_filled], fills = _fill_from_fit(
        fit, [calibration, validation], list(features)
    )
    model = train_logistic_baseline(
        fit_filled[list(features)].to_numpy(float),
        pd.to_numeric(fit_filled[TARGET_NAME], errors="coerce").to_numpy(float),
        sample_weight=_duplicate_weights(fit_filled),
        scaler_type=recipe["scaler_type"],
        winsor_quantiles=recipe["winsor_quantiles"],
        l2=l2,
    )
    model = _artifact(model, list(features), version=V31_CANDIDATE_VERSION)
    model.forecast_horizon = FORECAST_HORIZON
    cal_raw = predict_proba(model, cal_filled[list(features)].to_numpy(float))
    calibration_fit = fit_probability_calibration(
        cal_raw, cal_filled[TARGET_NAME].to_numpy(float)
    )
    val_raw = predict_proba(model, val_filled[list(features)].to_numpy(float))
    slope, intercept = float(calibration_fit["slope"]), float(
        calibration_fit["intercept"]
    )
    raw_logits = np.log(
        np.clip(val_raw, 1e-9, 1 - 1e-9) / np.clip(1 - val_raw, 1e-9, 1)
    )
    val_cal = 1 / (1 + np.exp(-np.clip(slope * raw_logits + intercept, -35, 35)))
    labels = val_filled[TARGET_NAME].to_numpy(float)
    weights = _duplicate_weights(val_filled)
    raw_metrics = _score(val_filled, val_raw, 0.55, weights)
    calibrated_metrics = _score(val_filled, val_cal, 0.55, weights)
    raw_brier = raw_metrics["brier_score"]
    calibrated_brier = calibrated_metrics["brier_score"]
    report = {
        "fit_start": str(_event_dates(fit_filled).min().date()),
        "fit_end": str(_event_dates(fit_filled).max().date()),
        "validation_start": str(_event_dates(val_filled).min().date()),
        "validation_end": str(_event_dates(val_filled).max().date()),
        "fit_rows": len(fit_filled),
        "validation_rows": len(val_filled),
        "raw_brier": raw_brier,
        "calibrated_brier": calibrated_brier,
        "delta_brier": calibrated_brier - raw_brier,
        "raw_log_loss": _log_loss(val_raw, labels, weights),
        "calibrated_log_loss": _log_loss(val_cal, labels, weights),
        "raw_probability_mean": float(np.average(val_raw, weights=weights)),
        "calibrated_probability_mean": float(np.average(val_cal, weights=weights)),
        "observed_positive_rate": float(np.average(labels, weights=weights)),
        "calibration_slope": slope,
        "calibration_intercept": intercept,
        **{
            f"raw_{key}": value
            for key, value in raw_metrics.items()
            if key
            in {
                "accuracy",
                "positive_rate",
                "avg_selected_return",
                "big_gain_capture_rate",
                "big_loss_false_positive_rate",
            }
        },
        "downside_risk": raw_metrics["big_loss_false_positive_rate"],
    }
    return {"report": report, "fills": fills, "calibration": calibration_fit}, model


def _selection_score(folds: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    brier = float(np.mean([fold["raw_brier"] for fold in folds]))
    returns = [
        fold["raw_avg_selected_return"]
        for fold in folds
        if fold["raw_avg_selected_return"] is not None
    ]
    avg_return = float(np.mean(returns)) if returns else -1.0
    big_loss = float(
        np.mean([fold["raw_big_loss_false_positive_rate"] or 1.0 for fold in folds])
    )
    big_gain = float(
        np.mean([fold["raw_big_gain_capture_rate"] or 0.0 for fold in folds])
    )
    return (brier, -avg_return, big_loss, -big_gain)


def select_recipe(train: pd.DataFrame) -> tuple[dict[str, Any], dict[str, Any]]:
    """Select solely from TRAIN; this function has no holdout path or frame parameter."""
    folds = _walk_forward_folds(train)
    experiments = []
    for scaler in V31_SCALER_RECIPES:
        for l2 in V31_L2_VALUES:
            fold_reports = [
                _fit_recipe(*fold, tuple(ALPHA_ATLAS_V3_FEATURES), scaler, l2)[0][
                    "report"
                ]
                for fold in folds
            ]
            experiments.append(
                {
                    "recipe_id": f"{scaler['name']}-l2-{l2:g}-all",
                    "feature_subset": list(ALPHA_ATLAS_V3_FEATURES),
                    "scaler_type": scaler["scaler_type"],
                    "winsor_quantiles": scaler["winsor_quantiles"],
                    "l2": l2,
                    "folds": fold_reports,
                    "selection_score": _selection_score(fold_reports),
                }
            )
    best_full = min(experiments, key=lambda item: tuple(item["selection_score"]))
    scaler = next(
        item
        for item in V31_SCALER_RECIPES
        if item["scaler_type"] == best_full["scaler_type"]
        and item["winsor_quantiles"] == best_full["winsor_quantiles"]
    )
    ablations = []
    for name, features in ABLATIONS[1:]:
        fold_reports = [
            _fit_recipe(*fold, features, scaler, best_full["l2"])[0]["report"]
            for fold in folds
        ]
        ablations.append(
            {
                "recipe_id": f"{best_full['recipe_id']}-{name}",
                "feature_subset": list(features),
                "scaler_type": scaler["scaler_type"],
                "winsor_quantiles": scaler["winsor_quantiles"],
                "l2": best_full["l2"],
                "folds": fold_reports,
                "selection_score": _selection_score(fold_reports),
            }
        )
    winner = min(
        [best_full, *ablations], key=lambda item: tuple(item["selection_score"])
    )
    return winner, {
        "schema_version": "moneybot-alpha-atlas-v31-experiments.v1",
        "holdout_accessed": False,
        "full_feature_matrix": experiments,
        "ablations": ablations,
        "selected_recipe_id": winner["recipe_id"],
    }


def _load_holdout_after_freeze(test_path: Path, frozen_path: Path) -> pd.DataFrame:
    if not frozen_path.is_file():
        raise RuntimeError(
            "Refusing to open final holdout before the V3.1 recipe is frozen"
        )
    return _load_jsonl(test_path)


def _distribution_shift(
    reference: pd.DataFrame, frames: dict[str, pd.DataFrame], features: tuple[str, ...]
) -> dict[str, Any]:
    output = {"reference": "fit", "features": {}}
    for feature in features:
        ref = pd.to_numeric(reference[feature], errors="coerce").dropna()
        bins = np.unique(np.quantile(ref, np.linspace(0, 1, 11)))
        feature_report = {}
        for name, frame in frames.items():
            values = pd.to_numeric(frame[feature], errors="coerce").dropna()
            if len(bins) > 1 and len(values):
                expected = np.histogram(ref, bins=bins)[0].astype(float) + 1e-6
                actual = np.histogram(values, bins=bins)[0].astype(float) + 1e-6
                expected /= expected.sum()
                actual /= actual.sum()
                psi = float(np.sum((actual - expected) * np.log(actual / expected)))
            else:
                psi = None
            feature_report[name] = {
                "mean_shift": (
                    float(values.mean() - ref.mean()) if len(values) else None
                ),
                "median_shift": (
                    float(values.median() - ref.median()) if len(values) else None
                ),
                "std_ratio": (
                    float(values.std(ddof=0) / ref.std(ddof=0))
                    if len(values) and ref.std(ddof=0) > 0
                    else None
                ),
                "psi": psi,
            }
        output["features"][feature] = feature_report
    return output


def _metric_deltas(v3: dict[str, Any], v31: dict[str, Any]) -> dict[str, Any]:
    names = (
        "accuracy",
        "brier_score",
        "positive_rate",
        "avg_selected_return",
        "big_gain_capture_rate",
        "big_loss_false_positive_rate",
    )
    result = {}
    for name in names:
        old, new = v3.get(name), v31.get(name)
        absolute = float(new - old) if old is not None and new is not None else None
        result[name] = {
            "v3": old,
            "v31": new,
            "absolute_delta": absolute,
            "relative_delta": (
                (absolute / abs(old))
                if absolute is not None and old not in {0, 0.0}
                else None
            ),
        }
    return result


def _date_block_bootstrap(
    frame: pd.DataFrame,
    v3_probs: np.ndarray,
    v31_probs: np.ndarray,
    v3_threshold: float,
    v31_threshold: float,
    *,
    samples: int = 300,
) -> dict[str, Any]:
    dates = _event_dates(frame).dt.strftime("%Y-%m-%d")
    unique_dates = np.asarray(sorted(dates.dropna().unique()))
    if len(unique_dates) < 2:
        return {"available": False, "reason": "at least two holdout dates are required"}
    rng = np.random.default_rng(31)
    deltas = {
        "brier_score": [],
        "avg_selected_return": [],
        "big_loss_false_positive_rate": [],
    }
    for _ in range(samples):
        selected_dates = rng.choice(unique_dates, size=len(unique_dates), replace=True)
        indices = np.concatenate(
            [np.flatnonzero(dates.to_numpy() == day) for day in selected_dates]
        )
        sampled = frame.iloc[indices].reset_index(drop=True)
        old = _score(
            sampled, v3_probs[indices], v3_threshold, _duplicate_weights(sampled)
        )
        new = _score(
            sampled, v31_probs[indices], v31_threshold, _duplicate_weights(sampled)
        )
        for metric in deltas:
            if old.get(metric) is not None and new.get(metric) is not None:
                deltas[metric].append(float(new[metric] - old[metric]))
    result = {
        "available": True,
        "samples": samples,
        "date_blocked": True,
        "metrics": {},
    }
    for metric, values in deltas.items():
        array = np.asarray(values, dtype=float)
        lower, upper = np.quantile(array, [0.025, 0.975])
        lower_is_better = metric in {"brier_score", "big_loss_false_positive_rate"}
        improves = array < 0 if lower_is_better else array > 0
        result["metrics"][metric] = {
            "mean_delta": float(array.mean()),
            "confidence_interval_95": [float(lower), float(upper)],
            "probability_v31_improves": float(improves.mean()),
        }
    return result


def _serving_dry_runs(artifact_path: Path, service: Any) -> list[dict[str, Any]]:
    artifact = load_artifact(artifact_path)
    payload = json.loads(artifact_path.read_text())
    fills = payload["feature_fill_values"]
    spy = service.get_price_history_data("SPY", days=90)
    runs = []
    for symbol in REPRESENTATIVE_SYMBOLS:
        history = (
            spy if symbol == "SPY" else service.get_price_history_data(symbol, days=90)
        )
        raw = build_alpha_atlas_v3_features(
            symbol_bars=history.get("bars") or [], spy_bars=spy.get("bars") or []
        )
        missing = [f for f in artifact.feature_columns if raw.get(f) is None]
        vector = [
            raw.get(f) if raw.get(f) is not None else fills.get(f)
            for f in artifact.feature_columns
        ]
        massive = history.get("source") == "massive" and spy.get("source") == "massive"
        usable = massive and all(value is not None for value in vector)
        transformed = np.asarray(vector, float) if usable else None
        if usable and artifact.clip_lower is not None:
            transformed = np.clip(transformed, artifact.clip_lower, artifact.clip_upper)
        probability = (
            float(predict_proba(artifact, np.asarray([vector], float))[0])
            if usable
            else None
        )
        runs.append(
            {
                "symbol": symbol,
                "required_feature_count": len(artifact.feature_columns),
                "available_feature_count": len(artifact.feature_columns) - len(missing),
                "missing_required_features": missing,
                "imputed_feature_count": len(missing),
                "raw_feature_vector": vector,
                "transformed_vector": (
                    (
                        (transformed - np.asarray(artifact.means))
                        / np.asarray(artifact.stds)
                    ).tolist()
                    if usable
                    else None
                ),
                "raw_probability": probability,
                "final_probability": probability,
                "feature_contract_servable": usable,
                "feature_vector_is_training_mean": bool(
                    usable and np.allclose(vector, artifact.means)
                ),
                "history_source": history.get("source"),
                "forecast_horizon": FORECAST_HORIZON,
            }
        )
    return runs


def train_v31(
    train_path: Path,
    test_path: Path,
    all_path: Path,
    output_dir: Path,
    market_service: Any,
) -> dict[str, Any]:
    _require_inputs([train_path, test_path, all_path])
    output_dir.mkdir(parents=True, exist_ok=True)
    train = _load_jsonl(train_path)
    periods, boundaries = _temporal_train_periods(train)
    fit_raw, calibration_raw, threshold_raw = periods
    _write(
        output_dir / "alpha_atlas_v31_feature_distribution_audit.json",
        feature_distribution_audit(fit_raw),
    )
    outlier = extreme_return_audit(fit_raw)
    outlier["provenance_note"] = (
        "close/bar provenance is null when canonical cleaned rows do not persist those fields; values are never invented"
    )
    _write(output_dir / "alpha_atlas_v31_outlier_audit.json", outlier)
    winner, experiments = select_recipe(train)
    _write(output_dir / "alpha_atlas_v31_scaler_experiment_report.json", experiments)
    _write(
        output_dir / "alpha_atlas_v31_ablation_report.json",
        {
            "selected_recipe_id": winner["recipe_id"],
            "experiments": experiments["ablations"],
        },
    )
    features = tuple(winner["feature_subset"])
    scaler = {
        "scaler_type": winner["scaler_type"],
        "winsor_quantiles": (
            tuple(winner["winsor_quantiles"]) if winner["winsor_quantiles"] else None
        ),
    }
    fit, [calibration, threshold], fills = _fill_from_fit(
        fit_raw, [calibration_raw, threshold_raw], list(features)
    )
    base = train_logistic_baseline(
        fit[list(features)].to_numpy(float),
        fit[TARGET_NAME].to_numpy(float),
        sample_weight=_duplicate_weights(fit),
        scaler_type=scaler["scaler_type"],
        winsor_quantiles=scaler["winsor_quantiles"],
        l2=winner["l2"],
    )
    model = _artifact(base, list(features), version=V31_CANDIDATE_VERSION)
    model.forecast_horizon = FORECAST_HORIZON
    raw_cal = predict_proba(model, calibration[list(features)].to_numpy(float))
    calibration_fit = fit_probability_calibration(
        raw_cal, calibration[TARGET_NAME].to_numpy(float)
    )
    stable = calibration_is_stable(
        float(calibration_fit["calibrated_brier_score"])
        - float(calibration_fit["raw_brier_score"]),
        [fold["delta_brier"] for fold in winner["folds"]],
        maximum_fold_regression=CALIBRATION_MAX_FOLD_REGRESSION,
    )
    if stable:
        model.calibration_slope, model.calibration_intercept = float(
            calibration_fit["slope"]
        ), float(calibration_fit["intercept"])
    else:
        model.calibration_slope, model.calibration_intercept = 1.0, 0.0
    calibration_report = {
        "designated_block": calibration_fit,
        "walk_forward_folds": winner["folds"],
        "maximum_fold_regression": CALIBRATION_MAX_FOLD_REGRESSION,
        "calibration_applied": stable,
        "final_slope": model.calibration_slope,
        "final_intercept": model.calibration_intercept,
    }
    _write(
        output_dir / "alpha_atlas_v31_calibration_stability_report.json",
        calibration_report,
    )
    threshold_probs = predict_proba(model, threshold[list(features)].to_numpy(float))
    threshold_report = _select_threshold(threshold, threshold_probs)
    model.decision_threshold = float(threshold_report["selected_threshold"])
    recipe = {
        "candidate_version": V31_CANDIDATE_VERSION,
        "feature_subset": list(features),
        "scaler_type": model.scaler_type,
        "scaler_version": model.scaler_version,
        "winsor_quantiles": winner["winsor_quantiles"],
        "clip_lower": model.clip_lower,
        "clip_upper": model.clip_upper,
        "l2": winner["l2"],
        "fill_values": fills,
        "calibration_applied": stable,
        "calibration_slope": model.calibration_slope,
        "calibration_intercept": model.calibration_intercept,
        "decision_threshold": model.decision_threshold,
        "threshold_selection_sufficient": threshold_report[
            "threshold_selection_sufficient"
        ],
        "target": target_metadata(),
        "forecast_horizon": FORECAST_HORIZON,
        "sample_weight_policy": SAMPLE_WEIGHT_POLICY,
        "selection_input": str(train_path),
        "holdout_input": None,
        "recipe_frozen_before_holdout": True,
    }
    recipe_hash = hashlib.sha256(
        json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    recipe["recipe_hash"] = recipe_hash
    frozen_path = output_dir / "alpha_atlas_v31_frozen_recipe.json"
    _write(frozen_path, recipe)
    holdout_raw = _load_holdout_after_freeze(test_path, frozen_path)
    # all_cleaned contains the holdout partition, so it is deliberately not
    # opened until after recipe freezing for post-selection coverage reporting.
    all_cleaned = _load_jsonl(all_path)
    _, [holdout], _ = _fill_from_fit(fit, [holdout_raw], list(features))
    holdout_probs = predict_proba(model, holdout[list(features)].to_numpy(float))
    metrics = _score(
        holdout, holdout_probs, model.decision_threshold, _duplicate_weights(holdout)
    )
    model.lineage = {
        "schema_version": "moneybot-challenger-lineage.v1",
        "lineage_id": f"recipe-{recipe_hash[:16]}",
        "recipe_hash": recipe_hash,
        "recipe": recipe,
        "training_source": "cleaned_massive_training_quality",
        "train_path": str(train_path),
        "test_path": str(test_path),
        "all_cleaned_path": str(all_path),
    }
    candidate_path = output_dir / "candidate_alpha_atlas_v31_clean.json"
    save_artifact(model, candidate_path)
    payload = json.loads(candidate_path.read_text())
    payload.update(
        {
            "model_type": "logistic_regression",
            "model_version": V31_CANDIDATE_VERSION,
            "candidate_lane": "decision",
            "forecast_horizon": FORECAST_HORIZON,
            "feature_fill_values": fills,
            "decision_target": target_metadata(),
            "target_name": TARGET_NAME,
            "evaluation_return_column": RETURN_COLUMN,
            "automatic_promotion": False,
            "recipe_hash": recipe_hash,
        }
    )
    _write(candidate_path, payload)
    if not os.environ.get("MASSIVE_API_KEY", "").strip():
        candidate_path.unlink(missing_ok=True)
        raise RuntimeError(
            "MASSIVE_API_KEY is required; uncertified V3.1 candidate was removed"
        )
    dry_runs = _serving_dry_runs(candidate_path, market_service)
    _write(
        output_dir / "alpha_atlas_v31_representative_serving_dry_runs.json",
        {"runs": dry_runs, "massive_required": True},
    )
    declarations = [
        d for d in v3_feature_declarations() if d["feature_name"] in features
    ]
    transform = {
        "engine": FEATURE_ENGINE_VERSION,
        "order": "fill_then_clip_then_scale_then_logistic_then_artifact_calibration",
        "scaler_type": model.scaler_type,
        "scaler_version": model.scaler_version,
        "clip_lower": model.clip_lower,
        "clip_upper": model.clip_upper,
    }
    payload["production_feature_contract"] = {
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
        "forecast_horizon": FORECAST_HORIZON,
        "lane": "decision",
        "leakage_safe": True,
        "feature_columns": list(features),
        "required_features": list(features),
        "optional_features": [],
        "features": declarations,
        "training_transform": transform,
        "serving_transform": transform,
        "training_fill_policy": "fit_period_median",
        "serving_fill_policy": "fit_period_median",
        "fill_values": fills,
        "representative_dry_runs": dry_runs,
        "warnings": [],
    }
    _write(candidate_path, payload)
    certification = certify_candidate(candidate_path)
    _write(output_dir / "production_servability_certification.json", certification)
    coverage = {
        feature: {
            "availability_rate": float(
                pd.to_numeric(all_cleaned[feature], errors="coerce").notna().mean()
            ),
            "fill_value": fills[feature],
        }
        for feature in features
    }
    _write(
        output_dir / "alpha_atlas_v31_feature_coverage_report.json",
        {"features": coverage},
    )
    _write(
        output_dir / "alpha_atlas_v31_distribution_shift_report.json",
        _distribution_shift(
            fit,
            {
                "calibration": calibration,
                "threshold_selection": threshold,
                "final_test": holdout,
            },
            features,
        ),
    )
    v3_path = (
        output_dir.parent / "alpha_atlas_v3" / "candidate_alpha_atlas_v3_clean.json"
    )
    comparison = None
    if v3_path.is_file():
        v3 = load_artifact(v3_path)
        v3_payload = json.loads(v3_path.read_text(encoding="utf-8"))
        v3_fills = v3_payload.get("feature_fill_values") or {}
        v3_frame = holdout_raw.copy()
        for feature in v3.feature_columns:
            values = pd.to_numeric(v3_frame.get(feature), errors="coerce").replace(
                [np.inf, -np.inf], np.nan
            )
            v3_frame[feature] = values.fillna(float(v3_fills.get(feature, 0.0)))
        v3_probs = predict_proba(v3, v3_frame[v3.feature_columns].to_numpy(float))
        v3_metrics = _score(
            v3_frame, v3_probs, v3.decision_threshold, _duplicate_weights(v3_frame)
        )
        comparison = {
            "comparison_type": "candidate_iteration_comparison",
            "holdout_path": str(test_path),
            "identical_holdout_rows": len(v3_frame) == len(holdout),
            "target_name": TARGET_NAME,
            "forecast_horizon": FORECAST_HORIZON,
            "sample_weight_policy": SAMPLE_WEIGHT_POLICY,
            "metrics": _metric_deltas(v3_metrics, metrics),
            "v3": v3_metrics,
            "v31": metrics,
            "bootstrap": _date_block_bootstrap(
                v3_frame,
                v3_probs,
                holdout_probs,
                v3.decision_threshold,
                model.decision_threshold,
            ),
        }
        _write(
            output_dir / "alpha_atlas_v31_candidate_iteration_comparison.json",
            comparison,
        )
    report = {
        "candidate_version": V31_CANDIDATE_VERSION,
        "recipe_frozen_before_holdout": True,
        "row_counts": {
            "train": len(train),
            "fit": len(fit),
            "calibration": len(calibration),
            "threshold": len(threshold),
            "test": len(holdout),
        },
        "duplicate_weighted_metrics": metrics,
        "threshold_selection": threshold_report,
        "temporal_boundaries": boundaries,
        "certification_passed": certification["passed"],
        "promotion_candidate": bool(
            certification["passed"]
            and threshold_report["threshold_selection_sufficient"]
            and (metrics["avg_selected_return"] or -1) >= 0
        ),
        "automatic_promotion": False,
        "candidate_iteration_comparison": comparison,
    }
    _write(output_dir / "alpha_atlas_v31_model_report.json", report)
    _write(
        output_dir / "alpha_atlas_v31_backtest_report.json",
        {
            "duplicate_weighted_metrics": metrics,
            "threshold_selection": threshold_report,
        },
    )
    _write(
        output_dir / "alpha_atlas_v31_recovery_rebaseline_report.json",
        {
            "comparison_mode": "candidate_iteration_recovery",
            "candidate_version": V31_CANDIDATE_VERSION,
            "servability_certification_passed": certification["passed"],
            "automatic_promotion": False,
            "human_review_required": True,
            "status": (
                "reviewable"
                if report["promotion_candidate"]
                else "NO V3.1 PROMOTION CANDIDATE"
            ),
        },
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--train", default="data/track_b/training_quality/cleaned_train.jsonl"
    )
    parser.add_argument(
        "--test", default="data/track_b/training_quality/cleaned_test.jsonl"
    )
    parser.add_argument(
        "--all-cleaned", default="data/track_b/training_quality/cleaned_all.jsonl"
    )
    parser.add_argument("--output-dir", default="data/track_b/alpha_atlas_v31")
    args = parser.parse_args()
    if not os.environ.get("MASSIVE_API_KEY", "").strip():
        raise SystemExit(
            "MASSIVE_API_KEY is required for real V3.1 dry runs and certification; no candidate was generated"
        )
    result = train_v31(
        Path(args.train),
        Path(args.test),
        Path(args.all_cleaned),
        Path(args.output_dir),
        MarketDataService(),
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
