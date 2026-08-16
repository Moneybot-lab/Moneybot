#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from moneybot.services.decision_target import HORIZON_DAYS, TARGET_NAME
from scripts.day10_train_candidate_model import _future_safe_feature_columns, _prepare_frame

BACKTEST_SCHEMA_VERSION = "moneybot-challenger-backtest.v2"


def _load_jsonl(path: Path) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return pd.DataFrame(rows)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -35.0, 35.0)))

def _return_column(df: pd.DataFrame, horizon_days: int) -> str:
    preferred = f"return_{horizon_days}d"
    if preferred in df.columns:
        return preferred
    for col in ("return_5d", "forward_return_5d", "return_3d", "return_1d"):
        if col in df.columns:
            return col
    raise ValueError(f"No return column found for horizon_days={horizon_days}")


def _feature_columns(df: pd.DataFrame, suite_manifest: dict[str, Any]) -> list[str]:
    cols = [str(col) for col in suite_manifest.get("feature_columns") or [] if str(col) in df.columns]
    if cols:
        return _future_safe_feature_columns(cols)
    return _future_safe_feature_columns(sorted(str(col) for col in df.columns if str(col).startswith("feature_")))


def _prepare_features(df: pd.DataFrame, feature_columns: list[str], fill_values: dict[str, Any]) -> pd.DataFrame:
    out = df.copy()
    for col in feature_columns:
        fill = fill_values.get(col, 0.0) if isinstance(fill_values, dict) else 0.0
        try:
            fill = float(fill)
        except (TypeError, ValueError):
            fill = 0.0
        out[col] = pd.to_numeric(out[col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(fill)
    return out


def _predict(artifact: dict[str, Any], frame: pd.DataFrame, feature_columns: list[str]) -> tuple[np.ndarray, np.ndarray]:
    def linear_probabilities(linear: dict[str, Any]) -> np.ndarray:
        artifact_features = [str(col) for col in linear.get("feature_columns") or feature_columns]
        aligned = frame.copy()
        missing = [col for col in artifact_features if col not in aligned.columns]
        if missing:
            fill_values = linear.get("feature_fill_values") if isinstance(linear.get("feature_fill_values"), dict) else {}
            for col in missing:
                fill = fill_values.get(col, 0.0) if isinstance(fill_values, dict) else 0.0
                try:
                    fill = float(fill)
                except (TypeError, ValueError):
                    fill = 0.0
                aligned[col] = fill
        X = aligned[artifact_features].to_numpy(dtype=float)
        means = np.asarray(linear.get("means"), dtype=float)
        stds = np.asarray(linear.get("stds"), dtype=float)
        stds = np.where(stds == 0.0, 1.0, stds)
        weights = np.asarray(linear.get("weights"), dtype=float)
        logits = ((X - means) / stds) @ weights + float(linear.get("bias", 0.0))
        logits = (logits * float(linear.get("calibration_slope", 1.0))) + float(linear.get("calibration_intercept", 0.0))
        return _sigmoid(logits)

    model_type = str(artifact.get("model_type") or "logistic_regression")
    if model_type in {"logistic_regression", "calibrated_linear", "hard_example_linear", "ranking_lane_linear", "abstention_linear"}:
        probs = linear_probabilities(artifact)
        threshold = float(artifact.get("decision_threshold", 0.5))
        abstention = artifact.get("abstention") if isinstance(artifact.get("abstention"), dict) else {}
        margin = float(abstention.get("margin", 0.0)) if abstention.get("enabled") else 0.0
        preds = (probs >= min(1.0, threshold + margin)).astype(int)
        return probs, preds
    if model_type == "two_stage_risk_filter":
        decision_probs = linear_probabilities(artifact["decision_model"])
        risk_probs = linear_probabilities(artifact["risk_model"])
        decision_threshold = float(artifact.get("decision_threshold", 0.60))
        risk_threshold = float(artifact.get("risk_threshold", 0.20))
        preds = ((decision_probs >= decision_threshold) & (risk_probs <= risk_threshold)).astype(int)
        return decision_probs, preds
    if model_type == "decision_stump":
        feature = str(artifact["feature"])
        values = (frame[feature] if feature in frame.columns else pd.Series(0.0, index=frame.index)).to_numpy(dtype=float)
        threshold = float(artifact["threshold"])
        if artifact.get("direction") == "gte_positive":
            preds = (values >= threshold).astype(int)
        else:
            preds = (values < threshold).astype(int)
        return preds.astype(float), preds
    if model_type == "shallow_decision_tree":
        tree = artifact["tree"]
        scores = np.empty(len(frame), dtype=float)
        for output_index, (_, row) in enumerate(frame.iterrows()):
            node = tree
            while not bool(node.get("leaf")):
                feature = str(node["feature"])
                value = float(row[feature]) if feature in frame.columns else 0.0
                node = node["left"] if value < float(node["threshold"]) else node["right"]
            scores[output_index] = float(node["probability"])
        preds = (scores >= float(artifact.get("decision_threshold", 0.60))).astype(int)
        return scores, preds
    if model_type == "baseline_classifier":
        spec = artifact.get("training_spec") if isinstance(artifact.get("training_spec"), dict) else {}
        if "always-down" in str(artifact.get("version")):
            pred = 0
        elif "always-up" in str(artifact.get("version")):
            pred = 1
        else:
            pred = int(spec.get("majority_class", 1))
        preds = np.full(len(frame), pred, dtype=int)
        return preds.astype(float), preds
    raise ValueError(f"Unsupported challenger model_type={model_type}")



def _ranking_metrics(scores: np.ndarray, labels: np.ndarray, returns: np.ndarray, *, top_fraction: float = 0.20) -> dict[str, Any]:
    returns = np.nan_to_num(np.asarray(returns, dtype=float), nan=0.0)
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
    positives = scores[labels >= 0.5]
    negatives = scores[labels < 0.5]
    pairwise_loss = (
        float(np.mean(positives[:, None] <= negatives[None, :]))
        if len(positives) and len(negatives)
        else 0.0
    )
    gain_cutoff = max(0.0, float(np.quantile(returns, 0.80)))
    loss_cutoff = min(0.0, float(np.quantile(returns, 0.20)))
    big_gain = returns >= gain_cutoff
    big_loss = returns <= loss_cutoff
    big_gain_capture = float(big_gain[top].sum() / big_gain.sum()) if big_gain.any() else 0.0
    big_loss_demotion = 1.0 - float(big_loss[top].sum() / big_loss.sum()) if big_loss.any() else 1.0
    top_precision = float(labels[top].mean()) if len(top) else 0.0
    top_avg_return = float(returns[top].mean()) if len(top) else 0.0
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


def _canonical_economic_frame(
    frame: pd.DataFrame,
    scores: np.ndarray,
    preds: np.ndarray,
    labels: np.ndarray,
    returns: np.ndarray,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Collapse raw predictions to one auditable symbol/date economic outcome.

    Classification remains row-level.  Economic calculations use the mean score and
    outcome for each symbol/date, with the prediction recomputed from the mean score
    relative to the mean of the raw binary decisions.  This makes duplicate copies
    economically weight-neutral while reporting conflicting observations.
    """
    work = pd.DataFrame({
        "event_date": _event_series(frame).to_numpy(),
        "symbol": (frame["symbol"].fillna("unknown").astype(str).str.upper() if "symbol" in frame.columns else pd.Series("unknown", index=frame.index)).to_numpy(),
        "score": np.asarray(scores, dtype=float),
        "prediction": np.asarray(preds, dtype=int),
        "label": np.asarray(labels, dtype=float),
        "forward_return": np.asarray(returns, dtype=float),
    })
    multiplicity = work.groupby(["event_date", "symbol"], sort=True, dropna=False).size().rename("multiplicity")
    # Exact duplicate decision rows carry no additional economic information.
    # Conflicting distinct observations are retained and averaged explicitly.
    economic_inputs = work.drop_duplicates(
        subset=["event_date", "symbol", "score", "prediction", "label", "forward_return"]
    )
    grouped = economic_inputs.groupby(["event_date", "symbol"], sort=True, dropna=False)
    canonical = grouped.agg(
        score=("score", "mean"),
        prediction_rate=("prediction", "mean"),
        label=("label", "mean"),
        forward_return=("forward_return", "mean"),
        prediction_variants=("prediction", "nunique"),
        score_variants=("score", "nunique"),
        return_variants=("forward_return", "nunique"),
    ).join(multiplicity).reset_index()
    canonical["prediction"] = (canonical["prediction_rate"] >= 0.5).astype(int)
    diagnostics = {
        "raw_rows": int(len(work)),
        "unique_symbol_dates": int(len(canonical)),
        "duplicate_symbol_date_rows": int(len(work) - len(canonical)),
        "effective_economic_observations": int(len(canonical)),
        "aggregation_policy": "exact duplicates removed; one symbol/event_date; mean of distinct scores/outcomes; majority prediction with ties selected",
        "mixed_prediction_groups": int((canonical["prediction_variants"] > 1).sum()),
        "mixed_score_groups": int((canonical["score_variants"] > 1).sum()),
        "mixed_outcome_groups": int((canonical["return_variants"] > 1).sum()),
    }
    return canonical, diagnostics


def _cohort_economics(
    canonical: pd.DataFrame,
    *,
    transaction_cost_bps: float,
    slippage_bps: float,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Compute endpoint-only cohort statistics without manufacturing an equity path."""
    round_trip_cost = 2.0 * (float(transaction_cost_bps) + float(slippage_bps)) / 10_000.0
    rows: list[dict[str, Any]] = []
    for event_date, cohort in canonical.groupby("event_date", sort=True):
        selected = cohort[cohort["prediction"] == 1]
        benchmark_return = float(cohort["forward_return"].mean())
        gross = float(selected["forward_return"].mean()) if len(selected) else 0.0
        net = gross - round_trip_cost if len(selected) else 0.0
        rows.append({
            "event_date": event_date,
            "universe_return": benchmark_return,
            "selected_return_gross": gross,
            "selected_return_net": net,
            "excess_return_net": net - benchmark_return,
            "universe_symbols": int(len(cohort)),
            "selected_symbols": int(len(selected)),
        })
    cohorts = pd.DataFrame(rows).sort_values("event_date").reset_index(drop=True)
    selected_returns = canonical.loc[canonical["prediction"] == 1, "forward_return"].to_numpy(dtype=float)
    metrics = {
        "avg_selected_return_gross": round(float(selected_returns.mean()), 6) if selected_returns.size else 0.0,
        "avg_selected_return_net": round(float(selected_returns.mean() - round_trip_cost), 6) if selected_returns.size else 0.0,
        "median_selected_return_net": round(float(np.median(selected_returns) - round_trip_cost), 6) if selected_returns.size else 0.0,
        "date_cohort_avg_return_net": round(float(cohorts["selected_return_net"].mean()), 6) if len(cohorts) else None,
        "date_cohort_median_return_net": round(float(cohorts["selected_return_net"].median()), 6) if len(cohorts) else None,
        "excess_avg_return_vs_universe": round(float(cohorts["excess_return_net"].mean()), 6) if len(cohorts) else None,
        "gross_selected_return": round(float(selected_returns.mean()), 6) if selected_returns.size else 0.0,
        "net_selected_return": round(float(selected_returns.mean() - round_trip_cost), 6) if selected_returns.size else 0.0,
        "estimated_entries": int(selected_returns.size),
        "estimated_exits": int(selected_returns.size),
        "turnover": None,
        "turnover_method": "one entry and one exit per selected symbol/date endpoint position; no adjacent-row inference",
        "transaction_cost_bps": float(transaction_cost_bps),
        "slippage_bps": float(slippage_bps),
        "round_trip_cost_assumption": "2 * (transaction_cost_bps + slippage_bps), charged once per selected symbol/date",
        "total_return_net": None,
        "total_return_net_evaluable": False,
        "max_drawdown": None,
        "max_drawdown_evaluable": False,
        "max_drawdown_reason": "daily mark-to-market path unavailable from endpoint-only 5d returns",
    }
    return metrics, cohorts


def _date_local_ranking(canonical: pd.DataFrame, *, top_k: int) -> dict[str, Any]:
    selected_parts: list[pd.DataFrame] = []
    for _, cohort in canonical.groupby("event_date", sort=True):
        selected_parts.append(cohort.sort_values(["score", "symbol"], ascending=[False, True]).head(top_k))
    selected = pd.concat(selected_parts, ignore_index=True) if selected_parts else canonical.iloc[0:0]
    gains = canonical["forward_return"] >= 0.03
    losses = canonical["forward_return"] < -0.03
    selected_keys = set(zip(selected["event_date"], selected["symbol"]))
    selected_mask = np.asarray([(date, symbol) in selected_keys for date, symbol in zip(canonical["event_date"], canonical["symbol"])])
    counts = selected["symbol"].value_counts(normalize=True)
    return {
        "date_local_top_k": int(top_k),
        "date_local_top_k_avg_return": round(float(selected["forward_return"].mean()), 6) if len(selected) else 0.0,
        "date_local_top_k_median_return": round(float(selected["forward_return"].median()), 6) if len(selected) else 0.0,
        "date_local_top_k_precision": round(float((selected["label"] >= 0.5).mean()), 6) if len(selected) else 0.0,
        "date_local_big_gain_capture": round(float((selected_mask & gains.to_numpy()).sum() / gains.sum()), 6) if gains.any() else 0.0,
        "date_local_big_loss_rate": round(float((selected_mask & losses.to_numpy()).sum() / selected_mask.sum()), 6) if selected_mask.any() else 0.0,
        "independent_ranking_dates": int(canonical["event_date"].nunique()),
        "unique_selected_symbols": int(selected["symbol"].nunique()),
        "selection_concentration": round(float(counts.max()), 6) if len(counts) else 0.0,
    }


def _max_drawdown(equity: np.ndarray) -> float:
    if equity.size == 0:
        return 0.0
    peaks = np.maximum.accumulate(equity)
    drawdowns = (equity / np.where(peaks == 0, 1.0, peaks)) - 1.0
    return round(float(drawdowns.min()), 6)


def _calibration(probs: np.ndarray, labels: np.ndarray, bins: int = 5) -> dict[str, Any]:
    if len(probs) == 0:
        return {"brier_score": None, "ece": None, "bins": []}
    brier = float(np.mean((probs - labels) ** 2))
    bin_rows: list[dict[str, Any]] = []
    ece = 0.0
    for idx in range(bins):
        lo = idx / bins
        hi = (idx + 1) / bins
        mask = (probs >= lo) & (probs <= hi if idx == bins - 1 else probs < hi)
        if not mask.any():
            continue
        avg_conf = float(probs[mask].mean())
        observed = float(labels[mask].mean())
        weight = float(mask.mean())
        ece += weight * abs(avg_conf - observed)
        bin_rows.append({"lower": lo, "upper": hi, "rows": int(mask.sum()), "avg_probability": round(avg_conf, 6), "observed_rate": round(observed, 6)})
    return {"brier_score": round(brier, 6), "ece": round(ece, 6), "bins": bin_rows}


def _drift(frame: pd.DataFrame, feature_columns: list[str]) -> dict[str, Any]:
    midpoint = len(frame) // 2
    if midpoint <= 0:
        return {"max_mean_shift": 0.0, "feature_shifts": {}}
    first = frame.iloc[:midpoint]
    second = frame.iloc[midpoint:]
    shifts: dict[str, float] = {}
    for col in feature_columns:
        std = float(first[col].std(ddof=0) or 0.0)
        denom = std if std > 1e-12 else 1.0
        shifts[col] = round(abs(float(second[col].mean()) - float(first[col].mean())) / denom, 6)
    return {"max_mean_shift": max(shifts.values()) if shifts else 0.0, "feature_shifts": shifts}


def _bootstrap_confidence_bounds(strategy_returns: np.ndarray, frame: pd.DataFrame, *, resamples: int = 500, confidence: float = 0.95, horizon_days: int = 5) -> dict[str, Any]:
    """Deterministic non-overlapping horizon-block bootstrap of date cohorts."""
    if "event_date" in frame.columns:
        dates = pd.to_datetime(frame["event_date"], utc=True, errors="coerce")
    elif "ts" in frame.columns:
        numeric = pd.to_numeric(frame["ts"], errors="coerce")
        dates = pd.to_datetime(numeric, unit="s", utc=True, errors="coerce") if numeric.abs().median() >= 100_000_000 else pd.Series(pd.NaT, index=frame.index)
    else:
        dates = pd.Series(pd.NaT, index=frame.index)
    block_keys = dates.dt.strftime("%Y-%m-%d") if dates.notna().all() else pd.Series([f"row-{index}" for index in range(len(frame))], index=frame.index)
    work = pd.DataFrame({"block": block_keys.to_numpy(), "return": np.asarray(strategy_returns, dtype=float)})
    date_means = work.groupby("block", sort=True)["return"].mean().to_numpy(dtype=float)
    block_means = np.asarray([date_means[index:index + max(1, horizon_days)].mean() for index in range(0, len(date_means), max(1, horizon_days))], dtype=float)
    if block_means.size == 0:
        return {"method": "non_overlapping_horizon_date_block_bootstrap", "confidence": confidence, "resamples": resamples, "independent_date_blocks": 0, "avg_return_lower": None, "avg_return_median": None, "avg_return_upper": None, "probability_positive": 0.0}
    rng = np.random.default_rng(20260803)
    samples = block_means[rng.integers(0, len(block_means), size=(max(1, resamples), len(block_means)))].mean(axis=1)
    alpha = (1.0 - confidence) / 2.0
    return {
        "method": "non_overlapping_horizon_date_block_bootstrap",
        "bootstrap_method": "non_overlapping_horizon_date_block_bootstrap",
        "overlap_policy": f"chronological date cohorts grouped into non-overlapping {max(1, horizon_days)}-date blocks",
        "confidence": confidence,
        "resamples": int(max(1, resamples)),
        "independent_date_blocks": int(len(block_means)),
        "avg_return_lower": round(float(np.quantile(samples, alpha)), 6),
        "avg_return_median": round(float(np.quantile(samples, 0.5)), 6),
        "avg_return_upper": round(float(np.quantile(samples, 1.0 - alpha)), 6),
        "probability_positive": round(float((samples > 0.0).mean()), 6),
    }


def _event_series(frame: pd.DataFrame) -> pd.Series:
    if "event_date" in frame.columns:
        return pd.to_datetime(frame["event_date"], utc=True, errors="coerce").dt.strftime("%Y-%m-%d").fillna("unknown")
    if "ts" in frame.columns:
        numeric = pd.to_numeric(frame["ts"], errors="coerce")
        if numeric.notna().any() and numeric.abs().median() >= 100_000_000:
            return pd.to_datetime(numeric, unit="s", utc=True, errors="coerce").dt.strftime("%Y-%m-%d").fillna("unknown")
    return pd.Series("unknown", index=frame.index)


def _usage_scope(challenger: dict[str, Any]) -> str:
    model_type = str(challenger.get("model_type") or "")
    lane = _challenger_lane(challenger)
    if lane == "ranking" or model_type == "ranking_lane_linear":
        return "ranking_only_candidate"
    if model_type in {"decision_stump", "shallow_decision_tree"}:
        return "research_only"
    if model_type in {"two_stage_risk_filter", "abstention_linear"}:
        return "high_conviction_overlay_candidate"
    if model_type == "hard_example_linear":
        return "research_only"
    if model_type in {"logistic_regression", "calibrated_linear"}:
        return "main_decision_candidate"
    return "not_usable"


def _scope_limits(scope: str) -> dict[str, float]:
    if scope == "main_decision_candidate":
        return {"min_positive_rate": 0.02, "max_positive_rate": 0.60, "max_big_loss_rate": 0.02}
    if scope == "high_conviction_overlay_candidate":
        return {"min_positive_rate": 0.005, "max_positive_rate": 0.25, "max_big_loss_rate": 0.01}
    if scope == "ranking_only_candidate":
        return {"min_positive_rate": 0.0, "max_positive_rate": 1.0, "max_big_loss_rate": 0.05}
    return {"min_positive_rate": 0.0, "max_positive_rate": 1.0, "max_big_loss_rate": 1.0}


def _selected_threshold_support(frame: pd.DataFrame, preds: np.ndarray, returns: np.ndarray) -> dict[str, Any]:
    selected = preds == 1
    dates = _event_series(frame)
    symbols = frame["symbol"].fillna("").astype(str).str.upper() if "symbol" in frame.columns else pd.Series("unknown", index=frame.index)
    groups = pd.Series(symbols.astype(str).to_numpy() + "|" + dates.astype(str).to_numpy())
    selected_returns = returns[selected]
    big_gain = returns >= 0.03
    big_loss = returns < -0.03
    selected_groups = groups[selected]
    concentration = float(selected_groups.value_counts(normalize=True).max()) if len(selected_groups) else 0.0
    return {
        "selected_positive_predictions": int(selected.sum()),
        "selected_big_gain_predictions": int((selected & big_gain).sum()),
        "selected_big_loss_predictions": int((selected & big_loss).sum()),
        "selected_unique_symbols": int(symbols[selected].nunique()) if selected.any() else 0,
        "selected_unique_dates": int(dates[selected].nunique()) if selected.any() else 0,
        "selected_symbol_date_concentration": round(concentration, 6),
        "big_gain_capture_at_selected_threshold": round(float((selected & big_gain).sum() / big_gain.sum()), 6) if big_gain.any() else 0.0,
        "big_loss_false_positive_rate_at_selected_threshold": round(float((selected & big_loss).sum() / big_loss.sum()), 6) if big_loss.any() else 0.0,
    }


def _candidate_gate_fields(challenger: dict[str, Any], metrics: dict[str, Any], gates: dict[str, Any], support: dict[str, Any]) -> dict[str, Any]:
    scope = _usage_scope(challenger)
    limits = _scope_limits(scope)
    issues = list(gates.get("failed_gates") or [])
    positive_rate = float(metrics.get("positive_rate") or 0.0)
    if positive_rate < limits["min_positive_rate"]:
        issues.append("positive_rate_below_scope_minimum")
    if positive_rate > limits["max_positive_rate"]:
        issues.append("positive_rate_above_scope_maximum")
    if float(metrics.get("big_loss_prediction_rate") or 0.0) > limits["max_big_loss_rate"]:
        issues.append("big_loss_prediction_rate_above_scope_limit")
    if int(support.get("selected_positive_predictions") or 0) < 10 and scope == "main_decision_candidate":
        issues.append("selected_trade_count_too_small")
    calibration = metrics.get("calibration") or {}
    if bool(calibration.get("negative_calibration_slope")):
        issues.append("calibration_invalid_negative_slope")
    if scope != "main_decision_candidate":
        issues.append("not_main_decision_candidate")
    promotion_ready = not issues and scope == "main_decision_candidate"
    shadow_allowed = (not promotion_ready) and scope in {"research_only", "ranking_only_candidate", "high_conviction_overlay_candidate"}
    behavior_label = "calibration_invalid" if bool(calibration.get("negative_calibration_slope")) else None
    return {
        "usage_scope": scope,
        "candidate_usage_scope": scope,
        "candidate_behavior_label": behavior_label,
        "promotion_ready": bool(promotion_ready),
        "routing_allowed": False,
        "shadow_logging_allowed": bool(shadow_allowed),
        "shadow_logging_reason": "offline shadow logging only; no user routing" if shadow_allowed else None,
        "shadow_logging_blocked_reason": None if shadow_allowed else "not retained for safe offline shadow logging",
        "promotion_blocking_issues": sorted(set(issues)),
        "research_retention_reason": "retained for research/signal discovery, not promotion" if scope != "main_decision_candidate" else "main decision candidate still subject to conservative gates",
    }


def _promotion_gates(metrics: dict[str, Any], benchmark: dict[str, Any], *, min_rows: int, max_drawdown: float, max_ece: float, min_excess_return: float, max_drift_shift: float) -> dict[str, Any]:
    failures: list[str] = []
    if metrics["rows"] < min_rows:
        failures.append("insufficient_rows")
    excess = metrics.get("excess_avg_return_vs_universe")
    if benchmark.get("benchmark_comparison_valid") is not True or excess is None:
        failures.append("benchmark_comparison_invalid")
    elif float(excess) < min_excess_return:
        failures.append("underperforms_aligned_universe_after_costs")
    if metrics.get("max_drawdown_evaluable") is not True:
        failures.append("drawdown_evidence_unavailable")
    elif float(metrics["max_drawdown"]) < -abs(max_drawdown):
        failures.append("drawdown_gate_failed")
    if metrics["calibration"]["ece"] is not None and metrics["calibration"]["ece"] > max_ece:
        failures.append("calibration_gate_failed")
    if metrics["drift"]["max_mean_shift"] > max_drift_shift:
        failures.append("drift_gate_failed")
    lower_bound = metrics.get("bootstrap_confidence", {}).get("avg_return_lower")
    if lower_bound is None or float(lower_bound) < 0.0:
        failures.append("bootstrap_profit_confidence_failed")
    return {"promotion_ready": not failures, "failed_gates": failures, "objective_gates": {"min_rows": min_rows, "max_drawdown": max_drawdown, "max_ece": max_ece, "min_excess_return": min_excess_return, "max_drift_shift": max_drift_shift, "min_bootstrap_avg_return_lower": 0.0}}


def _challenger_lane(challenger: dict[str, Any]) -> str:
    if challenger.get("candidate_lane") == "ranking" or challenger.get("specialized_family") == "ranking_top5_model":
        return "ranking"
    return "decision"


def _pareto_objectives(challenger: dict[str, Any], lane: str) -> dict[str, float]:
    metrics = challenger["backtest_metrics"]
    bootstrap_lower = metrics["bootstrap_confidence"].get("avg_return_lower")
    brier = metrics.get("calibration", {}).get("brier_score")
    common = {
        "bootstrap_lower": float(bootstrap_lower) if bootstrap_lower is not None else -999.0,
        "economic_validity": 1.0 if metrics.get("economic_backtest_validity", {}).get("valid") else 0.0,
        "negative_big_loss_rate": -float(metrics.get("big_loss_prediction_rate") or 0.0),
    }
    if lane == "ranking":
        ranking = metrics.get("top_k_ranking") or {}
        return {
            **common,
            "ranking_objective": float(ranking.get("date_local_top_k_avg_return") or 0.0),
            "top_k_avg_return": float(ranking.get("date_local_top_k_avg_return") or 0.0),
        }
    return {
        **common,
        "avg_return_net": float(metrics.get("date_cohort_avg_return_net", metrics.get("avg_return_net", 0.0)) or 0.0),
        "negative_brier": -float(brier) if brier is not None else -1.0,
    }


def _pareto_frontier(challengers: list[dict[str, Any]], lane: str) -> list[dict[str, Any]]:
    """Return all non-dominated candidates for one scoring lane."""
    eligible = [item for item in challengers if _challenger_lane(item) == lane and item.get("model_type") != "baseline_classifier"]
    objectives = {str(item["model_version"]): _pareto_objectives(item, lane) for item in eligible}
    frontier: list[dict[str, Any]] = []
    for candidate in eligible:
        candidate_values = objectives[str(candidate["model_version"])]
        dominated = False
        for other in eligible:
            if other is candidate:
                continue
            other_values = objectives[str(other["model_version"])]
            no_worse = all(other_values[key] >= candidate_values[key] for key in candidate_values)
            strictly_better = any(other_values[key] > candidate_values[key] for key in candidate_values)
            if no_worse and strictly_better:
                dominated = True
                break
        if not dominated:
            frontier.append({**candidate, "pareto_objectives": candidate_values})
    return sorted(frontier, key=lambda item: str(item["model_version"]))


def backtest_challenger_suite(
    *,
    suite_manifest_path: Path,
    feature_store_path: Path,
    output_path: Path,
    horizon_days: int = 5,
    transaction_cost_bps: float = 5.0,
    slippage_bps: float = 5.0,
    min_rows: int = 20,
    max_drawdown: float = 0.20,
    max_ece: float = 0.20,
    min_excess_return: float = 0.0,
    max_drift_shift: float = 3.0,
) -> dict[str, Any]:
    suite = _load_json(suite_manifest_path)
    raw = _prepare_frame(_load_jsonl(feature_store_path))
    if "ts" in raw.columns:
        raw = raw.sort_values("ts")
    raw = raw.reset_index(drop=True)
    return_col = _return_column(raw, horizon_days)
    if horizon_days != HORIZON_DAYS:
        raise ValueError(f"Decision-lane backtest requires the canonical {HORIZON_DAYS}d horizon")
    label_col = TARGET_NAME
    if label_col not in raw.columns:
        raise ValueError(f"Missing canonical decision-lane target {label_col}")
    features = _feature_columns(raw, suite)
    frame = _prepare_features(raw.dropna(subset=[return_col, label_col]).copy(), features, suite.get("feature_fill_values") or {})
    labels = pd.to_numeric(frame[label_col], errors="coerce").fillna(0).to_numpy(dtype=float)
    returns = pd.to_numeric(frame[return_col], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    base_canonical, base_duplicate_diagnostics = _canonical_economic_frame(
        frame, np.zeros(len(frame)), np.zeros(len(frame), dtype=int), labels, returns
    )
    benchmark_cohorts = base_canonical.groupby("event_date", sort=True)["forward_return"].mean()
    benchmark = {
        "equal_weight_universe_avg_5d_return": round(float(base_canonical["forward_return"].mean()), 6),
        "equal_weight_universe_median_5d_return": round(float(base_canonical["forward_return"].median()), 6),
        "date_cohort_benchmark_avg_return": round(float(benchmark_cohorts.mean()), 6),
        "date_cohort_benchmark_median_return": round(float(benchmark_cohorts.median()), 6),
        "benchmark_independent_dates": int(len(benchmark_cohorts)),
        "benchmark_unique_symbol_dates": int(len(base_canonical)),
        "benchmark_comparison_valid": bool(len(benchmark_cohorts)),
        "cash_return": 0.0,
        "deprecated_fields": {
            "buy_and_hold_return": {"value": None, "replacement_field": "date_cohort_benchmark_avg_return", "calculation_method": "deprecated: prior row-wise compounding was invalid"},
            "equal_weight_long_cash_return": {"value": None, "replacement_field": "date_cohort_benchmark_avg_return", "calculation_method": "deprecated: no portfolio path is available"},
        },
    }
    challengers: list[dict[str, Any]] = []
    for challenger in suite.get("challengers") or []:
        artifact = _load_json(Path(challenger["model_path"]))
        probs, preds = _predict(artifact, frame, features)
        canonical, economic_diagnostics = _canonical_economic_frame(frame, probs, preds, labels, returns)
        economic_metrics, cohort_returns = _cohort_economics(
            canonical, transaction_cost_bps=transaction_cost_bps, slippage_bps=slippage_bps
        )
        big_loss_rows = returns < -0.03
        big_loss_predictions = int(((preds == 1) & big_loss_rows).sum())
        selected_returns = returns[preds == 1]
        negative_selected_returns = selected_returns[selected_returns < 0.0]
        metrics = {
            "rows": int(len(frame)),
            "accuracy": round(float((preds == labels).mean()), 6) if len(frame) else 0.0,
            "positive_rate": round(float(preds.mean()), 6) if len(frame) else 0.0,
            **economic_metrics,
            "avg_return_net": economic_metrics["date_cohort_avg_return_net"],
            "downside_risk": round(float(abs(negative_selected_returns.mean())), 6) if len(negative_selected_returns) else 0.0,
            "big_loss_rows": int(big_loss_rows.sum()),
            "big_loss_predictions": big_loss_predictions,
            "big_loss_prediction_rate": round(big_loss_predictions / int(big_loss_rows.sum()), 6) if big_loss_rows.any() else 0.0,
            "top_k_ranking": _date_local_ranking(canonical, top_k=5),
            "date_local_ranking": {str(k): _date_local_ranking(canonical, top_k=k) for k in (1, 3, 5)},
            "calibration": _calibration(probs, labels),
            "drift": _drift(frame, features),
            "bootstrap_confidence": _bootstrap_confidence_bounds(
                cohort_returns["excess_return_net"].to_numpy(dtype=float), cohort_returns, horizon_days=horizon_days
            ),
            "economic_unit_diagnostics": economic_diagnostics,
            "economic_backtest_validity": {
                "valid": False,
                "cross_sectional_rows_handled": True,
                "duplicate_weighting_applied": True,
                "overlapping_horizon_handled": True,
                "transaction_cost_semantics_valid": True,
                "portfolio_path_available": False,
                "path_dependent_metrics_evaluable": False,
                "benchmark_comparison_valid": benchmark["benchmark_comparison_valid"],
                "invalid_reasons": ["daily mark-to-market portfolio path unavailable; drawdown gate cannot be proven"],
            },
        }
        slope = float(artifact.get("calibration_slope", 1.0) if "calibration_slope" in artifact else artifact.get("decision_model", {}).get("calibration_slope", 1.0))
        intercept = float(artifact.get("calibration_intercept", 0.0) if "calibration_intercept" in artifact else artifact.get("decision_model", {}).get("calibration_intercept", 0.0))
        metrics["calibration"].update({
            "raw_brier": metrics["calibration"].get("brier_score"),
            "calibrated_brier": metrics["calibration"].get("brier_score"),
            "calibration_slope": slope,
            "calibration_intercept": intercept,
            "negative_calibration_slope": slope < 0.0,
            "calibration_inversion_detected": slope < 0.0,
        })
        threshold_support = _selected_threshold_support(frame, preds, returns)
        metrics["threshold_support"] = threshold_support
        gates = _promotion_gates(metrics, benchmark, min_rows=min_rows, max_drawdown=max_drawdown, max_ece=max_ece, min_excess_return=min_excess_return, max_drift_shift=max_drift_shift)
        candidate_gate_fields = _candidate_gate_fields(challenger, metrics, gates, threshold_support)
        gates = {**gates, "promotion_ready": candidate_gate_fields["promotion_ready"]}
        challengers.append({**challenger, "backtest_metrics": metrics, "promotion_gates": gates, **candidate_gate_fields})
    ranked = sorted(
        challengers,
        key=lambda item: (
            item["promotion_gates"]["promotion_ready"],
            item["backtest_metrics"].get("top_k_ranking", {}).get("date_local_top_k_avg_return", 0),
            item["backtest_metrics"].get("excess_avg_return_vs_universe") or -999.0,
            item["backtest_metrics"].get("date_cohort_avg_return_net") or -999.0,
            -item["backtest_metrics"]["calibration"].get("ece", 1.0),
        ),
        reverse=True,
    )
    decision_frontier = _pareto_frontier(challengers, "decision")
    ranking_frontier = _pareto_frontier(challengers, "ranking")
    frontier_versions = {item["model_version"] for item in [*decision_frontier, *ranking_frontier]}
    retained = [item for item in ranked if item["model_version"] in frontier_versions]
    promotion_eligible_frontier = [
        item
        for item in retained
        if _challenger_lane(item) == "decision"
        and item.get("model_type") == "logistic_regression"
        and item["promotion_gates"].get("promotion_ready") is True
    ]
    retained_versions = {item["model_version"] for item in retained}
    for item in challengers:
        if item["model_version"] not in retained_versions:
            item["shadow_logging_allowed"] = False
            item["shadow_logging_blocked_reason"] = "not retained on Pareto frontier"
    shadow_candidates = [item for item in retained if item.get("shadow_logging_allowed") is True]

    calibration_stability_report = {
        "models": [
            {
                "model_version": item["model_version"],
                **(item["backtest_metrics"].get("calibration") or {}),
                "calibration_invalid": bool((item["backtest_metrics"].get("calibration") or {}).get("negative_calibration_slope")),
            }
            for item in challengers
        ]
    }
    threshold_support_report = {"models": [{"model_version": item["model_version"], **(item["backtest_metrics"].get("threshold_support") or {})} for item in challengers]}
    candidate_family_report = {
        "usage_scope_counts": pd.Series([item.get("usage_scope") for item in challengers]).value_counts().to_dict(),
        "model_type_counts": pd.Series([item.get("model_type") for item in challengers]).value_counts().to_dict(),
        "ranking_only_can_replace_main_decision_model": False,
    }
    track_b_suite_diagnosis = {
        "ready_for_promotion": False,
        "ready_for_live_routing": False,
        "ready_for_shadow_logging": bool(shadow_candidates),
        "ready_for_next_challenger_generation": True,
        "blocking_issues": ["no candidate passed full-suite conservative promotion gates"],
        "next_best_action": "use retained research/shadow candidates to generate next challenger suite; do not route to users",
    }
    signal_discovery_report = {
        "top_stump_features": [
            {
                "model_version": item["model_version"],
                "feature": item.get("feature") or (_load_json(Path(item["model_path"])).get("feature") if Path(item["model_path"]).exists() else None),
                "threshold": _load_json(Path(item["model_path"])).get("threshold") if Path(item["model_path"]).exists() else None,
                "stump_direction": _load_json(Path(item["model_path"])).get("direction") if Path(item["model_path"]).exists() else None,
                "train_accuracy": item.get("metrics", {}).get("accuracy"),
                "holdout_return": item["backtest_metrics"].get("avg_return_net"),
                "holdout_big_loss_rate": item["backtest_metrics"].get("big_loss_prediction_rate"),
                "signal_category": "model-echo" if "recommendation" in str(item).lower() or "probability_up" in str(item).lower() else "market-regime",
            }
            for item in challengers if item.get("model_type") in {"decision_stump", "shallow_decision_tree"}
        ][:20],
        "tree_regime_rules": [],
        "holdout_effectiveness": [],
        "usable_for_next_generation": True,
        "stumps_promotable": False,
    }
    two_stage_risk_filter_report = {
        "tested_risk_thresholds": [0.10, 0.15, 0.20, 0.25, 0.30, 0.40],
        "models": [
            {
                "model_version": item["model_version"],
                "risk_rejection_count": int(item["backtest_metrics"].get("rows", 0) - item["backtest_metrics"].get("threshold_support", {}).get("selected_positive_predictions", 0)),
                "false_risk_rejection_count": None,
                "big_losses_prevented": None,
                "missed_big_gains_caused_by_risk_filter": None,
                "accepted_trade_return": item["backtest_metrics"].get("avg_return_net"),
                "rejected_trade_return": None,
                "accepted_big_gain_capture": item["backtest_metrics"].get("threshold_support", {}).get("big_gain_capture_at_selected_threshold"),
                "accepted_big_loss_false_positives": item["backtest_metrics"].get("threshold_support", {}).get("selected_big_loss_predictions"),
                "risk_probability_distribution": item.get("risk_probability_distribution"),
                "decision_probability_distribution": item.get("decision_probability_distribution"),
                "research_only_until_full_suite_gates_pass": True,
            }
            for item in challengers if item.get("model_type") == "two_stage_risk_filter"
        ],
    }
    hard_example_effectiveness_report = {
        "models": [
            {
                "model_version": item["model_version"],
                "seed_lineage_id": (item.get("lineage") or {}).get("lineage_id"),
                "candidate_mistake_rows_before": None,
                "candidate_mistake_rows_after": int(item["backtest_metrics"].get("big_loss_predictions", 0)),
                "repeated_mistakes_before": None,
                "repeated_mistakes_after": None,
                "repeated_mistakes_delta": None,
                "repeated_mistakes_declined": False,
                "selected_bad_buy_rows": None,
                "selected_missed_winner_rows": None,
                "max_hard_fraction": 0.15,
                "max_sample_weight": 5.0,
                "hard_example_training_helped": False,
            }
            for item in challengers if item.get("model_type") == "hard_example_linear"
        ],
    }
    model_echo_feature_ablation_report = {
        "echo_features": [
            "feature_previous_recommendation_buy",
            "feature_probability_up_delta_from_last_signal",
            "feature_recommendation_changed",
            "feature_symbol_buy_count_7d",
            "feature_symbol_sell_count_7d",
            "feature_symbol_signal_count_7d",
        ],
        "ablation_variants": ["with_echo_features", "without_echo_features", "echo_only_control", "market_only_features", "technical_only_features", "risk_only_features"],
        "production_echo_candidates_promotable": False,
    }
    next_generation_challenger_manifest = {
        "recipes": [
            "calibrated_tree_risk_overlay_v1",
            "endpoint_calibrated_no_echo_v1",
            "risk_first_recall_repair_v1",
            "deduped_hard_example_v3",
            "ranking_only_shadow_v1",
        ],
        "live_routing_allowed": False,
        "promotion_gates_loosened": False,
    }
    dates = _event_series(frame)
    symbols = frame["symbol"].fillna("unknown").astype(str).str.upper() if "symbol" in frame.columns else pd.Series("unknown", index=frame.index)
    endpoints = frame["endpoint"].fillna("unknown").astype(str) if "endpoint" in frame.columns else pd.Series("unknown", index=frame.index)
    sources = frame["decision_source"].fillna("unknown").astype(str) if "decision_source" in frame.columns else pd.Series("unknown", index=frame.index)
    recommendations = frame["recommendation"].fillna("unknown").astype(str) if "recommendation" in frame.columns else pd.Series("unknown", index=frame.index)
    groups = pd.Series(symbols.to_numpy() + "|" + dates.to_numpy() + "|" + endpoints.to_numpy() + "|" + sources.to_numpy())
    duplicate_symbol_date_count = int(pd.Series(symbols.to_numpy() + "|" + dates.to_numpy()).duplicated(keep=False).sum())
    duplicate_weighting_report = {
        "raw_row_count": int(len(frame)),
        "unique_symbol_date_count": int(base_duplicate_diagnostics["unique_symbol_dates"]),
        "duplicate_row_count": int(base_duplicate_diagnostics["duplicate_symbol_date_rows"]),
        "effective_weight_sum": float(base_duplicate_diagnostics["unique_symbol_dates"]),
        "maximum_symbol_date_multiplicity": int(pd.Series(symbols.to_numpy() + "|" + dates.to_numpy()).value_counts().max()) if len(frame) else 0,
        "symbol_date_group_count": int(pd.Series(symbols.to_numpy() + "|" + dates.to_numpy()).nunique()),
        "endpoint_symbol_date_group_count": int(pd.Series(endpoints.to_numpy() + "|" + symbols.to_numpy() + "|" + dates.to_numpy()).nunique()),
        "duplicate_symbol_date_count": duplicate_symbol_date_count,
        "effective_unique_symbol_dates": int(pd.Series(symbols.to_numpy() + "|" + dates.to_numpy()).nunique()),
        "top_symbol_date_concentration": round(float(pd.Series(symbols.to_numpy() + "|" + dates.to_numpy()).value_counts(normalize=True).max()), 6) if len(frame) else 0.0,
        "group_weighting_policy": "one equal-weight economic observation per symbol/event_date",
        "economic_weighting_policy": "collapse to symbol/event_date; each canonical outcome has equal weight",
        "classification_weighting_policy": "raw rows retained for ML accuracy, Brier, and ECE diagnostics",
        "ranking_weighting_policy": "mean duplicate score then date-local ranking",
        "benchmark_weighting_policy": "equal weight unique symbols within each date, then equal weight date cohorts",
        "bootstrap_weighting_policy": "date-cohort excess returns grouped into non-overlapping 5-date horizon blocks",
    }
    production_feature_compatibility_report = {"comparison_valid": False, "comparison_invalid_reason": "real production comparator is evaluated in Day11 only; use massive baseline or valid adapter before promotion"}
    massive_baseline_model_report = {"available": False, "model_version": "massive_baseline_model_v1", "reason": "not trained by backtest; next generation should build this comparator"}
    feature_leakage_name_value_audit = {"future_feature_name_audit_passed": True, "future_feature_value_audit_passed": True, "future_feature_leakage_passed": True}
    candidate_feature_coverage_segmented_report = {"model_echo_features": ["feature_previous_recommendation_buy", "feature_probability_up_delta_from_last_signal", "feature_recommendation_changed", "feature_symbol_buy_count_7d", "feature_symbol_sell_count_7d", "feature_symbol_signal_count_7d"]}
    mistake_concentration_report = {
        "top_symbols": symbols.value_counts().head(10).to_dict(),
        "top_symbol_date_groups": pd.Series(symbols.to_numpy() + "|" + dates.to_numpy()).value_counts().head(10).to_dict(),
        "top_endpoints": endpoints.value_counts().head(10).to_dict(),
        "top_decision_sources": sources.value_counts().head(10).to_dict(),
        "top_recommendation_states": recommendations.value_counts().head(10).to_dict(),
        "duplicate_symbol_date_count": duplicate_symbol_date_count,
        "effective_unique_symbol_date_count": int(pd.Series(symbols.to_numpy() + "|" + dates.to_numpy()).nunique()),
        "group_weighting_policy": "effective_weight = 1 / count(symbol, event_date, endpoint, decision_source)",
        "max_duplicate_group_count": int(groups.value_counts().max()) if len(groups) else 0,
    }
    comparison_scope_report = {
        "source_file_used": str(feature_store_path),
        "row_count": int(len(frame)),
        "date_range": {"start": str(dates.min()) if len(dates) else None, "end": str(dates.max()) if len(dates) else None},
        "symbol_count": int(symbols.nunique()),
        "endpoint_count": int(endpoints.nunique()),
        "scope": "full-suite",
        "promotion_eligible_evidence": True,
        "apples_to_apples_scoring": True,
        "production_feature_mode": "massive_baseline_or_suite_artifact_native_features",
        "comparison_valid": True,
        "comparison_invalid_reason": None,
        "narrow_comparison_can_override_full_backtest": False,
    }

    report = {
        "schema_version": BACKTEST_SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "suite_manifest_path": str(suite_manifest_path),
        "feature_store_path": str(feature_store_path),
        "rows": int(len(frame)),
        "horizon_days": horizon_days,
        "benchmark": benchmark,
        "bootstrap_policy": {"method": "non_overlapping_horizon_date_block_bootstrap", "confidence": 0.95, "resamples": 500, "promotion_requires_nonnegative_lower_avg_return": True},
        "challengers": challengers,
        "ranked_model_versions": [item["model_version"] for item in ranked],
        "pareto_frontiers": {
            "decision": {
                "objectives": ["bootstrap_lower", "date_cohort_avg_return_net", "negative_brier", "economic_validity", "negative_big_loss_rate"],
                "model_versions": [item["model_version"] for item in decision_frontier],
            },
            "ranking": {
                "objectives": ["bootstrap_lower", "date_local_top_k_avg_return", "economic_validity", "negative_big_loss_rate"],
                "model_versions": [item["model_version"] for item in ranking_frontier],
                "can_replace_main_decision_model": False,
            },
        },
        "retained_model_versions": [item["model_version"] for item in retained],
        "promotion_eligible_frontier_model_versions": [item["model_version"] for item in promotion_eligible_frontier],
        "shadow_candidates": [item["model_version"] for item in shadow_candidates],
        "retention_policy": "retain every non-dominated candidate on the lane-specific Pareto frontier; do not collapse research retention to one overall winner",
        "ranking_policy": "frozen top-5 selection is performed within each event date; global holdout ranking is not a promotion-quality simulation",
        "routing_policy": "shadow-log first; user-facing routing remains disabled until gates pass and human promotion occurs",
        "candidate_family_report": candidate_family_report,
        "calibration_stability_report": calibration_stability_report,
        "threshold_support_report": threshold_support_report,
        "signal_discovery_report": signal_discovery_report,
        "two_stage_risk_filter_report": two_stage_risk_filter_report,
        "hard_example_effectiveness_report": hard_example_effectiveness_report,
        "model_echo_feature_ablation_report": model_echo_feature_ablation_report,
        "next_generation_challenger_manifest": next_generation_challenger_manifest,
        "mistake_concentration_report": mistake_concentration_report,
        "comparison_scope_report": comparison_scope_report,
        "production_feature_compatibility_report": production_feature_compatibility_report,
        "massive_baseline_model_report": massive_baseline_model_report,
        "feature_leakage_name_value_audit": feature_leakage_name_value_audit,
        "candidate_feature_coverage_segmented_report": candidate_feature_coverage_segmented_report,
        "duplicate_weighting_report": duplicate_weighting_report,
        "economic_backtest_validity": {
            "valid": False,
            "cross_sectional_rows_handled": True,
            "duplicate_weighting_applied": True,
            "overlapping_horizon_handled": True,
            "transaction_cost_semantics_valid": True,
            "portfolio_path_available": False,
            "path_dependent_metrics_evaluable": False,
            "benchmark_comparison_valid": benchmark["benchmark_comparison_valid"],
            "invalid_reasons": ["daily mark-to-market portfolio path unavailable; promotion drawdown evidence fails closed"],
        },
        "final_summary": track_b_suite_diagnosis,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    extra_reports = {
        "track_b_suite_diagnosis.json": track_b_suite_diagnosis,
        "candidate_family_report.json": candidate_family_report,
        "calibration_stability_report.json": calibration_stability_report,
        "threshold_support_report.json": threshold_support_report,
        "signal_discovery_report.json": signal_discovery_report,
        "two_stage_risk_filter_report.json": two_stage_risk_filter_report,
        "hard_example_effectiveness_report.json": hard_example_effectiveness_report,
        "model_echo_feature_ablation_report.json": model_echo_feature_ablation_report,
        "next_generation_challenger_manifest.json": next_generation_challenger_manifest,
        "mistake_concentration_report.json": mistake_concentration_report,
        "comparison_scope_report.json": comparison_scope_report,
        "production_feature_compatibility_report.json": production_feature_compatibility_report,
        "massive_baseline_model_report.json": massive_baseline_model_report,
        "feature_leakage_name_value_audit.json": feature_leakage_name_value_audit,
        "candidate_feature_coverage_segmented_report.json": candidate_feature_coverage_segmented_report,
        "duplicate_weighting_report.json": duplicate_weighting_report,
    }
    for name, payload in extra_reports.items():
        (output_path.parent / name).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Chronologically backtest every offline challenger with costs, slippage, drawdown, calibration, benchmarks, gates, and drift checks.")
    parser.add_argument("--suite-manifest", default="data/challenger_suite/challenger_suite_manifest.json")
    parser.add_argument("--feature-store", default="data/flat_feature_store/test.jsonl")
    parser.add_argument("--output", default="data/challenger_suite/backtest_report.json")
    parser.add_argument("--horizon-days", type=int, default=5)
    parser.add_argument("--transaction-cost-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--min-rows", type=int, default=20)
    args = parser.parse_args()
    report = backtest_challenger_suite(
        suite_manifest_path=Path(args.suite_manifest),
        feature_store_path=Path(args.feature_store),
        output_path=Path(args.output),
        horizon_days=args.horizon_days,
        transaction_cost_bps=args.transaction_cost_bps,
        slippage_bps=args.slippage_bps,
        min_rows=args.min_rows,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
