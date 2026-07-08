#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from moneybot.services.deterministic_model import classify, summarize_binary_predictions, train_logistic_baseline
from scripts.day10_train_candidate_model import _backtest_compatible_feature_columns, _chronological_split, _fill_feature_gaps, _prepare_frame, _select_feature_columns

SUITE_SCHEMA_VERSION = "moneybot-challenger-suite.v2"
LOGISTIC_L2_GRID = (5e-4, 1e-3, 5e-3)
LOGISTIC_THRESHOLD_GRID = (0.45, 0.50, 0.55, 0.60)
MAX_STUMP_CHALLENGERS = 8


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
        preds = classify(artifact, X_test)
        metrics = summarize_binary_predictions(y_test, preds)
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
        payload = {"version": model_version, "model_type": "decision_stump", "feature": spec["feature"], "threshold": spec["threshold"], "direction": spec["direction"], "training_spec": spec}
        _write_artifact(model_path, payload)
        challengers.append({"model_version": model_version, "model_type": "decision_stump", "model_path": str(model_path), "metrics": metrics, "spec": spec})


def _add_baseline_challengers(challengers: list[dict[str, Any]], *, output_dir: Path, y_train: np.ndarray, y_test: np.ndarray) -> None:
    majority_class = int(float(y_train.mean()) >= 0.5)
    baselines = [
        ("challenger-baseline-majority-v1", np.full_like(y_test, majority_class, dtype=int), {"majority_class": majority_class}),
        ("challenger-baseline-always-up-v1", np.ones_like(y_test, dtype=int), {}),
        ("challenger-baseline-always-down-v1", np.zeros_like(y_test, dtype=int), {}),
    ]
    for model_version, preds, spec in baselines:
        model_path = output_dir / f"{model_version}.json"
        _write_artifact(model_path, {"version": model_version, "model_type": "baseline_classifier", "training_spec": spec})
        challengers.append({"model_version": model_version, "model_type": "baseline_classifier", "model_path": str(model_path), "metrics": summarize_binary_predictions(y_test, preds), "spec": spec})


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

    output_dir.mkdir(parents=True, exist_ok=True)
    challengers: list[dict[str, Any]] = []
    _add_logistic_challengers(challengers, output_dir=output_dir, X_train=X_train, y_train=y_train, X_test=X_test, y_test=y_test, feature_columns=feature_columns)
    _add_stump_challengers(challengers, output_dir=output_dir, train_df=train_df, test_df=test_df, y_train=y_train, y_test=y_test, feature_columns=feature_columns)
    _add_baseline_challengers(challengers, output_dir=output_dir, y_train=y_train, y_test=y_test)

    ranked = sorted(challengers, key=lambda item: (item["metrics"].get("accuracy", 0), item["metrics"].get("positive_rate", 0)), reverse=True)
    model_type_counts = {model_type: sum(1 for item in challengers if item["model_type"] == model_type) for model_type in sorted({item["model_type"] for item in challengers})}
    manifest = {
        "schema_version": SUITE_SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path),
        "output_dir": str(output_dir),
        "rows": len(clean),
        "train_rows": len(train_df),
        "test_rows": len(test_df),
        "target_column": target_col,
        "feature_columns": feature_columns,
        "feature_fill_values": fill_values,
        "model_type_counts": model_type_counts,
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
