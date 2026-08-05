# Track B rollout notes

Copy/paste checklist for enabling and monitoring the Track B offline challenger rollout.

## GitHub repository secrets

Set or verify these in **GitHub → Moneybot → Settings → Secrets and variables → Actions**:

```text
MONEYBOT_BASE_URL=https://<your-render-service-hostname>
DAILY_OPS_TOKEN=<same-secret-token-configured-in-render>
TRACK_B_PROMOTION_TOKEN=<same-secret-token-configured-in-render-for-manual-promotion>
```

`MONEYBOT_BASE_URL` is used by `.github/workflows/track-b-offline.yml` to export the live decision log, `DAILY_OPS_TOKEN` authorizes `/api/export-decision-log`, and `TRACK_B_PROMOTION_TOKEN` authorizes the manual Track B promotion endpoint.

## Render environment variables

Set or verify these in **Render → Moneybot service → Environment**:

```text
# Required for authenticated daily ops / decision-log export endpoints
DAILY_OPS_TOKEN=<strong-shared-secret>

# Required for manual GitHub-to-Render Track B promotion
TRACK_B_PROMOTION_TOKEN=<strong-shared-secret>

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
DETERMINISTIC_ROLLOUT_PERCENTAGE=10
DETERMINISTIC_PORTFOLIO_ROLLOUT_PERCENTAGE=10
DETERMINISTIC_ROLLOUT_SEED=moneybot
DETERMINISTIC_ROLLOUT_DRY_RUN=false
```

Optional guardrails while ramping:

```text
DETERMINISTIC_ROLLOUT_ALLOWLIST=
DETERMINISTIC_ROLLOUT_BLOCKLIST=
```

Use `DETERMINISTIC_ROLLOUT_BLOCKLIST` for symbols that repeatedly create bad external-market-data lookups or should not receive deterministic routing.


## Rollout gate plan

Start both Quick Ask and Portfolio at 10%, then promote one surface at a time through 25%, 50%, 75%, and 100% after the matching gate passes. Keep `DETERMINISTIC_ROLLOUT_SEED` stable between stages.

```bash
# Quick Ask
BASE_URL="$MONEYBOT_BASE_URL" ./scripts/gate_check.sh --gate 10_to_25
BASE_URL="$MONEYBOT_BASE_URL" ./scripts/gate_check.sh --gate 25_to_50
BASE_URL="$MONEYBOT_BASE_URL" ./scripts/gate_check.sh --gate 50_to_75
BASE_URL="$MONEYBOT_BASE_URL" ./scripts/gate_check.sh --gate 75_to_100

# Portfolio
BASE_URL="$MONEYBOT_BASE_URL" ./scripts/gate_check.sh --gate portfolio_10_to_25
BASE_URL="$MONEYBOT_BASE_URL" ./scripts/gate_check.sh --gate portfolio_25_to_50
BASE_URL="$MONEYBOT_BASE_URL" ./scripts/gate_check.sh --gate portfolio_50_to_75
BASE_URL="$MONEYBOT_BASE_URL" ./scripts/gate_check.sh --gate portfolio_75_to_100
```

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
data/track_b/decision_training_snapshot_massive.jsonl
data/track_b/decision_training_snapshot_massive.jsonl.manifest.json
data/track_b/production_model.json
data/track_b/candidate_model_track_b.json
data/track_b/model_comparison_track_b.json
```

The Massive-backed snapshot is the canonical Track B training input. The legacy `decision_training_snapshot_track_b.jsonl` path is only available through `--training-source legacy` for emergency debugging. Day10 and Day11 carry the Massive row manifest (`*.manifest.json`), `leakage_safe`, join policy, and per-row `leakage_guard` values into metadata/reporting so Phase 1 can fail if training reverts to the older yfinance/day8 source. Feature selection ranks Massive lagged returns, technical indicators, volume/liquidity, market-regime, and SPY-relative features ahead of app-signal columns.

## Manual GitHub promotion workflow

After a successful Track B run is explicitly approved for rollout, run **Promote Track B Candidate** manually from GitHub Actions.

Required GitHub secret:

```text
TRACK_B_PROMOTION_TOKEN=<same-value-as-render-track-b-promotion-token>
```

Required Render env var:

```text
TRACK_B_PROMOTION_TOKEN=<strong-shared-secret>
```

Workflow input:

```text
track_b_run_id=<successful Track B Offline Challenger run id>
```

The Track B workflow exports the current production model from `/api/export-production-model` into `data/track_b/production_model.json` before challenger comparison, so a candidate promoted by this workflow becomes the baseline for the next Track B comparison. The promotion workflow downloads the `track-b-offline-output` artifact for that run, verifies `model_comparison_track_b.json` and `candidate_model_track_b.json`, blocks by default unless `candidate_win=true`, then posts both JSON files to `/api/promote-track-b-candidate`. The protected Render endpoint stores them under the persistent runtime `track_b/` directory and runs `scripts/day14_promote_candidate.py` against the configured production model path.

Leave `force=false` unless a human has separately approved overriding the comparison report.

## Expected Track B run signals

A healthy Track B challenger run should show. Day10 also prints `selected_decision_threshold`, `threshold_selection`, `calibration`, and `training_periods`; inspect those fields when the model probabilities improve but `candidate_win` remains false. Day10 uses four disjoint chronological periods: model fitting, probability calibration, threshold selection, and an untouched final test period. Every adjacent boundary purges rows whose five-day label horizon overlaps the next period and embargoes the first day of the next period; Day11 applies the same policy before final comparison. Feature fill medians come only from the fit period, Platt calibration is retained only when it improves calibration-period Brier score, and neither calibration nor threshold selection can see final-test outcomes. The search treats calibrated model + threshold as the challenger artifact, evaluates 0.55 through 0.70, and records big-loss predictions/rates so the selected threshold is optimized for the same profit-aware utility used by the promotion gate without assuming 0.55 is live-best. Phase 3 threshold selection uses the center of the broadest utility plateau within an absolute 0.005 or relative 2% tolerance of peak utility, preferring a stable neighborhood over one lucky point and retaining the current threshold when it already lies in that plateau. Day11 reports the chosen threshold, `threshold_flat_optimum`, a guarded threshold optimizer with `recommended_threshold`, `current_threshold`, `threshold_change_recommended`, `threshold_change_reason`, and `threshold_walk_forward_results`, prediction overlap and deterministic prediction fingerprints, symbol/date examples for big-loss false positives and missed big-gain rows, threshold stability across independently optimized windows, symbol/date utility concentration, a feature-risk audit, CMI false-positive diagnostics when present, and a promotion decision of `PROMOTE`, `HOLD`, `WATCH`, or `NO_OP_CLONE`. No-op fingerprints are checked before full promotion evaluation; threshold changes require both walk-forward utility consistency and a stable threshold neighborhood; and candidates are blocked when one symbol/date dominates utility or raw `feature_price` is the top positive contributor too frequently. The CMI 2026-07-10 failure is stored under `regression_examples` and is linked into matching diagnostics so its bad-buy classification and hard big-loss penalty remain regression-protected. Day11 also blocks `no_op_clone` candidates whose predictions are nearly identical to production, requires consistency across rolling walk-forward windows, and reports separate `decision_model` and `ranking` scoring lanes: the decision lane checks utility, avg_return, Brier, downside risk, and big-loss avoidance; the ranking lane checks top-k total return, objective score, max drawdown, and big-loss selection rate. Day11 applies a hard false-positive penalty when the candidate predicts any big-loss rows while production predicts zero, and still requires candidate big_loss_prediction_rate <= production.

Every Day11 report also includes one compact top-level `phase_1_gate`. Read `ready_for_phase_2` as the final harness answer. The gate contains twelve named booleans covering reproducibility, lineage, walk-forward recipe reproduction, chronological split hygiene, purge/embargo, future-feature leakage, artifact-scored mistakes, CMI regression detection, clone detection, threshold guardrails, symbol/date concentration handling, and report traceability. The concentration field is named `symbol_date_concentration_handling_passed` because Phase 1 certifies detection and enforcement, not candidate performance. A correctly explained `HOLD`, `WATCH`, or `NO_OP_CLONE` does not fail this harness gate; neither does a correctly blocked threshold change, a detected CMI recurrence, or a ranking-only win. `blocking_issues` contains only failed harness requirements, while `warnings` contains non-blocking diagnostics such as production raw-price contribution monitoring. A false Phase 1 gate can block promotion but never relaxes or overrides the ordinary promotion gates. The separate top-level `production_promotion_gates` is deliberately stricter: production can change only when Phase 1 is certified, the decision lane passes all promotion gates, ranking independently supports rather than drives the change, the artifact is not a clone, every walk-forward recipe and outcome is consistent, paired date-block bootstrap utility is positive at 95% confidence, Brier and penalized profit utility improve, no production-avoided big-loss signal is introduced, big-loss counts/rates do not worsen, threshold changes pass all stability checks, concentration and feature-risk audits pass, and the deployable lineage contains model family, calibration, threshold, feature subset, weighting, and abstention config. The top-level `candidate_behavior` label explains selective candidates such as `high_precision_low_recall` without changing promotion eligibility.


```text
day8 labeled_rows >= 200
day10 rows_after_feature_filter >= 200
day11 candidate_metrics.rows >= 200
day11 candidate_metrics.brier_score < production_metrics.brier_score
day11 candidate_metrics.avg_return >= production_metrics.avg_return OR candidate_metrics.downside_risk <= production_metrics.downside_risk
day11 candidate_metrics.big_loss_prediction_rate <= production_metrics.big_loss_prediction_rate
day11 candidate_metrics.big_gain_capture_rate >= 0.10
day11 candidate_metrics.utility_score > production_metrics.utility_score
day10/day11 return buckets include big_loss, loss, flat, gain, big_gain
```


Track B uses 5-day return buckets (`big_loss`, `loss`, `flat`, `gain`, `big_gain`) so a tiny positive move is treated as `flat` instead of being trained/evaluated the same as a meaningful gain. Candidate training now targets `label_gain_5d`, where only `gain` and `big_gain` are positive classes, and day10 applies extra sample weight to `big_loss` and `big_gain` rows so the learner pays more attention to tail outcomes. Day11 reports accuracy as diagnostics, but the promotion gate is profit-utility driven: it requires better Brier, acceptable return/downside, no big-loss regression, at least 10% big-gain capture, and higher utility than production.

Warnings from yfinance for invalid/delisted symbols are expected as long as day8 still reports enough labeled rows and day10 keeps enough rows after sparse feature filling. Day8 now applies a symbol-quality filter before yfinance lookup: it normalizes common typos such as `NVDIA`/`NVSIA` to `NVDA`, rejects unsupported foreign suffix/non-equity/fund-like symbols, and records repeated yfinance failures in the runtime cache at `track_b/bad_symbols.json` so noisy symbols can be skipped in later runs.

## Live paper P&L by recommendation

Decision outcomes now track action-level paper P&L for `BUY`, `SELL`, `HOLD`, `HOLD OFF FOR NOW`, and `STRONG BUY`. Each evaluated row can include 1D/5D/10D/20D raw returns, action-adjusted paper returns, 20D max drawdown, 20D max favorable excursion, SPY benchmark return, and benchmark-relative 20D paper return. The `paper_pnl_by_recommendation` summary groups those metrics by recommendation so Track B can optimize toward avoiding large adverse moves and capturing large favorable moves instead of only counting up/down labels.

## Production safety notes

- Track B offline does **not** promote a model or change live routing.
- Promotion remains manual/separate; do not run `day14_promote_candidate.py` unless the comparison report and rollout decision are explicitly approved.
- If the Performance page shows `snapshot_source=materialized_stale`, daily ops is stale but the app is intentionally avoiding expensive live fan-out.
- If Track B fails, first inspect `track_b_summary.json` and the day10 `rows_after_feature_filter` / `feature_fill_values` fields.
