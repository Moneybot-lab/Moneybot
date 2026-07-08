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
THRESHOLD_SEARCH_VALUES = (0.50, 0.525, 0.55, 0.575, 0.60, 0.625, 0.65, 0.675, 0.70)
RANKING_TOP_K_VALUES = (1, 3, 5)
RANKING_MAX_EXPOSURE_PER_SIGNAL = 0.10


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
        "rows": int(len(usable)),
    }
    return metrics


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
    c_big_gain_rate = _numeric_metric(candidate, "big_gain_capture_rate")
    c_utility = _utility_score(candidate)
    p_utility = _utility_score(production)
    if c_utility is None or p_utility is None:
        reasons.append("insufficient comparable utility metrics")
        return False, reasons

    accuracy_ok = c_acc > p_acc
    brier_ok = c_brier < p_brier
    return_ok = c_return >= p_return
    downside_ok = c_downside <= p_downside
    big_loss_ok = True if c_big_loss_rate is None or p_big_loss_rate is None else c_big_loss_rate <= p_big_loss_rate
    big_gain_floor_ok = (c_big_gain_rate or 0.0) >= MIN_BIG_GAIN_CAPTURE_RATE
    utility_ok = c_utility > (p_utility + MIN_UTILITY_IMPROVEMENT)

    if not accuracy_ok:
        reasons.append("candidate accuracy is below production, but accuracy is informational when profit utility improves")
    if not brier_ok:
        reasons.append("candidate brier score does not improve production")
    if not (return_ok or downside_ok):
        reasons.append("candidate avg_return is lower and downside_risk is higher than production")
    if not big_loss_ok:
        reasons.append("candidate signals too many big-loss rows versus production")
    if not big_gain_floor_ok:
        reasons.append(f"candidate big-gain capture is below minimum ({c_big_gain_rate or 0.0:.4f} < {MIN_BIG_GAIN_CAPTURE_RATE:.4f})")
    if not utility_ok:
        reasons.append("candidate profit utility does not exceed production")

    if brier_ok and (return_ok or downside_ok) and big_loss_ok and big_gain_floor_ok and utility_ok:
        reasons.append("candidate improves profit utility with acceptable brier, return/downside, big-loss avoidance, and minimum big-gain capture")
        return True, reasons

    reasons.append("candidate did not satisfy profit-aware promotion thresholds")
    return False, reasons


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

    candidate_win, reasons = _decide(candidate_metrics, production_metrics, min_rows=max(1, args.min_rows))

    report = {
        "candidate_metrics": candidate_metrics,
        "production_metrics": production_metrics,
        "candidate_win": candidate_win,
        "reasons": reasons,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
