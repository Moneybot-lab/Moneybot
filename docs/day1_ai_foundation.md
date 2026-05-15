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
If the artifact file is missing (for example after cleaning local runtime data), Moneybot now uses a built-in deterministic fallback artifact so deterministic scoring remains available until you regenerate `data/day1_baseline_model.json`.

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
DETERMINISTIC_CALIBRATION_ENABLED=false
DETERMINISTIC_CALIBRATION_SLOPE=1.0
DETERMINISTIC_CALIBRATION_INTERCEPT=0.0
DETERMINISTIC_ROLLOUT_PERCENTAGE=100
DETERMINISTIC_ROLLOUT_SEED=moneybot
DETERMINISTIC_ROLLOUT_ALLOWLIST=
DETERMINISTIC_ROLLOUT_BLOCKLIST=
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


## Day-9 usage (outcome tracking)

You can now evaluate logged decisions against later price moves with:

```bash
python3 scripts/day9_evaluate_decision_outcomes.py --input data/decision_events.jsonl --limit 200
```

This is meant to answer:
- was a recent `BUY` / `STRONG BUY` directionally correct 1 day later?
- was it correct 5 trading days later?
- how often are logged decisions being evaluated as correct vs incorrect?


## Day-11 usage (artifact metadata and version history)

Training now writes sidecar metadata files next to the model artifact:

- `data/day1_baseline_model.json.meta.json`
- `data/day1_baseline_model.json.history.json`

These track:
- when the artifact was recorded
- input snapshot path
- train/test row counts
- simple holdout metrics
- recent artifact history entries

`GET /api/model-health` now includes `artifact_metadata` and `artifact_history` so you can inspect model lineage without opening files manually.


## Day-10 usage (recent decisions + outcomes table)

The home page now includes a **Recent Decisions & Outcomes** table.

It reads:
- `GET /api/decision-outcomes?limit=20`

The table is designed to show:
- symbol
- endpoint
- decision source
- action
- artifact/model version
- 1-day return + outcome
- 5-day return + outcome


## Day-12 usage (materialized outcomes snapshot)

To reduce live API work, you can precompute outcomes to a snapshot file:

```bash
python3 scripts/day12_materialize_outcomes.py --input data/decision_events.jsonl --output data/decision_outcomes_snapshot.json --limit 2000 --rows-limit 20
```

Then `/api/decision-outcomes` will serve that materialized snapshot when it is fresh enough.

Optional env vars:

```bash
DECISION_OUTCOMES_SNAPSHOT_PATH=data/decision_outcomes_snapshot.json
DECISION_OUTCOMES_SNAPSHOT_MAX_AGE_SECONDS=900
```

To bypass the snapshot and force live computation for debugging:

```bash
GET /api/decision-outcomes?limit=20&force_live=true
```

## Day-12 deterministic calibration + rollout controls

You can gradually roll out deterministic decisions and apply simple logit calibration without retraining.

Recommended controls:

```bash
DETERMINISTIC_CALIBRATION_ENABLED=true
DETERMINISTIC_CALIBRATION_SLOPE=0.90
DETERMINISTIC_CALIBRATION_INTERCEPT=-0.15
DETERMINISTIC_ROLLOUT_PERCENTAGE=35
DETERMINISTIC_ROLLOUT_SEED=day12
DETERMINISTIC_ROLLOUT_ALLOWLIST=AAPL,MSFT
DETERMINISTIC_ROLLOUT_BLOCKLIST=TSLA
DETERMINISTIC_ROLLOUT_DRY_RUN=true
```

Behavior:
- rollout is deterministic per symbol using `seed + symbol` hash buckets
- allowlist always enables deterministic decisions for listed symbols
- blocklist always disables deterministic decisions for listed symbols
- when a symbol is outside rollout, API behavior falls back to the existing rule-based path

`GET /api/model-health` now reports rollout and calibration settings so you can confirm production configuration quickly.

### 5.1 rollout dry-run mode

If you want to observe deterministic recommendations without exposing them to users yet:

```bash
DETERMINISTIC_ROLLOUT_DRY_RUN=true
DETERMINISTIC_ROLLOUT_PERCENTAGE=0
```

In this mode, `/api/quick-ask` continues to serve the existing rule-based response, while logging deterministic shadow decisions under `quick_ask_shadow` for comparison.

### 5.2 rollout promotion plan (starting from `DETERMINISTIC_PORTFOLIO_ROLLOUT_PERCENTAGE=20`)

Use a staged promotion schedule so you can validate quality and safety at each step before broadening exposure.

Suggested progression:

1. **20% (current baseline, 24-48h)**
   - keep `DETERMINISTIC_ROLLOUT_DRY_RUN=false` (live traffic)
   - monitor `/api/model-health`, `quick_ask` latency, and decision outcome drift vs baseline
2. **35% (next step, 24-48h)**
   - increase only `DETERMINISTIC_PORTFOLIO_ROLLOUT_PERCENTAGE=35`
   - keep the same `DETERMINISTIC_ROLLOUT_SEED` to avoid cohort churn
3. **50% (confidence gate, 2-3 days)**
   - promote if error/complaint rates remain flat and deterministic outcomes are not materially worse
4. **75% (broad exposure, 2-3 days)**
   - verify no concentration risk by symbol/sector in the deterministic cohort
5. **100% (full rollout)**
   - promote only after stable metrics across at least one full market cycle for your strategy horizon

Rollback rule:
- if quality/safety metrics regress at any stage, revert to the previous percentage immediately and investigate before retrying.

Allowlist/blocklist guidance when switching dry-run to false:
- **No mandatory change is required** just because `DETERMINISTIC_ROLLOUT_DRY_RUN` moved to `false`.
- Keep allowlist empty unless you intentionally want to force specific symbols into deterministic mode regardless of percentage.
- Keep blocklist for known-problem symbols you want to suppress regardless of percentage.
- Avoid putting the same symbol in both lists; if that happens, blocklist takes precedence in rollout gating logic.

### 5.3 terminal test runbook (example: promote quick-ask from 50% to 75%)

Yes — you can run a terminal validation loop before and after changing rollout percentage.

1) Set rollout to 50% (baseline window):

```bash
export DETERMINISTIC_ROLLOUT_DRY_RUN=false
export DETERMINISTIC_ROLLOUT_PERCENTAGE=50
```

2) Verify runtime config is live:

```bash
curl -s http://localhost:5000/api/model-health | jq '.data.rollout_percentage, .data.rollout_dry_run, .data.rollout_allowlist, .data.rollout_blocklist'
```

3) Sample quick-ask responses across a symbol basket and measure deterministic share:

```bash
for s in AAPL MSFT NVDA AMZN GOOGL META TSLA NFLX AMD CRM; do
  curl -s "http://localhost:5000/api/quick-ask?symbol=${s}" | jq -r '"\($s),\(.data.decision_source)"'
done
```

4) Promote to 75%:

```bash
export DETERMINISTIC_ROLLOUT_PERCENTAGE=75
```

5) Re-run the same checks (steps 2-3) and compare deterministic share + latency/error metrics.

Optional automated guardrail check:

```bash
BASE_URL=http://localhost:5000 bash scripts/gate_check.sh
```

Portfolio rollout promotion gates (same style as quick-ask):

```bash
BASE_URL="https://moneybotlabs.com" ./scripts/gate_check.sh --gate portfolio_20_to_35
BASE_URL="https://moneybotlabs.com" ./scripts/gate_check.sh --gate portfolio_35_to_50
BASE_URL="https://moneybotlabs.com" ./scripts/gate_check.sh --gate portfolio_50_to_75
BASE_URL="https://moneybotlabs.com" ./scripts/gate_check.sh --gate portfolio_75_to_100
```

Use the gate that matches your **current** portfolio rollout percentage. Promote only when the gate passes.

If `portfolio_35_to_50` passes, promote with:

```bash
export DETERMINISTIC_PORTFOLIO_ROLLOUT_PERCENTAGE=50
```

Then validate the new live value:

```bash
curl -s "https://moneybotlabs.com/api/model-health" | jq '.data.portfolio_rollout_percentage, .data.rollout_dry_run'
```

Optional unit tests for rollout logic:

```bash
pytest -q tests/test_deterministic_advisor.py -k rollout
pytest -q tests/test_dashboard_api.py -k "quick_ask and rollout"
```

### Day-13 calibration diagnostics + plan

Generate a calibration report from decision telemetry:

```bash
python3 scripts/day13_calibration_report.py --input data/decision_events.jsonl --output data/day13_calibration_report.json --limit 1000 --horizon-days 5
```

Generate a bounded recalibration plan (proposal only):

```bash
python3 scripts/day13_recalibrate.py --report data/day13_calibration_report.json --output data/day13_recalibration_plan.json --current-slope 1.0 --current-intercept 0.0
```

Optional model-health report wiring:

```bash
DETERMINISTIC_CALIBRATION_REPORT_PATH=data/day13_calibration_report.json
DETERMINISTIC_CALIBRATION_REPORT_MAX_AGE_SECONDS=43200
```

## Ops shortcuts (daily/weekly in 2-3 commands)

Instead of running many day scripts manually, use these wrappers:

All runtime artifacts/logs now use:

```bash
MONEYBOT_PERSISTENT_DATA_DIR=/path/to/persistent/data
```

When unset, defaults remain `data/`.

### Daily (single command)

```bash
python3 scripts/run_daily_ops.py --input-log data/decision_events.jsonl
```

This runs:
- Day 7 decision summary
- Day 12 outcomes materialization
- Day 13 calibration report + recalibration plan
- daily markdown auto-report generation (`data/daily_report.md`)

### Weekly (single command)

```bash
python3 scripts/run_weekly_model_refresh.py --input-log data/decision_events.jsonl
```

This runs:
- Day 1 model refresh workflow
- then the daily ops bundle above

### Auto-fill daily report only

```bash
python3 scripts/autofill_daily_report.py --output data/daily_report.md
```

## What should be pushed to GitHub vs kept local

Push to GitHub:
- Code and script changes under `moneybot/`, `scripts/`, `tests/`, `docs/`
- Any config/template updates that define reproducible behavior

Keep local / do not commit:
- `data/decision_events.jsonl`
- `data/decision_outcomes_snapshot.json`
- `data/day13_calibration_report.json`
- `data/day13_recalibration_plan.json`
- `data/daily_report.md`
- other runtime artifacts/logs generated during ops
