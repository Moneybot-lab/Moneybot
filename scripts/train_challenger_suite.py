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

from moneybot.services.deterministic_model import fit_probability_calibration, load_artifact, predict_proba, summarize_binary_predictions, train_logistic_baseline
from moneybot.services.temporal_validation import purged_embargoed_split
from moneybot.services.alpha_atlas_v4_temporal_split import validate_split_plan
from moneybot.services.decision_target import HORIZON_DAYS, TARGET_NAME, target_metadata
from scripts.day10_train_candidate_model import _backtest_compatible_feature_columns, _chronological_split, _fill_feature_gaps, _future_safe_feature_columns, _prepare_frame, _select_feature_columns
from moneybot.services.alpha_atlas_v4_phase0 import apply_feature_fill_policy, fit_feature_fill_policy

SUITE_SCHEMA_VERSION = "moneybot-challenger-suite.v2"
LINEAGE_SCHEMA_VERSION = "moneybot-challenger-lineage.v1"
LOGISTIC_L2_GRID = (5e-4, 1e-3, 5e-3)
LOGISTIC_THRESHOLD_GRID = (0.45, 0.50, 0.55, 0.60)
MAX_STUMP_CHALLENGERS = 8
SHALLOW_TREE_DEPTHS = (2, 3)
SHALLOW_TREE_MIN_LEAF = 20
CALIBRATED_LINEAR_VARIANTS = (
    {"name": "full-balanced", "feature_subset_policy": "all", "training_window_policy": "full", "sample_weight_policy": "balanced", "l2": 0.001},
    {"name": "no-raw-price", "feature_subset_policy": "exclude_raw_price", "training_window_policy": "full", "sample_weight_policy": "tail_safe", "l2": 0.002},
    {"name": "momentum-recent-half", "feature_subset_policy": "momentum", "training_window_policy": "recent_half", "sample_weight_policy": "balanced", "l2": 0.005},
    {"name": "no-price-recent-quarter", "feature_subset_policy": "exclude_raw_price", "training_window_policy": "recent_quarter", "sample_weight_policy": "tail_safe", "l2": 0.008},
)
RISK_FILTER_THRESHOLDS = (0.20, 0.30)
HARD_EXAMPLE_MAX_FRACTION = 0.20
HARD_EXAMPLE_WEIGHT = 4.0
HARD_EXAMPLE_MAX_WEIGHT = 5.0
ABSTENTION_MARGINS = (0.025, 0.05)
SPECIALIZED_CHALLENGER_FAMILIES = ("big_loss_avoider", "big_gain_hunter", "recent_window_model", "ranking_top5_model")
EMBARGO_DAYS = 1


def _return_column(df: pd.DataFrame, horizon_days: int) -> str | None:
    preferred = f"return_{horizon_days}d"
    if preferred in df.columns:
        return preferred
    for col in ("return_5d", "forward_return_5d", "return_3d", "return_1d"):
        if col in df.columns:
            return col
    return None


def _ranking_metrics(scores: np.ndarray, labels: np.ndarray, returns: np.ndarray | None, *, top_fraction: float = 0.20) -> dict[str, Any]:
    n = int(len(scores))
    if n == 0:
        return {
            "top_k": 0,
            "top_k_precision": 0.0,
            "top_k_avg_return": 0.0,
            "pairwise_ranking_loss": 0.0,
            "big_gain_capture": 0.0,
            "big_loss_demotion": 0.0,
            "ranking_objective": 0.0,
        }
    top_k = max(1, int(np.ceil(n * top_fraction)))
    order = np.argsort(-scores)
    top = order[:top_k]
    ret = np.zeros(n, dtype=float) if returns is None else np.nan_to_num(np.asarray(returns, dtype=float), nan=0.0)
    positives = scores[labels >= 0.5]
    negatives = scores[labels < 0.5]
    if len(positives) and len(negatives):
        pairwise_loss = float(np.mean(positives[:, None] <= negatives[None, :]))
    else:
        pairwise_loss = 0.0
    gain_cutoff = max(0.0, float(np.quantile(ret, 0.80))) if n else 0.0
    loss_cutoff = min(0.0, float(np.quantile(ret, 0.20))) if n else 0.0
    big_gain = ret >= gain_cutoff
    big_loss = ret <= loss_cutoff
    big_gain_capture = float(big_gain[top].sum() / big_gain.sum()) if big_gain.any() else 0.0
    big_loss_demotion = 1.0 - float(big_loss[top].sum() / big_loss.sum()) if big_loss.any() else 1.0
    top_precision = float(labels[top].mean()) if len(top) else 0.0
    top_avg_return = float(ret[top].mean()) if len(top) else 0.0
    objective = (
        top_avg_return
        + (0.10 * top_precision)
        + (0.10 * big_gain_capture)
        + (0.05 * big_loss_demotion)
        - (0.10 * pairwise_loss)
    )
    return {
        "top_k": int(top_k),
        "top_k_precision": round(top_precision, 6),
        "top_k_avg_return": round(top_avg_return, 6),
        "pairwise_ranking_loss": round(pairwise_loss, 6),
        "big_gain_capture": round(big_gain_capture, 6),
        "big_loss_demotion": round(big_loss_demotion, 6),
        "ranking_objective": round(float(objective), 6),
    }


def _prediction_profile(predictions: np.ndarray, frame: pd.DataFrame, return_col: str | None) -> dict[str, Any]:
    values = np.asarray(predictions, dtype=np.uint8)
    buckets = _return_bucket_series(frame, return_col).to_numpy()
    big_loss = buckets == "big_loss"
    big_loss_predictions = int(((values == 1) & big_loss).sum())
    return {
        "prediction_fingerprint": hashlib.sha256(values.tobytes()).hexdigest(),
        "positive_predictions": int(values.sum()),
        "big_loss_predictions": big_loss_predictions,
        "big_loss_prediction_rate": round(big_loss_predictions / int(big_loss.sum()), 6) if big_loss.any() else 0.0,
    }


def _abstention_predictions(probabilities: np.ndarray, threshold: float, abstention: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    margin = float(abstention.get("margin", 0.0)) if abstention.get("enabled") else 0.0
    lower = max(0.0, threshold - margin)
    upper = min(1.0, threshold + margin)
    abstained = (probabilities >= lower) & (probabilities < upper) if margin > 0.0 else np.zeros(len(probabilities), dtype=bool)
    predictions = (probabilities >= upper).astype(int)
    return predictions, abstained


def _walk_forward_splits(df: pd.DataFrame, *, max_windows: int = 3) -> list[tuple[int, int, int]]:
    """Return rolling/expanding chronological folds as (train_start, train_end, test_end)."""
    n = int(len(df))
    if n < 6:
        return []
    test_size = max(1, n // 6)
    window_count = min(max_windows, max(1, n // test_size - 2))
    first_test_start = n - (window_count * test_size)
    if first_test_start < test_size * 2:
        first_test_start = test_size * 2
    folds: list[tuple[int, int, int]] = []
    for idx in range(window_count):
        train_end = first_test_start + (idx * test_size)
        test_end = min(n, train_end + test_size)
        if test_end <= train_end or train_end < test_size * 2:
            continue
        train_start = 0 if idx < 2 else max(0, train_end - (test_size * 3))
        if train_end - train_start < 2:
            continue
        folds.append((train_start, train_end, test_end))
    return folds


def _average_metric_dicts(metric_dicts: list[dict[str, Any]]) -> dict[str, Any]:
    if not metric_dicts:
        return {}
    keys = sorted({key for metrics in metric_dicts for key in metrics if isinstance(metrics.get(key), (int, float))})
    averaged = {key: round(float(np.mean([float(metrics.get(key, 0.0)) for metrics in metric_dicts])), 6) for key in keys}
    averaged["window_count"] = len(metric_dicts)
    return averaged


def _specialized_training_inputs(
    train_df: pd.DataFrame,
    y_train: np.ndarray,
    return_col: str | None,
    family: str,
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """Apply a specialized family's row, target, and weighting recipe."""
    family_train_df = train_df
    family_y_train = np.asarray(y_train, dtype=float)
    if family == "recent_window_model" and len(train_df) >= 10:
        recent_rows = max(5, len(train_df) // 2)
        family_train_df = train_df.tail(recent_rows)
        family_y_train = family_y_train[-recent_rows:]
    elif family == "ranking_top5_model":
        ranking_labels = _ranking_top5_labels(train_df, return_col)
        if ranking_labels.sum() > 0:
            family_y_train = ranking_labels

    sample_weight = _specialized_sample_weight(family_train_df, return_col, family)
    return family_train_df, family_y_train, sample_weight


def _train_logistic_recipe(
    train_df: pd.DataFrame,
    y_train: np.ndarray,
    feature_columns: list[str],
    return_col: str | None,
    spec: dict[str, Any],
):
    """Train one logistic recipe identically for final and walk-forward fits."""
    recipe_train_df = train_df
    recipe_y_train = np.asarray(y_train, dtype=float)
    sample_weight = None
    family = str(spec.get("family") or "")
    if family in SPECIALIZED_CHALLENGER_FAMILIES:
        recipe_train_df, recipe_y_train, sample_weight = _specialized_training_inputs(
            train_df,
            recipe_y_train,
            return_col,
            family,
        )
    return train_logistic_baseline(
        recipe_train_df[feature_columns].to_numpy(dtype=float),
        recipe_y_train,
        learning_rate=float(spec.get("lr", 0.08)),
        l2=float(spec.get("l2", 0.001)),
        decision_threshold=float(spec.get("threshold", 0.5)),
        epochs=int(spec.get("epochs", 550)),
        sample_weight=sample_weight,
    )


def _linear_feature_subset(feature_columns: list[str], policy: str) -> list[str]:
    if policy == "exclude_raw_price":
        selected = [column for column in feature_columns if column != "feature_price" and not column.endswith("_close")]
    elif policy == "momentum":
        tokens = ("return", "change", "rsi", "macd", "momentum", "volume")
        selected = [column for column in feature_columns if any(token in column.lower() for token in tokens)]
    else:
        selected = list(feature_columns)
    return selected or list(feature_columns)


def _linear_sample_weights(frame: pd.DataFrame, labels: np.ndarray, return_col: str | None, policy: str) -> np.ndarray:
    weights = np.ones(len(frame), dtype=float)
    if policy == "balanced":
        positives = max(1, int((labels >= 0.5).sum()))
        negatives = max(1, int((labels < 0.5).sum()))
        weights[labels >= 0.5] = len(labels) / (2.0 * positives)
        weights[labels < 0.5] = len(labels) / (2.0 * negatives)
    elif policy == "tail_safe":
        buckets = _return_bucket_series(frame, return_col).to_numpy()
        weights[buckets == "big_loss"] = 8.0
        weights[buckets == "loss"] = 2.0
        weights[buckets == "big_gain"] = 3.0
    return weights


def _apply_training_window(frame: pd.DataFrame, policy: str) -> pd.DataFrame:
    if policy == "recent_half":
        return frame.tail(max(1, len(frame) // 2)).copy()
    if policy == "recent_quarter":
        return frame.tail(max(1, len(frame) // 4)).copy()
    return frame.copy()


def _train_calibrated_linear_recipe(
    train_df: pd.DataFrame,
    target_col: str,
    feature_columns: list[str],
    return_col: str | None,
    spec: dict[str, Any],
    *,
    horizon_days: int,
):
    """Fit and calibrate a linear recipe on separate purged periods."""
    subset = _linear_feature_subset(feature_columns, str(spec["feature_subset_policy"]))
    train_df = _apply_training_window(train_df, str(spec.get("training_window_policy", "full")))
    fit_df, calibration_df = _chronological_split(train_df, 0.8)
    fit_df, calibration_df, split = purged_embargoed_split(
        fit_df,
        calibration_df,
        horizon_days=horizon_days,
        embargo_days=EMBARGO_DAYS,
    )
    if fit_df.empty or calibration_df.empty:
        fit_df = train_df
        calibration_df = train_df.iloc[0:0]
        split = {**split, "calibration_available": False, "fallback": "identity_calibration"}
    fit_labels = fit_df[target_col].to_numpy(dtype=float)
    weights = _linear_sample_weights(fit_df, fit_labels, return_col, str(spec["sample_weight_policy"]))
    artifact = train_logistic_baseline(
        fit_df[subset].to_numpy(dtype=float),
        fit_labels,
        learning_rate=float(spec.get("lr", 0.06)),
        l2=float(spec["l2"]),
        decision_threshold=float(spec.get("threshold", 0.60)),
        epochs=int(spec.get("epochs", 650)),
        sample_weight=weights,
    )
    artifact.feature_columns = subset
    if calibration_df.empty:
        calibration = {"method": "identity_insufficient_calibration_rows", "slope": 1.0, "intercept": 0.0, "applied": False}
    else:
        raw = predict_proba(artifact, calibration_df[subset].to_numpy(dtype=float))
        calibration = fit_probability_calibration(raw, calibration_df[target_col].to_numpy(dtype=float))
    artifact.calibration_slope = float(calibration["slope"])
    artifact.calibration_intercept = float(calibration["intercept"])
    return artifact, calibration, split


def _fit_artifact_calibration(artifact, frame: pd.DataFrame, labels: np.ndarray) -> dict[str, Any]:
    if frame.empty:
        return {"method": "identity_insufficient_calibration_rows", "slope": 1.0, "intercept": 0.0, "applied": False}
    raw = predict_proba(artifact, frame[artifact.feature_columns].to_numpy(dtype=float))
    calibration = fit_probability_calibration(raw, labels)
    artifact.calibration_slope = float(calibration["slope"])
    artifact.calibration_intercept = float(calibration["intercept"])
    return calibration


def _train_two_stage_risk_recipe(
    train_df: pd.DataFrame,
    target_col: str,
    feature_columns: list[str],
    return_col: str | None,
    spec: dict[str, Any],
    *,
    horizon_days: int,
):
    """Train independent return and big-loss models on one leakage-safe recipe."""
    recipe_df = _apply_training_window(train_df, str(spec.get("training_window_policy", "full")))
    subset = _linear_feature_subset(feature_columns, str(spec["feature_subset_policy"]))
    fit_df, calibration_df = _chronological_split(recipe_df, 0.8)
    fit_df, calibration_df, split = purged_embargoed_split(fit_df, calibration_df, horizon_days=horizon_days, embargo_days=EMBARGO_DAYS)
    if fit_df.empty or calibration_df.empty:
        fit_df = recipe_df
        calibration_df = recipe_df.iloc[0:0]
        split = {**split, "calibration_available": False, "fallback": "identity_calibration"}

    decision_labels = fit_df[target_col].to_numpy(dtype=float)
    decision_weights = _linear_sample_weights(fit_df, decision_labels, return_col, "tail_safe")
    decision_model = train_logistic_baseline(
        fit_df[subset].to_numpy(dtype=float),
        decision_labels,
        learning_rate=0.06,
        l2=float(spec["decision_l2"]),
        decision_threshold=float(spec["decision_threshold"]),
        epochs=650,
        sample_weight=decision_weights,
    )
    decision_model.feature_columns = subset

    risk_labels = (_return_bucket_series(fit_df, return_col).to_numpy() == "big_loss").astype(float)
    risk_weights = np.where(risk_labels >= 0.5, 10.0, 1.0)
    risk_model = train_logistic_baseline(
        fit_df[subset].to_numpy(dtype=float),
        risk_labels,
        learning_rate=0.06,
        l2=float(spec["risk_l2"]),
        decision_threshold=float(spec["risk_threshold"]),
        epochs=650,
        sample_weight=risk_weights,
    )
    risk_model.feature_columns = subset

    decision_calibration = _fit_artifact_calibration(
        decision_model,
        calibration_df,
        calibration_df[target_col].to_numpy(dtype=float) if not calibration_df.empty else np.array([], dtype=float),
    )
    calibration_risk_labels = (_return_bucket_series(calibration_df, return_col).to_numpy() == "big_loss").astype(float)
    risk_calibration = _fit_artifact_calibration(risk_model, calibration_df, calibration_risk_labels)
    return decision_model, risk_model, decision_calibration, risk_calibration, split


def _two_stage_scores(decision_model, risk_model, frame: pd.DataFrame, risk_threshold: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    decision_probs = predict_proba(decision_model, frame[decision_model.feature_columns].to_numpy(dtype=float))
    risk_probs = predict_proba(risk_model, frame[risk_model.feature_columns].to_numpy(dtype=float))
    predictions = ((decision_probs >= decision_model.decision_threshold) & (risk_probs <= risk_threshold)).astype(int)
    return decision_probs, risk_probs, predictions


def _bounded_hard_example_weights(
    frame: pd.DataFrame,
    labels: np.ndarray,
    feature_columns: list[str],
    return_col: str | None,
    spec: dict[str, Any],
) -> tuple[np.ndarray, dict[str, Any], str]:
    """Mine a bounded set of tail mistakes from an actual seed artifact."""
    seed = train_logistic_baseline(
        frame[feature_columns].to_numpy(dtype=float),
        labels,
        learning_rate=0.06,
        l2=float(spec["seed_l2"]),
        decision_threshold=float(spec["threshold"]),
        epochs=500,
    )
    seed.feature_columns = list(feature_columns)
    probabilities = predict_proba(seed, frame[feature_columns].to_numpy(dtype=float))
    predictions = (probabilities >= seed.decision_threshold).astype(int)
    buckets = _return_bucket_series(frame, return_col).to_numpy()
    bad_buy = (buckets == "big_loss") & (predictions == 1)
    missed_winner = (buckets == "big_gain") & (predictions == 0)
    candidate_indexes = np.flatnonzero(bad_buy | missed_winner)
    max_rows = min(len(candidate_indexes), max(1, int(np.floor(len(frame) * float(spec["max_hard_fraction"])))))
    severity = np.where(bad_buy, probabilities, 1.0 - probabilities)
    selected = candidate_indexes[np.argsort(-severity[candidate_indexes], kind="stable")[:max_rows]]
    weights = np.ones(len(frame), dtype=float)
    weights[selected] = min(float(spec["hard_example_weight"]), float(spec["max_sample_weight"]))
    seed_recipe = {
        "model_type": "logistic_regression_seed",
        "feature_columns": feature_columns,
        "l2": spec["seed_l2"],
        "threshold": spec["threshold"],
    }
    seed_id = f"recipe-{hashlib.sha256(json.dumps(seed_recipe, sort_keys=True).encode('utf-8')).hexdigest()[:16]}"
    diagnostics = {
        "scoring_method": "artifact_predictions",
        "seed_lineage_id": seed_id,
        "candidate_mistake_rows": int(len(candidate_indexes)),
        "selected_hard_rows": int(len(selected)),
        "selected_bad_buy_rows": int(bad_buy[selected].sum()),
        "selected_missed_winner_rows": int(missed_winner[selected].sum()),
        "max_hard_rows": int(max(1, np.floor(len(frame) * float(spec["max_hard_fraction"])) )),
        "selected_fraction": round(len(selected) / len(frame), 6) if len(frame) else 0.0,
        "hard_example_weight": float(weights[selected[0]]) if len(selected) else 1.0,
        "max_sample_weight": float(weights.max()) if len(weights) else 1.0,
    }
    return weights, diagnostics, seed_id


def _train_hard_example_recipe(
    train_df: pd.DataFrame,
    target_col: str,
    feature_columns: list[str],
    return_col: str | None,
    spec: dict[str, Any],
):
    subset = _linear_feature_subset(feature_columns, str(spec["feature_subset_policy"]))
    labels = train_df[target_col].to_numpy(dtype=float)
    weights, diagnostics, parent_id = _bounded_hard_example_weights(train_df, labels, subset, return_col, spec)
    artifact = train_logistic_baseline(
        train_df[subset].to_numpy(dtype=float),
        labels,
        learning_rate=0.06,
        l2=float(spec["l2"]),
        decision_threshold=float(spec["threshold"]),
        epochs=650,
        sample_weight=weights,
    )
    artifact.feature_columns = subset
    return artifact, diagnostics, parent_id


def _train_ranking_lane_recipe(
    train_df: pd.DataFrame,
    feature_columns: list[str],
    return_col: str | None,
    spec: dict[str, Any],
):
    recipe_df = _apply_training_window(train_df, str(spec["training_window_policy"]))
    subset = _linear_feature_subset(feature_columns, str(spec["feature_subset_policy"]))
    labels = _ranking_top5_labels(recipe_df, return_col)
    weights = _specialized_sample_weight(recipe_df, return_col, "ranking_top5_model")
    artifact = train_logistic_baseline(
        recipe_df[subset].to_numpy(dtype=float),
        labels,
        learning_rate=0.06,
        l2=float(spec["l2"]),
        decision_threshold=float(spec["threshold"]),
        epochs=650,
        sample_weight=weights,
    )
    artifact.feature_columns = subset
    return artifact


def _apply_walk_forward_metrics(
    challengers: list[dict[str, Any]],
    *,
    clean: pd.DataFrame,
    folds: list[tuple[int, int, int]],
    feature_columns: list[str],
    target_col: str,
    return_col: str | None,
    horizon_days: int,
    fit_source: pd.DataFrame | None = None,
) -> None:
    if not folds:
        return
    for challenger in challengers:
        fold_metrics: list[dict[str, Any]] = []
        for train_start, train_end, test_end in folds:
            source = fit_source if fit_source is not None else clean
            fold_train = source.iloc[train_start:train_end]
            fold_test = source.iloc[train_end:test_end]
            fold_train, fold_test, _ = purged_embargoed_split(
                fold_train,
                fold_test,
                horizon_days=horizon_days,
                embargo_days=EMBARGO_DAYS,
            )
            if fold_train.empty or fold_test.empty:
                continue
            if fit_source is not None:
                fold_policy = fit_feature_fill_policy(fold_train, feature_columns)
                fold_train = apply_feature_fill_policy(fold_train, fold_policy, expected_feature_contract_version="alpha-atlas-v4-features.v2")
                fold_test = apply_feature_fill_policy(fold_test, fold_policy, expected_feature_contract_version="alpha-atlas-v4-features.v2")
            y_train = fold_train[target_col].to_numpy(dtype=float)
            X_test = fold_test[feature_columns].to_numpy(dtype=float)
            y_test = fold_test[target_col].to_numpy(dtype=float)
            fold_returns = fold_test[return_col].to_numpy(dtype=float) if return_col else None
            model_type = challenger.get("model_type")
            spec = challenger.get("spec", {})
            if model_type == "logistic_regression":
                artifact = _train_logistic_recipe(fold_train, y_train, feature_columns, return_col, spec)
                scores = predict_proba(artifact, X_test)
                preds = (scores >= artifact.decision_threshold).astype(int)
            elif model_type == "calibrated_linear":
                artifact, _, _ = _train_calibrated_linear_recipe(
                    fold_train,
                    target_col,
                    feature_columns,
                    return_col,
                    spec,
                    horizon_days=horizon_days,
                )
                scores = predict_proba(artifact, fold_test[artifact.feature_columns].to_numpy(dtype=float))
                preds = (scores >= artifact.decision_threshold).astype(int)
            elif model_type == "shallow_decision_tree":
                min_leaf = min(int(spec["min_leaf"]), max(2, len(fold_train) // 10))
                tree = _fit_shallow_tree(
                    fold_train,
                    y_train,
                    feature_columns,
                    max_depth=int(spec["max_depth"]),
                    min_leaf=min_leaf,
                )
                scores = _shallow_tree_scores(tree, fold_test)
                preds = (scores >= float(spec["threshold"])).astype(int)
            elif model_type == "two_stage_risk_filter":
                decision_model, risk_model, _, _, _ = _train_two_stage_risk_recipe(
                    fold_train,
                    target_col,
                    feature_columns,
                    return_col,
                    spec,
                    horizon_days=horizon_days,
                )
                scores, _, preds = _two_stage_scores(decision_model, risk_model, fold_test, float(spec["risk_threshold"]))
            elif model_type == "hard_example_linear":
                artifact, _, _ = _train_hard_example_recipe(fold_train, target_col, feature_columns, return_col, spec)
                scores = predict_proba(artifact, fold_test[artifact.feature_columns].to_numpy(dtype=float))
                preds = (scores >= artifact.decision_threshold).astype(int)
            elif model_type == "ranking_lane_linear":
                artifact = _train_ranking_lane_recipe(fold_train, feature_columns, return_col, spec)
                scores = predict_proba(artifact, fold_test[artifact.feature_columns].to_numpy(dtype=float))
                preds = (scores >= artifact.decision_threshold).astype(int)
            elif model_type == "abstention_linear":
                artifact, _, _ = _train_calibrated_linear_recipe(fold_train, target_col, feature_columns, return_col, spec, horizon_days=horizon_days)
                scores = predict_proba(artifact, fold_test[artifact.feature_columns].to_numpy(dtype=float))
                preds, _ = _abstention_predictions(scores, artifact.decision_threshold, spec["abstention"])
            elif model_type == "decision_stump":
                feature = str(spec.get("feature", ""))
                if feature not in fold_train.columns or feature not in fold_test.columns:
                    continue
                threshold = float(np.median(fold_train[feature].to_numpy(dtype=float)))
                direction = str(spec.get("direction", "gte_positive"))
                scores = fold_test[feature].to_numpy(dtype=float)
                preds = _stump_predictions(scores, threshold, direction)
            elif model_type == "baseline_classifier":
                majority_class = int(float(y_train.mean()) >= 0.5)
                if challenger.get("model_version") == "challenger-baseline-always-up-v1":
                    preds = np.ones_like(y_test, dtype=int)
                elif challenger.get("model_version") == "challenger-baseline-always-down-v1":
                    preds = np.zeros_like(y_test, dtype=int)
                else:
                    preds = np.full_like(y_test, majority_class, dtype=int)
                scores = preds.astype(float)
            else:
                continue
            metrics = summarize_binary_predictions(y_test, preds)
            metrics.update(_ranking_metrics(scores, y_test, fold_returns))
            metrics.update(_prediction_profile(preds, fold_test, return_col))
            fold_metrics.append(metrics)
        walk_forward = _average_metric_dicts(fold_metrics)
        if walk_forward:
            positive_windows = sum(1 for metrics in fold_metrics if float(metrics.get("ranking_objective", 0.0)) > 0.0)
            min_positive_windows = min(len(fold_metrics), max(2, int(np.ceil(len(fold_metrics) / 2))))
            walk_forward["positive_ranking_windows"] = positive_windows
            walk_forward["min_positive_windows_required"] = min_positive_windows
            walk_forward["passed"] = positive_windows >= min_positive_windows
            walk_forward["zero_big_loss_windows"] = sum(1 for metrics in fold_metrics if int(metrics.get("big_loss_predictions", 0)) == 0)
            walk_forward["zero_big_loss_window_rate"] = round(walk_forward["zero_big_loss_windows"] / len(fold_metrics), 6)
            challenger["metrics"]["walk_forward"] = walk_forward
            challenger["metrics"]["walk_forward_passed"] = walk_forward["passed"]
            challenger["metrics"]["walk_forward_ranking_objective"] = walk_forward.get("ranking_objective", 0.0)
            challenger["metrics"]["walk_forward_recipe_reproduced"] = True



def _event_date_values(df: pd.DataFrame) -> pd.Series:
    if "event_date" in df.columns:
        dates = df["event_date"].fillna("").astype(str)
        if dates.str.strip().any():
            return dates.replace("", "unknown")
    if "ts" in df.columns:
        parsed = pd.to_datetime(pd.to_numeric(df["ts"], errors="coerce"), unit="s", utc=True, errors="coerce")
        return parsed.dt.strftime("%Y-%m-%d").fillna("unknown")
    return pd.Series("unknown", index=df.index)


def _return_bucket_series(df: pd.DataFrame, return_col: str | None) -> pd.Series:
    if "return_bin_5d" in df.columns:
        return df["return_bin_5d"].fillna("").astype(str)
    if return_col and return_col in df.columns:
        returns = pd.to_numeric(df[return_col], errors="coerce")
        buckets = pd.Series(
            np.select(
                [returns < -0.03, returns < -0.005, returns <= 0.005, returns <= 0.03],
                ["big_loss", "loss", "flat", "gain"],
                default="big_gain",
            ),
            index=df.index,
        )
        buckets.loc[returns.isna()] = ""
        return buckets
    return pd.Series("", index=df.index)


def _artifact_scored_mistake_rows(df: pd.DataFrame, model_path: Path, return_col: str | None) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """Find tail mistakes from an artifact's actual probabilities and threshold."""
    artifact = load_artifact(model_path)
    scored = df.copy()
    for idx, feature in enumerate(artifact.feature_columns):
        fallback = float(artifact.means[idx]) if idx < len(artifact.means) else 0.0
        if feature not in scored.columns:
            scored[feature] = fallback
        numeric = pd.to_numeric(scored[feature], errors="coerce").replace([np.inf, -np.inf], np.nan)
        scored[feature] = numeric.fillna(fallback).astype(float)
    probabilities = predict_proba(artifact, scored[artifact.feature_columns].to_numpy(dtype=float))
    predictions = (probabilities >= artifact.decision_threshold).astype(int)
    scored["artifact_model_version"] = artifact.version
    scored["artifact_model_path"] = str(model_path)
    scored["artifact_decision_threshold"] = float(artifact.decision_threshold)
    scored["artifact_probability"] = probabilities
    scored["artifact_prediction"] = predictions
    buckets = _return_bucket_series(scored, return_col)
    missed_big_gain = scored.loc[(buckets == "big_gain") & (predictions == 0)].copy()
    missed_big_gain["mistake_type"] = "missed_big_gain_winner"
    bad_buy_big_loss = scored.loc[(buckets == "big_loss") & (predictions == 1)].copy()
    bad_buy_big_loss["mistake_type"] = "bad_buy_big_loss_false_positive"
    return (
        {
            "missed_big_gain_winners": missed_big_gain,
            "bad_buy_big_loss_false_positives": bad_buy_big_loss,
        },
        {
            "scoring_method": "artifact_predictions",
            "model_version": artifact.version,
            "model_path": str(model_path),
            "decision_threshold": float(artifact.decision_threshold),
            "rows_scored": int(len(scored)),
        },
    )


def _resolve_mistake_scoring_model(output_dir: Path, model_path: Path | None) -> Path | None:
    """Resolve an explicit scoring artifact, with compatibility discovery for older callers."""
    if model_path is not None:
        if not model_path.exists():
            raise FileNotFoundError(f"Mistake-scoring model does not exist: {model_path}")
        return model_path
    for candidate in sorted(output_dir.glob("*.json")):
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("model_type") == "logistic_regression" and all(key in payload for key in ("feature_columns", "means", "stds", "weights", "bias")):
            return candidate
    return None


def _write_daily_mistake_slices(df: pd.DataFrame, output_dir: Path, return_col: str | None, model_path: Path | None = None) -> dict[str, Any]:
    slice_root = output_dir / "mistake_slices"
    slice_root.mkdir(parents=True, exist_ok=True)
    resolved_model_path = _resolve_mistake_scoring_model(output_dir, model_path)
    if resolved_model_path is None:
        return {
            "slice_root": str(slice_root),
            "scoring_method": "unavailable_no_artifact",
            "model_version": None,
            "model_path": None,
            "decision_threshold": None,
            "rows_scored": 0,
            "slices": {
                "missed_big_gain_winners": {"rows": 0, "daily_files": []},
                "bad_buy_big_loss_false_positives": {"rows": 0, "daily_files": []},
            },
        }
    slices, scoring = _artifact_scored_mistake_rows(df, resolved_model_path, return_col)
    manifest: dict[str, Any] = {"slice_root": str(slice_root), **scoring, "slices": {}}
    for slice_name, selected in slices.items():
        slice_dir = slice_root / slice_name
        slice_dir.mkdir(parents=True, exist_ok=True)
        manifest["slices"][slice_name] = {"rows": int(len(selected)), "daily_files": []}
        if selected.empty:
            continue
        selected_dates = _event_date_values(selected)
        for day, group in selected.groupby(selected_dates):
            safe_day = str(day or "unknown").replace("/", "-")
            path = slice_dir / f"{safe_day}.jsonl"
            path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in group.to_dict(orient="records")), encoding="utf-8")
            manifest["slices"][slice_name]["daily_files"].append({"date": safe_day, "path": str(path), "rows": int(len(group))})
    return manifest

def _load_jsonl(path: Path) -> pd.DataFrame:
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return pd.DataFrame(rows)


def _target(df: pd.DataFrame, horizon_days: int) -> str:
    if horizon_days != HORIZON_DAYS:
        raise ValueError(
            f"Decision-lane target is fixed at {HORIZON_DAYS}d; got {horizon_days}d"
        )
    if TARGET_NAME in df.columns:
        return TARGET_NAME
    raise ValueError(f"Missing canonical decision-lane target {TARGET_NAME}")


def _write_artifact(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _candidate_lineage(
    *,
    model_version: str,
    model_type: str,
    spec: dict[str, Any],
    feature_columns: list[str],
    decision_threshold: float | None,
    sample_weight_policy: str = "uniform",
    calibration: dict[str, Any] | None = None,
    abstention: dict[str, Any] | None = None,
    extra_deployable_config: dict[str, Any] | None = None,
    generation: int = 1,
    parent_lineage_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Build stable lineage and deployable configuration for one recipe."""
    deployable_config = {
        "model_family": model_type,
        "decision_threshold": decision_threshold,
        "calibration": calibration or {"method": "identity", "slope": 1.0, "intercept": 0.0},
        "feature_subset": list(feature_columns),
        "training_window_policy": str(spec.get("training_window_policy", "full_window")),
        "sample_weight_policy": sample_weight_policy,
        "abstention": abstention or {"enabled": False, "margin": 0.0},
    }
    if extra_deployable_config:
        deployable_config.update(extra_deployable_config)
    recipe = {"model_version": model_version, "model_type": model_type, "training_spec": spec, "deployable_config": deployable_config}
    recipe_hash = hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {
        "schema_version": LINEAGE_SCHEMA_VERSION,
        "lineage_id": f"recipe-{recipe_hash[:16]}",
        "recipe_hash": recipe_hash,
        "generation": int(generation),
        "parent_lineage_ids": list(parent_lineage_ids or []),
        "model_version": model_version,
        "recipe": recipe,
    }


def _logistic_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for l2 in LOGISTIC_L2_GRID:
        for threshold in LOGISTIC_THRESHOLD_GRID:
            specs.append(
                {
                    "model_type": "logistic_regression",
                    "name": f"challenger-logreg-l2{str(l2).replace('.', 'p').replace('-', 'm')}-thr{int(threshold * 100)}-v1",
                    "lr": 0.08,
                    "l2": l2,
                    "threshold": threshold,
                    "epochs": 550,
                }
            )
    return specs


def _add_logistic_challengers(
    challengers: list[dict[str, Any]],
    *,
    output_dir: Path,
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray,
    feature_columns: list[str],
    test_returns: np.ndarray | None,
) -> None:
    for spec in _logistic_specs():
        artifact = train_logistic_baseline(
            X_train,
            y_train,
            learning_rate=float(spec["lr"]),
            l2=float(spec["l2"]),
            decision_threshold=float(spec["threshold"]),
            epochs=int(spec["epochs"]),
        )
        artifact.version = str(spec["name"])
        artifact.feature_columns = list(feature_columns)
        model_path = output_dir / f"{artifact.version}.json"
        lineage = _candidate_lineage(model_version=artifact.version, model_type="logistic_regression", spec=spec, feature_columns=feature_columns, decision_threshold=artifact.decision_threshold)
        _write_artifact(model_path, {"model_type": "logistic_regression", **artifact.to_dict(), "training_spec": spec, "lineage": lineage})
        probs = predict_proba(artifact, X_test)
        preds = (probs >= artifact.decision_threshold).astype(int)
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(probs, y_test, test_returns))
        challengers.append({"model_version": artifact.version, "model_type": "logistic_regression", "model_path": str(model_path), "metrics": metrics, "spec": spec, "lineage": lineage})


def _add_calibrated_linear_challengers(
    challengers: list[dict[str, Any]],
    *,
    output_dir: Path,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    target_col: str,
    feature_columns: list[str],
    return_col: str | None,
    test_returns: np.ndarray | None,
    horizon_days: int,
) -> None:
    y_test = test_df[target_col].to_numpy(dtype=float)
    for variant in CALIBRATED_LINEAR_VARIANTS:
        spec = {
            "family": "calibrated_linear",
            **variant,
            "lr": 0.06,
            "threshold": 0.60,
            "epochs": 650,
            "calibration_policy": "platt_if_brier_improves",
            "abstention": {"enabled": False, "margin": 0.0},
        }
        artifact, calibration, calibration_split = _train_calibrated_linear_recipe(
            train_df,
            target_col,
            feature_columns,
            return_col,
            spec,
            horizon_days=horizon_days,
        )
        artifact.version = f"challenger-calibrated-linear-{variant['name']}-v1"
        model_path = output_dir / f"{artifact.version}.json"
        lineage = _candidate_lineage(
            model_version=artifact.version,
            model_type="calibrated_linear",
            spec=spec,
            feature_columns=artifact.feature_columns,
            decision_threshold=artifact.decision_threshold,
            sample_weight_policy=str(spec["sample_weight_policy"]),
            calibration={"method": calibration["method"], "slope": artifact.calibration_slope, "intercept": artifact.calibration_intercept},
            abstention=spec["abstention"],
        )
        payload = {
            "model_type": "calibrated_linear",
            **artifact.to_dict(),
            "training_spec": spec,
            "calibration": calibration,
            "calibration_split": calibration_split,
            "lineage": lineage,
        }
        _write_artifact(model_path, payload)
        probs = predict_proba(artifact, test_df[artifact.feature_columns].to_numpy(dtype=float))
        preds = (probs >= artifact.decision_threshold).astype(int)
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(probs, y_test, test_returns))
        metrics.update(_prediction_profile(preds, test_df, return_col))
        challengers.append({
            "model_version": artifact.version,
            "model_type": "calibrated_linear",
            "model_path": str(model_path),
            "metrics": metrics,
            "spec": spec,
            "lineage": lineage,
        })


def _add_abstention_challengers(
    challengers: list[dict[str, Any]],
    *,
    output_dir: Path,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    target_col: str,
    feature_columns: list[str],
    return_col: str | None,
    test_returns: np.ndarray | None,
    horizon_days: int,
) -> None:
    y_test = test_df[target_col].to_numpy(dtype=float)
    for margin in ABSTENTION_MARGINS:
        abstention = {"enabled": True, "policy": "positive_only_probability_margin", "margin": margin, "abstained_action": "cash_no_signal"}
        spec = {
            "family": "abstention_linear",
            "candidate_lane": "decision",
            "feature_subset_policy": "exclude_raw_price",
            "training_window_policy": "full",
            "sample_weight_policy": "tail_safe",
            "l2": 0.003,
            "lr": 0.06,
            "threshold": 0.60,
            "epochs": 650,
            "calibration_policy": "platt_if_brier_improves",
            "abstention": abstention,
        }
        artifact, calibration, calibration_split = _train_calibrated_linear_recipe(train_df, target_col, feature_columns, return_col, spec, horizon_days=horizon_days)
        model_version = f"challenger-abstention-margin-{int(round(margin * 1000)):03d}-v1"
        artifact.version = model_version
        model_path = output_dir / f"{model_version}.json"
        lineage = _candidate_lineage(
            model_version=model_version,
            model_type="abstention_linear",
            spec=spec,
            feature_columns=artifact.feature_columns,
            decision_threshold=artifact.decision_threshold,
            sample_weight_policy="tail_safe",
            calibration={"method": calibration["method"], "slope": artifact.calibration_slope, "intercept": artifact.calibration_intercept},
            abstention=abstention,
            extra_deployable_config={"candidate_lane": "decision", "effective_positive_threshold": round(artifact.decision_threshold + margin, 6)},
        )
        _write_artifact(model_path, {"model_type": "abstention_linear", "candidate_lane": "decision", **artifact.to_dict(), "abstention": abstention, "calibration": calibration, "calibration_split": calibration_split, "training_spec": spec, "lineage": lineage})
        probabilities = predict_proba(artifact, test_df[artifact.feature_columns].to_numpy(dtype=float))
        predictions, abstained = _abstention_predictions(probabilities, artifact.decision_threshold, abstention)
        metrics = summarize_binary_predictions(y_test, predictions)
        metrics.update(_ranking_metrics(probabilities, y_test, test_returns))
        metrics.update(_prediction_profile(predictions, test_df, return_col))
        metrics["abstained_rows"] = int(abstained.sum())
        metrics["abstention_rate"] = round(float(abstained.mean()), 6) if len(abstained) else 0.0
        challengers.append({"model_version": model_version, "model_type": "abstention_linear", "candidate_lane": "decision", "model_path": str(model_path), "metrics": metrics, "spec": spec, "lineage": lineage})


def _add_two_stage_risk_challengers(
    challengers: list[dict[str, Any]],
    *,
    output_dir: Path,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    target_col: str,
    feature_columns: list[str],
    return_col: str | None,
    test_returns: np.ndarray | None,
    horizon_days: int,
) -> None:
    y_test = test_df[target_col].to_numpy(dtype=float)
    for risk_threshold in RISK_FILTER_THRESHOLDS:
        feature_policy = "exclude_raw_price" if risk_threshold <= 0.20 else "all"
        spec = {
            "family": "two_stage_risk_filter",
            "feature_subset_policy": feature_policy,
            "training_window_policy": "full",
            "sample_weight_policy": "tail_safe_decision_and_10x_big_loss_risk",
            "decision_l2": 0.002,
            "risk_l2": 0.005,
            "decision_threshold": 0.60,
            "risk_threshold": risk_threshold,
            "calibration_policy": "platt_if_brier_improves_per_stage",
            "abstention": {"enabled": True, "behavior": "reject_when_big_loss_probability_exceeds_limit", "margin": 0.0},
        }
        decision_model, risk_model, decision_calibration, risk_calibration, calibration_split = _train_two_stage_risk_recipe(
            train_df,
            target_col,
            feature_columns,
            return_col,
            spec,
            horizon_days=horizon_days,
        )
        model_version = f"challenger-two-stage-risk-{int(risk_threshold * 100):02d}-v1"
        decision_model.version = f"{model_version}-decision"
        risk_model.version = f"{model_version}-risk"
        model_path = output_dir / f"{model_version}.json"
        calibration_config = {
            "method": "per_stage",
            "decision": {"method": decision_calibration["method"], "slope": decision_model.calibration_slope, "intercept": decision_model.calibration_intercept},
            "risk": {"method": risk_calibration["method"], "slope": risk_model.calibration_slope, "intercept": risk_model.calibration_intercept},
        }
        lineage = _candidate_lineage(
            model_version=model_version,
            model_type="two_stage_risk_filter",
            spec=spec,
            feature_columns=decision_model.feature_columns,
            decision_threshold=float(spec["decision_threshold"]),
            sample_weight_policy=str(spec["sample_weight_policy"]),
            calibration=calibration_config,
            abstention=spec["abstention"],
            extra_deployable_config={
                "risk_filter": {
                    "risk_threshold": risk_threshold,
                    "positive_class": "big_loss",
                    "combination_rule": "decision_positive_and_risk_at_or_below_threshold",
                }
            },
        )
        payload = {
            "version": model_version,
            "model_type": "two_stage_risk_filter",
            "feature_columns": decision_model.feature_columns,
            "decision_threshold": spec["decision_threshold"],
            "risk_threshold": risk_threshold,
            "decision_model": decision_model.to_dict(),
            "risk_model": risk_model.to_dict(),
            "calibration": calibration_config,
            "calibration_split": calibration_split,
            "training_spec": spec,
            "lineage": lineage,
        }
        _write_artifact(model_path, payload)
        scores, risk_scores, preds = _two_stage_scores(decision_model, risk_model, test_df, risk_threshold)
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(scores, y_test, test_returns))
        metrics.update(_prediction_profile(preds, test_df, return_col))
        metrics["avg_risk_probability"] = round(float(risk_scores.mean()), 6) if len(risk_scores) else 0.0
        metrics["risk_rejections"] = int(((scores >= decision_model.decision_threshold) & (risk_scores > risk_threshold)).sum())
        challengers.append({"model_version": model_version, "model_type": "two_stage_risk_filter", "model_path": str(model_path), "metrics": metrics, "spec": spec, "lineage": lineage})


def _add_hard_example_challengers(
    challengers: list[dict[str, Any]],
    *,
    output_dir: Path,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    target_col: str,
    feature_columns: list[str],
    return_col: str | None,
    test_returns: np.ndarray | None,
) -> None:
    y_test = test_df[target_col].to_numpy(dtype=float)
    for feature_policy in ("all", "exclude_raw_price"):
        spec = {
            "family": "bounded_hard_example",
            "candidate_lane": "decision",
            "feature_subset_policy": feature_policy,
            "training_window_policy": "full",
            "sample_weight_policy": "artifact_scored_bounded_tail_mistakes",
            "seed_l2": 0.002,
            "l2": 0.004,
            "threshold": 0.60,
            "max_hard_fraction": HARD_EXAMPLE_MAX_FRACTION,
            "hard_example_weight": HARD_EXAMPLE_WEIGHT,
            "max_sample_weight": HARD_EXAMPLE_MAX_WEIGHT,
            "abstention": {"enabled": False, "margin": 0.0},
        }
        artifact, mining, parent_id = _train_hard_example_recipe(train_df, target_col, feature_columns, return_col, spec)
        train_scores = predict_proba(artifact, train_df[artifact.feature_columns].to_numpy(dtype=float))
        train_predictions = (train_scores >= artifact.decision_threshold).astype(int)
        train_buckets = _return_bucket_series(train_df, return_col).to_numpy()
        remaining_mistakes = int((((train_buckets == "big_loss") & (train_predictions == 1)) | ((train_buckets == "big_gain") & (train_predictions == 0))).sum())
        mining["repeated_mistakes_after_training"] = remaining_mistakes
        mining["repeated_mistake_delta"] = remaining_mistakes - int(mining["candidate_mistake_rows"])
        mining["repeated_mistakes_declined"] = remaining_mistakes < int(mining["candidate_mistake_rows"])
        suffix = "all" if feature_policy == "all" else "no-price"
        model_version = f"challenger-hard-example-{suffix}-v2"
        artifact.version = model_version
        model_path = output_dir / f"{model_version}.json"
        lineage = _candidate_lineage(
            model_version=model_version,
            model_type="hard_example_linear",
            spec=spec,
            feature_columns=artifact.feature_columns,
            decision_threshold=artifact.decision_threshold,
            sample_weight_policy=str(spec["sample_weight_policy"]),
            abstention=spec["abstention"],
            extra_deployable_config={"candidate_lane": "decision", "hard_example_mining": mining},
            generation=2,
            parent_lineage_ids=[parent_id],
        )
        _write_artifact(model_path, {"model_type": "hard_example_linear", "candidate_lane": "decision", **artifact.to_dict(), "hard_example_mining": mining, "training_spec": spec, "lineage": lineage})
        scores = predict_proba(artifact, test_df[artifact.feature_columns].to_numpy(dtype=float))
        preds = (scores >= artifact.decision_threshold).astype(int)
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(scores, y_test, test_returns))
        metrics.update(_prediction_profile(preds, test_df, return_col))
        metrics["hard_example_mining"] = mining
        challengers.append({"model_version": model_version, "model_type": "hard_example_linear", "candidate_lane": "decision", "model_path": str(model_path), "metrics": metrics, "spec": spec, "lineage": lineage})


def _add_ranking_lane_challengers(
    challengers: list[dict[str, Any]],
    *,
    output_dir: Path,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    y_test: np.ndarray,
    feature_columns: list[str],
    return_col: str | None,
    test_returns: np.ndarray | None,
) -> None:
    for window_policy in ("full", "recent_half"):
        spec = {
            "family": "ranking_top5_linear",
            "candidate_lane": "ranking",
            "feature_subset_policy": "exclude_raw_price",
            "training_window_policy": window_policy,
            "sample_weight_policy": "ranking_top5_tail_aware",
            "target_policy": "daily_top5",
            "l2": 0.004,
            "threshold": 0.60,
            "abstention": {"enabled": False, "margin": 0.0},
        }
        artifact = _train_ranking_lane_recipe(train_df, feature_columns, return_col, spec)
        model_version = f"challenger-ranking-lane-{window_policy.replace('_', '-')}-v1"
        artifact.version = model_version
        model_path = output_dir / f"{model_version}.json"
        lineage = _candidate_lineage(
            model_version=model_version,
            model_type="ranking_lane_linear",
            spec=spec,
            feature_columns=artifact.feature_columns,
            decision_threshold=artifact.decision_threshold,
            sample_weight_policy=str(spec["sample_weight_policy"]),
            abstention=spec["abstention"],
            extra_deployable_config={"candidate_lane": "ranking", "promotion_scope": "ranking_only_never_main_decision_replacement"},
        )
        _write_artifact(model_path, {"model_type": "ranking_lane_linear", "candidate_lane": "ranking", **artifact.to_dict(), "training_spec": spec, "lineage": lineage})
        scores = predict_proba(artifact, test_df[artifact.feature_columns].to_numpy(dtype=float))
        preds = (scores >= artifact.decision_threshold).astype(int)
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(scores, y_test, test_returns))
        metrics.update(_prediction_profile(preds, test_df, return_col))
        challengers.append({"model_version": model_version, "model_type": "ranking_lane_linear", "candidate_lane": "ranking", "model_path": str(model_path), "metrics": metrics, "spec": spec, "lineage": lineage})


def _stump_predictions(values: np.ndarray, threshold: float, direction: str) -> np.ndarray:
    if direction == "gte_positive":
        return (values >= threshold).astype(int)
    return (values < threshold).astype(int)


def _tree_leaf_probability(labels: np.ndarray) -> float:
    return float((labels.sum() + 1.0) / (len(labels) + 2.0))


def _fit_shallow_tree(
    frame: pd.DataFrame,
    labels: np.ndarray,
    feature_columns: list[str],
    *,
    max_depth: int,
    min_leaf: int,
    depth: int = 0,
) -> dict[str, Any]:
    probability = _tree_leaf_probability(labels)
    if depth >= max_depth or len(labels) < (2 * min_leaf) or np.unique(labels).size < 2:
        return {"leaf": True, "probability": probability, "rows": int(len(labels))}
    best: tuple[float, str, float, np.ndarray] | None = None
    for feature in feature_columns:
        values = frame[feature].to_numpy(dtype=float)
        for threshold in np.unique(np.quantile(values, (0.25, 0.5, 0.75))):
            left = values < float(threshold)
            if int(left.sum()) < min_leaf or int((~left).sum()) < min_leaf:
                continue
            impurity = 0.0
            for mask in (left, ~left):
                rate = float(labels[mask].mean())
                impurity += int(mask.sum()) * (2.0 * rate * (1.0 - rate))
            candidate = (impurity, feature, float(threshold), left)
            if best is None or candidate[:3] < best[:3]:
                best = candidate
    if best is None:
        return {"leaf": True, "probability": probability, "rows": int(len(labels))}
    _, feature, threshold, left = best
    return {
        "leaf": False,
        "probability": probability,
        "rows": int(len(labels)),
        "feature": feature,
        "threshold": threshold,
        "left": _fit_shallow_tree(frame.loc[left], labels[left], feature_columns, max_depth=max_depth, min_leaf=min_leaf, depth=depth + 1),
        "right": _fit_shallow_tree(frame.loc[~left], labels[~left], feature_columns, max_depth=max_depth, min_leaf=min_leaf, depth=depth + 1),
    }


def _shallow_tree_scores(tree: dict[str, Any], frame: pd.DataFrame) -> np.ndarray:
    scores = np.empty(len(frame), dtype=float)
    for output_index, (_, row) in enumerate(frame.iterrows()):
        node = tree
        while not bool(node.get("leaf")):
            node = node["left"] if float(row[str(node["feature"])]) < float(node["threshold"]) else node["right"]
        scores[output_index] = float(node["probability"])
    return scores


def _add_shallow_tree_challengers(
    challengers: list[dict[str, Any]],
    *,
    output_dir: Path,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    y_train: np.ndarray,
    y_test: np.ndarray,
    feature_columns: list[str],
    test_returns: np.ndarray | None,
    return_col: str | None,
) -> None:
    min_leaf = min(SHALLOW_TREE_MIN_LEAF, max(2, len(train_df) // 10))
    for max_depth in SHALLOW_TREE_DEPTHS:
        spec = {
            "family": "shallow_decision_tree",
            "max_depth": max_depth,
            "min_leaf": min_leaf,
            "threshold": 0.60,
            "feature_subset_policy": "all",
            "sample_weight_policy": "uniform",
            "calibration": {"method": "identity", "slope": 1.0, "intercept": 0.0},
            "abstention": {"enabled": False, "margin": 0.0},
        }
        tree = _fit_shallow_tree(train_df, y_train, feature_columns, max_depth=max_depth, min_leaf=min_leaf)
        model_version = f"challenger-shallow-tree-depth{max_depth}-v1"
        model_path = output_dir / f"{model_version}.json"
        lineage = _candidate_lineage(
            model_version=model_version,
            model_type="shallow_decision_tree",
            spec=spec,
            feature_columns=feature_columns,
            decision_threshold=float(spec["threshold"]),
            calibration=spec["calibration"],
            abstention=spec["abstention"],
        )
        _write_artifact(model_path, {
            "version": model_version,
            "model_type": "shallow_decision_tree",
            "feature_columns": feature_columns,
            "decision_threshold": spec["threshold"],
            "tree": tree,
            "training_spec": spec,
            "lineage": lineage,
        })
        scores = _shallow_tree_scores(tree, test_df)
        preds = (scores >= float(spec["threshold"])).astype(int)
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(scores, y_test, test_returns))
        metrics.update(_prediction_profile(preds, test_df, return_col))
        challengers.append({"model_version": model_version, "model_type": "shallow_decision_tree", "model_path": str(model_path), "metrics": metrics, "spec": spec, "lineage": lineage})


def _add_stump_challengers(
    challengers: list[dict[str, Any]],
    *,
    output_dir: Path,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    y_train: np.ndarray,
    y_test: np.ndarray,
    feature_columns: list[str],
    test_returns: np.ndarray | None,
) -> None:
    candidates: list[dict[str, Any]] = []
    for feature in feature_columns:
        train_values = train_df[feature].to_numpy(dtype=float)
        threshold = float(np.median(train_values))
        for direction in ("gte_positive", "lt_positive"):
            train_preds = _stump_predictions(train_values, threshold, direction)
            train_metrics = summarize_binary_predictions(y_train, train_preds)
            candidates.append({"feature": feature, "threshold": threshold, "direction": direction, "train_accuracy": train_metrics["accuracy"]})

    for rank, spec in enumerate(sorted(candidates, key=lambda item: item["train_accuracy"], reverse=True)[:MAX_STUMP_CHALLENGERS], start=1):
        model_version = f"challenger-stump-{rank:02d}-{spec['feature'].replace('feature_', '').replace('_', '-')}-v1"
        model_path = output_dir / f"{model_version}.json"
        test_values = test_df[spec["feature"]].to_numpy(dtype=float)
        preds = _stump_predictions(test_values, float(spec["threshold"]), str(spec["direction"]))
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(preds.astype(float), y_test, test_returns))
        lineage = _candidate_lineage(model_version=model_version, model_type="decision_stump", spec=spec, feature_columns=[str(spec["feature"])], decision_threshold=float(spec["threshold"]))
        payload = {"version": model_version, "model_type": "decision_stump", "feature": spec["feature"], "threshold": spec["threshold"], "direction": spec["direction"], "training_spec": spec, "lineage": lineage}
        _write_artifact(model_path, payload)
        challengers.append({"model_version": model_version, "model_type": "decision_stump", "model_path": str(model_path), "metrics": metrics, "spec": spec, "lineage": lineage})

def _specialized_sample_weight(train_df: pd.DataFrame, return_col: str | None, family: str) -> np.ndarray:
    buckets = _return_bucket_series(train_df, return_col)
    weights = np.ones(len(train_df), dtype=float)
    if family == "big_loss_avoider":
        weights[buckets.to_numpy() == "big_loss"] = 6.0
        weights[buckets.to_numpy() == "loss"] = 2.0
    elif family == "big_gain_hunter":
        weights[buckets.to_numpy() == "big_gain"] = 6.0
        weights[buckets.to_numpy() == "gain"] = 2.0
    elif family == "recent_window_model":
        ramp = np.linspace(0.5, 2.5, num=len(train_df)) if len(train_df) else np.array([], dtype=float)
        weights = ramp.astype(float)
    elif family == "ranking_top5_model":
        weights[buckets.to_numpy() == "big_gain"] = 4.0
        weights[buckets.to_numpy() == "big_loss"] = 3.0
    return weights


def _add_specialized_challengers(
    challengers: list[dict[str, Any]],
    *,
    output_dir: Path,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    y_train: np.ndarray,
    y_test: np.ndarray,
    feature_columns: list[str],
    return_col: str | None,
    test_returns: np.ndarray | None,
) -> None:
    for family in SPECIALIZED_CHALLENGER_FAMILIES:
        candidate_lane = "ranking" if family == "ranking_top5_model" else "decision"
        threshold = 0.60 if family in {"big_loss_avoider", "ranking_top5_model"} else 0.55
        spec = {
            "family": family,
            "lr": 0.06,
            "l2": 0.002,
            "threshold": threshold,
            "epochs": 650,
            "sample_weight_policy": family,
            "target_policy": "daily_top5" if family == "ranking_top5_model" else "configured_target",
            "training_window_policy": "recent_half" if family == "recent_window_model" else "full_window",
        }
        artifact = _train_logistic_recipe(train_df, y_train, feature_columns, return_col, spec)
        artifact.version = f"challenger-{family.replace('_', '-')}-v1"
        artifact.feature_columns = list(feature_columns)
        model_path = output_dir / f"{artifact.version}.json"
        lineage = _candidate_lineage(
            model_version=artifact.version,
            model_type="logistic_regression",
            spec=spec,
            feature_columns=feature_columns,
            decision_threshold=artifact.decision_threshold,
            sample_weight_policy=family,
            extra_deployable_config={"candidate_lane": candidate_lane},
        )
        _write_artifact(model_path, {"model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, **artifact.to_dict(), "training_spec": spec, "lineage": lineage})
        probs = predict_proba(artifact, test_df[feature_columns].to_numpy(dtype=float))
        preds = (probs >= artifact.decision_threshold).astype(int)
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(probs, y_test, test_returns))
        metrics["specialized_family"] = family
        challengers.append({"model_version": artifact.version, "model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, "model_path": str(model_path), "metrics": metrics, "spec": spec, "lineage": lineage})


def _ranking_top5_labels(train_df: pd.DataFrame, return_col: str | None) -> np.ndarray:
    if not return_col or return_col not in train_df.columns:
        return np.zeros(len(train_df), dtype=float)
    work = train_df.copy()
    work["_return"] = pd.to_numeric(work[return_col], errors="coerce").fillna(0.0)
    work["_event_date"] = _event_date_values(work)
    labels = pd.Series(0.0, index=work.index)
    for _, group in work.groupby("_event_date"):
        top_n = min(5, len(group))
        if top_n <= 0:
            continue
        labels.loc[group.sort_values("_return", ascending=False).head(top_n).index] = 1.0
    return labels.loc[train_df.index].to_numpy(dtype=float)


def _specialized_sample_weight(train_df: pd.DataFrame, return_col: str | None, family: str) -> np.ndarray:
    buckets = _return_bucket_series(train_df, return_col)
    weights = np.ones(len(train_df), dtype=float)
    if family == "big_loss_avoider":
        weights[buckets.to_numpy() == "big_loss"] = 6.0
        weights[buckets.to_numpy() == "loss"] = 2.0
    elif family == "big_gain_hunter":
        weights[buckets.to_numpy() == "big_gain"] = 6.0
        weights[buckets.to_numpy() == "gain"] = 2.0
    elif family == "recent_window_model":
        ramp = np.linspace(0.5, 2.5, num=len(train_df)) if len(train_df) else np.array([], dtype=float)
        weights = ramp.astype(float)
    elif family == "ranking_top5_model":
        weights[buckets.to_numpy() == "big_gain"] = 4.0
        weights[buckets.to_numpy() == "big_loss"] = 3.0
    return weights


def _add_specialized_challengers(
    challengers: list[dict[str, Any]],
    *,
    output_dir: Path,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    y_train: np.ndarray,
    y_test: np.ndarray,
    feature_columns: list[str],
    return_col: str | None,
    test_returns: np.ndarray | None,
) -> None:
    for family in SPECIALIZED_CHALLENGER_FAMILIES:
        candidate_lane = "ranking" if family == "ranking_top5_model" else "decision"
        threshold = 0.60 if family in {"big_loss_avoider", "ranking_top5_model"} else 0.55
        spec = {
            "family": family,
            "lr": 0.06,
            "l2": 0.002,
            "threshold": threshold,
            "epochs": 650,
            "sample_weight_policy": family,
            "target_policy": "daily_top5" if family == "ranking_top5_model" else "configured_target",
            "training_window_policy": "recent_half" if family == "recent_window_model" else "full_window",
        }
        artifact = _train_logistic_recipe(train_df, y_train, feature_columns, return_col, spec)
        artifact.version = f"challenger-{family.replace('_', '-')}-v1"
        artifact.feature_columns = list(feature_columns)
        model_path = output_dir / f"{artifact.version}.json"
        lineage = _candidate_lineage(
            model_version=artifact.version,
            model_type="logistic_regression",
            spec=spec,
            feature_columns=feature_columns,
            decision_threshold=artifact.decision_threshold,
            sample_weight_policy=family,
            extra_deployable_config={"candidate_lane": candidate_lane},
        )
        _write_artifact(model_path, {"model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, **artifact.to_dict(), "training_spec": spec, "lineage": lineage})
        probs = predict_proba(artifact, test_df[feature_columns].to_numpy(dtype=float))
        preds = (probs >= artifact.decision_threshold).astype(int)
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(probs, y_test, test_returns))
        metrics["specialized_family"] = family
        challengers.append({"model_version": artifact.version, "model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, "model_path": str(model_path), "metrics": metrics, "spec": spec, "lineage": lineage})


def _ranking_top5_labels(train_df: pd.DataFrame, return_col: str | None) -> np.ndarray:
    if not return_col or return_col not in train_df.columns:
        return np.zeros(len(train_df), dtype=float)
    work = train_df.copy()
    work["_return"] = pd.to_numeric(work[return_col], errors="coerce").fillna(0.0)
    work["_event_date"] = _event_date_values(work)
    labels = pd.Series(0.0, index=work.index)
    for _, group in work.groupby("_event_date"):
        top_n = min(5, len(group))
        if top_n <= 0:
            continue
        labels.loc[group.sort_values("_return", ascending=False).head(top_n).index] = 1.0
    return labels.loc[train_df.index].to_numpy(dtype=float)


def _specialized_sample_weight(train_df: pd.DataFrame, return_col: str | None, family: str) -> np.ndarray:
    buckets = _return_bucket_series(train_df, return_col)
    weights = np.ones(len(train_df), dtype=float)
    if family == "big_loss_avoider":
        weights[buckets.to_numpy() == "big_loss"] = 6.0
        weights[buckets.to_numpy() == "loss"] = 2.0
    elif family == "big_gain_hunter":
        weights[buckets.to_numpy() == "big_gain"] = 6.0
        weights[buckets.to_numpy() == "gain"] = 2.0
    elif family == "recent_window_model":
        ramp = np.linspace(0.5, 2.5, num=len(train_df)) if len(train_df) else np.array([], dtype=float)
        weights = ramp.astype(float)
    elif family == "ranking_top5_model":
        weights[buckets.to_numpy() == "big_gain"] = 4.0
        weights[buckets.to_numpy() == "big_loss"] = 3.0
    return weights


def _add_specialized_challengers(
    challengers: list[dict[str, Any]],
    *,
    output_dir: Path,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    y_train: np.ndarray,
    y_test: np.ndarray,
    feature_columns: list[str],
    return_col: str | None,
    test_returns: np.ndarray | None,
) -> None:
    for family in SPECIALIZED_CHALLENGER_FAMILIES:
        candidate_lane = "ranking" if family == "ranking_top5_model" else "decision"
        threshold = 0.60 if family in {"big_loss_avoider", "ranking_top5_model"} else 0.55
        spec = {
            "family": family,
            "lr": 0.06,
            "l2": 0.002,
            "threshold": threshold,
            "epochs": 650,
            "sample_weight_policy": family,
            "target_policy": "daily_top5" if family == "ranking_top5_model" else "configured_target",
            "training_window_policy": "recent_half" if family == "recent_window_model" else "full_window",
        }
        artifact = _train_logistic_recipe(train_df, y_train, feature_columns, return_col, spec)
        artifact.version = f"challenger-{family.replace('_', '-')}-v1"
        artifact.feature_columns = list(feature_columns)
        model_path = output_dir / f"{artifact.version}.json"
        lineage = _candidate_lineage(
            model_version=artifact.version,
            model_type="logistic_regression",
            spec=spec,
            feature_columns=feature_columns,
            decision_threshold=artifact.decision_threshold,
            sample_weight_policy=family,
            extra_deployable_config={"candidate_lane": candidate_lane},
        )
        _write_artifact(model_path, {"model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, **artifact.to_dict(), "training_spec": spec, "lineage": lineage})
        probs = predict_proba(artifact, test_df[feature_columns].to_numpy(dtype=float))
        preds = (probs >= artifact.decision_threshold).astype(int)
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(probs, y_test, test_returns))
        metrics["specialized_family"] = family
        challengers.append({"model_version": artifact.version, "model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, "model_path": str(model_path), "metrics": metrics, "spec": spec, "lineage": lineage})


def _ranking_top5_labels(train_df: pd.DataFrame, return_col: str | None) -> np.ndarray:
    if not return_col or return_col not in train_df.columns:
        return np.zeros(len(train_df), dtype=float)
    work = train_df.copy()
    work["_return"] = pd.to_numeric(work[return_col], errors="coerce").fillna(0.0)
    work["_event_date"] = _event_date_values(work)
    labels = pd.Series(0.0, index=work.index)
    for _, group in work.groupby("_event_date"):
        top_n = min(5, len(group))
        if top_n <= 0:
            continue
        labels.loc[group.sort_values("_return", ascending=False).head(top_n).index] = 1.0
    return labels.loc[train_df.index].to_numpy(dtype=float)


def _specialized_sample_weight(train_df: pd.DataFrame, return_col: str | None, family: str) -> np.ndarray:
    buckets = _return_bucket_series(train_df, return_col)
    weights = np.ones(len(train_df), dtype=float)
    if family == "big_loss_avoider":
        weights[buckets.to_numpy() == "big_loss"] = 6.0
        weights[buckets.to_numpy() == "loss"] = 2.0
    elif family == "big_gain_hunter":
        weights[buckets.to_numpy() == "big_gain"] = 6.0
        weights[buckets.to_numpy() == "gain"] = 2.0
    elif family == "recent_window_model":
        ramp = np.linspace(0.5, 2.5, num=len(train_df)) if len(train_df) else np.array([], dtype=float)
        weights = ramp.astype(float)
    elif family == "ranking_top5_model":
        weights[buckets.to_numpy() == "big_gain"] = 4.0
        weights[buckets.to_numpy() == "big_loss"] = 3.0
    return weights


def _add_specialized_challengers(
    challengers: list[dict[str, Any]],
    *,
    output_dir: Path,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    y_train: np.ndarray,
    y_test: np.ndarray,
    feature_columns: list[str],
    return_col: str | None,
    test_returns: np.ndarray | None,
) -> None:
    for family in SPECIALIZED_CHALLENGER_FAMILIES:
        candidate_lane = "ranking" if family == "ranking_top5_model" else "decision"
        threshold = 0.60 if family in {"big_loss_avoider", "ranking_top5_model"} else 0.55
        spec = {
            "family": family,
            "lr": 0.06,
            "l2": 0.002,
            "threshold": threshold,
            "epochs": 650,
            "sample_weight_policy": family,
            "target_policy": "daily_top5" if family == "ranking_top5_model" else "configured_target",
            "training_window_policy": "recent_half" if family == "recent_window_model" else "full_window",
        }
        artifact = _train_logistic_recipe(train_df, y_train, feature_columns, return_col, spec)
        artifact.version = f"challenger-{family.replace('_', '-')}-v1"
        artifact.feature_columns = list(feature_columns)
        model_path = output_dir / f"{artifact.version}.json"
        lineage = _candidate_lineage(
            model_version=artifact.version,
            model_type="logistic_regression",
            spec=spec,
            feature_columns=feature_columns,
            decision_threshold=artifact.decision_threshold,
            sample_weight_policy=family,
            extra_deployable_config={"candidate_lane": candidate_lane},
        )
        _write_artifact(model_path, {"model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, **artifact.to_dict(), "training_spec": spec, "lineage": lineage})
        probs = predict_proba(artifact, test_df[feature_columns].to_numpy(dtype=float))
        preds = (probs >= artifact.decision_threshold).astype(int)
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(probs, y_test, test_returns))
        metrics["specialized_family"] = family
        challengers.append({"model_version": artifact.version, "model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, "model_path": str(model_path), "metrics": metrics, "spec": spec, "lineage": lineage})


def _ranking_top5_labels(train_df: pd.DataFrame, return_col: str | None) -> np.ndarray:
    if not return_col or return_col not in train_df.columns:
        return np.zeros(len(train_df), dtype=float)
    work = train_df.copy()
    work["_return"] = pd.to_numeric(work[return_col], errors="coerce").fillna(0.0)
    work["_event_date"] = _event_date_values(work)
    labels = pd.Series(0.0, index=work.index)
    for _, group in work.groupby("_event_date"):
        top_n = min(5, len(group))
        if top_n <= 0:
            continue
        labels.loc[group.sort_values("_return", ascending=False).head(top_n).index] = 1.0
    return labels.loc[train_df.index].to_numpy(dtype=float)


def _specialized_sample_weight(train_df: pd.DataFrame, return_col: str | None, family: str) -> np.ndarray:
    buckets = _return_bucket_series(train_df, return_col)
    weights = np.ones(len(train_df), dtype=float)
    if family == "big_loss_avoider":
        weights[buckets.to_numpy() == "big_loss"] = 6.0
        weights[buckets.to_numpy() == "loss"] = 2.0
    elif family == "big_gain_hunter":
        weights[buckets.to_numpy() == "big_gain"] = 6.0
        weights[buckets.to_numpy() == "gain"] = 2.0
    elif family == "recent_window_model":
        ramp = np.linspace(0.5, 2.5, num=len(train_df)) if len(train_df) else np.array([], dtype=float)
        weights = ramp.astype(float)
    elif family == "ranking_top5_model":
        weights[buckets.to_numpy() == "big_gain"] = 4.0
        weights[buckets.to_numpy() == "big_loss"] = 3.0
    return weights


def _add_specialized_challengers(
    challengers: list[dict[str, Any]],
    *,
    output_dir: Path,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    y_train: np.ndarray,
    y_test: np.ndarray,
    feature_columns: list[str],
    return_col: str | None,
    test_returns: np.ndarray | None,
) -> None:
    for family in SPECIALIZED_CHALLENGER_FAMILIES:
        candidate_lane = "ranking" if family == "ranking_top5_model" else "decision"
        threshold = 0.60 if family in {"big_loss_avoider", "ranking_top5_model"} else 0.55
        spec = {
            "family": family,
            "lr": 0.06,
            "l2": 0.002,
            "threshold": threshold,
            "epochs": 650,
            "sample_weight_policy": family,
            "target_policy": "daily_top5" if family == "ranking_top5_model" else "configured_target",
            "training_window_policy": "recent_half" if family == "recent_window_model" else "full_window",
        }
        artifact = _train_logistic_recipe(train_df, y_train, feature_columns, return_col, spec)
        artifact.version = f"challenger-{family.replace('_', '-')}-v1"
        artifact.feature_columns = list(feature_columns)
        model_path = output_dir / f"{artifact.version}.json"
        lineage = _candidate_lineage(
            model_version=artifact.version,
            model_type="logistic_regression",
            spec=spec,
            feature_columns=feature_columns,
            decision_threshold=artifact.decision_threshold,
            sample_weight_policy=family,
            extra_deployable_config={"candidate_lane": candidate_lane},
        )
        _write_artifact(model_path, {"model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, **artifact.to_dict(), "training_spec": spec, "lineage": lineage})
        probs = predict_proba(artifact, test_df[feature_columns].to_numpy(dtype=float))
        preds = (probs >= artifact.decision_threshold).astype(int)
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(probs, y_test, test_returns))
        metrics["specialized_family"] = family
        challengers.append({"model_version": artifact.version, "model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, "model_path": str(model_path), "metrics": metrics, "spec": spec, "lineage": lineage})


def _ranking_top5_labels(train_df: pd.DataFrame, return_col: str | None) -> np.ndarray:
    if not return_col or return_col not in train_df.columns:
        return np.zeros(len(train_df), dtype=float)
    work = train_df.copy()
    work["_return"] = pd.to_numeric(work[return_col], errors="coerce").fillna(0.0)
    work["_event_date"] = _event_date_values(work)
    labels = pd.Series(0.0, index=work.index)
    for _, group in work.groupby("_event_date"):
        top_n = min(5, len(group))
        if top_n <= 0:
            continue
        labels.loc[group.sort_values("_return", ascending=False).head(top_n).index] = 1.0
    return labels.loc[train_df.index].to_numpy(dtype=float)


def _specialized_sample_weight(train_df: pd.DataFrame, return_col: str | None, family: str) -> np.ndarray:
    buckets = _return_bucket_series(train_df, return_col)
    weights = np.ones(len(train_df), dtype=float)
    if family == "big_loss_avoider":
        weights[buckets.to_numpy() == "big_loss"] = 6.0
        weights[buckets.to_numpy() == "loss"] = 2.0
    elif family == "big_gain_hunter":
        weights[buckets.to_numpy() == "big_gain"] = 6.0
        weights[buckets.to_numpy() == "gain"] = 2.0
    elif family == "recent_window_model":
        ramp = np.linspace(0.5, 2.5, num=len(train_df)) if len(train_df) else np.array([], dtype=float)
        weights = ramp.astype(float)
    elif family == "ranking_top5_model":
        weights[buckets.to_numpy() == "big_gain"] = 4.0
        weights[buckets.to_numpy() == "big_loss"] = 3.0
    return weights


def _add_specialized_challengers(
    challengers: list[dict[str, Any]],
    *,
    output_dir: Path,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    y_train: np.ndarray,
    y_test: np.ndarray,
    feature_columns: list[str],
    return_col: str | None,
    test_returns: np.ndarray | None,
) -> None:
    for family in SPECIALIZED_CHALLENGER_FAMILIES:
        candidate_lane = "ranking" if family == "ranking_top5_model" else "decision"
        threshold = 0.60 if family in {"big_loss_avoider", "ranking_top5_model"} else 0.55
        spec = {
            "family": family,
            "lr": 0.06,
            "l2": 0.002,
            "threshold": threshold,
            "epochs": 650,
            "sample_weight_policy": family,
            "target_policy": "daily_top5" if family == "ranking_top5_model" else "configured_target",
            "training_window_policy": "recent_half" if family == "recent_window_model" else "full_window",
        }
        artifact = _train_logistic_recipe(train_df, y_train, feature_columns, return_col, spec)
        artifact.version = f"challenger-{family.replace('_', '-')}-v1"
        artifact.feature_columns = list(feature_columns)
        model_path = output_dir / f"{artifact.version}.json"
        lineage = _candidate_lineage(
            model_version=artifact.version,
            model_type="logistic_regression",
            spec=spec,
            feature_columns=feature_columns,
            decision_threshold=artifact.decision_threshold,
            sample_weight_policy=family,
            extra_deployable_config={"candidate_lane": candidate_lane},
        )
        _write_artifact(model_path, {"model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, **artifact.to_dict(), "training_spec": spec, "lineage": lineage})
        probs = predict_proba(artifact, test_df[feature_columns].to_numpy(dtype=float))
        preds = (probs >= artifact.decision_threshold).astype(int)
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(probs, y_test, test_returns))
        metrics["specialized_family"] = family
        challengers.append({"model_version": artifact.version, "model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, "model_path": str(model_path), "metrics": metrics, "spec": spec, "lineage": lineage})


def _ranking_top5_labels(train_df: pd.DataFrame, return_col: str | None) -> np.ndarray:
    if not return_col or return_col not in train_df.columns:
        return np.zeros(len(train_df), dtype=float)
    work = train_df.copy()
    work["_return"] = pd.to_numeric(work[return_col], errors="coerce").fillna(0.0)
    work["_event_date"] = _event_date_values(work)
    labels = pd.Series(0.0, index=work.index)
    for _, group in work.groupby("_event_date"):
        top_n = min(5, len(group))
        if top_n <= 0:
            continue
        labels.loc[group.sort_values("_return", ascending=False).head(top_n).index] = 1.0
    return labels.loc[train_df.index].to_numpy(dtype=float)


def _specialized_sample_weight(train_df: pd.DataFrame, return_col: str | None, family: str) -> np.ndarray:
    buckets = _return_bucket_series(train_df, return_col)
    weights = np.ones(len(train_df), dtype=float)
    if family == "big_loss_avoider":
        weights[buckets.to_numpy() == "big_loss"] = 6.0
        weights[buckets.to_numpy() == "loss"] = 2.0
    elif family == "big_gain_hunter":
        weights[buckets.to_numpy() == "big_gain"] = 6.0
        weights[buckets.to_numpy() == "gain"] = 2.0
    elif family == "recent_window_model":
        ramp = np.linspace(0.5, 2.5, num=len(train_df)) if len(train_df) else np.array([], dtype=float)
        weights = ramp.astype(float)
    elif family == "ranking_top5_model":
        weights[buckets.to_numpy() == "big_gain"] = 4.0
        weights[buckets.to_numpy() == "big_loss"] = 3.0
    return weights


def _add_specialized_challengers(
    challengers: list[dict[str, Any]],
    *,
    output_dir: Path,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    y_train: np.ndarray,
    y_test: np.ndarray,
    feature_columns: list[str],
    return_col: str | None,
    test_returns: np.ndarray | None,
) -> None:
    for family in SPECIALIZED_CHALLENGER_FAMILIES:
        candidate_lane = "ranking" if family == "ranking_top5_model" else "decision"
        threshold = 0.60 if family in {"big_loss_avoider", "ranking_top5_model"} else 0.55
        spec = {
            "family": family,
            "lr": 0.06,
            "l2": 0.002,
            "threshold": threshold,
            "epochs": 650,
            "sample_weight_policy": family,
            "target_policy": "daily_top5" if family == "ranking_top5_model" else "configured_target",
            "training_window_policy": "recent_half" if family == "recent_window_model" else "full_window",
        }
        artifact = _train_logistic_recipe(train_df, y_train, feature_columns, return_col, spec)
        artifact.version = f"challenger-{family.replace('_', '-')}-v1"
        artifact.feature_columns = list(feature_columns)
        model_path = output_dir / f"{artifact.version}.json"
        lineage = _candidate_lineage(
            model_version=artifact.version,
            model_type="logistic_regression",
            spec=spec,
            feature_columns=feature_columns,
            decision_threshold=artifact.decision_threshold,
            sample_weight_policy=family,
            extra_deployable_config={"candidate_lane": candidate_lane},
        )
        _write_artifact(model_path, {"model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, **artifact.to_dict(), "training_spec": spec, "lineage": lineage})
        probs = predict_proba(artifact, test_df[feature_columns].to_numpy(dtype=float))
        preds = (probs >= artifact.decision_threshold).astype(int)
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(probs, y_test, test_returns))
        metrics["specialized_family"] = family
        challengers.append({"model_version": artifact.version, "model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, "model_path": str(model_path), "metrics": metrics, "spec": spec, "lineage": lineage})


def _ranking_top5_labels(train_df: pd.DataFrame, return_col: str | None) -> np.ndarray:
    if not return_col or return_col not in train_df.columns:
        return np.zeros(len(train_df), dtype=float)
    work = train_df.copy()
    work["_return"] = pd.to_numeric(work[return_col], errors="coerce").fillna(0.0)
    work["_event_date"] = _event_date_values(work)
    labels = pd.Series(0.0, index=work.index)
    for _, group in work.groupby("_event_date"):
        top_n = min(5, len(group))
        if top_n <= 0:
            continue
        labels.loc[group.sort_values("_return", ascending=False).head(top_n).index] = 1.0
    return labels.loc[train_df.index].to_numpy(dtype=float)


def _specialized_sample_weight(train_df: pd.DataFrame, return_col: str | None, family: str) -> np.ndarray:
    buckets = _return_bucket_series(train_df, return_col)
    weights = np.ones(len(train_df), dtype=float)
    if family == "big_loss_avoider":
        weights[buckets.to_numpy() == "big_loss"] = 6.0
        weights[buckets.to_numpy() == "loss"] = 2.0
    elif family == "big_gain_hunter":
        weights[buckets.to_numpy() == "big_gain"] = 6.0
        weights[buckets.to_numpy() == "gain"] = 2.0
    elif family == "recent_window_model":
        ramp = np.linspace(0.5, 2.5, num=len(train_df)) if len(train_df) else np.array([], dtype=float)
        weights = ramp.astype(float)
    elif family == "ranking_top5_model":
        weights[buckets.to_numpy() == "big_gain"] = 4.0
        weights[buckets.to_numpy() == "big_loss"] = 3.0
    return weights


def _add_specialized_challengers(
    challengers: list[dict[str, Any]],
    *,
    output_dir: Path,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    y_train: np.ndarray,
    y_test: np.ndarray,
    feature_columns: list[str],
    return_col: str | None,
    test_returns: np.ndarray | None,
) -> None:
    for family in SPECIALIZED_CHALLENGER_FAMILIES:
        candidate_lane = "ranking" if family == "ranking_top5_model" else "decision"
        threshold = 0.60 if family in {"big_loss_avoider", "ranking_top5_model"} else 0.55
        spec = {
            "family": family,
            "lr": 0.06,
            "l2": 0.002,
            "threshold": threshold,
            "epochs": 650,
            "sample_weight_policy": family,
            "target_policy": "daily_top5" if family == "ranking_top5_model" else "configured_target",
            "training_window_policy": "recent_half" if family == "recent_window_model" else "full_window",
        }
        artifact = _train_logistic_recipe(train_df, y_train, feature_columns, return_col, spec)
        artifact.version = f"challenger-{family.replace('_', '-')}-v1"
        artifact.feature_columns = list(feature_columns)
        model_path = output_dir / f"{artifact.version}.json"
        lineage = _candidate_lineage(
            model_version=artifact.version,
            model_type="logistic_regression",
            spec=spec,
            feature_columns=feature_columns,
            decision_threshold=artifact.decision_threshold,
            sample_weight_policy=family,
            extra_deployable_config={"candidate_lane": candidate_lane},
        )
        _write_artifact(model_path, {"model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, **artifact.to_dict(), "training_spec": spec, "lineage": lineage})
        probs = predict_proba(artifact, test_df[feature_columns].to_numpy(dtype=float))
        preds = (probs >= artifact.decision_threshold).astype(int)
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(probs, y_test, test_returns))
        metrics["specialized_family"] = family
        challengers.append({"model_version": artifact.version, "model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, "model_path": str(model_path), "metrics": metrics, "spec": spec, "lineage": lineage})


def _ranking_top5_labels(train_df: pd.DataFrame, return_col: str | None) -> np.ndarray:
    if not return_col or return_col not in train_df.columns:
        return np.zeros(len(train_df), dtype=float)
    work = train_df.copy()
    work["_return"] = pd.to_numeric(work[return_col], errors="coerce").fillna(0.0)
    work["_event_date"] = _event_date_values(work)
    labels = pd.Series(0.0, index=work.index)
    for _, group in work.groupby("_event_date"):
        top_n = min(5, len(group))
        if top_n <= 0:
            continue
        labels.loc[group.sort_values("_return", ascending=False).head(top_n).index] = 1.0
    return labels.loc[train_df.index].to_numpy(dtype=float)


def _specialized_sample_weight(train_df: pd.DataFrame, return_col: str | None, family: str) -> np.ndarray:
    buckets = _return_bucket_series(train_df, return_col)
    weights = np.ones(len(train_df), dtype=float)
    if family == "big_loss_avoider":
        weights[buckets.to_numpy() == "big_loss"] = 6.0
        weights[buckets.to_numpy() == "loss"] = 2.0
    elif family == "big_gain_hunter":
        weights[buckets.to_numpy() == "big_gain"] = 6.0
        weights[buckets.to_numpy() == "gain"] = 2.0
    elif family == "recent_window_model":
        ramp = np.linspace(0.5, 2.5, num=len(train_df)) if len(train_df) else np.array([], dtype=float)
        weights = ramp.astype(float)
    elif family == "ranking_top5_model":
        weights[buckets.to_numpy() == "big_gain"] = 4.0
        weights[buckets.to_numpy() == "big_loss"] = 3.0
    return weights


def _add_specialized_challengers(
    challengers: list[dict[str, Any]],
    *,
    output_dir: Path,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    y_train: np.ndarray,
    y_test: np.ndarray,
    feature_columns: list[str],
    return_col: str | None,
    test_returns: np.ndarray | None,
) -> None:
    for family in SPECIALIZED_CHALLENGER_FAMILIES:
        candidate_lane = "ranking" if family == "ranking_top5_model" else "decision"
        threshold = 0.60 if family in {"big_loss_avoider", "ranking_top5_model"} else 0.55
        spec = {
            "family": family,
            "lr": 0.06,
            "l2": 0.002,
            "threshold": threshold,
            "epochs": 650,
            "sample_weight_policy": family,
            "target_policy": "daily_top5" if family == "ranking_top5_model" else "configured_target",
            "training_window_policy": "recent_half" if family == "recent_window_model" else "full_window",
        }
        artifact = _train_logistic_recipe(train_df, y_train, feature_columns, return_col, spec)
        artifact.version = f"challenger-{family.replace('_', '-')}-v1"
        artifact.feature_columns = list(feature_columns)
        model_path = output_dir / f"{artifact.version}.json"
        lineage = _candidate_lineage(
            model_version=artifact.version,
            model_type="logistic_regression",
            spec=spec,
            feature_columns=feature_columns,
            decision_threshold=artifact.decision_threshold,
            sample_weight_policy=family,
            extra_deployable_config={"candidate_lane": candidate_lane},
        )
        _write_artifact(model_path, {"model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, **artifact.to_dict(), "training_spec": spec, "lineage": lineage})
        probs = predict_proba(artifact, test_df[feature_columns].to_numpy(dtype=float))
        preds = (probs >= artifact.decision_threshold).astype(int)
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(probs, y_test, test_returns))
        metrics["specialized_family"] = family
        challengers.append({"model_version": artifact.version, "model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, "model_path": str(model_path), "metrics": metrics, "spec": spec, "lineage": lineage})


def _ranking_top5_labels(train_df: pd.DataFrame, return_col: str | None) -> np.ndarray:
    if not return_col or return_col not in train_df.columns:
        return np.zeros(len(train_df), dtype=float)
    work = train_df.copy()
    work["_return"] = pd.to_numeric(work[return_col], errors="coerce").fillna(0.0)
    work["_event_date"] = _event_date_values(work)
    labels = pd.Series(0.0, index=work.index)
    for _, group in work.groupby("_event_date"):
        top_n = min(5, len(group))
        if top_n <= 0:
            continue
        labels.loc[group.sort_values("_return", ascending=False).head(top_n).index] = 1.0
    return labels.loc[train_df.index].to_numpy(dtype=float)


def _specialized_sample_weight(train_df: pd.DataFrame, return_col: str | None, family: str) -> np.ndarray:
    buckets = _return_bucket_series(train_df, return_col)
    weights = np.ones(len(train_df), dtype=float)
    if family == "big_loss_avoider":
        weights[buckets.to_numpy() == "big_loss"] = 6.0
        weights[buckets.to_numpy() == "loss"] = 2.0
    elif family == "big_gain_hunter":
        weights[buckets.to_numpy() == "big_gain"] = 6.0
        weights[buckets.to_numpy() == "gain"] = 2.0
    elif family == "recent_window_model":
        ramp = np.linspace(0.5, 2.5, num=len(train_df)) if len(train_df) else np.array([], dtype=float)
        weights = ramp.astype(float)
    elif family == "ranking_top5_model":
        weights[buckets.to_numpy() == "big_gain"] = 4.0
        weights[buckets.to_numpy() == "big_loss"] = 3.0
    return weights


def _add_specialized_challengers(
    challengers: list[dict[str, Any]],
    *,
    output_dir: Path,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    y_train: np.ndarray,
    y_test: np.ndarray,
    feature_columns: list[str],
    return_col: str | None,
    test_returns: np.ndarray | None,
) -> None:
    for family in SPECIALIZED_CHALLENGER_FAMILIES:
        candidate_lane = "ranking" if family == "ranking_top5_model" else "decision"
        threshold = 0.60 if family in {"big_loss_avoider", "ranking_top5_model"} else 0.55
        spec = {
            "family": family,
            "lr": 0.06,
            "l2": 0.002,
            "threshold": threshold,
            "epochs": 650,
            "sample_weight_policy": family,
            "target_policy": "daily_top5" if family == "ranking_top5_model" else "configured_target",
            "training_window_policy": "recent_half" if family == "recent_window_model" else "full_window",
        }
        artifact = _train_logistic_recipe(train_df, y_train, feature_columns, return_col, spec)
        artifact.version = f"challenger-{family.replace('_', '-')}-v1"
        artifact.feature_columns = list(feature_columns)
        model_path = output_dir / f"{artifact.version}.json"
        lineage = _candidate_lineage(
            model_version=artifact.version,
            model_type="logistic_regression",
            spec=spec,
            feature_columns=feature_columns,
            decision_threshold=artifact.decision_threshold,
            sample_weight_policy=family,
            extra_deployable_config={"candidate_lane": candidate_lane},
        )
        _write_artifact(model_path, {"model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, **artifact.to_dict(), "training_spec": spec, "lineage": lineage})
        probs = predict_proba(artifact, test_df[feature_columns].to_numpy(dtype=float))
        preds = (probs >= artifact.decision_threshold).astype(int)
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(probs, y_test, test_returns))
        metrics["specialized_family"] = family
        challengers.append({"model_version": artifact.version, "model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, "model_path": str(model_path), "metrics": metrics, "spec": spec, "lineage": lineage})


def _ranking_top5_labels(train_df: pd.DataFrame, return_col: str | None) -> np.ndarray:
    if not return_col or return_col not in train_df.columns:
        return np.zeros(len(train_df), dtype=float)
    work = train_df.copy()
    work["_return"] = pd.to_numeric(work[return_col], errors="coerce").fillna(0.0)
    work["_event_date"] = _event_date_values(work)
    labels = pd.Series(0.0, index=work.index)
    for _, group in work.groupby("_event_date"):
        top_n = min(5, len(group))
        if top_n <= 0:
            continue
        labels.loc[group.sort_values("_return", ascending=False).head(top_n).index] = 1.0
    return labels.loc[train_df.index].to_numpy(dtype=float)


def _specialized_sample_weight(train_df: pd.DataFrame, return_col: str | None, family: str) -> np.ndarray:
    buckets = _return_bucket_series(train_df, return_col)
    weights = np.ones(len(train_df), dtype=float)
    if family == "big_loss_avoider":
        weights[buckets.to_numpy() == "big_loss"] = 6.0
        weights[buckets.to_numpy() == "loss"] = 2.0
    elif family == "big_gain_hunter":
        weights[buckets.to_numpy() == "big_gain"] = 6.0
        weights[buckets.to_numpy() == "gain"] = 2.0
    elif family == "recent_window_model":
        ramp = np.linspace(0.5, 2.5, num=len(train_df)) if len(train_df) else np.array([], dtype=float)
        weights = ramp.astype(float)
    elif family == "ranking_top5_model":
        weights[buckets.to_numpy() == "big_gain"] = 4.0
        weights[buckets.to_numpy() == "big_loss"] = 3.0
    return weights


def _add_specialized_challengers(
    challengers: list[dict[str, Any]],
    *,
    output_dir: Path,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    y_train: np.ndarray,
    y_test: np.ndarray,
    feature_columns: list[str],
    return_col: str | None,
    test_returns: np.ndarray | None,
) -> None:
    for family in SPECIALIZED_CHALLENGER_FAMILIES:
        candidate_lane = "ranking" if family == "ranking_top5_model" else "decision"
        threshold = 0.60 if family in {"big_loss_avoider", "ranking_top5_model"} else 0.55
        spec = {
            "family": family,
            "lr": 0.06,
            "l2": 0.002,
            "threshold": threshold,
            "epochs": 650,
            "sample_weight_policy": family,
            "target_policy": "daily_top5" if family == "ranking_top5_model" else "configured_target",
            "training_window_policy": "recent_half" if family == "recent_window_model" else "full_window",
        }
        artifact = _train_logistic_recipe(train_df, y_train, feature_columns, return_col, spec)
        artifact.version = f"challenger-{family.replace('_', '-')}-v1"
        artifact.feature_columns = list(feature_columns)
        model_path = output_dir / f"{artifact.version}.json"
        lineage = _candidate_lineage(
            model_version=artifact.version,
            model_type="logistic_regression",
            spec=spec,
            feature_columns=feature_columns,
            decision_threshold=artifact.decision_threshold,
            sample_weight_policy=family,
            extra_deployable_config={"candidate_lane": candidate_lane},
        )
        _write_artifact(model_path, {"model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, **artifact.to_dict(), "training_spec": spec, "lineage": lineage})
        probs = predict_proba(artifact, test_df[feature_columns].to_numpy(dtype=float))
        preds = (probs >= artifact.decision_threshold).astype(int)
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(probs, y_test, test_returns))
        metrics["specialized_family"] = family
        challengers.append({"model_version": artifact.version, "model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, "model_path": str(model_path), "metrics": metrics, "spec": spec, "lineage": lineage})


def _ranking_top5_labels(train_df: pd.DataFrame, return_col: str | None) -> np.ndarray:
    if not return_col or return_col not in train_df.columns:
        return np.zeros(len(train_df), dtype=float)
    work = train_df.copy()
    work["_return"] = pd.to_numeric(work[return_col], errors="coerce").fillna(0.0)
    work["_event_date"] = _event_date_values(work)
    labels = pd.Series(0.0, index=work.index)
    for _, group in work.groupby("_event_date"):
        top_n = min(5, len(group))
        if top_n <= 0:
            continue
        labels.loc[group.sort_values("_return", ascending=False).head(top_n).index] = 1.0
    return labels.loc[train_df.index].to_numpy(dtype=float)


def _specialized_sample_weight(train_df: pd.DataFrame, return_col: str | None, family: str) -> np.ndarray:
    buckets = _return_bucket_series(train_df, return_col)
    weights = np.ones(len(train_df), dtype=float)
    if family == "big_loss_avoider":
        weights[buckets.to_numpy() == "big_loss"] = 6.0
        weights[buckets.to_numpy() == "loss"] = 2.0
    elif family == "big_gain_hunter":
        weights[buckets.to_numpy() == "big_gain"] = 6.0
        weights[buckets.to_numpy() == "gain"] = 2.0
    elif family == "recent_window_model":
        ramp = np.linspace(0.5, 2.5, num=len(train_df)) if len(train_df) else np.array([], dtype=float)
        weights = ramp.astype(float)
    elif family == "ranking_top5_model":
        weights[buckets.to_numpy() == "big_gain"] = 4.0
        weights[buckets.to_numpy() == "big_loss"] = 3.0
    return weights


def _add_specialized_challengers(
    challengers: list[dict[str, Any]],
    *,
    output_dir: Path,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    y_train: np.ndarray,
    y_test: np.ndarray,
    feature_columns: list[str],
    return_col: str | None,
    test_returns: np.ndarray | None,
) -> None:
    for family in SPECIALIZED_CHALLENGER_FAMILIES:
        candidate_lane = "ranking" if family == "ranking_top5_model" else "decision"
        threshold = 0.60 if family in {"big_loss_avoider", "ranking_top5_model"} else 0.55
        spec = {
            "family": family,
            "lr": 0.06,
            "l2": 0.002,
            "threshold": threshold,
            "epochs": 650,
            "sample_weight_policy": family,
            "target_policy": "daily_top5" if family == "ranking_top5_model" else "configured_target",
            "training_window_policy": "recent_half" if family == "recent_window_model" else "full_window",
        }
        artifact = _train_logistic_recipe(train_df, y_train, feature_columns, return_col, spec)
        artifact.version = f"challenger-{family.replace('_', '-')}-v1"
        artifact.feature_columns = list(feature_columns)
        model_path = output_dir / f"{artifact.version}.json"
        lineage = _candidate_lineage(
            model_version=artifact.version,
            model_type="logistic_regression",
            spec=spec,
            feature_columns=feature_columns,
            decision_threshold=artifact.decision_threshold,
            sample_weight_policy=family,
            extra_deployable_config={"candidate_lane": candidate_lane},
        )
        _write_artifact(model_path, {"model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, **artifact.to_dict(), "training_spec": spec, "lineage": lineage})
        probs = predict_proba(artifact, test_df[feature_columns].to_numpy(dtype=float))
        preds = (probs >= artifact.decision_threshold).astype(int)
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(probs, y_test, test_returns))
        metrics["specialized_family"] = family
        challengers.append({"model_version": artifact.version, "model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, "model_path": str(model_path), "metrics": metrics, "spec": spec, "lineage": lineage})


def _ranking_top5_labels(train_df: pd.DataFrame, return_col: str | None) -> np.ndarray:
    if not return_col or return_col not in train_df.columns:
        return np.zeros(len(train_df), dtype=float)
    work = train_df.copy()
    work["_return"] = pd.to_numeric(work[return_col], errors="coerce").fillna(0.0)
    work["_event_date"] = _event_date_values(work)
    labels = pd.Series(0.0, index=work.index)
    for _, group in work.groupby("_event_date"):
        top_n = min(5, len(group))
        if top_n <= 0:
            continue
        labels.loc[group.sort_values("_return", ascending=False).head(top_n).index] = 1.0
    return labels.loc[train_df.index].to_numpy(dtype=float)


def _specialized_sample_weight(train_df: pd.DataFrame, return_col: str | None, family: str) -> np.ndarray:
    buckets = _return_bucket_series(train_df, return_col)
    weights = np.ones(len(train_df), dtype=float)
    if family == "big_loss_avoider":
        weights[buckets.to_numpy() == "big_loss"] = 6.0
        weights[buckets.to_numpy() == "loss"] = 2.0
    elif family == "big_gain_hunter":
        weights[buckets.to_numpy() == "big_gain"] = 6.0
        weights[buckets.to_numpy() == "gain"] = 2.0
    elif family == "recent_window_model":
        ramp = np.linspace(0.5, 2.5, num=len(train_df)) if len(train_df) else np.array([], dtype=float)
        weights = ramp.astype(float)
    elif family == "ranking_top5_model":
        weights[buckets.to_numpy() == "big_gain"] = 4.0
        weights[buckets.to_numpy() == "big_loss"] = 3.0
    return weights


def _add_specialized_challengers(
    challengers: list[dict[str, Any]],
    *,
    output_dir: Path,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    y_train: np.ndarray,
    y_test: np.ndarray,
    feature_columns: list[str],
    return_col: str | None,
    test_returns: np.ndarray | None,
) -> None:
    for family in SPECIALIZED_CHALLENGER_FAMILIES:
        candidate_lane = "ranking" if family == "ranking_top5_model" else "decision"
        threshold = 0.60 if family in {"big_loss_avoider", "ranking_top5_model"} else 0.55
        spec = {
            "family": family,
            "lr": 0.06,
            "l2": 0.002,
            "threshold": threshold,
            "epochs": 650,
            "sample_weight_policy": family,
            "target_policy": "daily_top5" if family == "ranking_top5_model" else "configured_target",
            "training_window_policy": "recent_half" if family == "recent_window_model" else "full_window",
        }
        artifact = _train_logistic_recipe(train_df, y_train, feature_columns, return_col, spec)
        artifact.version = f"challenger-{family.replace('_', '-')}-v1"
        artifact.feature_columns = list(feature_columns)
        model_path = output_dir / f"{artifact.version}.json"
        lineage = _candidate_lineage(
            model_version=artifact.version,
            model_type="logistic_regression",
            spec=spec,
            feature_columns=feature_columns,
            decision_threshold=artifact.decision_threshold,
            sample_weight_policy=family,
            extra_deployable_config={"candidate_lane": candidate_lane},
        )
        _write_artifact(model_path, {"model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, **artifact.to_dict(), "training_spec": spec, "lineage": lineage})
        probs = predict_proba(artifact, test_df[feature_columns].to_numpy(dtype=float))
        preds = (probs >= artifact.decision_threshold).astype(int)
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(probs, y_test, test_returns))
        metrics["specialized_family"] = family
        challengers.append({"model_version": artifact.version, "model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, "model_path": str(model_path), "metrics": metrics, "spec": spec, "lineage": lineage})


def _ranking_top5_labels(train_df: pd.DataFrame, return_col: str | None) -> np.ndarray:
    if not return_col or return_col not in train_df.columns:
        return np.zeros(len(train_df), dtype=float)
    work = train_df.copy()
    work["_return"] = pd.to_numeric(work[return_col], errors="coerce").fillna(0.0)
    work["_event_date"] = _event_date_values(work)
    labels = pd.Series(0.0, index=work.index)
    for _, group in work.groupby("_event_date"):
        top_n = min(5, len(group))
        if top_n <= 0:
            continue
        labels.loc[group.sort_values("_return", ascending=False).head(top_n).index] = 1.0
    return labels.loc[train_df.index].to_numpy(dtype=float)


def _specialized_sample_weight(train_df: pd.DataFrame, return_col: str | None, family: str) -> np.ndarray:
    buckets = _return_bucket_series(train_df, return_col)
    weights = np.ones(len(train_df), dtype=float)
    if family == "big_loss_avoider":
        weights[buckets.to_numpy() == "big_loss"] = 6.0
        weights[buckets.to_numpy() == "loss"] = 2.0
    elif family == "big_gain_hunter":
        weights[buckets.to_numpy() == "big_gain"] = 6.0
        weights[buckets.to_numpy() == "gain"] = 2.0
    elif family == "recent_window_model":
        ramp = np.linspace(0.5, 2.5, num=len(train_df)) if len(train_df) else np.array([], dtype=float)
        weights = ramp.astype(float)
    elif family == "ranking_top5_model":
        weights[buckets.to_numpy() == "big_gain"] = 4.0
        weights[buckets.to_numpy() == "big_loss"] = 3.0
    return weights


def _add_specialized_challengers(
    challengers: list[dict[str, Any]],
    *,
    output_dir: Path,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    y_train: np.ndarray,
    y_test: np.ndarray,
    feature_columns: list[str],
    return_col: str | None,
    test_returns: np.ndarray | None,
) -> None:
    for family in SPECIALIZED_CHALLENGER_FAMILIES:
        candidate_lane = "ranking" if family == "ranking_top5_model" else "decision"
        threshold = 0.60 if family in {"big_loss_avoider", "ranking_top5_model"} else 0.55
        spec = {
            "family": family,
            "lr": 0.06,
            "l2": 0.002,
            "threshold": threshold,
            "epochs": 650,
            "sample_weight_policy": family,
            "target_policy": "daily_top5" if family == "ranking_top5_model" else "configured_target",
            "training_window_policy": "recent_half" if family == "recent_window_model" else "full_window",
        }
        artifact = _train_logistic_recipe(train_df, y_train, feature_columns, return_col, spec)
        artifact.version = f"challenger-{family.replace('_', '-')}-v1"
        artifact.feature_columns = list(feature_columns)
        model_path = output_dir / f"{artifact.version}.json"
        lineage = _candidate_lineage(
            model_version=artifact.version,
            model_type="logistic_regression",
            spec=spec,
            feature_columns=feature_columns,
            decision_threshold=artifact.decision_threshold,
            sample_weight_policy=family,
            extra_deployable_config={"candidate_lane": candidate_lane},
        )
        _write_artifact(model_path, {"model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, **artifact.to_dict(), "training_spec": spec, "lineage": lineage})
        probs = predict_proba(artifact, test_df[feature_columns].to_numpy(dtype=float))
        preds = (probs >= artifact.decision_threshold).astype(int)
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(probs, y_test, test_returns))
        metrics["specialized_family"] = family
        challengers.append({"model_version": artifact.version, "model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, "model_path": str(model_path), "metrics": metrics, "spec": spec, "lineage": lineage})


def _ranking_top5_labels(train_df: pd.DataFrame, return_col: str | None) -> np.ndarray:
    if not return_col or return_col not in train_df.columns:
        return np.zeros(len(train_df), dtype=float)
    work = train_df.copy()
    work["_return"] = pd.to_numeric(work[return_col], errors="coerce").fillna(0.0)
    work["_event_date"] = _event_date_values(work)
    labels = pd.Series(0.0, index=work.index)
    for _, group in work.groupby("_event_date"):
        top_n = min(5, len(group))
        if top_n <= 0:
            continue
        labels.loc[group.sort_values("_return", ascending=False).head(top_n).index] = 1.0
    return labels.loc[train_df.index].to_numpy(dtype=float)


def _specialized_sample_weight(train_df: pd.DataFrame, return_col: str | None, family: str) -> np.ndarray:
    buckets = _return_bucket_series(train_df, return_col)
    weights = np.ones(len(train_df), dtype=float)
    if family == "big_loss_avoider":
        weights[buckets.to_numpy() == "big_loss"] = 6.0
        weights[buckets.to_numpy() == "loss"] = 2.0
    elif family == "big_gain_hunter":
        weights[buckets.to_numpy() == "big_gain"] = 6.0
        weights[buckets.to_numpy() == "gain"] = 2.0
    elif family == "recent_window_model":
        ramp = np.linspace(0.5, 2.5, num=len(train_df)) if len(train_df) else np.array([], dtype=float)
        weights = ramp.astype(float)
    elif family == "ranking_top5_model":
        weights[buckets.to_numpy() == "big_gain"] = 4.0
        weights[buckets.to_numpy() == "big_loss"] = 3.0
    return weights


def _add_specialized_challengers(
    challengers: list[dict[str, Any]],
    *,
    output_dir: Path,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    y_train: np.ndarray,
    y_test: np.ndarray,
    feature_columns: list[str],
    return_col: str | None,
    test_returns: np.ndarray | None,
) -> None:
    for family in SPECIALIZED_CHALLENGER_FAMILIES:
        candidate_lane = "ranking" if family == "ranking_top5_model" else "decision"
        threshold = 0.60 if family in {"big_loss_avoider", "ranking_top5_model"} else 0.55
        spec = {
            "family": family,
            "lr": 0.06,
            "l2": 0.002,
            "threshold": threshold,
            "epochs": 650,
            "sample_weight_policy": family,
            "target_policy": "daily_top5" if family == "ranking_top5_model" else "configured_target",
            "training_window_policy": "recent_half" if family == "recent_window_model" else "full_window",
        }
        artifact = _train_logistic_recipe(train_df, y_train, feature_columns, return_col, spec)
        artifact.version = f"challenger-{family.replace('_', '-')}-v1"
        artifact.feature_columns = list(feature_columns)
        model_path = output_dir / f"{artifact.version}.json"
        lineage = _candidate_lineage(
            model_version=artifact.version,
            model_type="logistic_regression",
            spec=spec,
            feature_columns=feature_columns,
            decision_threshold=artifact.decision_threshold,
            sample_weight_policy=family,
            extra_deployable_config={"candidate_lane": candidate_lane},
        )
        _write_artifact(model_path, {"model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, **artifact.to_dict(), "training_spec": spec, "lineage": lineage})
        probs = predict_proba(artifact, test_df[feature_columns].to_numpy(dtype=float))
        preds = (probs >= artifact.decision_threshold).astype(int)
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(probs, y_test, test_returns))
        metrics["specialized_family"] = family
        challengers.append({"model_version": artifact.version, "model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, "model_path": str(model_path), "metrics": metrics, "spec": spec, "lineage": lineage})


def _ranking_top5_labels(train_df: pd.DataFrame, return_col: str | None) -> np.ndarray:
    if not return_col or return_col not in train_df.columns:
        return np.zeros(len(train_df), dtype=float)
    work = train_df.copy()
    work["_return"] = pd.to_numeric(work[return_col], errors="coerce").fillna(0.0)
    work["_event_date"] = _event_date_values(work)
    labels = pd.Series(0.0, index=work.index)
    for _, group in work.groupby("_event_date"):
        top_n = min(5, len(group))
        if top_n <= 0:
            continue
        labels.loc[group.sort_values("_return", ascending=False).head(top_n).index] = 1.0
    return labels.loc[train_df.index].to_numpy(dtype=float)


def _specialized_sample_weight(train_df: pd.DataFrame, return_col: str | None, family: str) -> np.ndarray:
    buckets = _return_bucket_series(train_df, return_col)
    weights = np.ones(len(train_df), dtype=float)
    if family == "big_loss_avoider":
        weights[buckets.to_numpy() == "big_loss"] = 6.0
        weights[buckets.to_numpy() == "loss"] = 2.0
    elif family == "big_gain_hunter":
        weights[buckets.to_numpy() == "big_gain"] = 6.0
        weights[buckets.to_numpy() == "gain"] = 2.0
    elif family == "recent_window_model":
        ramp = np.linspace(0.5, 2.5, num=len(train_df)) if len(train_df) else np.array([], dtype=float)
        weights = ramp.astype(float)
    elif family == "ranking_top5_model":
        weights[buckets.to_numpy() == "big_gain"] = 4.0
        weights[buckets.to_numpy() == "big_loss"] = 3.0
    return weights


def _add_specialized_challengers(
    challengers: list[dict[str, Any]],
    *,
    output_dir: Path,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    y_train: np.ndarray,
    y_test: np.ndarray,
    feature_columns: list[str],
    return_col: str | None,
    test_returns: np.ndarray | None,
) -> None:
    for family in SPECIALIZED_CHALLENGER_FAMILIES:
        candidate_lane = "ranking" if family == "ranking_top5_model" else "decision"
        threshold = 0.60 if family in {"big_loss_avoider", "ranking_top5_model"} else 0.55
        spec = {
            "family": family,
            "lr": 0.06,
            "l2": 0.002,
            "threshold": threshold,
            "epochs": 650,
            "sample_weight_policy": family,
            "target_policy": "daily_top5" if family == "ranking_top5_model" else "configured_target",
            "training_window_policy": "recent_half" if family == "recent_window_model" else "full_window",
        }
        artifact = _train_logistic_recipe(train_df, y_train, feature_columns, return_col, spec)
        artifact.version = f"challenger-{family.replace('_', '-')}-v1"
        artifact.feature_columns = list(feature_columns)
        model_path = output_dir / f"{artifact.version}.json"
        lineage = _candidate_lineage(
            model_version=artifact.version,
            model_type="logistic_regression",
            spec=spec,
            feature_columns=feature_columns,
            decision_threshold=artifact.decision_threshold,
            sample_weight_policy=family,
            extra_deployable_config={"candidate_lane": candidate_lane},
        )
        _write_artifact(model_path, {"model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, **artifact.to_dict(), "training_spec": spec, "lineage": lineage})
        probs = predict_proba(artifact, test_df[feature_columns].to_numpy(dtype=float))
        preds = (probs >= artifact.decision_threshold).astype(int)
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(probs, y_test, test_returns))
        metrics["specialized_family"] = family
        challengers.append({"model_version": artifact.version, "model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, "model_path": str(model_path), "metrics": metrics, "spec": spec, "lineage": lineage})


def _ranking_top5_labels(train_df: pd.DataFrame, return_col: str | None) -> np.ndarray:
    if not return_col or return_col not in train_df.columns:
        return np.zeros(len(train_df), dtype=float)
    work = train_df.copy()
    work["_return"] = pd.to_numeric(work[return_col], errors="coerce").fillna(0.0)
    work["_event_date"] = _event_date_values(work)
    labels = pd.Series(0.0, index=work.index)
    for _, group in work.groupby("_event_date"):
        top_n = min(5, len(group))
        if top_n <= 0:
            continue
        labels.loc[group.sort_values("_return", ascending=False).head(top_n).index] = 1.0
    return labels.loc[train_df.index].to_numpy(dtype=float)


def _specialized_sample_weight(train_df: pd.DataFrame, return_col: str | None, family: str) -> np.ndarray:
    buckets = _return_bucket_series(train_df, return_col)
    weights = np.ones(len(train_df), dtype=float)
    if family == "big_loss_avoider":
        weights[buckets.to_numpy() == "big_loss"] = 6.0
        weights[buckets.to_numpy() == "loss"] = 2.0
    elif family == "big_gain_hunter":
        weights[buckets.to_numpy() == "big_gain"] = 6.0
        weights[buckets.to_numpy() == "gain"] = 2.0
    elif family == "recent_window_model":
        ramp = np.linspace(0.5, 2.5, num=len(train_df)) if len(train_df) else np.array([], dtype=float)
        weights = ramp.astype(float)
    elif family == "ranking_top5_model":
        weights[buckets.to_numpy() == "big_gain"] = 4.0
        weights[buckets.to_numpy() == "big_loss"] = 3.0
    return weights


def _add_specialized_challengers(
    challengers: list[dict[str, Any]],
    *,
    output_dir: Path,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    y_train: np.ndarray,
    y_test: np.ndarray,
    feature_columns: list[str],
    return_col: str | None,
    test_returns: np.ndarray | None,
) -> None:
    for family in SPECIALIZED_CHALLENGER_FAMILIES:
        candidate_lane = "ranking" if family == "ranking_top5_model" else "decision"
        threshold = 0.60 if family in {"big_loss_avoider", "ranking_top5_model"} else 0.55
        spec = {
            "family": family,
            "lr": 0.06,
            "l2": 0.002,
            "threshold": threshold,
            "epochs": 650,
            "sample_weight_policy": family,
            "target_policy": "daily_top5" if family == "ranking_top5_model" else "configured_target",
            "training_window_policy": "recent_half" if family == "recent_window_model" else "full_window",
        }
        artifact = _train_logistic_recipe(train_df, y_train, feature_columns, return_col, spec)
        artifact.version = f"challenger-{family.replace('_', '-')}-v1"
        artifact.feature_columns = list(feature_columns)
        model_path = output_dir / f"{artifact.version}.json"
        lineage = _candidate_lineage(
            model_version=artifact.version,
            model_type="logistic_regression",
            spec=spec,
            feature_columns=feature_columns,
            decision_threshold=artifact.decision_threshold,
            sample_weight_policy=family,
            extra_deployable_config={"candidate_lane": candidate_lane},
        )
        _write_artifact(model_path, {"model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, **artifact.to_dict(), "training_spec": spec, "lineage": lineage})
        probs = predict_proba(artifact, test_df[feature_columns].to_numpy(dtype=float))
        preds = (probs >= artifact.decision_threshold).astype(int)
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(probs, y_test, test_returns))
        metrics["specialized_family"] = family
        challengers.append({"model_version": artifact.version, "model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, "model_path": str(model_path), "metrics": metrics, "spec": spec, "lineage": lineage})


def _ranking_top5_labels(train_df: pd.DataFrame, return_col: str | None) -> np.ndarray:
    if not return_col or return_col not in train_df.columns:
        return np.zeros(len(train_df), dtype=float)
    work = train_df.copy()
    work["_return"] = pd.to_numeric(work[return_col], errors="coerce").fillna(0.0)
    work["_event_date"] = _event_date_values(work)
    labels = pd.Series(0.0, index=work.index)
    for _, group in work.groupby("_event_date"):
        top_n = min(5, len(group))
        if top_n <= 0:
            continue
        labels.loc[group.sort_values("_return", ascending=False).head(top_n).index] = 1.0
    return labels.loc[train_df.index].to_numpy(dtype=float)


def _specialized_sample_weight(train_df: pd.DataFrame, return_col: str | None, family: str) -> np.ndarray:
    buckets = _return_bucket_series(train_df, return_col)
    weights = np.ones(len(train_df), dtype=float)
    if family == "big_loss_avoider":
        weights[buckets.to_numpy() == "big_loss"] = 6.0
        weights[buckets.to_numpy() == "loss"] = 2.0
    elif family == "big_gain_hunter":
        weights[buckets.to_numpy() == "big_gain"] = 6.0
        weights[buckets.to_numpy() == "gain"] = 2.0
    elif family == "recent_window_model":
        ramp = np.linspace(0.5, 2.5, num=len(train_df)) if len(train_df) else np.array([], dtype=float)
        weights = ramp.astype(float)
    elif family == "ranking_top5_model":
        weights[buckets.to_numpy() == "big_gain"] = 4.0
        weights[buckets.to_numpy() == "big_loss"] = 3.0
    return weights


def _add_specialized_challengers(
    challengers: list[dict[str, Any]],
    *,
    output_dir: Path,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    y_train: np.ndarray,
    y_test: np.ndarray,
    feature_columns: list[str],
    return_col: str | None,
    test_returns: np.ndarray | None,
) -> None:
    for family in SPECIALIZED_CHALLENGER_FAMILIES:
        candidate_lane = "ranking" if family == "ranking_top5_model" else "decision"
        threshold = 0.60 if family in {"big_loss_avoider", "ranking_top5_model"} else 0.55
        spec = {
            "family": family,
            "lr": 0.06,
            "l2": 0.002,
            "threshold": threshold,
            "epochs": 650,
            "sample_weight_policy": family,
            "target_policy": "daily_top5" if family == "ranking_top5_model" else "configured_target",
            "training_window_policy": "recent_half" if family == "recent_window_model" else "full_window",
        }
        artifact = _train_logistic_recipe(train_df, y_train, feature_columns, return_col, spec)
        artifact.version = f"challenger-{family.replace('_', '-')}-v1"
        artifact.feature_columns = list(feature_columns)
        model_path = output_dir / f"{artifact.version}.json"
        lineage = _candidate_lineage(
            model_version=artifact.version,
            model_type="logistic_regression",
            spec=spec,
            feature_columns=feature_columns,
            decision_threshold=artifact.decision_threshold,
            sample_weight_policy=family,
            extra_deployable_config={"candidate_lane": candidate_lane},
        )
        _write_artifact(model_path, {"model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, **artifact.to_dict(), "training_spec": spec, "lineage": lineage})
        probs = predict_proba(artifact, test_df[feature_columns].to_numpy(dtype=float))
        preds = (probs >= artifact.decision_threshold).astype(int)
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(probs, y_test, test_returns))
        metrics["specialized_family"] = family
        challengers.append({"model_version": artifact.version, "model_type": "logistic_regression", "candidate_lane": candidate_lane, "specialized_family": family, "model_path": str(model_path), "metrics": metrics, "spec": spec, "lineage": lineage})

def _add_baseline_challengers(challengers: list[dict[str, Any]], *, output_dir: Path, y_train: np.ndarray, y_test: np.ndarray, test_returns: np.ndarray | None) -> None:
    majority_class = int(float(y_train.mean()) >= 0.5)
    baselines = [
        ("challenger-baseline-majority-v1", np.full_like(y_test, majority_class, dtype=int), {"majority_class": majority_class}),
        ("challenger-baseline-always-up-v1", np.ones_like(y_test, dtype=int), {}),
        ("challenger-baseline-always-down-v1", np.zeros_like(y_test, dtype=int), {}),
    ]
    for model_version, preds, spec in baselines:
        model_path = output_dir / f"{model_version}.json"
        lineage = _candidate_lineage(model_version=model_version, model_type="baseline_classifier", spec=spec, feature_columns=[], decision_threshold=None)
        _write_artifact(model_path, {"version": model_version, "model_type": "baseline_classifier", "training_spec": spec, "lineage": lineage})
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(preds.astype(float), y_test, test_returns))
        challengers.append({"model_version": model_version, "model_type": "baseline_classifier", "model_path": str(model_path), "metrics": metrics, "spec": spec, "lineage": lineage})


def train_challenger_suite(input_path: Path, output_dir: Path, *, train_ratio: float = 0.8, horizon_days: int = 5, min_rows: int = 200, split_plan_path: Path | None = None) -> dict[str, Any]:
    df = _load_jsonl(input_path)
    if df.empty:
        raise ValueError("No rows available in input dataset")
    persisted_feature_columns = {str(col) for col in df.columns if str(col).startswith("feature_")}
    is_v4 = "canonicalization_contract_version" in df.columns
    frozen_plan = None
    train_ids: set[str] = set()
    test_ids: set[str] = set()
    if is_v4:
        if split_plan_path is None or not split_plan_path.is_file():
            raise ValueError("V4 challenger training requires a frozen temporal split plan")
        frozen_plan = json.loads(split_plan_path.read_text(encoding="utf-8"))
        train_ids, test_ids = validate_split_plan(frozen_plan, input_path=input_path)
    if "ts" in df.columns:
        df = df.sort_values("ts").reset_index(drop=True)
    df = _prepare_frame(df)
    target_col = _target(df, horizon_days)
    df[target_col] = pd.to_numeric(df[target_col], errors="coerce")
    df = df.dropna(subset=[target_col]).copy()
    feature_columns = _backtest_compatible_feature_columns(_future_safe_feature_columns(_select_feature_columns(df)), persisted_feature_columns)
    if not feature_columns:
        raise ValueError("No numeric feature columns found")
    unfilled = df.copy()
    fill_policy = None
    if is_v4:
        if "canonical_observation_id" not in unfilled.columns:
            raise ValueError("V4 challenger input lacks canonical observation IDs")
        fit_rows = unfilled.loc[unfilled["canonical_observation_id"].astype(str).isin(train_ids)].copy()
        if fit_rows.empty:
            raise ValueError("V4 fill policy has no frozen-plan fit rows")
        fill_policy = fit_feature_fill_policy(fit_rows, feature_columns)
        clean = apply_feature_fill_policy(unfilled, fill_policy, expected_feature_contract_version="alpha-atlas-v4-features.v2")
        fill_values = {name: float(spec["fitted_value"]) for name, spec in fill_policy["features"].items()}
    else:
        clean, fill_values = _fill_feature_gaps(df, feature_columns)
    if len(clean) < max(1, min_rows):
        raise ValueError(f"Not enough rows to train challenger suite (have={len(clean)}, need={min_rows})")
    if is_v4:
        if "canonical_observation_id" not in clean.columns:
            raise ValueError("V4 challenger input lacks canonical observation IDs")
        available = set(clean["canonical_observation_id"].astype(str))
        if not (train_ids | test_ids) <= available:
            raise ValueError("Frozen split plan references rows removed before training")
        train_df = clean.loc[clean["canonical_observation_id"].astype(str).isin(train_ids)].copy()
        test_df = clean.loc[clean["canonical_observation_id"].astype(str).isin(test_ids)].copy()
        holdout_temporal_split = {
            "method": "alpha-atlas-v4-purged-temporal-split.v1",
            "plan_sha256": frozen_plan["plan_sha256"],
            "input_sha256": frozen_plan["input_sha256"],
            "boundary_date": frozen_plan["boundary_date"],
            "train_rows_after": len(train_df),
            "test_rows_after": len(test_df),
        }
    else:
        train_df, test_df = _chronological_split(clean, train_ratio)
        train_df, test_df, holdout_temporal_split = purged_embargoed_split(
            train_df,
            test_df,
            horizon_days=horizon_days,
            embargo_days=EMBARGO_DAYS,
        )
    if train_df.empty or test_df.empty:
        raise ValueError("purging/embargo leaves an empty challenger train or test period")
    if len(train_df) + len(test_df) < max(1, min_rows):
        raise ValueError(f"Not enough rows after purging/embargo (have={len(train_df) + len(test_df)}, need={min_rows})")
    X_train = train_df[feature_columns].to_numpy(dtype=float)
    y_train = train_df[target_col].to_numpy(dtype=float)
    X_test = test_df[feature_columns].to_numpy(dtype=float)
    y_test = test_df[target_col].to_numpy(dtype=float)
    return_col = _return_column(clean, horizon_days)
    test_returns = test_df[return_col].to_numpy(dtype=float) if return_col else None

    output_dir.mkdir(parents=True, exist_ok=True)
    if fill_policy is not None:
        (output_dir / "feature_fill_policy.json").write_text(json.dumps(fill_policy, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    mistake_slices = _write_daily_mistake_slices(clean, output_dir, return_col)
    challengers: list[dict[str, Any]] = []
    _add_logistic_challengers(challengers, output_dir=output_dir, X_train=X_train, y_train=y_train, X_test=X_test, y_test=y_test, feature_columns=feature_columns, test_returns=test_returns)
    _add_calibrated_linear_challengers(
        challengers,
        output_dir=output_dir,
        train_df=train_df,
        test_df=test_df,
        target_col=target_col,
        feature_columns=feature_columns,
        return_col=return_col,
        test_returns=test_returns,
        horizon_days=horizon_days,
    )
    _add_abstention_challengers(
        challengers,
        output_dir=output_dir,
        train_df=train_df,
        test_df=test_df,
        target_col=target_col,
        feature_columns=feature_columns,
        return_col=return_col,
        test_returns=test_returns,
        horizon_days=horizon_days,
    )
    _add_two_stage_risk_challengers(
        challengers,
        output_dir=output_dir,
        train_df=train_df,
        test_df=test_df,
        target_col=target_col,
        feature_columns=feature_columns,
        return_col=return_col,
        test_returns=test_returns,
        horizon_days=horizon_days,
    )
    _add_hard_example_challengers(
        challengers,
        output_dir=output_dir,
        train_df=train_df,
        test_df=test_df,
        target_col=target_col,
        feature_columns=feature_columns,
        return_col=return_col,
        test_returns=test_returns,
    )
    _add_ranking_lane_challengers(
        challengers,
        output_dir=output_dir,
        train_df=train_df,
        test_df=test_df,
        y_test=y_test,
        feature_columns=feature_columns,
        return_col=return_col,
        test_returns=test_returns,
    )
    _add_stump_challengers(challengers, output_dir=output_dir, train_df=train_df, test_df=test_df, y_train=y_train, y_test=y_test, feature_columns=feature_columns, test_returns=test_returns)
    _add_shallow_tree_challengers(challengers, output_dir=output_dir, train_df=train_df, test_df=test_df, y_train=y_train, y_test=y_test, feature_columns=feature_columns, test_returns=test_returns, return_col=return_col)
    _add_specialized_challengers(challengers, output_dir=output_dir, train_df=train_df, test_df=test_df, y_train=y_train, y_test=y_test, feature_columns=feature_columns, return_col=return_col, test_returns=test_returns)
    _add_baseline_challengers(challengers, output_dir=output_dir, y_train=y_train, y_test=y_test, test_returns=test_returns)
    dataset_lineage = None
    if {"canonical_dataset_schema_version", "split_metadata_hash", "price_adjustment_policy"}.issubset(clean.columns):
        dataset_lineage = {
            "canonical_dataset_schema_version": str(clean["canonical_dataset_schema_version"].iloc[0]),
            "split_metadata_hash": str(clean["split_metadata_hash"].iloc[0]),
            "price_adjustment_policy": str(clean["price_adjustment_policy"].iloc[0]),
        }
        for challenger in challengers:
            challenger["lineage"]["dataset_lineage"] = dataset_lineage
            model_path = Path(str(challenger["model_path"]))
            payload = json.loads(model_path.read_text(encoding="utf-8"))
            payload.setdefault("lineage", {})["dataset_lineage"] = dataset_lineage
            _write_artifact(model_path, payload)
    walk_forward_folds = _walk_forward_splits(clean)
    _apply_walk_forward_metrics(
        challengers,
        clean=clean,
        folds=walk_forward_folds,
        feature_columns=feature_columns,
        target_col=target_col,
        return_col=return_col,
        horizon_days=horizon_days,
        fit_source=unfilled if is_v4 else None,
    )

    ranked = sorted(
        challengers,
        key=lambda item: (
            bool(item["metrics"].get("walk_forward_passed", False)),
            item["metrics"].get("walk_forward_ranking_objective", item["metrics"].get("ranking_objective", 0)),
            item["metrics"].get("ranking_objective", 0),
            item["metrics"].get("top_k_avg_return", 0),
            item["metrics"].get("accuracy", 0),
        ),
        reverse=True,
    )
    scoring_challenger = next((item for item in ranked if item.get("model_type") == "logistic_regression"), None)
    if scoring_challenger is None:
        raise ValueError("No logistic artifact available for artifact-scored mistake mining")
    mistake_slices = _write_daily_mistake_slices(
        test_df,
        output_dir,
        return_col,
        model_path=Path(str(scoring_challenger["model_path"])),
    )
    walk_forward_windows: list[dict[str, Any]] = []
    for start, train_end, test_end in walk_forward_folds:
        _, _, diagnostics = purged_embargoed_split(
            clean.iloc[start:train_end],
            clean.iloc[train_end:test_end],
            horizon_days=horizon_days,
            embargo_days=EMBARGO_DAYS,
        )
        walk_forward_windows.append(
            {
                "train_start_row": start,
                "train_end_row": train_end,
                "test_start_row": train_end,
                "test_end_row": test_end,
                "temporal_split": diagnostics,
            }
        )
    model_type_counts = {model_type: sum(1 for item in challengers if item["model_type"] == model_type) for model_type in sorted({item["model_type"] for item in challengers})}
    phase_2_challengers = [item for item in challengers if item["model_type"] in {"calibrated_linear", "shallow_decision_tree", "two_stage_risk_filter", "hard_example_linear", "ranking_lane_linear"}]
    phase_2_fingerprints = {
        str(item["metrics"]["prediction_fingerprint"])
        for item in phase_2_challengers
        if item["metrics"].get("prediction_fingerprint")
    }
    ranking_lane = [item for item in challengers if item.get("candidate_lane") == "ranking" or item.get("specialized_family") == "ranking_top5_model"]
    ranking_versions = {item["model_version"] for item in ranking_lane}
    decision_lane = [item for item in challengers if item["model_version"] not in ranking_versions]
    decision_lane_ranked = sorted(decision_lane, key=lambda item: (bool(item["metrics"].get("walk_forward_passed")), item["metrics"].get("accuracy", 0.0), -item["metrics"].get("big_loss_prediction_rate", 0.0)), reverse=True)
    ranking_lane_ranked = sorted(ranking_lane, key=lambda item: (bool(item["metrics"].get("walk_forward_passed")), item["metrics"].get("walk_forward_ranking_objective", 0.0), item["metrics"].get("ranking_objective", 0.0)), reverse=True)
    manifest = {
        "schema_version": SUITE_SCHEMA_VERSION,
        "lineage_schema_version": LINEAGE_SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path),
        "output_dir": str(output_dir),
        "rows": len(clean),
        "train_rows": len(train_df),
        "test_rows": len(test_df),
        "temporal_split": holdout_temporal_split,
        "temporal_validation_policy": {
            "purged": True,
            "label_horizon_days": int(horizon_days),
            "embargo_days": EMBARGO_DAYS,
            **({"split_plan_sha256": frozen_plan["plan_sha256"], "split_input_sha256": frozen_plan["input_sha256"]} if frozen_plan else {}),
        },
        "walk_forward_windows": walk_forward_windows,
        "target_column": target_col,
        "decision_target": target_metadata(),
        "dataset_lineage": dataset_lineage,
        "ranking_selection_policy": "rank by walk-forward pass status across multiple windows, then walk-forward top-K capped-exposure ranking_objective, holdout ranking objective, top-K average return, then accuracy",
        "promotion_policy": "prefer candidates with positive ranking_objective in at least two walk-forward windows before promotion",
        "ranking_metric_names": ["top_k_precision", "top_k_avg_return", "pairwise_ranking_loss", "big_gain_capture", "big_loss_demotion", "ranking_objective", "walk_forward_ranking_objective", "walk_forward_passed"],
        "feature_columns": feature_columns,
        "feature_fill_values": fill_values,
        "feature_fill_policy": fill_policy,
        "model_type_counts": model_type_counts,
        "specialized_challenger_families": list(SPECIALIZED_CHALLENGER_FAMILIES),
        "phase_2_candidate_families": {
            "calibrated_linear": [str(spec["name"]) for spec in CALIBRATED_LINEAR_VARIANTS],
            "shallow_decision_tree": {"max_depths": list(SHALLOW_TREE_DEPTHS), "maximum_allowed_depth": max(SHALLOW_TREE_DEPTHS)},
            "two_stage_risk_filter": {"risk_thresholds": list(RISK_FILTER_THRESHOLDS), "decision_threshold": 0.60},
            "bounded_hard_example": {"max_fraction": HARD_EXAMPLE_MAX_FRACTION, "hard_example_weight": HARD_EXAMPLE_WEIGHT, "max_sample_weight": HARD_EXAMPLE_MAX_WEIGHT},
            "ranking_lane_linear": {"training_windows": ["full", "recent_half"], "promotion_scope": "ranking_only"},
        },
        "phase_3_candidate_families": {
            "abstention_linear": {"margins": list(ABSTENTION_MARGINS), "base_threshold": 0.60, "abstained_action": "cash_no_signal"},
        },
        "candidate_lanes": {
            "decision": {"candidate_count": len(decision_lane), "ranked_model_versions": [item["model_version"] for item in decision_lane_ranked]},
            "ranking": {"candidate_count": len(ranking_lane), "ranked_model_versions": [item["model_version"] for item in ranking_lane_ranked], "can_replace_main_decision_model": False},
        },
        "phase_2_diversity": {
            "candidate_count": len(phase_2_challengers),
            "distinct_prediction_clusters": len(phase_2_fingerprints),
            "prediction_fingerprints": sorted(phase_2_fingerprints),
            "feature_subset_policies": sorted({str(item["spec"].get("feature_subset_policy", "all")) for item in phase_2_challengers}),
            "training_window_policies": sorted({str(item["spec"].get("training_window_policy", "full")) for item in phase_2_challengers}),
        },
        "mistake_mining": mistake_slices,
        "challenger_count": len(challengers),
        "challengers": challengers,
        "ranked_model_versions": [item["model_version"] for item in ranked],
        "live_routing": False,
    }
    (output_dir / "challenger_suite_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Train many offline MoneyBot challenger models from one reproducible feature-store snapshot.")
    parser.add_argument("--input", default="data/flat_feature_store/train.jsonl")
    parser.add_argument("--output-dir", default="data/challenger_suite")
    parser.add_argument("--train-ratio", type=float, default=0.8)
    parser.add_argument("--horizon-days", type=int, default=5)
    parser.add_argument("--min-rows", type=int, default=200)
    parser.add_argument("--split-plan")
    args = parser.parse_args()
    manifest = train_challenger_suite(Path(args.input), Path(args.output_dir), train_ratio=args.train_ratio, horizon_days=args.horizon_days, min_rows=args.min_rows, split_plan_path=Path(args.split_plan) if args.split_plan else None)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
