#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
        return {"rows": 0, "prediction_agreement": None, "probability_mae": None, "no_op_clone": False}
    c_preds = candidate_preds[:rows]
    p_preds = production_preds[:rows]
    c_probs = candidate_probs[:rows]
    p_probs = production_probs[:rows]
    prediction_agreement = float((c_preds == p_preds).mean())
    probability_mae = float(np.mean(np.abs(c_probs - p_probs)))
    no_op_clone = prediction_agreement >= NO_OP_CLONE_PREDICTION_AGREEMENT and probability_mae <= NO_OP_CLONE_PROBABILITY_MAE
    return {
        "rows": rows,
        "prediction_agreement": round(prediction_agreement, 4),
        "probability_mae": round(probability_mae, 4),
        "no_op_clone": bool(no_op_clone),
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


def _clone_detection(candidate_model_path: str, production_model_path: str, test_df: pd.DataFrame) -> dict[str, Any]:
    candidate_preds, candidate_probs = _artifact_predictions(candidate_model_path, test_df)
    production_preds, production_probs = _artifact_predictions(production_model_path, test_df)
    return _no_op_clone_summary(candidate_preds, production_preds, candidate_probs, production_probs)

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

    if brier_ok and (return_ok or downside_ok) and big_loss_ok and big_gain_floor_ok and utility_ok:
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
    return {
        "chosen_threshold": round(float(joined["_candidate_threshold"].iloc[0]), 6) if rows else None,
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
    }


def _promotion_decision(candidate_win: bool, no_op_clone: bool, decision_win: bool, ranking_win: bool, walk_forward_consistent: bool) -> str:
    if no_op_clone:
        return "NO_OP_CLONE"
    if candidate_win:
        return "PROMOTE"
    if decision_win and ranking_win and not walk_forward_consistent:
        return "WATCH"
    return "HOLD"

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
    chunks = [chunk.copy() for chunk in np.array_split(test_df, WALK_FORWARD_WINDOWS) if len(chunk)]
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
            "reasons": [*(f"decision lane: {reason}" for reason in decision_reasons), *(f"ranking lane: {reason}" for reason in ranking_reasons)],
        })
    return _walk_forward_consistency(window_results)

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

    _, test_df = _chronological_split(df, args.train_ratio)
    candidate_metrics = _evaluate(args.candidate_model, test_df.copy())
    production_metrics = _evaluate(args.production_model, test_df.copy())

    decision_win, decision_reasons = _decide(candidate_metrics, production_metrics, min_rows=max(1, args.min_rows))
    ranking_win, ranking_reasons, ranking_metrics = _ranking_lane_decide(candidate_metrics, production_metrics)
    clone_detection = _clone_detection(args.candidate_model, args.production_model, test_df.copy())
    walk_forward = _walk_forward_validation(args.candidate_model, args.production_model, test_df.copy(), min_rows=max(1, args.min_rows))
    no_op_clone = bool(clone_detection.get("no_op_clone"))
    walk_forward_consistent = bool(walk_forward.get("consistent"))
    report_examples = _prediction_error_examples(args.candidate_model, args.production_model, test_df.copy())
    candidate_win = decision_win and ranking_win and not no_op_clone and walk_forward_consistent
    promotion_decision = _promotion_decision(candidate_win, no_op_clone, decision_win, ranking_win, walk_forward_consistent)
    reasons = [
        *(f"decision lane: {reason}" for reason in decision_reasons),
        *(f"ranking lane: {reason}" for reason in ranking_reasons),
    ]
    if no_op_clone:
        reasons.append("clone detection: candidate predictions are nearly identical to production; no_op_clone cannot be promoted")
    if not walk_forward_consistent:
        reasons.append("walk-forward validation: candidate is not consistently better across rolling windows")

    report = {
        "candidate_metrics": candidate_metrics,
        "production_metrics": production_metrics,
        "chosen_threshold": report_examples.get("chosen_threshold"),
        "prediction_overlap": report_examples.get("prediction_overlap"),
        "big_loss_false_positives": report_examples.get("big_loss_false_positives"),
        "big_loss_false_positive_count": report_examples.get("big_loss_false_positive_count"),
        "missed_big_gain_rows": report_examples.get("missed_big_gain_rows"),
        "missed_big_gain_count": report_examples.get("missed_big_gain_count"),
        "promotion_decision": promotion_decision,
        "clone_detection": clone_detection,
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
