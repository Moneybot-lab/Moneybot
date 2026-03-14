# Day 1 AI Foundation (Practical Path)

This Day-1 foundation adds deterministic data + model tooling so Moneybot can move from rule-only signals to measurable predictions.

## 1) Build a training snapshot

```bash
python3 scripts/day1_generate_training_data.py --output data/day1_training_snapshot.csv
```

What it does:
- Pulls OHLCV history from yfinance for a starter universe.
- Engineers deterministic features (`return_1d`, `return_5d`, `rsi_14`, `macd_hist`, `vol_ratio_20d`).
- Adds forward-return labels (`forward_return_5d`, `label_up_5d`).

## 2) Train deterministic baseline model

```bash
python3 scripts/day1_train_baseline_model.py --input data/day1_training_snapshot.csv --output-model data/day1_baseline_model.json
```

What it does:
- Runs a chronological train/test split.
- Trains deterministic logistic regression with full-batch gradient descent.
- Saves a model artifact (means/stds/weights/bias/threshold) to JSON.
- Prints simple holdout metrics.

## Why this is useful now

- Deterministic behavior means reproducible outputs for the same data snapshot.
- Model artifact format is simple and can be loaded in API services.
- This creates the foundation for Day-2+ integration into quick ask, hot momentum, and portfolio endpoints.

## Day-3 usage (hot momentum ranking)

The backend can now use this same artifact to rank `/api/hot-momentum-buys`.

Optional env vars:

```bash
DETERMINISTIC_MOMENTUM_ENABLED=true
DETERMINISTIC_MODEL_PATH=data/day1_baseline_model.json
```

If model loading fails, hot momentum automatically falls back to existing rule-based ranking.

## Day-4 usage (portfolio/watchlist advice)

`/api/user-watchlist` now attempts deterministic portfolio advice before AI narrative enhancement.

- deterministic output is exposed in `deterministic_portfolio`
- final `advice` may still be overridden by AI advisor when AI is enabled
- if deterministic model is unavailable, watchlist advice safely falls back to rule-based logic

## Day-5 usage (model health + decision logging)

New endpoint:

```bash
GET /api/model-health
```

This reports deterministic model load status plus decision source counters.

Optional env vars:

```bash
DECISION_LOGGING_ENABLED=true
DECISION_LOG_PATH=data/decision_events.jsonl
```


### Troubleshooting

- If your shell reports `command not found: python`, use `python3` as shown above.
- If you run scripts directly and see `ModuleNotFoundError: No module named moneybot`, ensure you are executing from the repository root (`Moneybot/`).
