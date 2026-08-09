#!/usr/bin/env python3
"""Generate differentiated, research-only challengers against the Massive baseline."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moneybot.services.deterministic_model import (
    BaselineModelArtifact,
    fit_probability_calibration,
    load_artifact,
    predict_proba,
    train_logistic_baseline,
)
from scripts.train_massive_baseline_model import (
    GROUP_COLUMNS,
    RETURN_COLUMN,
    TARGET_COLUMN,
    _duplicate_weights,
    _event_dates,
    _fill_from_fit,
    _load_jsonl,
    _market_feature_columns,
    _temporal_train_periods,
)

HORIZON_DAYS = 5
THRESHOLDS = (0.50, 0.525, 0.55, 0.575, 0.60, 0.625, 0.65, 0.675, 0.70)
LAMBDAS = (0.25, 0.50, 0.75, 1.00, 1.50, 2.00)
MARGINS = (0.00, 0.025, 0.05, 0.075, 0.10)
TOP_K = (1, 3, 5, 10)
MAX_CONCENTRATION = 0.50


def _hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _artifact(base: BaselineModelArtifact, features: list[str], version: str, calibration: dict[str, Any]) -> BaselineModelArtifact:
    recipe = {
        "model_family": "logistic_regression",
        "feature_subset": features,
        "calibration": calibration,
        "sample_weight_policy": "1 / count(symbol, event_date, endpoint, decision_source)",
        "target_column": TARGET_COLUMN,
        "evaluation_return_column": RETURN_COLUMN,
        "horizon_days": HORIZON_DAYS,
    }
    recipe_hash = _hash({"version": version, **recipe})
    return BaselineModelArtifact(
        version=version,
        feature_columns=features,
        means=base.means,
        stds=base.stds,
        weights=base.weights,
        bias=base.bias,
        decision_threshold=0.55,
        calibration_slope=float(calibration["slope"]),
        calibration_intercept=float(calibration["intercept"]),
        lineage={
            "schema_version": "moneybot-challenger-lineage.v1",
            "lineage_id": f"recipe-{recipe_hash[:16]}",
            "recipe_hash": recipe_hash,
            "recipe": recipe,
            "target_column": TARGET_COLUMN,
            "evaluation_return_column": RETURN_COLUMN,
            "horizon_days": HORIZON_DAYS,
            "sample_weight_policy": recipe["sample_weight_policy"],
        },
    )


def _fit_model(
    fit: pd.DataFrame,
    calibration: pd.DataFrame,
    features: list[str],
    labels: np.ndarray,
    weights: np.ndarray,
    version: str,
) -> BaselineModelArtifact:
    base = train_logistic_baseline(fit[features].to_numpy(dtype=float), labels.astype(float), sample_weight=weights)
    identity = _artifact(base, features, version, {"method": "identity", "slope": 1.0, "intercept": 0.0})
    raw = predict_proba(identity, calibration[features].to_numpy(dtype=float))
    calibration_labels = pd.to_numeric(calibration[TARGET_COLUMN], errors="coerce").to_numpy(dtype=float)
    fitted = fit_probability_calibration(raw, calibration_labels)
    return _artifact(base, features, version, fitted)


def _fingerprint(probs: np.ndarray, preds: np.ndarray) -> str:
    payload = np.column_stack([np.round(probs, 8), preds.astype(int)]).tobytes()
    return hashlib.sha256(payload).hexdigest()


def _metrics(frame: pd.DataFrame, probs: np.ndarray, preds: np.ndarray) -> dict[str, Any]:
    returns = pd.to_numeric(frame[RETURN_COLUMN], errors="coerce").to_numpy(dtype=float)
    labels = pd.to_numeric(frame[TARGET_COLUMN], errors="coerce").to_numpy(dtype=float)
    weights = _duplicate_weights(frame)
    weights = weights / weights.sum()
    selected_weights = weights[preds]
    selected_returns = returns[preds]
    selected_weight = float(selected_weights.sum())
    big_loss = returns < -0.03
    big_gain = returns >= 0.03
    dates = _event_dates(frame).dt.strftime("%Y-%m-%d").fillna("unknown")
    symbols = frame["symbol"].fillna("unknown").astype(str)
    selected_groups = pd.DataFrame({"symbol": symbols[preds], "date": dates[preds], "weight": selected_weights})
    if len(selected_groups):
        grouped = selected_groups.groupby(["symbol", "date"])["weight"].sum()
        concentration = float(grouped.max() / grouped.sum())
    else:
        concentration = 0.0
    avg_return = float(np.average(selected_returns, weights=selected_weights)) if selected_weight > 0 else None
    downside = float(abs(np.average(selected_returns[selected_returns < 0], weights=selected_weights[selected_returns < 0]))) if (selected_returns < 0).any() else 0.0
    big_loss_rate = float(weights[preds & big_loss].sum() / weights[big_loss].sum()) if big_loss.any() else 0.0
    big_gain_capture = float(weights[preds & big_gain].sum() / weights[big_gain].sum()) if big_gain.any() else 0.0
    utility = None if avg_return is None else avg_return - downside - big_loss_rate + (0.10 * big_gain_capture)
    daily = pd.DataFrame({"date": dates[preds], "return": selected_returns, "weight": selected_weights})
    daily_returns = daily.groupby("date").apply(lambda group: np.average(group["return"], weights=group["weight"]), include_groups=False).tolist() if len(daily) else []
    equity = peak = 1.0
    max_drawdown = 0.0
    for value in daily_returns:
        equity *= max(0.0, 1.0 + float(value))
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, 1.0 - (equity / peak))
    return {
        "rows": int(len(frame)),
        "brier_score": round(float(np.sum(weights * ((probs - labels) ** 2))), 6),
        "positive_predictions": int(preds.sum()),
        "positive_rate": round(float(preds.mean()), 6),
        "avg_selected_return": round(avg_return, 6) if avg_return is not None else None,
        "duplicate_weighted_utility": round(utility, 6) if utility is not None else None,
        "big_loss_predictions": int((preds & big_loss).sum()),
        "big_loss_prediction_rate": round(big_loss_rate, 6),
        "big_gain_predictions": int((preds & big_gain).sum()),
        "big_gain_capture_rate": round(big_gain_capture, 6),
        "selected_unique_symbols": int(symbols[preds].nunique()),
        "selected_unique_dates": int(dates[preds].nunique()),
        "top_symbol_date_concentration": round(concentration, 6),
        "max_drawdown": round(max_drawdown, 6),
        "cumulative_selected_return": round(equity - 1.0, 6),
    }


def _supported(metrics: dict[str, Any]) -> bool:
    return bool(
        metrics["positive_predictions"] >= 10
        and metrics["selected_unique_symbols"] >= 5
        and metrics["selected_unique_dates"] >= 5
        and metrics["big_gain_predictions"] > 0
        and metrics["top_symbol_date_concentration"] <= MAX_CONCENTRATION
        and metrics["avg_selected_return"] is not None
        and metrics["avg_selected_return"] > 0.0
    )


def _select(
    frame: pd.DataFrame,
    probs: np.ndarray,
    prediction_factory: Callable[[float], np.ndarray],
    values: tuple[float, ...],
) -> dict[str, Any]:
    search = []
    for value in values:
        preds = prediction_factory(value)
        metrics = _metrics(frame, probs, preds)
        search.append({"parameter": value, "threshold_support_passed": _supported(metrics), **metrics})
    viable = [item for item in search if item["threshold_support_passed"]]
    selected = max(viable, key=lambda item: (item["duplicate_weighted_utility"], item["avg_selected_return"])) if viable else max(search, key=lambda item: item["duplicate_weighted_utility"] if item["duplicate_weighted_utility"] is not None else -999.0)
    return {"selected": selected, "threshold_support_passed": bool(viable), "search": search}


def _bootstrap_delta(frame: pd.DataFrame, candidate_preds: np.ndarray, baseline_preds: np.ndarray, resamples: int = 300) -> dict[str, Any]:
    dates = _event_dates(frame).dt.strftime("%Y-%m-%d").fillna("unknown")
    returns = pd.to_numeric(frame[RETURN_COLUMN], errors="coerce").to_numpy(dtype=float)
    unique_dates = sorted(dates.unique())
    if len(unique_dates) < 2:
        return {"lower_bound": None, "median_delta": None, "passed": False}
    by_date = {}
    for date in unique_dates:
        mask = dates.to_numpy() == date
        candidate = returns[mask & candidate_preds]
        baseline = returns[mask & baseline_preds]
        by_date[date] = (float(candidate.mean()) if len(candidate) else 0.0) - (float(baseline.mean()) if len(baseline) else 0.0)
    rng = np.random.default_rng(20260809)
    deltas = [float(np.mean([by_date[date] for date in rng.choice(unique_dates, size=len(unique_dates), replace=True)])) for _ in range(resamples)]
    lower = float(np.quantile(deltas, 0.05))
    return {"lower_bound": round(lower, 6), "median_delta": round(float(np.median(deltas)), 6), "passed": lower > 0.0, "resamples": resamples}


def _clone(baseline_probs: np.ndarray, baseline_preds: np.ndarray, probs: np.ndarray, preds: np.ndarray) -> dict[str, Any]:
    agreement = float((baseline_preds == preds).mean())
    mae = float(np.mean(np.abs(baseline_probs - probs)))
    return {
        "prediction_agreement": round(agreement, 6),
        "probability_mae": round(mae, 8),
        "candidate_prediction_fingerprint": _fingerprint(probs, preds),
        "baseline_prediction_fingerprint": _fingerprint(baseline_probs, baseline_preds),
        "fingerprints_identical": _fingerprint(probs, preds) == _fingerprint(baseline_probs, baseline_preds),
        "no_op_clone": agreement >= 0.98 and mae <= 0.02,
    }


def _candidate_record(
    name: str,
    recipe: dict[str, Any],
    holdout: pd.DataFrame,
    probs: np.ndarray,
    preds: np.ndarray,
    baseline_probs: np.ndarray,
    baseline_preds: np.ndarray,
) -> dict[str, Any]:
    recipe_hash = _hash(recipe)
    metrics = _metrics(holdout, probs, preds)
    baseline_metrics = _metrics(holdout, baseline_probs, baseline_preds)
    clone = _clone(baseline_probs, baseline_preds, probs, preds)
    bootstrap = _bootstrap_delta(holdout, preds, baseline_preds)
    blocking = []
    if clone["no_op_clone"]:
        blocking.append("candidate is a no-op clone of massive_baseline_model_v1")
    if not _supported(metrics):
        blocking.append("threshold support, return, gain capture, or concentration gate failed")
    candidate_return = metrics.get("avg_selected_return")
    baseline_return = baseline_metrics.get("avg_selected_return")
    materially_higher_return = candidate_return is not None and baseline_return is not None and candidate_return >= baseline_return + 0.02
    selection_gates = {
        "comparison_valid": True,
        "no_op_clone_check_passed": not clone["no_op_clone"],
        "threshold_support_passed": _supported(metrics),
        "avg_selected_return_beats_baseline": candidate_return is not None and baseline_return is not None and candidate_return > baseline_return,
        "duplicate_weighted_utility_beats_baseline": metrics.get("duplicate_weighted_utility") is not None and baseline_metrics.get("duplicate_weighted_utility") is not None and metrics["duplicate_weighted_utility"] > baseline_metrics["duplicate_weighted_utility"],
        "big_loss_rate_acceptable": metrics["big_loss_prediction_rate"] <= baseline_metrics["big_loss_prediction_rate"] or materially_higher_return,
        "big_gain_capture_nonzero": metrics["big_gain_capture_rate"] > 0.0,
        "drawdown_not_worse": metrics["max_drawdown"] <= baseline_metrics["max_drawdown"],
        "ranking_return_not_worse": metrics["cumulative_selected_return"] >= baseline_metrics["cumulative_selected_return"],
        "concentration_passed": metrics["top_symbol_date_concentration"] <= MAX_CONCENTRATION,
        "bootstrap_lower_bound_positive": bool(bootstrap["passed"]),
    }
    return {
        "model_version": name,
        "recipe_hash": recipe_hash,
        "recipe": recipe,
        "target_column": TARGET_COLUMN,
        "evaluation_return_column": RETURN_COLUMN,
        "horizon_days": HORIZON_DAYS,
        "sample_weight_policy": "1 / count(symbol, event_date, endpoint, decision_source)",
        "same_cleaned_holdout_rows": True,
        "metrics": metrics,
        "clone_detection": clone,
        "bootstrap_utility_delta": bootstrap,
        "selection_gates": selection_gates,
        "research_winner": all(selection_gates.values()),
        "promotion_ready": False,
        "routing_allowed": False,
        "research_only": True,
        "promotion_blocking_issues": blocking or ["research-only candidate; promotion disabled"],
    }


def generate(
    train_path: Path,
    test_path: Path,
    all_path: Path,
    baseline_path: Path,
    comparison_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    if not comparison.get("comparison_valid") or not (comparison.get("comparison_scope_report") or {}).get("apples_to_apples_scoring"):
        raise ValueError("next-generation challengers require a valid apples-to-apples Massive baseline comparison")
    train = _load_jsonl(train_path)
    holdout_raw = _load_jsonl(test_path)
    _load_jsonl(all_path)  # Required canonical lineage input; never used for tuning.
    baseline = load_artifact(baseline_path)
    features = list(baseline.feature_columns)
    periods, boundaries = _temporal_train_periods(train)
    fit_raw, calibration_raw, threshold_raw = periods
    fit, [calibration, threshold, holdout], _ = _fill_from_fit(fit_raw, [calibration_raw, threshold_raw, holdout_raw], features)
    base_weights = _duplicate_weights(fit)
    fit_returns = pd.to_numeric(fit[RETURN_COLUMN], errors="coerce").to_numpy(dtype=float)
    threshold_returns = pd.to_numeric(threshold[RETURN_COLUMN], errors="coerce").to_numpy(dtype=float)
    baseline_threshold_probs = predict_proba(baseline, threshold[features].to_numpy(dtype=float))
    baseline_probs = predict_proba(baseline, holdout[features].to_numpy(dtype=float))
    baseline_preds = baseline_probs >= baseline.decision_threshold
    baseline_metrics = _metrics(holdout, baseline_probs, baseline_preds)
    candidates: list[dict[str, Any]] = []

    # 1. Return-adjusted classifier: reward large winners and heavily penalize big losses.
    return_weights = base_weights * (1.0 + (4.0 * np.clip(fit_returns, 0.0, 0.20)) + (5.0 * (fit_returns < -0.03)))
    risk_adjusted_model = _fit_model(fit, calibration, features, pd.to_numeric(fit[TARGET_COLUMN], errors="coerce").to_numpy(), return_weights, "candidate_risk_adjusted_return_v1")
    threshold_probs = predict_proba(risk_adjusted_model, threshold[features].to_numpy(dtype=float))
    selected = _select(threshold, threshold_probs, lambda value: threshold_probs >= value, THRESHOLDS)
    risk_adjusted_model.decision_threshold = float(selected["selected"]["parameter"])
    probs = predict_proba(risk_adjusted_model, holdout[features].to_numpy(dtype=float))
    preds = probs >= risk_adjusted_model.decision_threshold
    candidates.append(_candidate_record("candidate_risk_adjusted_return_v1", {"family": "risk_adjusted_return", "threshold": risk_adjusted_model.decision_threshold, "big_loss_weight": 5.0}, holdout, probs, preds, baseline_probs, baseline_preds))
    candidates[-1]["artifact"] = risk_adjusted_model.to_dict()

    # 2. Explicit big-loss model and lambda-combined final score.
    loss_labels = (fit_returns < -0.03).astype(float)
    risk_model = _fit_model(fit, calibration.assign(**{TARGET_COLUMN: (pd.to_numeric(calibration[RETURN_COLUMN], errors="coerce") < -0.03).astype(float)}), features, loss_labels, base_weights * (1.0 + (4.0 * loss_labels)), "candidate_big_loss_filter_v1-risk")
    threshold_risk = predict_proba(risk_model, threshold[features].to_numpy(dtype=float))
    risk_search = []
    for lam in LAMBDAS:
        score = baseline_threshold_probs - (lam * threshold_risk)
        selection = _select(threshold, score, lambda value, score=score: score >= value, THRESHOLDS)
        risk_search.append({"lambda": lam, "selection": selection, "utility": selection["selected"]["duplicate_weighted_utility"]})
    best_risk = max(risk_search, key=lambda item: item["utility"] if item["utility"] is not None else -999.0)
    holdout_risk = predict_proba(risk_model, holdout[features].to_numpy(dtype=float))
    probs = np.clip(baseline_probs - (best_risk["lambda"] * holdout_risk), 0.0, 1.0)
    risk_threshold = float(best_risk["selection"]["selected"]["parameter"])
    preds = probs >= risk_threshold
    candidates.append(_candidate_record("candidate_big_loss_filter_v1", {"family": "two_stage_big_loss_filter", "lambda": best_risk["lambda"], "threshold": risk_threshold, "lambda_sweep": list(LAMBDAS)}, holdout, probs, preds, baseline_probs, baseline_preds))
    candidates[-1]["artifact"] = {"baseline_model_path": str(baseline_path), "risk_model": risk_model.to_dict(), "lambda": best_risk["lambda"], "decision_threshold": risk_threshold}

    # 3. Big-gain ranker, trained only from in-window derived labels.
    gain_labels = (fit_returns >= 0.03).astype(float)
    gain_model = _fit_model(fit, calibration.assign(**{TARGET_COLUMN: (pd.to_numeric(calibration[RETURN_COLUMN], errors="coerce") >= 0.03).astype(float)}), features, gain_labels, base_weights * (1.0 + (3.0 * gain_labels)), "candidate_big_gain_ranker_v1")
    threshold_gain_probs = predict_proba(gain_model, threshold[features].to_numpy(dtype=float))
    rank_search = []
    threshold_dates = _event_dates(threshold).dt.strftime("%Y-%m-%d")
    for top_k in TOP_K:
        order = pd.DataFrame({"position": np.arange(len(threshold)), "date": threshold_dates.to_numpy(), "prob": threshold_gain_probs}).sort_values(["date", "prob"], ascending=[True, False]).groupby("date").head(top_k)["position"].to_numpy(dtype=int)
        rank_preds = np.zeros(len(threshold), dtype=bool); rank_preds[order] = True
        rank_search.append({"top_k": top_k, **_metrics(threshold, threshold_gain_probs, rank_preds)})
    best_rank = max(rank_search, key=lambda item: item["duplicate_weighted_utility"] if item["duplicate_weighted_utility"] is not None else -999.0)
    probs = predict_proba(gain_model, holdout[features].to_numpy(dtype=float))
    holdout_dates = _event_dates(holdout).dt.strftime("%Y-%m-%d")
    order = pd.DataFrame({"position": np.arange(len(holdout)), "date": holdout_dates.to_numpy(), "prob": probs}).sort_values(["date", "prob"], ascending=[True, False]).groupby("date").head(int(best_rank["top_k"]))["position"].to_numpy(dtype=int)
    preds = np.zeros(len(holdout), dtype=bool); preds[order] = True
    candidates.append(_candidate_record("candidate_big_gain_ranker_v1", {"family": "big_gain_ranker", "top_k": best_rank["top_k"], "top_k_sweep": list(TOP_K)}, holdout, probs, preds, baseline_probs, baseline_preds))
    candidates[-1]["artifact"] = {"ranking_model": gain_model.to_dict(), "top_k_per_day": best_rank["top_k"]}

    # 4. Baseline probability overlay with threshold, margin, and top-k controls.
    overlay_search = []
    for threshold_value in THRESHOLDS:
        for margin in MARGINS:
            for top_k in TOP_K:
                eligible = baseline_threshold_probs >= (threshold_value + margin)
                frame = pd.DataFrame({"position": np.arange(len(threshold)), "date": threshold_dates.to_numpy(), "prob": baseline_threshold_probs, "eligible": eligible})
                order = frame[frame["eligible"]].sort_values(["date", "prob"], ascending=[True, False]).groupby("date").head(top_k)["position"].to_numpy(dtype=int)
                overlay_preds = np.zeros(len(threshold), dtype=bool); overlay_preds[order] = True
                overlay_search.append({"threshold": threshold_value, "margin": margin, "top_k": top_k, **_metrics(threshold, baseline_threshold_probs, overlay_preds)})
    best_overlay = max(overlay_search, key=lambda item: item["duplicate_weighted_utility"] if item["duplicate_weighted_utility"] is not None else -999.0)
    eligible = baseline_probs >= (best_overlay["threshold"] + best_overlay["margin"])
    frame = pd.DataFrame({"position": np.arange(len(holdout)), "date": holdout_dates.to_numpy(), "prob": baseline_probs, "eligible": eligible})
    order = frame[frame["eligible"]].sort_values(["date", "prob"], ascending=[True, False]).groupby("date").head(int(best_overlay["top_k"]))["position"].to_numpy(dtype=int)
    preds = np.zeros(len(holdout), dtype=bool); preds[order] = True
    candidates.append(_candidate_record("candidate_threshold_sweep_v1", {"family": "threshold_abstention_ranking_overlay", "threshold": best_overlay["threshold"], "margin": best_overlay["margin"], "top_k": best_overlay["top_k"]}, holdout, baseline_probs, preds, baseline_probs, baseline_preds))
    candidates[-1]["artifact"] = {"baseline_model_path": str(baseline_path), "decision_overlay": candidates[-1]["recipe"]}

    # 5. Regime-specific threshold policy using only as-of regime columns.
    regime_feature = next((name for name in features if "regime" in name.lower()), None)
    if regime_feature:
        threshold_regime = pd.to_numeric(threshold[regime_feature], errors="coerce").fillna(0.0).to_numpy() > 0.0
        holdout_regime = pd.to_numeric(holdout[regime_feature], errors="coerce").fillna(0.0).to_numpy() > 0.0
    else:
        threshold_spy = threshold["feature_spy_return_5d"] if "feature_spy_return_5d" in threshold.columns else pd.Series(0.0, index=threshold.index)
        holdout_spy = holdout["feature_spy_return_5d"] if "feature_spy_return_5d" in holdout.columns else pd.Series(0.0, index=holdout.index)
        threshold_regime = pd.to_numeric(threshold_spy, errors="coerce").fillna(0.0).to_numpy() >= 0.0
        holdout_regime = pd.to_numeric(holdout_spy, errors="coerce").fillna(0.0).to_numpy() >= 0.0
    regime_search = []
    threshold_regime_score = np.clip(baseline_threshold_probs + np.where(threshold_regime, 0.05, -0.05), 0.0, 1.0)
    for risk_on_threshold in THRESHOLDS:
        for risk_off_threshold in THRESHOLDS:
            if risk_on_threshold == risk_off_threshold:
                continue
            regime_preds = np.where(threshold_regime, threshold_regime_score >= risk_on_threshold, threshold_regime_score >= risk_off_threshold)
            regime_search.append({"risk_on_threshold": risk_on_threshold, "risk_off_threshold": risk_off_threshold, **_metrics(threshold, threshold_regime_score, regime_preds)})
    best_regime = max(regime_search, key=lambda item: item["duplicate_weighted_utility"] if item["duplicate_weighted_utility"] is not None else -999.0)
    probs = np.clip(baseline_probs + np.where(holdout_regime, 0.05, -0.05), 0.0, 1.0)
    preds = np.where(holdout_regime, probs >= best_regime["risk_on_threshold"], probs >= best_regime["risk_off_threshold"])
    candidates.append(_candidate_record("candidate_regime_split_v1", {"family": "regime_split_threshold_policy", "regime_feature": regime_feature or "feature_spy_return_5d", "risk_on_score_adjustment": 0.05, "risk_off_score_adjustment": -0.05, "risk_on_threshold": best_regime["risk_on_threshold"], "risk_off_threshold": best_regime["risk_off_threshold"]}, holdout, probs, preds, baseline_probs, baseline_preds))
    candidates[-1]["artifact"] = {"baseline_model_path": str(baseline_path), "regime_overlay": candidates[-1]["recipe"]}

    baseline_hash = str((baseline.lineage or {}).get("recipe_hash") or "")
    for candidate in candidates:
        if candidate["recipe_hash"] == baseline_hash:
            raise AssertionError(f"{candidate['model_version']} duplicated baseline recipe hash")
        if candidate["clone_detection"]["no_op_clone"]:
            raise ValueError(f"{candidate['model_version']} is a no-op clone; generation aborted before publishing reports")
    output_dir.mkdir(parents=True, exist_ok=True)
    for candidate in candidates:
        (output_dir / f"{candidate['model_version']}.json").write_text(json.dumps(candidate, indent=2), encoding="utf-8")
    reports = {
        "risk_adjusted_challenger_report.json": candidates[0],
        "big_loss_filter_report.json": {**candidates[1], "lambda_search": risk_search},
        "big_gain_ranker_report.json": {**candidates[2], "ranking_search": rank_search},
        "threshold_overlay_report.json": {**candidates[3], "overlay_search": overlay_search},
        "regime_split_report.json": {**candidates[4], "regime_search": regime_search},
    }
    for filename, payload in reports.items():
        (output_dir / filename).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        (output_dir.parent / filename).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    manifest = {
        "schema_version": "track-b-next-generation-challengers.v1",
        "comparison_valid": True,
        "comparator_kind": "massive_baseline_model_v1",
        "baseline_model_path": str(baseline_path),
        "training_inputs": {"train": str(train_path), "test": str(test_path), "all_cleaned": str(all_path)},
        "same_cleaned_holdout_rows": True,
        "target_column": TARGET_COLUMN,
        "evaluation_return_column": RETURN_COLUMN,
        "horizon_days": HORIZON_DAYS,
        "sample_weight_policy": "1 / count(symbol, event_date, endpoint, decision_source)",
        "temporal_boundaries": boundaries,
        "baseline_metrics": baseline_metrics,
        "challengers": candidates,
        "promotion_allowed": False,
        "routing_allowed": False,
    }
    (output_dir / "next_generation_challenger_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (output_dir.parent / "next_generation_challenger_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    metric_fields = (
        "avg_selected_return",
        "duplicate_weighted_utility",
        "big_loss_prediction_rate",
        "big_gain_capture_rate",
        "max_drawdown",
    )
    leaderboard = {
        "baseline": {
            "model_version": "massive_baseline_model_v1",
            **{field: baseline_metrics.get(field) for field in metric_fields},
            "clone_status": False,
            "promotion_ready": False,
            "research_winner": False,
        },
        "challengers": [
            {
                "model_version": item["model_version"],
                **{field: item["metrics"].get(field) for field in metric_fields},
                "clone_status": bool(item["clone_detection"]["no_op_clone"]),
                "promotion_ready": bool(item["promotion_ready"]),
                "research_winner": bool(item["research_winner"]),
            }
            for item in candidates
        ],
    }
    scoreboard = {
        "comparison_valid": True,
        "apples_to_apples_scoring": True,
        "comparator_kind": "massive_baseline_model_v1",
        "baseline_metrics": baseline_metrics,
        "challengers": candidates,
        "materially_different_candidates": [item["model_version"] for item in candidates if not item["clone_detection"]["no_op_clone"]],
        "leaderboard": leaderboard,
        "promotion_allowed": False,
        "ready_for_live_routing": False,
    }
    (output_dir / "challenger_vs_massive_baseline_report.json").write_text(json.dumps(scoreboard, indent=2), encoding="utf-8")
    (output_dir.parent / "challenger_vs_massive_baseline_report.json").write_text(json.dumps(scoreboard, indent=2), encoding="utf-8")
    threshold_support_path = output_dir.parent / "threshold_support_report.json"
    if threshold_support_path.exists():
        threshold_support_report = json.loads(threshold_support_path.read_text(encoding="utf-8"))
    else:
        threshold_support_report = {"models": []}
    legacy_models = [
        item for item in threshold_support_report.get("models", [])
        if item.get("source") != "next_generation"
    ]
    next_generation_support = [
        {
            "model_version": item["model_version"],
            "source": "next_generation",
            "threshold_support_passed": bool(item["selection_gates"]["threshold_support_passed"]),
            "positive_predictions": item["metrics"]["positive_predictions"],
            "selected_unique_symbols": item["metrics"]["selected_unique_symbols"],
            "selected_unique_dates": item["metrics"]["selected_unique_dates"],
            "big_gain_predictions": item["metrics"]["big_gain_predictions"],
            "top_symbol_date_concentration": item["metrics"]["top_symbol_date_concentration"],
        }
        for item in candidates
    ]
    threshold_support_report["models"] = [*legacy_models, *next_generation_support]
    threshold_support_report["next_generation_challengers"] = next_generation_support
    threshold_support_path.write_text(json.dumps(threshold_support_report, indent=2), encoding="utf-8")
    (output_dir / "threshold_support_report.json").write_text(json.dumps(threshold_support_report, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", default="data/track_b/training_quality/cleaned_train.jsonl")
    parser.add_argument("--test", default="data/track_b/training_quality/cleaned_test.jsonl")
    parser.add_argument("--all-cleaned", default="data/track_b/training_quality/cleaned_all.jsonl")
    parser.add_argument("--baseline-model", default="data/track_b/massive_baseline_model_v1.json")
    parser.add_argument("--comparison-report", default="data/track_b/model_comparison_track_b.json")
    parser.add_argument("--output-dir", default="data/track_b/next_generation")
    args = parser.parse_args()
    manifest = generate(Path(args.train), Path(args.test), Path(args.all_cleaned), Path(args.baseline_model), Path(args.comparison_report), Path(args.output_dir))
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
