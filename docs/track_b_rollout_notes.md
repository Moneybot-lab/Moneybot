# Track B rollout notes

Copy/paste checklist for enabling and monitoring the Track B offline challenger rollout.

## GitHub repository secrets

Set or verify these in **GitHub → Moneybot → Settings → Secrets and variables → Actions**:

```text
MONEYBOT_BASE_URL=https://<your-render-service-hostname>
DAILY_OPS_TOKEN=<same-secret-token-configured-in-render>
```

`MONEYBOT_BASE_URL` is used by `.github/workflows/track-b-offline.yml` to export the live decision log, and `DAILY_OPS_TOKEN` authorizes `/api/export-decision-log`.

## Render environment variables

Set or verify these in **Render → Moneybot service → Environment**:

```text
# Required for authenticated daily ops / decision-log export endpoints
DAILY_OPS_TOKEN=<strong-shared-secret>

# Keep runtime artifacts on the Render disk instead of ephemeral app storage
MONEYBOT_PERSISTENT_DATA_DIR=/var/data/moneybot

# Decision logging and persisted paths
DECISION_LOGGING_ENABLED=true
DECISION_LOG_PATH=/var/data/moneybot/decision_events.jsonl
DECISION_OUTCOMES_SNAPSHOT_PATH=/var/data/moneybot/decision_outcomes_snapshot.json
DECISION_OUTCOMES_SNAPSHOT_MAX_AGE_SECONDS=129600

# Deterministic model / Track B observability paths
DETERMINISTIC_MODEL_PATH=/var/data/moneybot/day1_baseline_model.json
DETERMINISTIC_CALIBRATION_REPORT_PATH=/var/data/moneybot/day13_calibration_report.json
DETERMINISTIC_CALIBRATION_REPORT_MAX_AGE_SECONDS=604800
DETERMINISTIC_TRAINING_MAX_AGE_HOURS=36
DETERMINISTIC_CALIBRATION_AUTO_APPLY_PLAN=true

# Conservative rollout controls for initial rollout
DETERMINISTIC_QUICK_ENABLED=true
DETERMINISTIC_MOMENTUM_ENABLED=true
DETERMINISTIC_ROLLOUT_PERCENTAGE=100
DETERMINISTIC_PORTFOLIO_ROLLOUT_PERCENTAGE=100
DETERMINISTIC_ROLLOUT_SEED=moneybot
DETERMINISTIC_ROLLOUT_DRY_RUN=false
```

Optional guardrails while ramping:

```text
DETERMINISTIC_ROLLOUT_ALLOWLIST=
DETERMINISTIC_ROLLOUT_BLOCKLIST=
```

Use `DETERMINISTIC_ROLLOUT_BLOCKLIST` for symbols that repeatedly create bad external-market-data lookups or should not receive deterministic routing.

## Render disk / deploy checks

1. Confirm the Render service has a persistent disk mounted at `/var/data`.
2. Create/verify the runtime directory exists after deploy:

```bash
/var/data/moneybot
```

3. After deploy, verify these files are being created/refreshed:

```bash
/var/data/moneybot/decision_events.jsonl
/var/data/moneybot/decision_outcomes_snapshot.json
/var/data/moneybot/day13_calibration_report.json
/var/data/moneybot/day13_recalibration_plan.json
/var/data/moneybot/day1_baseline_model.json
```

## GitHub Actions to run after merge/deploy

1. Run **Moneybot Daily Ops** once to refresh materialized outcomes, calibration, and reports.
2. Run **Track B Offline Challenger** manually.
3. Open the uploaded `track-b-offline-output` artifact and check:

```text
data/track_b/track_b_summary.json
data/track_b/decision_training_snapshot_track_b.jsonl
data/track_b/candidate_model_track_b.json
data/track_b/model_comparison_track_b.json
```

## Expected Track B run signals

A healthy Track B challenger run should show:

```text
day8 labeled_rows >= 200
day10 rows_after_feature_filter >= 200
day11 candidate_metrics.rows >= 200
```

Warnings from yfinance for invalid/delisted symbols are expected as long as day8 still reports enough labeled rows and day10 keeps enough rows after sparse feature filling.

## Production safety notes

- Track B offline does **not** promote a model or change live routing.
- Promotion remains manual/separate; do not run `day14_promote_candidate.py` unless the comparison report and rollout decision are explicitly approved.
- If the Performance page shows `snapshot_source=materialized_stale`, daily ops is stale but the app is intentionally avoiding expensive live fan-out.
- If Track B fails, first inspect `track_b_summary.json` and the day10 `rows_after_feature_filter` / `feature_fill_values` fields.
