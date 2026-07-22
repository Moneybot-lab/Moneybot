#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from moneybot.services.deterministic_model import predict_proba, summarize_binary_predictions, train_logistic_baseline
from scripts.day10_train_candidate_model import _backtest_compatible_feature_columns, _chronological_split, _fill_feature_gaps, _prepare_frame, _select_feature_columns

SUITE_SCHEMA_VERSION = "moneybot-challenger-suite.v2"
LOGISTIC_L2_GRID = (5e-4, 1e-3, 5e-3)
LOGISTIC_THRESHOLD_GRID = (0.45, 0.50, 0.55, 0.60)
MAX_STUMP_CHALLENGERS = 8
SPECIALIZED_CHALLENGER_FAMILIES = ("big_loss_avoider", "big_gain_hunter", "recent_window_model", "ranking_top5_model")


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


def _apply_walk_forward_metrics(
    challengers: list[dict[str, Any]],
    *,
    clean: pd.DataFrame,
    folds: list[tuple[int, int, int]],
    feature_columns: list[str],
    target_col: str,
    return_col: str | None,
) -> None:
    if not folds:
        return
    for challenger in challengers:
        fold_metrics: list[dict[str, Any]] = []
        for train_start, train_end, test_end in folds:
            fold_train = clean.iloc[train_start:train_end]
            fold_test = clean.iloc[train_end:test_end]
            X_train = fold_train[feature_columns].to_numpy(dtype=float)
            y_train = fold_train[target_col].to_numpy(dtype=float)
            X_test = fold_test[feature_columns].to_numpy(dtype=float)
            y_test = fold_test[target_col].to_numpy(dtype=float)
            fold_returns = fold_test[return_col].to_numpy(dtype=float) if return_col else None
            model_type = challenger.get("model_type")
            spec = challenger.get("spec", {})
            if model_type == "logistic_regression":
                artifact = train_logistic_baseline(
                    X_train,
                    y_train,
                    learning_rate=float(spec.get("lr", 0.08)),
                    l2=float(spec.get("l2", 0.001)),
                    decision_threshold=float(spec.get("threshold", 0.5)),
                    epochs=int(spec.get("epochs", 550)),
                )
                scores = predict_proba(artifact, X_test)
                preds = (scores >= artifact.decision_threshold).astype(int)
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
            fold_metrics.append(metrics)
        walk_forward = _average_metric_dicts(fold_metrics)
        if walk_forward:
            positive_windows = sum(1 for metrics in fold_metrics if float(metrics.get("ranking_objective", 0.0)) > 0.0)
            min_positive_windows = min(len(fold_metrics), max(2, int(np.ceil(len(fold_metrics) / 2))))
            walk_forward["positive_ranking_windows"] = positive_windows
            walk_forward["min_positive_windows_required"] = min_positive_windows
            walk_forward["passed"] = positive_windows >= min_positive_windows
            challenger["metrics"]["walk_forward"] = walk_forward
            challenger["metrics"]["walk_forward_passed"] = walk_forward["passed"]
            challenger["metrics"]["walk_forward_ranking_objective"] = walk_forward.get("ranking_objective", 0.0)



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


def _mistake_slice_masks(df: pd.DataFrame, return_col: str | None) -> dict[str, pd.Series]:
    buckets = _return_bucket_series(df, return_col)
    rec_positive = df.get("feature_rec_positive", pd.Series(0.0, index=df.index)).astype(float) >= 0.5
    prob = pd.to_numeric(df.get("feature_probability_up", pd.Series(np.nan, index=df.index)), errors="coerce")
    missed_big_gain = (buckets == "big_gain") & (~rec_positive | (prob < 0.55))
    bad_buy_big_loss = (buckets == "big_loss") & (rec_positive | (prob >= 0.55))
    return {
        "missed_big_gain_winners": missed_big_gain.fillna(False),
        "bad_buy_big_loss_false_positives": bad_buy_big_loss.fillna(False),
    }


def _write_daily_mistake_slices(df: pd.DataFrame, output_dir: Path, return_col: str | None) -> dict[str, Any]:
    slice_root = output_dir / "mistake_slices"
    slice_root.mkdir(parents=True, exist_ok=True)
    dates = _event_date_values(df)
    masks = _mistake_slice_masks(df, return_col)
    manifest: dict[str, Any] = {"slice_root": str(slice_root), "slices": {}}
    for slice_name, mask in masks.items():
        slice_dir = slice_root / slice_name
        slice_dir.mkdir(parents=True, exist_ok=True)
        selected = df.loc[mask].copy()
        manifest["slices"][slice_name] = {"rows": int(len(selected)), "daily_files": []}
        if selected.empty:
            continue
        selected_dates = dates.loc[selected.index]
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
    col = f"label_up_{horizon_days}d"
    if col in df.columns:
        return col
    if "label_up_5d" in df.columns:
        return "label_up_5d"
    raise ValueError(f"Missing target label {col}")


def _write_artifact(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


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
        _write_artifact(model_path, {"model_type": "logistic_regression", **artifact.to_dict(), "training_spec": spec})
        probs = predict_proba(artifact, X_test)
        preds = (probs >= artifact.decision_threshold).astype(int)
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(probs, y_test, test_returns))
        challengers.append({"model_version": artifact.version, "model_type": "logistic_regression", "model_path": str(model_path), "metrics": metrics, "spec": spec})


def _stump_predictions(values: np.ndarray, threshold: float, direction: str) -> np.ndarray:
    if direction == "gte_positive":
        return (values >= threshold).astype(int)
    return (values < threshold).astype(int)


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
        payload = {"version": model_version, "model_type": "decision_stump", "feature": spec["feature"], "threshold": spec["threshold"], "direction": spec["direction"], "training_spec": spec}
        _write_artifact(model_path, payload)
        challengers.append({"model_version": model_version, "model_type": "decision_stump", "model_path": str(model_path), "metrics": metrics, "spec": spec})



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
        family_train_df = train_df
        family_y_train = y_train
        if family == "recent_window_model" and len(train_df) >= 10:
            recent_rows = max(5, len(train_df) // 2)
            family_train_df = train_df.tail(recent_rows)
            family_y_train = family_train_df.index.map(dict(zip(train_df.index, y_train))).to_numpy(dtype=float)
        elif family == "ranking_top5_model":
            family_y_train = _ranking_top5_labels(train_df, return_col)
            if family_y_train.sum() <= 0:
                family_y_train = y_train

        X_family = family_train_df[feature_columns].to_numpy(dtype=float)
        weights = _specialized_sample_weight(family_train_df, return_col, family)
        artifact = train_logistic_baseline(
            X_family,
            family_y_train,
            learning_rate=0.06,
            l2=0.002,
            decision_threshold=0.60 if family in {"big_loss_avoider", "ranking_top5_model"} else 0.55,
            epochs=650,
            sample_weight=weights,
        )
        artifact.version = f"challenger-{family.replace('_', '-')}-v1"
        artifact.feature_columns = list(feature_columns)
        model_path = output_dir / f"{artifact.version}.json"
        spec = {"family": family, "lr": 0.06, "l2": 0.002, "epochs": 650, "sample_weight_policy": family}
        _write_artifact(model_path, {"model_type": "logistic_regression", "specialized_family": family, **artifact.to_dict(), "training_spec": spec})
        probs = predict_proba(artifact, test_df[feature_columns].to_numpy(dtype=float))
        preds = (probs >= artifact.decision_threshold).astype(int)
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(probs, y_test, test_returns))
        metrics["specialized_family"] = family
        challengers.append({"model_version": artifact.version, "model_type": "logistic_regression", "specialized_family": family, "model_path": str(model_path), "metrics": metrics, "spec": spec})

def _add_baseline_challengers(challengers: list[dict[str, Any]], *, output_dir: Path, y_train: np.ndarray, y_test: np.ndarray, test_returns: np.ndarray | None) -> None:
    majority_class = int(float(y_train.mean()) >= 0.5)
    baselines = [
        ("challenger-baseline-majority-v1", np.full_like(y_test, majority_class, dtype=int), {"majority_class": majority_class}),
        ("challenger-baseline-always-up-v1", np.ones_like(y_test, dtype=int), {}),
        ("challenger-baseline-always-down-v1", np.zeros_like(y_test, dtype=int), {}),
    ]
    for model_version, preds, spec in baselines:
        model_path = output_dir / f"{model_version}.json"
        _write_artifact(model_path, {"version": model_version, "model_type": "baseline_classifier", "training_spec": spec})
        metrics = summarize_binary_predictions(y_test, preds)
        metrics.update(_ranking_metrics(preds.astype(float), y_test, test_returns))
        challengers.append({"model_version": model_version, "model_type": "baseline_classifier", "model_path": str(model_path), "metrics": metrics, "spec": spec})


def train_challenger_suite(input_path: Path, output_dir: Path, *, train_ratio: float = 0.8, horizon_days: int = 5, min_rows: int = 200) -> dict[str, Any]:
    df = _load_jsonl(input_path)
    if df.empty:
        raise ValueError("No rows available in input dataset")
    persisted_feature_columns = {str(col) for col in df.columns if str(col).startswith("feature_")}
    if "ts" in df.columns:
        df = df.sort_values("ts").reset_index(drop=True)
    df = _prepare_frame(df)
    target_col = _target(df, horizon_days)
    df[target_col] = pd.to_numeric(df[target_col], errors="coerce")
    df = df.dropna(subset=[target_col]).copy()
    feature_columns = _backtest_compatible_feature_columns(_select_feature_columns(df), persisted_feature_columns)
    if not feature_columns:
        raise ValueError("No numeric feature columns found")
    clean, fill_values = _fill_feature_gaps(df, feature_columns)
    if len(clean) < max(1, min_rows):
        raise ValueError(f"Not enough rows to train challenger suite (have={len(clean)}, need={min_rows})")
    train_df, test_df = _chronological_split(clean, train_ratio)
    X_train = train_df[feature_columns].to_numpy(dtype=float)
    y_train = train_df[target_col].to_numpy(dtype=float)
    X_test = test_df[feature_columns].to_numpy(dtype=float)
    y_test = test_df[target_col].to_numpy(dtype=float)
    return_col = _return_column(clean, horizon_days)
    test_returns = test_df[return_col].to_numpy(dtype=float) if return_col else None

    output_dir.mkdir(parents=True, exist_ok=True)
    mistake_slices = _write_daily_mistake_slices(clean, output_dir, return_col)
    challengers: list[dict[str, Any]] = []
    _add_logistic_challengers(challengers, output_dir=output_dir, X_train=X_train, y_train=y_train, X_test=X_test, y_test=y_test, feature_columns=feature_columns, test_returns=test_returns)
    _add_stump_challengers(challengers, output_dir=output_dir, train_df=train_df, test_df=test_df, y_train=y_train, y_test=y_test, feature_columns=feature_columns, test_returns=test_returns)
    _add_specialized_challengers(challengers, output_dir=output_dir, train_df=train_df, test_df=test_df, y_train=y_train, y_test=y_test, feature_columns=feature_columns, return_col=return_col, test_returns=test_returns)
    _add_baseline_challengers(challengers, output_dir=output_dir, y_train=y_train, y_test=y_test, test_returns=test_returns)
    walk_forward_folds = _walk_forward_splits(clean)
    _apply_walk_forward_metrics(
        challengers,
        clean=clean,
        folds=walk_forward_folds,
        feature_columns=feature_columns,
        target_col=target_col,
        return_col=return_col,
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
    model_type_counts = {model_type: sum(1 for item in challengers if item["model_type"] == model_type) for model_type in sorted({item["model_type"] for item in challengers})}
    manifest = {
        "schema_version": SUITE_SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path),
        "output_dir": str(output_dir),
        "rows": len(clean),
        "train_rows": len(train_df),
        "test_rows": len(test_df),
        "walk_forward_windows": [
            {"train_start_row": start, "train_end_row": train_end, "test_start_row": train_end, "test_end_row": test_end}
            for start, train_end, test_end in walk_forward_folds
        ],
        "target_column": target_col,
        "ranking_selection_policy": "rank by walk-forward pass status across multiple windows, then walk-forward top-K capped-exposure ranking_objective, holdout ranking objective, top-K average return, then accuracy",
        "promotion_policy": "prefer candidates with positive ranking_objective in at least two walk-forward windows before promotion",
        "ranking_metric_names": ["top_k_precision", "top_k_avg_return", "pairwise_ranking_loss", "big_gain_capture", "big_loss_demotion", "ranking_objective", "walk_forward_ranking_objective", "walk_forward_passed"],
        "feature_columns": feature_columns,
        "feature_fill_values": fill_values,
        "model_type_counts": model_type_counts,
        "specialized_challenger_families": list(SPECIALIZED_CHALLENGER_FAMILIES),
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
    args = parser.parse_args()
    manifest = train_challenger_suite(Path(args.input), Path(args.output_dir), train_ratio=args.train_ratio, horizon_days=args.horizon_days, min_rows=args.min_rows)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
