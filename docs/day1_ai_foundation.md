# Day 1 AI Foundation (Practical Path)

This Day-1 foundation adds deterministic data + model tooling so Moneybot can move from rule-only signals to measurable predictions.

## 1) Build a training snapshot

**Important:** run these commands from the repository root (`Moneybot/`), not from your home directory.

```bash
cd /path/to/Moneybot
python3 scripts/day1_generate_training_data.py --output data/day1_training_snapshot.csv
```

What it does:
- Pulls OHLCV history from yfinance for a starter universe.
- Engineers deterministic features (`return_1d`, `return_5d`, `rsi_14`, `macd_hist`, `vol_ratio_20d`).
- Adds forward-return labels (`forward_return_5d`, `label_up_5d`).

## 2) Train deterministic baseline model

```bash
cd /path/to/Moneybot
python3 scripts/day1_train_baseline_model.py --input data/day1_training_snapshot.csv --output-model data/day1_baseline_model.json
```

What it does:
- Runs a chronological train/test split.
- Trains deterministic logistic regression with full-batch gradient descent.
- Saves a model artifact (means/stds/weights/bias/threshold) to JSON.
- Prints simple holdout metrics.

## Faster Day-1 refresh command

If you want one command instead of two, use:

```bash
cd /path/to/Moneybot
python3 scripts/day1_refresh_artifact.py
```

This wrapper:
- rebuilds `data/day1_training_snapshot.csv`
- retrains `data/day1_baseline_model.json`
- works even if your current shell is not already at the repo root, because it resolves the internal script paths for you

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

- If you see `can't open file '/Users/yourname/scripts/day1_generate_training_data.py'`, you ran the command from the wrong directory. `scripts/...` is a relative path, so first `cd` into the Moneybot repo root.
- If your shell reports `command not found: python`, use `python3` as shown above.
- If you run scripts directly and see `ModuleNotFoundError: No module named moneybot`, ensure you are executing from the repository root (`Moneybot/`).


## Day-6 usage (threshold tuning + cleaner momentum transparency)

You can now tune deterministic cutoffs with env vars (no code changes required):

```bash
DETERMINISTIC_QUICK_BUY_THRESHOLD=0.58
DETERMINISTIC_QUICK_STRONG_BUY_THRESHOLD=0.74
DETERMINISTIC_PORTFOLIO_BUY_PROB_THRESHOLD=0.64
DETERMINISTIC_PORTFOLIO_SELL_PROB_THRESHOLD=0.44
DETERMINISTIC_PORTFOLIO_BUY_DIP_THRESHOLD_PCT=-5.0
DETERMINISTIC_PORTFOLIO_SELL_PROFIT_THRESHOLD_PCT=7.0
```

Notes:
- Leave `DETERMINISTIC_QUICK_BUY_THRESHOLD` unset (or `0`) to keep using artifact threshold.
- `Hot Momentum Buys` now keeps deterministic transparency concise by removing duplicated model/version boilerplate when the source column already says `deterministic_model`.


## Day-7 usage (decision-log summary workflow)

You can now summarize recent decision telemetry without opening the JSONL file manually.

API endpoint:

```bash
GET /api/decision-log-summary?limit=200
```

CLI command:

```bash
python3 scripts/day7_decision_log_summary.py --input data/decision_events.jsonl --limit 200
```

What this gives you:
- counts by `decision_source`
- counts by endpoint
- top requested symbols
- the latest logged event for quick sanity checks

Suggested Day-7 workflow:
1. Keep `DECISION_LOGGING_ENABLED=true`.
2. Let the app run long enough to collect real quick-ask / momentum usage.
3. Call the API or CLI summary and compare `deterministic_model` vs `rule_based` usage over time.


## Day-8 usage (home-page model ops snapshot)

The home page now includes a lightweight **Model Ops Snapshot** panel.

It automatically reads:
- `GET /api/model-health`
- `GET /api/decision-log-summary?limit=50`

The panel is meant to answer, at a glance:
- is the deterministic model loaded?
- is decision logging enabled?
- are recent decisions mostly deterministic or rule-based?
- which endpoint and symbols are most active lately?

Use it as a fast visual check after deploys or after refreshing the model artifact.
