#!/usr/bin/env python3
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

from moneybot.services.deterministic_model import load_artifact, predict_proba
from moneybot.services.temporal_validation import purged_embargoed_split
from scripts.day10_train_candidate_model import _flat_optimum_threshold, _future_safe_feature_columns

RETURN_BIN_EDGES = (-0.03, -0.005, 0.005, 0.03)
TARGET_GAIN_BUCKETS = {"gain", "big_gain"}
MIN_BIG_GAIN_CAPTURE_RATE = 0.10
UTILITY_BIG_GAIN_WEIGHT = 0.10
UTILITY_DOWNSIDE_WEIGHT = 1.0
UTILITY_BIG_LOSS_WEIGHT = 1.0
MIN_UTILITY_IMPROVEMENT = 0.0
HARD_BIG_LOSS_FALSE_POSITIVE_PENALTY = 1.0
THRESHOLD_SEARCH_VALUES = (0.55, 0.575, 0.60, 0.625, 0.65, 0.675, 0.70)
RANKING_TOP_K_VALUES = (1, 3, 5)
RANKING_MAX_EXPOSURE_PER_SIGNAL = 0.10
NO_OP_CLONE_PREDICTION_AGREEMENT = 0.98
NO_OP_CLONE_PROBABILITY_MAE = 0.02
WALK_FORWARD_WINDOWS = 3
MIN_THRESHOLD_BIG_GAIN_CAPTURE_RATE = 0.10
MIN_THRESHOLD_POSITIVE_PREDICTIONS = 10
MIN_THRESHOLD_POSITIVE_FRACTION_OF_CURRENT = 0.25
LABEL_HORIZON_DAYS = 5
EMBARGO_DAYS = 1
REGRESSION_EXAMPLES_DIR = PROJECT_ROOT / "regression_examples"
MAX_SYMBOL_UTILITY_CONCENTRATION = 0.50
MAX_DATE_UTILITY_CONCENTRATION = 0.50
MAX_STABLE_THRESHOLD_SPREAD = 0.05
RAW_PRICE_TOP_CONTRIBUTOR_RATE_LIMIT = 0.25
PRODUCTION_BOOTSTRAP_RESAMPLES = 1000
MIN_PRODUCTION_BOOTSTRAP_PROBABILITY_POSITIVE = 0.95


def _load_jsonl(path: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
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


def _chronological_split(df: pd.DataFrame, train_ratio: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    if "ts" in df.columns:
        df = df.sort_values("ts").reset_index(drop=True)
    pivot = int(len(df) * train_ratio)
    if pivot <= 0 or pivot >= len(df):
        raise ValueError("train_ratio creates empty split")
    return df.iloc[:pivot].copy(), df.iloc[pivot:].copy()


def _brier_score(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    return float(np.mean((y_prob - y_true) ** 2))


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


def _ensure_return_bins(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "return_bin_5d" not in out.columns:
        returns = pd.to_numeric(out.get("return_5d"), errors="coerce")
        out["return_bin_5d"] = [_return_bin(value) if pd.notna(value) else None for value in returns]
    return out


def _bucket_signal_rates(usable: pd.DataFrame, preds: np.ndarray) -> dict[str, float | int | None]:
    work = usable.copy()
    work["_pred"] = preds
    big_loss = work[work["return_bin_5d"].fillna("").astype(str) == "big_loss"]
    big_gain = work[work["return_bin_5d"].fillna("").astype(str) == "big_gain"]
    big_loss_positive = int((big_loss["_pred"] == 1).sum()) if len(big_loss) else 0
    big_gain_positive = int((big_gain["_pred"] == 1).sum()) if len(big_gain) else 0
    return {
        "big_loss_rows": int(len(big_loss)),
        "big_loss_predictions": big_loss_positive,
        "big_loss_prediction_rate": round(big_loss_positive / len(big_loss), 4) if len(big_loss) else None,
        "big_gain_rows": int(len(big_gain)),
        "big_gain_predictions": big_gain_positive,
        "big_gain_capture_rate": round(big_gain_positive / len(big_gain), 4) if len(big_gain) else None,
    }


def _bucket_metrics(usable: pd.DataFrame, preds: np.ndarray, probs: np.ndarray) -> dict[str, dict[str, float | int | None]]:
    out: dict[str, dict[str, float | int | None]] = {}
    work = usable.copy()
    work["_pred"] = preds
    work["_prob"] = probs
    for bucket, group in work.groupby("return_bin_5d", dropna=False):
        key = str(bucket or "unknown")
        returns = pd.to_numeric(group["return_5d"], errors="coerce")
        out[key] = {
            "rows": int(len(group)),
            "positive_predictions": int((group["_pred"] == 1).sum()),
            "avg_probability": round(float(group["_prob"].mean()), 4) if len(group) else None,
            "avg_return": round(float(returns.mean()), 4) if returns.notna().any() else None,
        }
    return dict(sorted(out.items()))


def _selected_concentration_metrics(usable: pd.DataFrame, preds: np.ndarray) -> dict[str, float | None]:
    selected = usable.loc[preds == 1].copy()
    if selected.empty:
        return {
            "symbol_selection_concentration": None,
            "date_selection_concentration": None,
            "symbol_utility_concentration": None,
            "date_utility_concentration": None,
        }
    selected["_absolute_return"] = pd.to_numeric(selected["return_5d"], errors="coerce").abs().fillna(0.0)

    def concentration(group: pd.Series | None) -> tuple[float | None, float | None]:
        if group is None:
            return None, None
        values = group.fillna("unknown").astype(str)
        selection_concentration = float(values.value_counts(normalize=True).max())
        absolute_total = float(selected["_absolute_return"].sum())
        if absolute_total <= 0.0:
            return selection_concentration, selection_concentration
        grouped_return = selected.groupby(values)["_absolute_return"].sum()
        return selection_concentration, float(grouped_return.max() / absolute_total)

    symbol_group = selected["symbol"] if "symbol" in selected.columns else None
    date_group = _event_date_series(selected) if "event_date" in selected.columns or "ts" in selected.columns else None
    symbol_selection, symbol_utility = concentration(symbol_group)
    date_selection, date_utility = concentration(date_group)
    return {
        "symbol_selection_concentration": round(symbol_selection, 4) if symbol_selection is not None else None,
        "date_selection_concentration": round(date_selection, 4) if date_selection is not None else None,
        "symbol_utility_concentration": round(symbol_utility, 4) if symbol_utility is not None else None,
        "date_utility_concentration": round(date_utility, 4) if date_utility is not None else None,
    }


def _prediction_return_metrics(usable: pd.DataFrame, preds: np.ndarray, probs: np.ndarray) -> dict[str, Any]:
    y = usable["return_bin_5d"].fillna("").astype(str).isin(TARGET_GAIN_BUCKETS).astype(int).to_numpy()
    signal_returns = usable.loc[preds == 1, "return_5d"].astype(float)
    if signal_returns.empty:
        avg_return = None
        downside_risk = None
    else:
        avg_return = float(signal_returns.mean())
        negative_signal_returns = signal_returns[signal_returns < 0.0]
        downside_risk = 0.0 if negative_signal_returns.empty else float(abs(negative_signal_returns.mean()))
    metrics = {
        "accuracy": round(float((preds == y).mean()), 4),
        "avg_return": round(avg_return, 4) if avg_return is not None else None,
        "brier_score": round(_brier_score(y.astype(float), probs.astype(float)), 4),
        "downside_risk": round(downside_risk, 4) if downside_risk is not None else None,
        "positive_predictions": int((preds == 1).sum()),
        **_bucket_signal_rates(usable, preds),
        **_selected_concentration_metrics(usable, preds),
    }
    utility = _utility_score(metrics)
    metrics["utility_score"] = round(utility, 4) if utility is not None else None
    return metrics


def _threshold_search(usable: pd.DataFrame, probs: np.ndarray) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for threshold in THRESHOLD_SEARCH_VALUES:
        preds = (probs >= threshold).astype(int)
        results.append({"threshold": threshold, **_prediction_return_metrics(usable, preds, probs)})
    return results


def _event_date_series(usable: pd.DataFrame) -> pd.Series:
    if "event_date" in usable.columns:
        dates = usable["event_date"].fillna("").astype(str)
        if dates.str.strip().any():
            return dates
    if "ts" in usable.columns:
        parsed = pd.to_datetime(pd.to_numeric(usable["ts"], errors="coerce"), unit="s", utc=True, errors="coerce")
        return parsed.dt.strftime("%Y-%m-%d").fillna("unknown")
    return pd.Series("unknown", index=usable.index)


def _max_drawdown_from_returns(returns: list[float]) -> float | None:
    if not returns:
        return None
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in returns:
        equity *= max(0.0, 1.0 + float(value))
        peak = max(peak, equity)
        if peak > 0.0:
            max_drawdown = min(max_drawdown, (equity / peak) - 1.0)
    return abs(max_drawdown)


def _ranking_backtests(usable: pd.DataFrame, probs: np.ndarray) -> list[dict[str, Any]]:
    work = usable.copy()
    work["_prob"] = probs
    work["_return"] = pd.to_numeric(work["return_5d"], errors="coerce")
    work["_event_date"] = _event_date_series(work)
    work = work.dropna(subset=["_return"]).copy()
    if work.empty:
        return []

    total_big_gain_rows = int((work["return_bin_5d"].fillna("").astype(str) == "big_gain").sum())
    total_big_loss_rows = int((work["return_bin_5d"].fillna("").astype(str) == "big_loss").sum())
    results: list[dict[str, Any]] = []
    for top_k in RANKING_TOP_K_VALUES:
        selected = (
            work.sort_values(["_event_date", "_prob"], ascending=[True, False])
            .groupby("_event_date", group_keys=False)
            .head(top_k)
            .copy()
        )
        daily_returns = (
            selected.assign(_weighted_return=selected["_return"] * RANKING_MAX_EXPOSURE_PER_SIGNAL)
            .groupby("_event_date")["_weighted_return"]
            .sum()
            .clip(lower=-1.0)
            .tolist()
        )
        selected_bins = selected["return_bin_5d"].fillna("").astype(str)
        big_gain_hits = int((selected_bins == "big_gain").sum())
        big_loss_hits = int((selected_bins == "big_loss").sum())
        total_return = float(np.prod([1.0 + float(value) for value in daily_returns]) - 1.0) if daily_returns else None
        max_drawdown = _max_drawdown_from_returns(daily_returns)
        objective = None
        if total_return is not None and max_drawdown is not None:
            objective = total_return - max_drawdown
        results.append(
            {
                "top_k": int(top_k),
                "max_exposure_per_signal": RANKING_MAX_EXPOSURE_PER_SIGNAL,
                "days": int(len(daily_returns)),
                "selected_rows": int(len(selected)),
                "avg_signal_return": round(float(selected["_return"].mean()), 4) if len(selected) else None,
                "avg_daily_return": round(float(np.mean(daily_returns)), 4) if daily_returns else None,
                "total_return": round(total_return, 4) if total_return is not None else None,
                "max_drawdown": round(max_drawdown, 4) if max_drawdown is not None else None,
                "big_gain_capture_rate": round(big_gain_hits / total_big_gain_rows, 4) if total_big_gain_rows else None,
                "big_loss_selection_rate": round(big_loss_hits / total_big_loss_rows, 4) if total_big_loss_rows else None,
                "objective_score": round(objective, 4) if objective is not None else None,
            }
        )
    return results


def _equal_weight_benchmark_backtest(usable: pd.DataFrame) -> dict[str, Any]:
    """Return a capped equal-weight long benchmark grouped by event date.

    The benchmark intentionally groups rows into dated portfolios before
    compounding. Treating every event row as a sequential all-in trade can turn a
    noisy decision log into an artificial -100% benchmark.
    """
    work = usable.copy()
    work["_return"] = pd.to_numeric(work["return_5d"], errors="coerce")
    work["_event_date"] = _event_date_series(work)
    work = work.dropna(subset=["_return"]).copy()
    if work.empty:
        return {
            "cash_return": 0.0,
            "equal_weight_long_cash_return": None,
            "equal_weight_long_cash_max_drawdown": None,
            "days": 0,
            "rows": 0,
            "max_exposure_per_signal": RANKING_MAX_EXPOSURE_PER_SIGNAL,
        }

    daily_returns: list[float] = []
    for _, group in work.groupby("_event_date"):
        exposure_per_signal = min(RANKING_MAX_EXPOSURE_PER_SIGNAL, 1.0 / float(len(group)))
        daily_returns.append(float((group["_return"] * exposure_per_signal).sum()))

    total_return = float(np.prod([1.0 + max(-1.0, float(value)) for value in daily_returns]) - 1.0)
    max_drawdown = _max_drawdown_from_returns(daily_returns)
    return {
        "cash_return": 0.0,
        "equal_weight_long_cash_return": round(total_return, 4),
        "equal_weight_long_cash_max_drawdown": round(max_drawdown, 4) if max_drawdown is not None else None,
        "avg_daily_return": round(float(np.mean(daily_returns)), 4) if daily_returns else None,
        "days": int(len(daily_returns)),
        "rows": int(len(work)),
        "max_exposure_per_signal": RANKING_MAX_EXPOSURE_PER_SIGNAL,
    }


def _best_ranking_backtest(backtests: list[dict[str, Any]]) -> dict[str, Any] | None:
    scored = [item for item in backtests if isinstance(item.get("objective_score"), (int, float))]
    if not scored:
        return None
    return max(scored, key=lambda item: (float(item["objective_score"]), float(item.get("total_return") or 0.0)))


def _evaluate(artifact_path: str, test_df: pd.DataFrame) -> dict[str, Any]:
    if not Path(artifact_path).exists():
        return {"accuracy": None, "avg_return": None, "brier_score": None, "downside_risk": None, "positive_predictions": 0, "rows": 0}
    artifact = load_artifact(artifact_path)
    usable = test_df.copy()
    for idx, col in enumerate(artifact.feature_columns):
        if col not in usable.columns:
            usable[col] = np.nan
        numeric = pd.to_numeric(usable[col], errors="coerce").replace([np.inf, -np.inf], np.nan)
        fallback = float(artifact.means[idx]) if idx < len(artifact.means) else 0.0
        usable[col] = numeric.fillna(fallback).astype(float)
    usable["return_5d"] = pd.to_numeric(usable.get("return_5d"), errors="coerce")
    usable = usable.dropna(subset=["return_5d"]).copy()
    usable = _ensure_return_bins(usable)
    if usable.empty:
        return {"accuracy": None, "avg_return": None, "brier_score": None, "downside_risk": None, "positive_predictions": 0, "rows": 0}

    X = usable[artifact.feature_columns].to_numpy(dtype=float)
    probs = predict_proba(artifact, X)
    preds = (probs >= artifact.decision_threshold).astype(int)
    ranking_backtests = _ranking_backtests(usable, probs)
    metrics = {
        **_prediction_return_metrics(usable, preds, probs),
        "return_bin_counts": {str(k): int(v) for k, v in sorted(usable["return_bin_5d"].fillna("unknown").astype(str).value_counts().to_dict().items())},
        "bucket_metrics": _bucket_metrics(usable, preds, probs),
        "threshold_search": _threshold_search(usable, probs),
        "ranking_backtests": ranking_backtests,
        "best_ranking_backtest": _best_ranking_backtest(ranking_backtests),
        "benchmark_backtest": _equal_weight_benchmark_backtest(usable),
        "rows": int(len(usable)),
    }
    return metrics



def _no_op_clone_summary(candidate_preds: np.ndarray, production_preds: np.ndarray, candidate_probs: np.ndarray, production_probs: np.ndarray) -> dict[str, Any]:
    rows = int(min(len(candidate_preds), len(production_preds), len(candidate_probs), len(production_probs)))
    if rows <= 0:
        return {"rows": 0, "prediction_agreement": None, "probability_mae": None, "no_op_clone": False, "candidate_prediction_fingerprint": None, "production_prediction_fingerprint": None, "fingerprints_identical": False}
    c_preds = candidate_preds[:rows]
    p_preds = production_preds[:rows]
    c_probs = candidate_probs[:rows]
    p_probs = production_probs[:rows]
    candidate_fingerprint = hashlib.sha256(c_preds.astype(np.int8).tobytes() + np.round(c_probs, 6).astype(np.float64).tobytes()).hexdigest()
    production_fingerprint = hashlib.sha256(p_preds.astype(np.int8).tobytes() + np.round(p_probs, 6).astype(np.float64).tobytes()).hexdigest()
    prediction_agreement = float((c_preds == p_preds).mean())
    probability_mae = float(np.mean(np.abs(c_probs - p_probs)))
    no_op_clone = prediction_agreement >= NO_OP_CLONE_PREDICTION_AGREEMENT and probability_mae <= NO_OP_CLONE_PROBABILITY_MAE
    return {
        "rows": rows,
        "prediction_agreement": round(prediction_agreement, 4),
        "probability_mae": round(probability_mae, 4),
        "no_op_clone": bool(no_op_clone),
        "candidate_prediction_fingerprint": candidate_fingerprint,
        "production_prediction_fingerprint": production_fingerprint,
        "fingerprints_identical": candidate_fingerprint == production_fingerprint,
        "prediction_agreement_threshold": NO_OP_CLONE_PREDICTION_AGREEMENT,
        "probability_mae_threshold": NO_OP_CLONE_PROBABILITY_MAE,
    }


def _artifact_predictions(artifact_path: str, test_df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    if not Path(artifact_path).exists():
        return np.array([], dtype=int), np.array([], dtype=float)
    artifact = load_artifact(artifact_path)
    usable = test_df.copy()
    for idx, col in enumerate(artifact.feature_columns):
        if col not in usable.columns:
            usable[col] = np.nan
        numeric = pd.to_numeric(usable[col], errors="coerce").replace([np.inf, -np.inf], np.nan)
        fallback = float(artifact.means[idx]) if idx < len(artifact.means) else 0.0
        usable[col] = numeric.fillna(fallback).astype(float)
    usable["return_5d"] = pd.to_numeric(usable.get("return_5d"), errors="coerce")
    usable = usable.dropna(subset=["return_5d"]).copy()
    if usable.empty:
        return np.array([], dtype=int), np.array([], dtype=float)
    probs = predict_proba(artifact, usable[artifact.feature_columns].to_numpy(dtype=float))
    preds = (probs >= artifact.decision_threshold).astype(int)
    return preds, probs


def _artifact_config_fingerprint(artifact_path: str) -> str | None:
    if not Path(artifact_path).exists():
        return None
    artifact = load_artifact(artifact_path)
    payload = json.dumps(artifact.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _clone_detection(candidate_model_path: str, production_model_path: str, test_df: pd.DataFrame) -> dict[str, Any]:
    candidate_preds, candidate_probs = _artifact_predictions(candidate_model_path, test_df)
    production_preds, production_probs = _artifact_predictions(production_model_path, test_df)
    return _no_op_clone_summary(candidate_preds, production_preds, candidate_probs, production_probs)


def _paired_date_bootstrap_utility_delta(candidate_model_path: str, production_model_path: str, test_df: pd.DataFrame) -> dict[str, Any]:
    """Bootstrap paired candidate-minus-production realized return by independent date."""
    candidate = _artifact_scored_frame(candidate_model_path, test_df, prefix="candidate")
    production = _artifact_scored_frame(production_model_path, test_df, prefix="production")
    common = candidate.index.intersection(production.index)
    if common.empty:
        return {"passed": False, "reason": "no common scored rows", "independent_date_blocks": 0}
    work = candidate.loc[common].copy()
    work["_production_pred"] = production.loc[common, "_production_pred"]
    work["_event_date"] = _event_date_series(work)
    returns = pd.to_numeric(work["return_5d"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    work["_utility_delta"] = (work["_candidate_pred"].to_numpy(dtype=float) - work["_production_pred"].to_numpy(dtype=float)) * returns
    daily = work.groupby("_event_date", sort=True)["_utility_delta"].mean().to_numpy(dtype=float)
    if daily.size == 0:
        return {"passed": False, "reason": "no independent date blocks", "independent_date_blocks": 0}
    rng = np.random.default_rng(20260803)
    samples = daily[rng.integers(0, len(daily), size=(PRODUCTION_BOOTSTRAP_RESAMPLES, len(daily)))].mean(axis=1)
    lower = float(np.quantile(samples, 0.025))
    median = float(np.quantile(samples, 0.5))
    upper = float(np.quantile(samples, 0.975))
    probability_positive = float((samples > 0.0).mean())
    passed = lower > 0.0 and median > 0.0 and probability_positive >= MIN_PRODUCTION_BOOTSTRAP_PROBABILITY_POSITIVE
    return {
        "passed": bool(passed),
        "method": "paired_date_block_bootstrap",
        "resamples": PRODUCTION_BOOTSTRAP_RESAMPLES,
        "confidence": 0.95,
        "independent_date_blocks": int(len(daily)),
        "utility_delta_lower": round(lower, 6),
        "utility_delta_median": round(median, 6),
        "utility_delta_upper": round(upper, 6),
        "probability_positive": round(probability_positive, 6),
        "minimum_probability_positive": MIN_PRODUCTION_BOOTSTRAP_PROBABILITY_POSITIVE,
    }


def _feature_risk_audit(artifact_path: str, test_df: pd.DataFrame) -> dict[str, Any]:
    if not Path(artifact_path).exists():
        return {"available": False, "requires_review": True, "reasons": ["artifact unavailable for feature-risk audit"]}
    artifact = load_artifact(artifact_path)
    if "feature_price" not in artifact.feature_columns:
        return {"available": True, "raw_feature_price_present": False, "requires_review": False, "reasons": []}
    usable = test_df.copy()
    for idx, feature in enumerate(artifact.feature_columns):
        fallback = float(artifact.means[idx]) if idx < len(artifact.means) else 0.0
        numeric = pd.to_numeric(usable.get(feature, pd.Series(np.nan, index=usable.index)), errors="coerce").replace([np.inf, -np.inf], np.nan)
        usable[feature] = numeric.fillna(fallback).astype(float)
    X = usable[artifact.feature_columns].to_numpy(dtype=float)
    probs = predict_proba(artifact, X)
    positive_mask = probs >= artifact.decision_threshold
    means = np.asarray(artifact.means, dtype=float)
    stds = np.asarray(artifact.stds, dtype=float)
    stds = np.where(stds == 0.0, 1.0, stds)
    weights = np.asarray(artifact.weights, dtype=float)
    contributions = ((X - means) / stds) * weights
    price_idx = artifact.feature_columns.index("feature_price")
    price_weight = float(weights[price_idx])
    positive_rows = np.flatnonzero(positive_mask)
    top_positive_count = 0
    examples: list[dict[str, Any]] = []
    for row_idx in positive_rows:
        positive_contributions = np.maximum(contributions[row_idx], 0.0)
        top_idx = int(np.argmax(positive_contributions)) if positive_contributions.size else -1
        if top_idx == price_idx and positive_contributions[price_idx] > 0.0:
            top_positive_count += 1
            row = usable.iloc[row_idx]
            examples.append({
                "symbol": row.get("symbol"),
                "event_date": str(_event_date_series(usable.iloc[[row_idx]]).iloc[0]),
                "probability": round(float(probs[row_idx]), 6),
                "feature_price": round(float(X[row_idx, price_idx]), 6),
                "price_contribution": round(float(contributions[row_idx, price_idx]), 6),
            })
    top_rate = float(top_positive_count / len(positive_rows)) if len(positive_rows) else 0.0
    requires_review = price_weight > 0.0 and top_rate > RAW_PRICE_TOP_CONTRIBUTOR_RATE_LIMIT
    reasons = []
    if requires_review:
        reasons.append(f"raw feature_price is the top positive contributor too often ({top_rate:.4f} > {RAW_PRICE_TOP_CONTRIBUTOR_RATE_LIMIT:.4f})")
    return {
        "available": True,
        "raw_feature_price_present": True,
        "raw_feature_price_weight": round(price_weight, 6),
        "positive_predictions": int(len(positive_rows)),
        "raw_price_top_positive_contributor_count": top_positive_count,
        "raw_price_top_positive_contributor_rate": round(top_rate, 4),
        "rate_limit": RAW_PRICE_TOP_CONTRIBUTOR_RATE_LIMIT,
        "requires_review": requires_review,
        "reasons": reasons,
        "examples": examples[:20],
    }

def _numeric_metric(metrics: dict[str, Any], key: str) -> float | None:
    value = metrics.get(key)
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if np.isfinite(numeric) else None


def _utility_score(metrics: dict[str, Any]) -> float | None:
    avg_return = _numeric_metric(metrics, "avg_return")
    downside = _numeric_metric(metrics, "downside_risk")
    big_loss_rate = _numeric_metric(metrics, "big_loss_prediction_rate")
    big_gain_rate = _numeric_metric(metrics, "big_gain_capture_rate")
    if avg_return is None or downside is None:
        return None
    downside = downside or 0.0
    big_loss_rate = big_loss_rate or 0.0
    big_gain_rate = big_gain_rate or 0.0
    return (
        avg_return
        - (UTILITY_DOWNSIDE_WEIGHT * downside)
        - (UTILITY_BIG_LOSS_WEIGHT * big_loss_rate)
        + (UTILITY_BIG_GAIN_WEIGHT * big_gain_rate)
    )


def _candidate_behavior(candidate: dict[str, Any], production: dict[str, Any]) -> str:
    """Describe candidate selectivity without affecting promotion eligibility."""
    candidate_positive = _numeric_metric(candidate, "positive_predictions")
    production_positive = _numeric_metric(production, "positive_predictions")
    candidate_capture = _numeric_metric(candidate, "big_gain_capture_rate")
    production_capture = _numeric_metric(production, "big_gain_capture_rate")
    candidate_utility = _numeric_metric(candidate, "utility_score_after_big_loss_penalty")
    production_utility = _utility_score(production)
    low_activity = candidate_positive is not None and (
        candidate_positive <= 25
        or (production_positive is not None and candidate_positive <= production_positive * 0.5)
    )
    low_recall = candidate_capture is not None and production_capture is not None and candidate_capture < production_capture
    high_utility = candidate_utility is not None and production_utility is not None and candidate_utility > production_utility
    if low_activity and low_recall and high_utility:
        return "high_precision_low_recall"
    if low_activity:
        return "abstention_style_candidate"
    return "balanced_activity_candidate"


def _decide(candidate: dict[str, Any], production: dict[str, Any], *, min_rows: int = 200) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    rows = int(candidate.get("rows") or 0)
    if rows < min_rows:
        reasons.append(f"candidate rows below minimum ({rows} < {min_rows})")
        return False, reasons

    c_acc = _numeric_metric(candidate, "accuracy")
    p_acc = _numeric_metric(production, "accuracy")
    c_brier = _numeric_metric(candidate, "brier_score")
    p_brier = _numeric_metric(production, "brier_score")
    c_return = _numeric_metric(candidate, "avg_return")
    p_return = _numeric_metric(production, "avg_return")
    c_downside = _numeric_metric(candidate, "downside_risk")
    p_downside = _numeric_metric(production, "downside_risk")
    if None in {c_acc, p_acc, c_brier, p_brier, c_return, p_return, c_downside, p_downside}:
        reasons.append("insufficient comparable accuracy, brier, return, or downside metrics")
        return False, reasons

    c_big_loss_rate = _numeric_metric(candidate, "big_loss_prediction_rate")
    p_big_loss_rate = _numeric_metric(production, "big_loss_prediction_rate")
    c_big_loss_predictions = _numeric_metric(candidate, "big_loss_predictions") or 0.0
    p_big_loss_predictions = _numeric_metric(production, "big_loss_predictions") or 0.0
    c_big_gain_rate = _numeric_metric(candidate, "big_gain_capture_rate")
    c_utility = _utility_score(candidate)
    p_utility = _utility_score(production)
    if c_utility is None or p_utility is None:
        reasons.append("insufficient comparable utility metrics")
        return False, reasons

    hard_big_loss_false_positive = p_big_loss_predictions == 0.0 and c_big_loss_predictions > 0.0
    big_loss_false_positive_penalty = HARD_BIG_LOSS_FALSE_POSITIVE_PENALTY if hard_big_loss_false_positive else 0.0
    c_utility_after_penalty = c_utility - big_loss_false_positive_penalty
    candidate["big_loss_false_positive_penalty"] = round(big_loss_false_positive_penalty, 4)
    candidate["utility_score_after_big_loss_penalty"] = round(c_utility_after_penalty, 4)

    accuracy_ok = c_acc > p_acc
    brier_ok = c_brier < p_brier
    return_ok = c_return >= p_return
    downside_ok = c_downside <= p_downside
    big_loss_ok = True if c_big_loss_rate is None or p_big_loss_rate is None else c_big_loss_rate <= p_big_loss_rate
    big_gain_floor_ok = (c_big_gain_rate or 0.0) >= MIN_BIG_GAIN_CAPTURE_RATE
    utility_ok = c_utility_after_penalty > (p_utility + MIN_UTILITY_IMPROVEMENT)
    symbol_concentration = _numeric_metric(candidate, "symbol_utility_concentration")
    date_concentration = _numeric_metric(candidate, "date_utility_concentration")
    symbol_concentration_ok = symbol_concentration is None or symbol_concentration <= MAX_SYMBOL_UTILITY_CONCENTRATION
    date_concentration_ok = date_concentration is None or date_concentration <= MAX_DATE_UTILITY_CONCENTRATION
    feature_risk_ok = not bool((candidate.get("feature_risk_audit") or {}).get("requires_review"))

    if not accuracy_ok:
        reasons.append("candidate accuracy is below production, but accuracy is informational when profit utility improves")
    if not brier_ok:
        reasons.append("candidate brier score does not improve production")
    if not (return_ok or downside_ok):
        reasons.append("candidate avg_return is lower and downside_risk is higher than production")
    if hard_big_loss_false_positive:
        reasons.append("candidate predicts big-loss rows while production predicts zero; hard false-positive penalty applied")
    if not big_loss_ok:
        reasons.append("candidate big_loss_prediction_rate exceeds production")
    if not big_gain_floor_ok:
        reasons.append(f"candidate big-gain capture is below minimum ({c_big_gain_rate or 0.0:.4f} < {MIN_BIG_GAIN_CAPTURE_RATE:.4f})")
    if not utility_ok:
        reasons.append("candidate profit utility after big-loss penalty does not exceed production")
    if not symbol_concentration_ok:
        reasons.append(f"candidate utility is too concentrated in one symbol ({symbol_concentration:.4f} > {MAX_SYMBOL_UTILITY_CONCENTRATION:.4f})")
    if not date_concentration_ok:
        reasons.append(f"candidate utility is too concentrated on one date ({date_concentration:.4f} > {MAX_DATE_UTILITY_CONCENTRATION:.4f})")
    if not feature_risk_ok:
        reasons.append("candidate feature-risk audit requires review")

    if brier_ok and (return_ok or downside_ok) and big_loss_ok and big_gain_floor_ok and utility_ok and symbol_concentration_ok and date_concentration_ok and feature_risk_ok:
        reasons.append("candidate improves profit utility with acceptable brier, return/downside, big-loss avoidance, and minimum big-gain capture")
        return True, reasons

    reasons.append("candidate did not satisfy profit-aware promotion thresholds")
    return False, reasons


def _ranking_lane_decide(candidate: dict[str, Any], production: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
    reasons: list[str] = []
    candidate_best = candidate.get("best_ranking_backtest") if isinstance(candidate.get("best_ranking_backtest"), dict) else None
    production_best = production.get("best_ranking_backtest") if isinstance(production.get("best_ranking_backtest"), dict) else None
    if not candidate_best or not production_best:
        return False, ["insufficient comparable ranking backtests"], {"candidate": candidate_best, "production": production_best}

    c_total_return = _numeric_metric(candidate_best, "total_return")
    p_total_return = _numeric_metric(production_best, "total_return")
    c_objective = _numeric_metric(candidate_best, "objective_score")
    p_objective = _numeric_metric(production_best, "objective_score")
    c_drawdown = _numeric_metric(candidate_best, "max_drawdown")
    p_drawdown = _numeric_metric(production_best, "max_drawdown")
    c_big_loss_selection_rate = _numeric_metric(candidate_best, "big_loss_selection_rate")
    p_big_loss_selection_rate = _numeric_metric(production_best, "big_loss_selection_rate")
    if None in {c_total_return, p_total_return, c_objective, p_objective, c_drawdown, p_drawdown}:
        return False, ["insufficient comparable ranking return, objective, or drawdown metrics"], {"candidate": candidate_best, "production": production_best}

    total_return_ok = c_total_return >= p_total_return
    objective_ok = c_objective > p_objective
    drawdown_ok = c_drawdown <= p_drawdown
    big_loss_selection_ok = True if c_big_loss_selection_rate is None or p_big_loss_selection_rate is None else c_big_loss_selection_rate <= p_big_loss_selection_rate

    if not total_return_ok:
        reasons.append("ranking challenger top-k total_return is below production")
    if not objective_ok:
        reasons.append("ranking challenger objective_score does not exceed production")
    if not drawdown_ok:
        reasons.append("ranking challenger max_drawdown exceeds production")
    if not big_loss_selection_ok:
        reasons.append("ranking challenger big_loss_selection_rate exceeds production")

    if total_return_ok and objective_ok and drawdown_ok and big_loss_selection_ok:
        reasons.append("ranking challenger improves objective with acceptable top-k return, drawdown, and big-loss selection rate")
        return True, reasons, {"candidate": candidate_best, "production": production_best}

    reasons.append("ranking challenger did not satisfy top-k promotion thresholds")
    return False, reasons, {"candidate": candidate_best, "production": production_best}



def _compact_error_row(row: pd.Series) -> dict[str, Any]:
    return {
        "symbol": str(row.get("symbol", "unknown")),
        "event_date": str(row.get("_event_date", "unknown")),
        "return_5d": round(float(row.get("return_5d", 0.0)), 6) if pd.notna(row.get("return_5d")) else None,
        "candidate_probability": round(float(row.get("_candidate_prob", 0.0)), 6),
        "candidate_prediction": int(row.get("_candidate_pred", 0)),
        "production_probability": round(float(row.get("_production_prob", 0.0)), 6),
        "production_prediction": int(row.get("_production_pred", 0)),
    }


def _artifact_scored_frame(artifact_path: str, test_df: pd.DataFrame, *, prefix: str) -> pd.DataFrame:
    if not Path(artifact_path).exists():
        return pd.DataFrame(index=test_df.index)
    artifact = load_artifact(artifact_path)
    usable = test_df.copy()
    for idx, col in enumerate(artifact.feature_columns):
        if col not in usable.columns:
            usable[col] = np.nan
        numeric = pd.to_numeric(usable[col], errors="coerce").replace([np.inf, -np.inf], np.nan)
        fallback = float(artifact.means[idx]) if idx < len(artifact.means) else 0.0
        usable[col] = numeric.fillna(fallback).astype(float)
    usable["return_5d"] = pd.to_numeric(usable.get("return_5d"), errors="coerce")
    usable = usable.dropna(subset=["return_5d"]).copy()
    usable = _ensure_return_bins(usable)
    if usable.empty:
        return usable
    probs = predict_proba(artifact, usable[artifact.feature_columns].to_numpy(dtype=float))
    usable[f"_{prefix}_prob"] = probs
    usable[f"_{prefix}_pred"] = (probs >= artifact.decision_threshold).astype(int)
    usable[f"_{prefix}_threshold"] = float(artifact.decision_threshold)
    return usable



def _feature_contributions(artifact_path: str, row: pd.Series) -> list[dict[str, Any]]:
    if not Path(artifact_path).exists():
        return []
    artifact = load_artifact(artifact_path)
    contributions: list[dict[str, Any]] = []
    for idx, feature in enumerate(artifact.feature_columns):
        value = row.get(feature, np.nan)
        value = float(pd.to_numeric(pd.Series([value]), errors="coerce").fillna(artifact.means[idx] if idx < len(artifact.means) else 0.0).iloc[0])
        mean = float(artifact.means[idx]) if idx < len(artifact.means) else 0.0
        std = float(artifact.stds[idx]) if idx < len(artifact.stds) and float(artifact.stds[idx]) != 0.0 else 1.0
        weight = float(artifact.weights[idx]) if idx < len(artifact.weights) else 0.0
        contribution = ((value - mean) / std) * weight
        contributions.append({"feature": feature, "value": round(value, 6), "weight": round(weight, 6), "contribution": round(float(contribution), 6)})
    return sorted(contributions, key=lambda item: abs(float(item["contribution"])), reverse=True)


def _stored_regression_example(symbol: str, event_date: str) -> dict[str, Any] | None:
    if not REGRESSION_EXAMPLES_DIR.exists():
        return None
    for path in sorted(REGRESSION_EXAMPLES_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(payload.get("symbol", "")).upper() == symbol.upper() and str(payload.get("event_date", "")) == event_date:
            return {**payload, "path": str(path.relative_to(PROJECT_ROOT))}
    return None


def _cmi_false_positive_diagnostic(candidate_model_path: str, production_model_path: str, false_positives: pd.DataFrame) -> dict[str, Any] | None:
    if false_positives.empty or "symbol" not in false_positives.columns:
        return None
    cmi_rows = false_positives[false_positives["symbol"].fillna("").astype(str).str.upper() == "CMI"]
    if cmi_rows.empty:
        return None
    row = cmi_rows.iloc[0]
    candidate_contributions = _feature_contributions(candidate_model_path, row)
    production_contributions = _feature_contributions(production_model_path, row)
    event_date = str(row.get("_event_date") or row.get("event_date") or "")
    regression_example = _stored_regression_example("CMI", event_date)
    return {
        **_compact_error_row(row),
        "diagnostic": "CMI big-loss false positive added to bad-buy mining rows; top positive candidate contributions show which features pushed the candidate above threshold.",
        "top_candidate_positive_features": [item for item in candidate_contributions if float(item["contribution"]) > 0.0][:10],
        "top_candidate_absolute_features": candidate_contributions[:10],
        "top_production_absolute_features": production_contributions[:10],
        "stored_regression_example": regression_example,
    }

def _prediction_error_examples(candidate_model_path: str, production_model_path: str, test_df: pd.DataFrame, *, limit: int = 100) -> dict[str, Any]:
    candidate = _artifact_scored_frame(candidate_model_path, test_df, prefix="candidate")
    production = _artifact_scored_frame(production_model_path, test_df, prefix="production")
    if candidate.empty or production.empty:
        return {"chosen_threshold": None, "prediction_overlap": {"rows": 0}, "big_loss_false_positives": [], "missed_big_gain_rows": []}
    joined = candidate.join(production[["_production_prob", "_production_pred", "_production_threshold"]], how="inner")
    joined["_event_date"] = _event_date_series(joined)
    rows = int(len(joined))
    agreement = float((joined["_candidate_pred"] == joined["_production_pred"]).mean()) if rows else 0.0
    both_positive = int(((joined["_candidate_pred"] == 1) & (joined["_production_pred"] == 1)).sum())
    candidate_positive = int((joined["_candidate_pred"] == 1).sum())
    production_positive = int((joined["_production_pred"] == 1).sum())
    union_positive = int(((joined["_candidate_pred"] == 1) | (joined["_production_pred"] == 1)).sum())
    bins = joined["return_bin_5d"].fillna("").astype(str)
    big_loss_fp = joined[(bins == "big_loss") & (joined["_candidate_pred"] == 1) & (joined["_production_pred"] == 0)]
    missed_big_gain = joined[(bins == "big_gain") & (joined["_candidate_pred"] == 0)]
    cmi_candidate_positive = joined[(bins == "big_loss") & (joined["_candidate_pred"] == 1) & (joined.get("symbol", pd.Series("", index=joined.index)).fillna("").astype(str).str.upper() == "CMI")]
    cmi_diagnostic = _cmi_false_positive_diagnostic(candidate_model_path, production_model_path, cmi_candidate_positive)
    return {
        "chosen_threshold": round(float(joined["_candidate_threshold"].iloc[0]), 6) if rows else None,
        "mistake_scoring_method": "artifact_predictions",
        "production_threshold": round(float(joined["_production_threshold"].iloc[0]), 6) if rows else None,
        "prediction_overlap": {
            "rows": rows,
            "prediction_agreement": round(agreement, 4),
            "candidate_positive_predictions": candidate_positive,
            "production_positive_predictions": production_positive,
            "shared_positive_predictions": both_positive,
            "positive_prediction_jaccard": round(both_positive / union_positive, 4) if union_positive else None,
        },
        "big_loss_false_positives": [_compact_error_row(row) for _, row in big_loss_fp.head(limit).iterrows()],
        "big_loss_false_positive_count": int(len(big_loss_fp)),
        "missed_big_gain_rows": [_compact_error_row(row) for _, row in missed_big_gain.head(limit).iterrows()],
        "missed_big_gain_count": int(len(missed_big_gain)),
        "cmi_false_positive_diagnostic": cmi_diagnostic,
    }


def _promotion_decision(candidate_win: bool, no_op_clone: bool, decision_win: bool, ranking_win: bool, walk_forward_consistent: bool) -> str:
    if no_op_clone:
        return "NO_OP_CLONE"
    if candidate_win:
        return "PROMOTE"
    if decision_win and ranking_win and not walk_forward_consistent:
        return "WATCH"
    return "HOLD"


def _threshold_item_at(metrics: dict[str, Any], threshold: float) -> dict[str, Any] | None:
    for item in metrics.get("threshold_search") or []:
        if abs(float(item.get("threshold", -1.0)) - float(threshold)) < 1e-9:
            return item
    return None


def _threshold_guardrails_pass(item: dict[str, Any], current: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    item_big_loss_predictions = _numeric_metric(item, "big_loss_predictions") or 0.0
    current_big_loss_predictions = _numeric_metric(current, "big_loss_predictions") or 0.0
    item_big_loss_rate = _numeric_metric(item, "big_loss_prediction_rate")
    current_big_loss_rate = _numeric_metric(current, "big_loss_prediction_rate")
    item_big_gain_capture = _numeric_metric(item, "big_gain_capture_rate") or 0.0
    item_positive = _numeric_metric(item, "positive_predictions") or 0.0
    current_positive = _numeric_metric(current, "positive_predictions") or 0.0
    min_positive = max(MIN_THRESHOLD_POSITIVE_PREDICTIONS, current_positive * MIN_THRESHOLD_POSITIVE_FRACTION_OF_CURRENT)

    if not (item_big_loss_predictions == 0.0 or item_big_loss_predictions <= current_big_loss_predictions):
        reasons.append("big_loss_predictions would increase versus current threshold")
    if item_big_loss_rate is not None and current_big_loss_rate is not None and item_big_loss_rate > current_big_loss_rate:
        reasons.append("big_loss_prediction_rate would exceed current threshold")
    if item_big_gain_capture < MIN_THRESHOLD_BIG_GAIN_CAPTURE_RATE:
        reasons.append(f"big_gain_capture_rate below minimum ({item_big_gain_capture:.4f} < {MIN_THRESHOLD_BIG_GAIN_CAPTURE_RATE:.4f})")
    if item_positive < min_positive:
        reasons.append(f"positive_predictions below minimum ({int(item_positive)} < {min_positive:.1f})")
    symbol_concentration = _numeric_metric(item, "symbol_utility_concentration")
    date_concentration = _numeric_metric(item, "date_utility_concentration")
    if symbol_concentration is not None and symbol_concentration > MAX_SYMBOL_UTILITY_CONCENTRATION:
        reasons.append(f"symbol utility concentration exceeds maximum ({symbol_concentration:.4f} > {MAX_SYMBOL_UTILITY_CONCENTRATION:.4f})")
    if date_concentration is not None and date_concentration > MAX_DATE_UTILITY_CONCENTRATION:
        reasons.append(f"date utility concentration exceeds maximum ({date_concentration:.4f} > {MAX_DATE_UTILITY_CONCENTRATION:.4f})")
    return not reasons, reasons


def _select_threshold_from_search(metrics: dict[str, Any], current_threshold: float) -> dict[str, Any]:
    current = _threshold_item_at(metrics, current_threshold) or {
        "threshold": current_threshold,
        "utility_score": metrics.get("utility_score"),
        "positive_predictions": metrics.get("positive_predictions"),
        "big_loss_predictions": metrics.get("big_loss_predictions"),
        "big_loss_prediction_rate": metrics.get("big_loss_prediction_rate"),
        "big_gain_capture_rate": metrics.get("big_gain_capture_rate"),
    }
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in metrics.get("threshold_search") or []:
        if not isinstance(item.get("utility_score"), (int, float)):
            continue
        passed, guardrail_reasons = _threshold_guardrails_pass(item, current)
        if passed:
            candidates.append(item)
        else:
            rejected.append({"threshold": item.get("threshold"), "reasons": guardrail_reasons})
    if not candidates:
        return {"recommended_threshold": float(current_threshold), "selected": current, "rejected": rejected, "reason": "no threshold passed guardrails"}
    best, flat_optimum = _flat_optimum_threshold(candidates)
    plateau_thresholds = [float(value) for value in flat_optimum["selected_plateau_thresholds"]]
    if any(abs(float(current_threshold) - value) < 1e-9 for value in plateau_thresholds):
        return {
            "recommended_threshold": float(current_threshold),
            "selected": current,
            "rejected": rejected,
            "flat_optimum": flat_optimum,
            "reason": "current threshold is inside the flat-optimum utility plateau",
        }
    current_utility = _numeric_metric(current, "utility_score")
    best_utility = _numeric_metric(best, "utility_score")
    if current_utility is not None and best_utility is not None and best_utility <= current_utility:
        return {"recommended_threshold": float(current_threshold), "selected": current, "rejected": rejected, "flat_optimum": flat_optimum, "reason": "current threshold utility is already best after guardrails"}
    return {"recommended_threshold": float(best["threshold"]), "selected": best, "rejected": rejected, "flat_optimum": flat_optimum, "reason": "flat-optimum threshold passed guardrails"}

def _walk_forward_consistency(window_results: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = [item for item in window_results if item.get("evaluated")]
    consistent = len(evaluated) >= 2 and all(item.get("candidate_win") for item in evaluated)
    return {
        "windows_requested": WALK_FORWARD_WINDOWS,
        "windows_evaluated": len(evaluated),
        "consistent": bool(consistent),
        "windows": window_results,
    }


def _walk_forward_validation(candidate_model_path: str, production_model_path: str, test_df: pd.DataFrame, *, min_rows: int) -> dict[str, Any]:
    if "ts" in test_df.columns:
        test_df = test_df.sort_values("ts").reset_index(drop=True)
    split_indices = np.array_split(np.arange(len(test_df)), WALK_FORWARD_WINDOWS)
    chunks = [test_df.iloc[indexes].copy() for indexes in split_indices if len(indexes)]
    candidate_recipe_fingerprint = _artifact_config_fingerprint(candidate_model_path)
    production_recipe_fingerprint = _artifact_config_fingerprint(production_model_path)
    window_results: list[dict[str, Any]] = []
    window_min_rows = max(1, int(min_rows) // max(1, len(chunks)))
    for index, window_df in enumerate(chunks, start=1):
        if len(window_df) < window_min_rows:
            window_results.append({"window": index, "rows": int(len(window_df)), "evaluated": False, "candidate_win": False, "reasons": [f"window rows below minimum ({len(window_df)} < {window_min_rows})"]})
            continue
        candidate_metrics = _evaluate(candidate_model_path, window_df)
        production_metrics = _evaluate(production_model_path, window_df)
        decision_win, decision_reasons = _decide(candidate_metrics, production_metrics, min_rows=window_min_rows)
        ranking_win, ranking_reasons, _ = _ranking_lane_decide(candidate_metrics, production_metrics)
        window_results.append({
            "window": index,
            "rows": int(len(window_df)),
            "evaluated": True,
            "candidate_win": bool(decision_win and ranking_win),
            "decision_model_win": bool(decision_win),
            "ranking_win": bool(ranking_win),
            "candidate_recipe_fingerprint": candidate_recipe_fingerprint,
            "production_recipe_fingerprint": production_recipe_fingerprint,
            "reasons": [*(f"decision lane: {reason}" for reason in decision_reasons), *(f"ranking lane: {reason}" for reason in ranking_reasons)],
        })
    result = _walk_forward_consistency(window_results)
    result["candidate_recipe_fingerprint"] = candidate_recipe_fingerprint
    result["production_recipe_fingerprint"] = production_recipe_fingerprint
    result["recipe_reproduction_passed"] = bool(
        candidate_recipe_fingerprint
        and production_recipe_fingerprint
        and all(item.get("candidate_recipe_fingerprint") == candidate_recipe_fingerprint and item.get("production_recipe_fingerprint") == production_recipe_fingerprint for item in window_results if item.get("evaluated"))
    )
    return result


def _evaluate_artifact_threshold(artifact_path: str, frame: pd.DataFrame, threshold: float) -> dict[str, Any]:
    if not Path(artifact_path).exists():
        return {"rows": 0}
    artifact = load_artifact(artifact_path)
    usable = frame.copy()
    for idx, col in enumerate(artifact.feature_columns):
        if col not in usable.columns:
            usable[col] = np.nan
        numeric = pd.to_numeric(usable[col], errors="coerce").replace([np.inf, -np.inf], np.nan)
        fallback = float(artifact.means[idx]) if idx < len(artifact.means) else 0.0
        usable[col] = numeric.fillna(fallback).astype(float)
    usable["return_5d"] = pd.to_numeric(usable.get("return_5d"), errors="coerce")
    usable = _ensure_return_bins(usable.dropna(subset=["return_5d"]).copy())
    if usable.empty:
        return {"rows": 0}
    probs = predict_proba(artifact, usable[artifact.feature_columns].to_numpy(dtype=float))
    preds = (probs >= float(threshold)).astype(int)
    return {**_prediction_return_metrics(usable, preds, probs), "threshold": float(threshold), "rows": int(len(usable))}


def _threshold_stability_summary(best_thresholds: list[float], recommended_threshold: float) -> dict[str, Any]:
    threshold_spread = max(best_thresholds) - min(best_thresholds) if best_thresholds else None
    agreement_count = sum(abs(value - recommended_threshold) <= 0.025 for value in best_thresholds)
    agreement_required = max(2, int(np.ceil(len(best_thresholds) * 2 / 3))) if best_thresholds else 0
    stable = bool(
        len(best_thresholds) >= 2
        and threshold_spread is not None
        and threshold_spread <= MAX_STABLE_THRESHOLD_SPREAD
        and agreement_count >= agreement_required
    )
    return {
        "stable": stable,
        "window_best_thresholds": best_thresholds,
        "max_threshold_spread": MAX_STABLE_THRESHOLD_SPREAD,
        "observed_threshold_spread": round(threshold_spread, 6) if threshold_spread is not None else None,
        "recommended_threshold_agreement_windows": agreement_count,
        "recommended_threshold_agreement_required": agreement_required,
    }


def _threshold_walk_forward_results(artifact_path: str, test_df: pd.DataFrame, current_threshold: float, recommended_threshold: float, *, min_rows: int) -> dict[str, Any]:
    if abs(float(current_threshold) - float(recommended_threshold)) < 1e-9:
        return {"consistent": False, "windows": [], "reason": "recommended threshold equals current threshold"}
    if "ts" in test_df.columns:
        test_df = test_df.sort_values("ts").reset_index(drop=True)
    split_indices = np.array_split(np.arange(len(test_df)), WALK_FORWARD_WINDOWS)
    chunks = [test_df.iloc[indexes].copy() for indexes in split_indices if len(indexes)]
    window_min_rows = max(1, int(min_rows) // max(1, len(chunks)))
    windows: list[dict[str, Any]] = []
    for index, window_df in enumerate(chunks, start=1):
        if len(window_df) < window_min_rows:
            windows.append({"window": index, "rows": int(len(window_df)), "passed": False, "reason": "window below minimum rows"})
            continue
        current = _evaluate_artifact_threshold(artifact_path, window_df, current_threshold)
        proposed = _evaluate_artifact_threshold(artifact_path, window_df, recommended_threshold)
        window_search = [_evaluate_artifact_threshold(artifact_path, window_df, threshold) for threshold in THRESHOLD_SEARCH_VALUES]
        window_selection = _select_threshold_from_search({"threshold_search": window_search, **current}, current_threshold)
        window_best_threshold = float(window_selection["recommended_threshold"])
        guardrails_ok, guardrail_reasons = _threshold_guardrails_pass(proposed, current)
        current_utility = _numeric_metric(current, "utility_score")
        proposed_utility = _numeric_metric(proposed, "utility_score")
        utility_ok = current_utility is not None and proposed_utility is not None and proposed_utility > current_utility
        windows.append({
            "window": index,
            "rows": int(len(window_df)),
            "current": current,
            "proposed": proposed,
            "window_best_guardrail_threshold": window_best_threshold,
            "window_threshold_selection_reason": window_selection["reason"],
            "passed": bool(guardrails_ok and utility_ok),
            "reasons": [*guardrail_reasons, *([] if utility_ok else ["proposed threshold utility did not beat current threshold"])],
        })
    evaluated = [item for item in windows if item.get("current", {}).get("rows", 0)]
    consistent = len(evaluated) >= 2 and all(item.get("passed") for item in evaluated)
    best_thresholds = [float(item["window_best_guardrail_threshold"]) for item in evaluated]
    stability = _threshold_stability_summary(best_thresholds, recommended_threshold)
    return {
        "consistent": bool(consistent),
        "threshold_stable": stability["stable"],
        "threshold_stability": stability,
        "windows_evaluated": len(evaluated),
        "windows": windows,
    }


def _threshold_optimizer_report(artifact_path: str, metrics: dict[str, Any], test_df: pd.DataFrame, *, min_rows: int) -> dict[str, Any]:
    current_threshold = None
    if Path(artifact_path).exists():
        current_threshold = float(load_artifact(artifact_path).decision_threshold)
    if current_threshold is None:
        return {"current_threshold": None, "recommended_threshold": None, "threshold_change_recommended": False, "threshold_change_reason": "artifact unavailable", "threshold_walk_forward_results": {}}
    selection = _select_threshold_from_search(metrics, current_threshold)
    recommended = float(selection["recommended_threshold"])
    walk_forward = _threshold_walk_forward_results(artifact_path, test_df, current_threshold, recommended, min_rows=min_rows)
    change_recommended = abs(recommended - current_threshold) > 1e-9 and bool(walk_forward.get("consistent")) and bool(walk_forward.get("threshold_stable"))
    if change_recommended:
        reason = f"{selection['reason']}; walk-forward consistent"
    elif abs(recommended - current_threshold) <= 1e-9:
        reason = str(selection["reason"])
    else:
        reason = f"{selection['reason']}; walk-forward consistency and threshold stability required before changing live threshold"
    return {
        "current_threshold": round(current_threshold, 6),
        "recommended_threshold": round(recommended if change_recommended else current_threshold, 6),
        "best_guardrail_threshold": round(recommended, 6),
        "threshold_change_recommended": bool(change_recommended),
        "threshold_change_reason": reason,
        "selected_threshold_metrics": selection.get("selected"),
        "flat_optimum": selection.get("flat_optimum"),
        "rejected_thresholds": selection.get("rejected"),
        "threshold_walk_forward_results": walk_forward,
        "deployable_model_config": {"model_path": artifact_path, "decision_threshold": round(recommended if change_recommended else current_threshold, 6)},
    }


LEAKAGE_OUTCOME_COLUMNS = (
    "return_5d",
    "return_1d",
    "label_up_5d",
    "label_gain_5d",
    "label_profit_5d",
    "outcome_1d",
    "outcome_5d",
    "forward_return_5d",
    "future_return_5d",
    "realized_return_5d",
)
VALUE_LEAKAGE_EQUALITY_RATE = 0.95
VALUE_LEAKAGE_CORRELATION = 0.995


def _future_feature_leakage_audit(*artifact_paths: str, frame: pd.DataFrame | None = None) -> dict[str, Any]:
    artifacts: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    value_checks: list[dict[str, Any]] = []
    for artifact_path in artifact_paths:
        if not Path(artifact_path).exists():
            violations.append({"artifact_path": artifact_path, "feature": "<artifact unavailable>", "reason": "artifact_unavailable"})
            continue
        artifact = load_artifact(artifact_path)
        artifacts.append({"artifact_path": artifact_path, "model_version": artifact.version, "feature_columns": artifact.feature_columns})
        safe_features = set(_future_safe_feature_columns([str(feature) for feature in artifact.feature_columns]))
        for feature in artifact.feature_columns:
            feature_name = str(feature)
            if feature_name not in safe_features:
                violations.append({"artifact_path": artifact_path, "feature": feature_name, "reason": "forbidden_feature_name"})
                continue
            if frame is None or feature_name not in frame.columns:
                continue
            feature_values = pd.to_numeric(frame[feature_name], errors="coerce")
            for outcome_col in LEAKAGE_OUTCOME_COLUMNS:
                if outcome_col not in frame.columns:
                    continue
                outcome_values = pd.to_numeric(frame[outcome_col], errors="coerce")
                paired = pd.DataFrame({"feature": feature_values, "outcome": outcome_values}).dropna()
                rows = int(len(paired))
                if rows < 20:
                    continue
                equality_rate = float(np.isclose(paired["feature"].to_numpy(), paired["outcome"].to_numpy(), rtol=1e-9, atol=1e-12).mean())
                correlation = None
                if float(paired["feature"].std()) > 0.0 and float(paired["outcome"].std()) > 0.0:
                    correlation = float(paired["feature"].corr(paired["outcome"]))
                check = {
                    "artifact_path": artifact_path,
                    "feature": feature_name,
                    "outcome_column": outcome_col,
                    "rows_compared": rows,
                    "equality_rate": round(equality_rate, 6),
                    "correlation": round(correlation, 6) if correlation is not None else None,
                }
                value_checks.append(check)
                if equality_rate >= VALUE_LEAKAGE_EQUALITY_RATE:
                    violations.append({**check, "reason": "feature_matches_outcome_values"})
                elif correlation is not None and abs(correlation) >= VALUE_LEAKAGE_CORRELATION:
                    violations.append({**check, "reason": "feature_correlates_with_outcome_values"})
    return {"passed": not violations, "violations": violations, "value_checks": value_checks, "artifacts": artifacts}


def _max_numeric_delta(left: Any, right: Any) -> float:
    if isinstance(left, dict) and isinstance(right, dict):
        keys = set(left) & set(right)
        return max((_max_numeric_delta(left[key], right[key]) for key in keys), default=0.0)
    if isinstance(left, list) and isinstance(right, list):
        return max((_max_numeric_delta(a, b) for a, b in zip(left, right)), default=0.0) if len(left) == len(right) else float("inf")
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right))
    return 0.0 if left == right else float("inf")


def _split_hygiene_certification(candidate_model_path: str) -> tuple[bool, bool, bool, list[str]]:
    metadata_path = Path(candidate_model_path).with_suffix(Path(candidate_model_path).suffix + ".meta.json")
    if not metadata_path.exists():
        return False, False, False, ["candidate metadata with four-period split diagnostics is unavailable"]
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, False, False, ["candidate metadata is unreadable"]
    periods = ((metadata.get("metrics") or {}).get("training_periods") or {})
    training_recipe_reproduced = bool((((metadata.get("metrics") or {}).get("recipe_reproduction") or {}).get("passed")))
    windows = periods.get("windows") or {}
    required = ["fit", "calibration", "threshold_selection", "final_test"]
    split_ok = all(name in windows and int((windows[name] or {}).get("rows") or 0) > 0 for name in required)
    ordered = True
    for left_name, right_name in zip(required, required[1:]):
        left_end = str((windows.get(left_name) or {}).get("end") or "")
        right_start = str((windows.get(right_name) or {}).get("start") or "")
        if not left_end or not right_start or left_end >= right_start:
            ordered = False
    split_ok = split_ok and ordered
    boundaries = periods.get("purge_embargo_boundaries") or []
    purge_ok = bool(periods.get("purged_embargoed")) and len(boundaries) == 3 and all(
        int(item.get("date_overlap_count") or 0) == 0
        and int(item.get("symbol_date_overlap_count") or 0) == 0
        and bool(item.get("label_horizon_gap_passed"))
        for item in boundaries
    )
    issues = []
    if not split_ok:
        issues.append("fit/calibration/threshold-selection/final-test windows are missing, overlapping, or out of order")
    if not purge_ok:
        issues.append("purge/embargo diagnostics do not prove zero date and symbol/date overlap at all boundaries")
    if not training_recipe_reproduced:
        issues.append("training recipe rerun did not reproduce model, calibration, and threshold parameters")
    return split_ok, purge_ok, training_recipe_reproduced, issues


def _phase_1_certification(
    *,
    candidate_model_path: str,
    production_model_path: str,
    test_df: pd.DataFrame,
    min_rows: int,
    candidate_metrics: dict[str, Any],
    clone_detection: dict[str, Any],
    walk_forward: dict[str, Any],
    threshold_optimizer: dict[str, Any],
    promotion_decision: str,
    report_examples: dict[str, Any],
) -> dict[str, Any]:
    rerun_metrics = _evaluate(candidate_model_path, test_df.copy())
    metric_delta = _max_numeric_delta({key: value for key, value in candidate_metrics.items() if key != "feature_risk_audit"}, rerun_metrics)
    rerun_clone = _clone_detection(candidate_model_path, production_model_path, test_df.copy())
    if clone_detection.get("no_op_clone"):
        candidate_recipe_fingerprint = _artifact_config_fingerprint(candidate_model_path)
        production_recipe_fingerprint = _artifact_config_fingerprint(production_model_path)
        rerun_walk_forward = {
            "consistent": False,
            "skipped": True,
            "reason": "no-op clone detected before full promotion evaluation",
            "windows": [],
            "candidate_recipe_fingerprint": candidate_recipe_fingerprint,
            "production_recipe_fingerprint": production_recipe_fingerprint,
            "recipe_reproduction_passed": bool(candidate_recipe_fingerprint and production_recipe_fingerprint),
        }
        rerun_promotion = "NO_OP_CLONE"
    else:
        rerun_walk_forward = _walk_forward_validation(candidate_model_path, production_model_path, test_df.copy(), min_rows=min_rows)
        rerun_candidate = _evaluate(candidate_model_path, test_df.copy())
        rerun_candidate["feature_risk_audit"] = _feature_risk_audit(candidate_model_path, test_df.copy())
        rerun_production = _evaluate(production_model_path, test_df.copy())
        decision_win, _ = _decide(rerun_candidate, rerun_production, min_rows=min_rows)
        ranking_win, _, _ = _ranking_lane_decide(rerun_candidate, rerun_production)
        rerun_win = decision_win and ranking_win and bool(rerun_walk_forward.get("consistent"))
        rerun_promotion = _promotion_decision(rerun_win, False, decision_win, ranking_win, bool(rerun_walk_forward.get("consistent")))
    rerun_threshold = _threshold_optimizer_report(candidate_model_path, rerun_metrics, test_df.copy(), min_rows=min_rows)
    fold_outcomes = [item.get("candidate_win") for item in walk_forward.get("windows", [])]
    rerun_fold_outcomes = [item.get("candidate_win") for item in rerun_walk_forward.get("windows", [])]
    reproducible = bool(
        metric_delta <= 1e-9
        and promotion_decision == rerun_promotion
        and fold_outcomes == rerun_fold_outcomes
        and threshold_optimizer.get("recommended_threshold") == rerun_threshold.get("recommended_threshold")
        and clone_detection.get("candidate_prediction_fingerprint") == rerun_clone.get("candidate_prediction_fingerprint")
    )
    split_ok, purge_ok, training_recipe_reproduced, split_issues = _split_hygiene_certification(candidate_model_path)
    recipe_reproduction = training_recipe_reproduced and bool(walk_forward.get("recipe_reproduction_passed")) and bool(rerun_walk_forward.get("recipe_reproduction_passed")) and fold_outcomes == rerun_fold_outcomes
    leakage_audit = _future_feature_leakage_audit(candidate_model_path, production_model_path, frame=test_df)
    mistake_ok = report_examples.get("mistake_scoring_method") == "artifact_predictions"
    stored_cmi = _stored_regression_example("CMI", "2026-07-10")
    candidate_scored = _artifact_scored_frame(candidate_model_path, test_df, prefix="candidate")
    cmi_positive = False
    if not candidate_scored.empty and "symbol" in candidate_scored.columns:
        dates = _event_date_series(candidate_scored)
        cmi_positive = bool(((candidate_scored["symbol"].fillna("").astype(str).str.upper() == "CMI") & (dates == "2026-07-10") & (candidate_scored["_candidate_pred"] == 1)).any())
    cmi_ok = stored_cmi is not None and (not cmi_positive or report_examples.get("cmi_false_positive_diagnostic") is not None)
    clone_ok = bool(clone_detection.get("candidate_prediction_fingerprint")) and (
        not clone_detection.get("no_op_clone") or promotion_decision == "NO_OP_CLONE"
    )
    threshold_change = bool(threshold_optimizer.get("threshold_change_recommended"))
    threshold_wf = threshold_optimizer.get("threshold_walk_forward_results") or {}
    threshold_ok = not threshold_change or bool(
        threshold_wf.get("consistent")
        and threshold_wf.get("threshold_stable")
        and threshold_wf.get("windows")
        and all(item.get("passed") for item in threshold_wf.get("windows", []) if item.get("current"))
    )
    symbol_concentration = _numeric_metric(candidate_metrics, "symbol_utility_concentration")
    date_concentration = _numeric_metric(candidate_metrics, "date_utility_concentration")
    concentration_exceeded = (symbol_concentration is not None and symbol_concentration > MAX_SYMBOL_UTILITY_CONCENTRATION) or (date_concentration is not None and date_concentration > MAX_DATE_UTILITY_CONCENTRATION)
    if concentration_exceeded:
        concentration_candidate = _evaluate(candidate_model_path, test_df.copy())
        concentration_candidate["feature_risk_audit"] = _feature_risk_audit(candidate_model_path, test_df.copy())
        concentration_production = _evaluate(production_model_path, test_df.copy())
        concentration_win, concentration_reasons = _decide(concentration_candidate, concentration_production, min_rows=min_rows)
        concentration_ok = not concentration_win and any("concentrated" in reason for reason in concentration_reasons)
    else:
        concentration_ok = True
    checks = {
        "reproducible": reproducible,
        "recipe_reproduction_passed": recipe_reproduction,
        "split_hygiene_passed": split_ok,
        "purge_embargo_passed": purge_ok,
        "future_feature_leakage_passed": bool(leakage_audit["passed"]),
        "artifact_scored_mistake_mining_passed": mistake_ok,
        "cmi_regression_test_passed": cmi_ok,
        "clone_detection_passed": clone_ok,
        "threshold_walk_forward_guardrails_passed": threshold_ok,
        "symbol_date_concentration_passed": concentration_ok,
    }
    blocking_issues = list(split_issues)
    blocking_issues.extend(f"{name} failed" for name, passed in checks.items() if not passed and name not in {"split_hygiene_passed", "purge_embargo_passed"})
    return {
        "phase_1_certified": all(checks.values()),
        **checks,
        "blocking_issues": blocking_issues,
        "diagnostics": {
            "maximum_metric_delta_between_runs": metric_delta,
            "first_promotion_decision": promotion_decision,
            "rerun_promotion_decision": rerun_promotion,
            "first_fold_outcomes": fold_outcomes,
            "rerun_fold_outcomes": rerun_fold_outcomes,
            "first_threshold_recommendation": threshold_optimizer.get("recommended_threshold"),
            "rerun_threshold_recommendation": rerun_threshold.get("recommended_threshold"),
            "future_feature_leakage_audit": leakage_audit,
        },
    }


def _phase_1_gate(
    *,
    candidate_model_path: str,
    production_model_path: str,
    detailed_certification: dict[str, Any],
    walk_forward: dict[str, Any],
    clone_detection: dict[str, Any],
    threshold_optimizer: dict[str, Any],
    report_examples: dict[str, Any],
    temporal_split: dict[str, Any],
    production_feature_risk: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate_lineage = load_artifact(candidate_model_path).lineage if Path(candidate_model_path).exists() else None
    recipe_lineage_passed = bool(
        candidate_lineage
        and candidate_lineage.get("schema_version") == "moneybot-challenger-lineage.v1"
        and candidate_lineage.get("lineage_id")
        and len(str(candidate_lineage.get("recipe_hash") or "")) == 64
        and isinstance(candidate_lineage.get("recipe"), dict)
    )
    no_op_detected_correctly = not clone_detection.get("no_op_clone") or bool(clone_detection.get("candidate_prediction_fingerprint"))
    threshold_change = bool(threshold_optimizer.get("threshold_change_recommended"))
    threshold_results = threshold_optimizer.get("threshold_walk_forward_results") or {}
    threshold_guardrails_passed = not threshold_change or bool(
        threshold_results.get("consistent")
        and threshold_results.get("threshold_stable")
        and threshold_results.get("windows")
        and all(item.get("passed") for item in threshold_results.get("windows", []) if item.get("current"))
    )
    report_traceability_passed = bool(
        Path(candidate_model_path).exists()
        and Path(production_model_path).exists()
        and clone_detection.get("candidate_prediction_fingerprint")
        and clone_detection.get("production_prediction_fingerprint")
        and report_examples.get("chosen_threshold") is not None
        and report_examples.get("mistake_scoring_method") == "artifact_predictions"
        and temporal_split.get("train_rows_before") is not None
        and temporal_split.get("test_rows_before") is not None
    )
    gate_results = {
        "reproducibility_passed": bool(detailed_certification.get("reproducible")),
        "recipe_lineage_passed": recipe_lineage_passed,
        "walk_forward_recipe_reproduction_passed": bool(detailed_certification.get("recipe_reproduction_passed")) and bool(walk_forward.get("recipe_reproduction_passed")),
        "split_hygiene_passed": bool(detailed_certification.get("split_hygiene_passed")),
        "purge_embargo_passed": bool(detailed_certification.get("purge_embargo_passed")),
        "future_feature_leakage_passed": bool(detailed_certification.get("future_feature_leakage_passed")),
        "artifact_scored_mistake_mining_passed": bool(detailed_certification.get("artifact_scored_mistake_mining_passed")),
        "cmi_regression_detection_passed": bool(detailed_certification.get("cmi_regression_test_passed")),
        "clone_detection_passed": bool(detailed_certification.get("clone_detection_passed")) and no_op_detected_correctly,
        "threshold_guardrails_passed": bool(detailed_certification.get("threshold_walk_forward_guardrails_passed")) and threshold_guardrails_passed,
        "symbol_date_concentration_handling_passed": bool(detailed_certification.get("symbol_date_concentration_passed")),
        "report_traceability_passed": report_traceability_passed,
    }
    issue_messages = {
        "reproducibility_passed": "candidate recipe rerun did not reproduce metrics, folds, threshold recommendation, fingerprint, and decision",
        "recipe_lineage_passed": "candidate artifact is missing valid recipe lineage",
        "walk_forward_recipe_reproduction_passed": "walk-forward folds did not preserve the exact candidate recipe fingerprint",
        "split_hygiene_passed": "fit, calibration, threshold-selection, and test windows are not separate and chronological",
        "purge_embargo_passed": "purge/embargo evidence does not prove a clean label-horizon and symbol/date boundary",
        "future_feature_leakage_passed": "candidate or production features contain labels, outcomes, future/forward returns, or realized returns",
        "artifact_scored_mistake_mining_passed": "mistake mining is not proven to use scored artifact predictions",
        "cmi_regression_detection_passed": "stored CMI 2026-07-10 regression detection is missing or did not trigger",
        "clone_detection_passed": "clone detection did not produce traceable fingerprints or correctly identify a near-duplicate",
        "threshold_guardrails_passed": "threshold changes are not proven to be blocked unless all walk-forward guardrails pass",
        "symbol_date_concentration_handling_passed": "symbol/date concentration is neither within limits nor correctly flagged as a promotion block",
        "report_traceability_passed": "report is missing artifact, fingerprint, threshold, mistake-scoring, or split traceability",
    }
    blocking_issues = [issue_messages[name] for name, passed in gate_results.items() if not passed]
    certified = all(gate_results.values())
    warnings: list[str] = []
    production_feature_risk = production_feature_risk or {}
    raw_price_count = int(production_feature_risk.get("raw_price_top_positive_contributor_count") or 0)
    if production_feature_risk.get("raw_feature_price_present") and raw_price_count > 0:
        raw_price_rate = float(production_feature_risk.get("raw_price_top_positive_contributor_rate") or 0.0)
        warnings.append(
            "production raw feature_price remains a top positive contributor "
            f"for {raw_price_count} scored rows (rate={raw_price_rate:.4f}); monitor high-price symbols"
        )
    return {
        "phase_1_certified": certified,
        "ready_for_phase_2": certified,
        "blocking_issues": blocking_issues,
        "warnings": warnings,
        "gate_results": gate_results,
    }


def _production_promotion_gates(
    *,
    candidate_model_path: str,
    candidate_metrics: dict[str, Any],
    production_metrics: dict[str, Any],
    decision_win: bool,
    ranking_win: bool,
    no_op_clone: bool,
    walk_forward: dict[str, Any],
    phase_1_gate: dict[str, Any],
    report_examples: dict[str, Any],
    threshold_optimizer: dict[str, Any],
    candidate_feature_risk: dict[str, Any],
    bootstrap: dict[str, Any],
) -> dict[str, Any]:
    lineage = load_artifact(candidate_model_path).lineage if Path(candidate_model_path).exists() else None
    recipe = lineage.get("recipe") if isinstance(lineage, dict) and isinstance(lineage.get("recipe"), dict) else {}
    deployable = recipe.get("deployable_config") if isinstance(recipe.get("deployable_config"), dict) else recipe
    required_config = {"model_family", "feature_subset", "sample_weight_policy", "calibration", "decision_threshold", "abstention"}
    candidate_big_loss_predictions = _numeric_metric(candidate_metrics, "big_loss_predictions") or 0.0
    production_big_loss_predictions = _numeric_metric(production_metrics, "big_loss_predictions") or 0.0
    candidate_big_loss_rate = _numeric_metric(candidate_metrics, "big_loss_prediction_rate") or 0.0
    production_big_loss_rate = _numeric_metric(production_metrics, "big_loss_prediction_rate") or 0.0
    candidate_brier = _numeric_metric(candidate_metrics, "brier_score")
    production_brier = _numeric_metric(production_metrics, "brier_score")
    candidate_utility = _numeric_metric(candidate_metrics, "utility_score_after_big_loss_penalty")
    production_utility = _utility_score(production_metrics)
    threshold_change = bool(threshold_optimizer.get("threshold_change_recommended"))
    threshold_results = threshold_optimizer.get("threshold_walk_forward_results") or {}
    threshold_safe = not threshold_change or bool(
        threshold_results.get("consistent")
        and threshold_results.get("threshold_stable")
        and threshold_results.get("windows")
        and all(item.get("passed") for item in threshold_results.get("windows", []) if item.get("current"))
    )
    symbol_concentration = _numeric_metric(candidate_metrics, "symbol_utility_concentration")
    date_concentration = _numeric_metric(candidate_metrics, "date_utility_concentration")
    gate_results = {
        "phase_1_certified": bool(phase_1_gate.get("phase_1_certified")),
        "decision_lane_passed": bool(decision_win),
        "ranking_lane_supportive": bool(ranking_win),
        "no_op_clone_blocked": not no_op_clone,
        "walk_forward_consistent": bool(walk_forward.get("consistent")),
        "walk_forward_recipe_reproduced": bool(walk_forward.get("recipe_reproduction_passed")),
        "paired_bootstrap_utility_passed": bool(bootstrap.get("passed")),
        "brier_improved": candidate_brier is not None and production_brier is not None and candidate_brier < production_brier,
        "profit_utility_improved": candidate_utility is not None and production_utility is not None and candidate_utility > production_utility,
        "candidate_only_big_loss_false_positives_zero": int(report_examples.get("big_loss_false_positive_count") or 0) == 0,
        "big_loss_predictions_not_worse": candidate_big_loss_predictions == 0.0 or candidate_big_loss_predictions <= production_big_loss_predictions,
        "big_loss_rate_not_worse": candidate_big_loss_rate <= production_big_loss_rate,
        "threshold_change_guarded": threshold_safe,
        "symbol_date_concentration_passed": (symbol_concentration is None or symbol_concentration <= MAX_SYMBOL_UTILITY_CONCENTRATION) and (date_concentration is None or date_concentration <= MAX_DATE_UTILITY_CONCENTRATION),
        "feature_risk_audit_passed": not bool(candidate_feature_risk.get("requires_review")),
        "deployable_config_complete": bool(lineage) and required_config.issubset(deployable),
    }
    issue_messages = {
        "phase_1_certified": "Phase 1 evaluation harness is not certified",
        "decision_lane_passed": "decision lane did not pass all promotion gates",
        "ranking_lane_supportive": "ranking lane did not independently support the candidate",
        "no_op_clone_blocked": "candidate is a no-op clone",
        "walk_forward_consistent": "candidate did not win consistently across walk-forward windows",
        "walk_forward_recipe_reproduced": "walk-forward folds did not reproduce the exact recipe",
        "paired_bootstrap_utility_passed": "paired date-block bootstrap did not prove positive utility delta",
        "brier_improved": "candidate Brier score did not improve production",
        "profit_utility_improved": "candidate penalized profit utility did not improve production",
        "candidate_only_big_loss_false_positives_zero": "candidate introduced one or more production-avoided big-loss signals",
        "big_loss_predictions_not_worse": "candidate big-loss predictions exceeded production",
        "big_loss_rate_not_worse": "candidate big-loss prediction rate exceeded production",
        "threshold_change_guarded": "threshold change did not pass every stability and walk-forward guardrail",
        "symbol_date_concentration_passed": "candidate utility was dominated by one symbol or date",
        "feature_risk_audit_passed": "candidate feature-risk audit requires review",
        "deployable_config_complete": "candidate deployable config is incomplete or lacks lineage",
    }
    blocking_issues = [issue_messages[name] for name, passed in gate_results.items() if not passed]
    return {
        "promotion_allowed": all(gate_results.values()),
        "decision": "PROMOTE" if all(gate_results.values()) else "HOLD",
        "gate_results": gate_results,
        "blocking_issues": blocking_issues,
        "paired_bootstrap_utility_delta": bootstrap,
    }

def main() -> None:
    parser = argparse.ArgumentParser(description="Compare candidate model against production model on same holdout.")
    parser.add_argument("--input", default="data/decision_training_snapshot.jsonl")
    parser.add_argument("--production-model", default="data/day1_baseline_model.json")
    parser.add_argument("--candidate-model", default="data/candidate_model.json")
    parser.add_argument("--output", default="data/model_comparison_report.json")
    parser.add_argument("--train-ratio", type=float, default=0.8)
    parser.add_argument("--min-rows", type=int, default=200)
    args = parser.parse_args()

    df = _load_jsonl(args.input)
    if df.empty:
        raise SystemExit("No rows available for model comparison")

    development_df, test_df = _chronological_split(df, args.train_ratio)
    _, test_df, temporal_split = purged_embargoed_split(
        development_df,
        test_df,
        horizon_days=LABEL_HORIZON_DAYS,
        embargo_days=EMBARGO_DAYS,
    )
    if test_df.empty:
        raise ValueError("purging/embargo leaves no final comparison rows")
    candidate_metrics = _evaluate(args.candidate_model, test_df.copy())
    production_metrics = _evaluate(args.production_model, test_df.copy())
    clone_detection = _clone_detection(args.candidate_model, args.production_model, test_df.copy())
    no_op_clone = bool(clone_detection.get("no_op_clone"))
    candidate_feature_risk = _feature_risk_audit(args.candidate_model, test_df.copy())
    production_feature_risk = _feature_risk_audit(args.production_model, test_df.copy())
    candidate_metrics["feature_risk_audit"] = candidate_feature_risk
    production_metrics["feature_risk_audit"] = production_feature_risk
    if no_op_clone:
        decision_win = False
        ranking_win = False
        decision_reasons = ["promotion evaluation skipped because prediction fingerprint/overlap identifies a no-op clone"]
        ranking_reasons = ["ranking promotion evaluation skipped for no-op clone"]
        ranking_metrics = {"candidate": {}, "production": {}}
        walk_forward = {
            "consistent": False,
            "skipped": True,
            "reason": "no-op clone detected before full promotion evaluation",
            "windows": [],
            "candidate_recipe_fingerprint": _artifact_config_fingerprint(args.candidate_model),
            "production_recipe_fingerprint": _artifact_config_fingerprint(args.production_model),
            "recipe_reproduction_passed": bool(_artifact_config_fingerprint(args.candidate_model) and _artifact_config_fingerprint(args.production_model)),
        }
    else:
        decision_win, decision_reasons = _decide(candidate_metrics, production_metrics, min_rows=max(1, args.min_rows))
        ranking_win, ranking_reasons, ranking_metrics = _ranking_lane_decide(candidate_metrics, production_metrics)
        walk_forward = _walk_forward_validation(args.candidate_model, args.production_model, test_df.copy(), min_rows=max(1, args.min_rows))
    walk_forward_consistent = bool(walk_forward.get("consistent"))
    report_examples = _prediction_error_examples(args.candidate_model, args.production_model, test_df.copy())
    production_threshold_optimizer = _threshold_optimizer_report(args.production_model, production_metrics, test_df.copy(), min_rows=max(1, args.min_rows))
    candidate_threshold_optimizer = _threshold_optimizer_report(args.candidate_model, candidate_metrics, test_df.copy(), min_rows=max(1, args.min_rows))
    candidate_win = decision_win and ranking_win and not no_op_clone and walk_forward_consistent
    promotion_decision = _promotion_decision(candidate_win, no_op_clone, decision_win, ranking_win, walk_forward_consistent)
    detailed_phase_1_certification = _phase_1_certification(
        candidate_model_path=args.candidate_model,
        production_model_path=args.production_model,
        test_df=test_df.copy(),
        min_rows=max(1, args.min_rows),
        candidate_metrics=candidate_metrics,
        clone_detection=clone_detection,
        walk_forward=walk_forward,
        threshold_optimizer=candidate_threshold_optimizer,
        promotion_decision=promotion_decision,
        report_examples=report_examples,
    )
    phase_1_gate = _phase_1_gate(
        candidate_model_path=args.candidate_model,
        production_model_path=args.production_model,
        detailed_certification=detailed_phase_1_certification,
        walk_forward=walk_forward,
        clone_detection=clone_detection,
        threshold_optimizer=candidate_threshold_optimizer,
        report_examples=report_examples,
        temporal_split=temporal_split,
        production_feature_risk=production_feature_risk,
    )
    paired_bootstrap = _paired_date_bootstrap_utility_delta(args.candidate_model, args.production_model, test_df.copy())
    production_promotion_gates = _production_promotion_gates(
        candidate_model_path=args.candidate_model,
        candidate_metrics=candidate_metrics,
        production_metrics=production_metrics,
        decision_win=decision_win,
        ranking_win=ranking_win,
        no_op_clone=no_op_clone,
        walk_forward=walk_forward,
        phase_1_gate=phase_1_gate,
        report_examples=report_examples,
        threshold_optimizer=candidate_threshold_optimizer,
        candidate_feature_risk=candidate_feature_risk,
        bootstrap=paired_bootstrap,
    )
    candidate_win = bool(production_promotion_gates["promotion_allowed"])
    promotion_decision = _promotion_decision(candidate_win, no_op_clone, decision_win, ranking_win, walk_forward_consistent)
    candidate_behavior = _candidate_behavior(candidate_metrics, production_metrics)
    reasons = [
        *(f"decision lane: {reason}" for reason in decision_reasons),
        *(f"ranking lane: {reason}" for reason in ranking_reasons),
    ]
    if no_op_clone:
        reasons.append("clone detection: candidate predictions are nearly identical to production; no_op_clone cannot be promoted")
    if not walk_forward_consistent:
        reasons.append("walk-forward validation: candidate is not consistently better across rolling windows")
    if not phase_1_gate["phase_1_certified"]:
        reasons.append("phase 1 certification failed; promotion remains blocked")
    reasons.extend(f"production promotion gate: {issue}" for issue in production_promotion_gates["blocking_issues"])

    report = {
        "temporal_split": temporal_split,
        "phase_1_gate": phase_1_gate,
        "production_promotion_gates": production_promotion_gates,
        "candidate_behavior": candidate_behavior,
        "candidate_metrics": candidate_metrics,
        "production_metrics": production_metrics,
        "recommended_threshold": production_threshold_optimizer.get("recommended_threshold"),
        "current_threshold": production_threshold_optimizer.get("current_threshold"),
        "threshold_change_recommended": production_threshold_optimizer.get("threshold_change_recommended"),
        "threshold_change_reason": production_threshold_optimizer.get("threshold_change_reason"),
        "threshold_walk_forward_results": production_threshold_optimizer.get("threshold_walk_forward_results"),
        "threshold_flat_optimum": production_threshold_optimizer.get("flat_optimum"),
        "threshold_optimizer": {"production": production_threshold_optimizer, "candidate": candidate_threshold_optimizer},
        "chosen_threshold": report_examples.get("chosen_threshold"),
        "prediction_overlap": report_examples.get("prediction_overlap"),
        "big_loss_false_positives": report_examples.get("big_loss_false_positives"),
        "big_loss_false_positive_count": report_examples.get("big_loss_false_positive_count"),
        "missed_big_gain_rows": report_examples.get("missed_big_gain_rows"),
        "missed_big_gain_count": report_examples.get("missed_big_gain_count"),
        "bad_buy_mining_rows": report_examples.get("big_loss_false_positives"),
        "cmi_false_positive_diagnostic": report_examples.get("cmi_false_positive_diagnostic"),
        "promotion_decision": promotion_decision,
        "clone_detection": clone_detection,
        "prediction_fingerprints": {
            "candidate": clone_detection.get("candidate_prediction_fingerprint"),
            "production": clone_detection.get("production_prediction_fingerprint"),
        },
        "feature_risk_audit": {"candidate": candidate_feature_risk, "production": production_feature_risk},
        "walk_forward_validation": walk_forward,
        "challenger_scoring_lanes": {
            "decision_model": {
                "candidate_win": decision_win,
                "metrics": ["utility_score_after_big_loss_penalty", "avg_return", "brier_score", "downside_risk", "big_loss_prediction_rate"],
                "reasons": decision_reasons,
            },
            "ranking": {
                "candidate_win": ranking_win,
                "metrics": ["total_return", "objective_score", "max_drawdown", "big_loss_selection_rate"],
                "best_ranking_backtests": ranking_metrics,
                "reasons": ranking_reasons,
            },
        },
        "candidate_win": candidate_win,
        "reasons": reasons,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
