#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from moneybot.services.deterministic_model import classify, save_artifact, summarize_binary_predictions, train_logistic_baseline
from scripts.day10_train_candidate_model import _chronological_split, _fill_feature_gaps, _prepare_frame, _select_feature_columns

SUITE_SCHEMA_VERSION = "moneybot-challenger-suite.v1"


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


def _train_variant(name: str, X: np.ndarray, y: np.ndarray, *, lr: float, l2: float, threshold: float, epochs: int, feature_columns: list[str]):
    artifact = train_logistic_baseline(X, y, learning_rate=lr, l2=l2, decision_threshold=threshold, epochs=epochs)
    artifact.version = name
    artifact.feature_columns = list(feature_columns)
    return artifact


def train_challenger_suite(input_path: Path, output_dir: Path, *, train_ratio: float = 0.8, horizon_days: int = 5, min_rows: int = 200) -> dict[str, Any]:
    df = _load_jsonl(input_path)
    if df.empty:
        raise ValueError("No rows available in input dataset")
    if "ts" in df.columns:
        df = df.sort_values("ts").reset_index(drop=True)
    df = _prepare_frame(df)
    target_col = _target(df, horizon_days)
    df[target_col] = pd.to_numeric(df[target_col], errors="coerce")
    df = df.dropna(subset=[target_col]).copy()
    feature_columns = _select_feature_columns(df)
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

    specs = [
        {"name": "challenger-logreg-balanced-v1", "lr": 0.08, "l2": 1e-3, "threshold": 0.50, "epochs": 500},
        {"name": "challenger-logreg-conservative-v1", "lr": 0.05, "l2": 5e-3, "threshold": 0.60, "epochs": 600},
        {"name": "challenger-logreg-aggressive-v1", "lr": 0.12, "l2": 5e-4, "threshold": 0.45, "epochs": 450},
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    challengers = []
    for spec in specs:
        artifact = _train_variant(feature_columns=feature_columns, X=X_train, y=y_train, **spec)
        model_path = output_dir / f"{artifact.version}.json"
        save_artifact(artifact, model_path)
        preds = classify(artifact, X_test)
        metrics = summarize_binary_predictions(y_test, preds)
        challengers.append({"model_version": artifact.version, "model_path": str(model_path), "metrics": metrics, "spec": spec})

    ranked = sorted(challengers, key=lambda item: (item["metrics"].get("accuracy", 0), item["metrics"].get("positive_rate", 0)), reverse=True)
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
        "challengers": challengers,
        "ranked_model_versions": [item["model_version"] for item in ranked],
        "live_routing": False,
    }
    (output_dir / "challenger_suite_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Train multiple offline MoneyBot challenger models from one reproducible feature-store snapshot.")
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
