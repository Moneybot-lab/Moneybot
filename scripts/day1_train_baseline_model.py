from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from moneybot.services.deterministic_model import (
    FEATURE_COLUMNS,
    attach_labels,
    build_training_matrix,
    chronological_split,
    classify,
    save_artifact,
    summarize_binary_predictions,
    train_logistic_baseline,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Train Day-1 deterministic logistic baseline model")
    parser.add_argument("--input", default="data/day1_training_snapshot.csv")
    parser.add_argument("--horizon-days", type=int, default=5)
    parser.add_argument("--target-return", type=float, default=0.0)
    parser.add_argument("--output-model", default="data/day1_baseline_model.json")
    parser.add_argument("--train-ratio", type=float, default=0.8)
    args = parser.parse_args()

    raw = pd.read_csv(args.input)
    if f"label_up_{args.horizon_days}d" not in raw.columns:
        raw = attach_labels(raw, horizon_days=args.horizon_days, target_return=args.target_return)

    clean = raw.replace([np.inf, -np.inf], np.nan).dropna(subset=FEATURE_COLUMNS + [f"label_up_{args.horizon_days}d"])
    train_df, test_df = chronological_split(clean, train_ratio=args.train_ratio)

    X_train, y_train, _ = build_training_matrix(train_df, horizon_days=args.horizon_days)
    artifact = train_logistic_baseline(X_train, y_train)

    X_test = test_df[FEATURE_COLUMNS].to_numpy(dtype=float)
    y_test = test_df[f"label_up_{args.horizon_days}d"].to_numpy(dtype=float)
    y_pred = classify(artifact, X_test)
    metrics = summarize_binary_predictions(y_test, y_pred)

    save_artifact(artifact, args.output_model)

    print(f"Saved model -> {args.output_model}")
    print(f"Metrics: accuracy={metrics['accuracy']}, positive_rate={metrics['positive_rate']}, rows={int(metrics['rows'])}")


if __name__ == "__main__":
    main()
