# Page 6 — Historical Validation and Rollout

**Status:** Validation foundation implemented; historical data pipeline and production gates pending
**Goal:** Prove that fresher data and personalized actions improve decisions out of sample before broad promotion.

[Previous: Live UI and alerts](05-live-ui-and-alerts.md) · [Back to dashboard](README.md)

## Delivered foundation

- [x] Versioned `dataset_manifest.v1` with source, rows, date range, adjustment method, point-in-time declaration, delisted-security declaration, and SHA-256 checksum.
- [x] Versioned `historical_validation.v1` report and `promotion_gates.v1` results.
- [x] Brier score and expected calibration error.
- [x] BUY/SELL precision and recall.
- [x] Gross and friction-adjusted returns.
- [x] Maximum adverse excursion fields when supplied.
- [x] Recommendation churn, profile override, stale-data, fallback, and source-mode metrics.
- [x] Privacy-safe aggregation by profile bucket.
- [x] Explicit promotion blockers with a safe `hold_shadow` default.
- [x] Licensing and privacy review blockers.
- [x] Protected report API and model-health summary.
- [x] Weekly report generation after outcome materialization.
- [x] Unit, API, and command-line tests.

See [Historical Validation Contract](../historical_validation_contract.md) for the artifact and gate schemas.

## Historical dataset work remaining

- [ ] Ingest Massive flat files for broad historical daily and intraday coverage.
- [ ] Build adjusted bars with explicit split/dividend treatment and corporate-action lineage.
- [ ] Add point-in-time reference, filing, and fundamental availability timestamps.
- [ ] Include delisted, failed, renamed, and merged securities where licensing permits.
- [ ] Persist immutable manifests beside every derived dataset and model artifact.
- [ ] Add dataset storage retention, encryption, and cost controls.

The current report consumes materialized MoneyBot decision outcomes. It is intentionally compatible with a future Massive flat-file pipeline, but it does not claim that pipeline already exists.

## Leakage and realism controls remaining

- [ ] Enforce historical availability timestamps for filings and fundamentals.
- [ ] Add time-based train, validation, and untouched test partitions.
- [ ] Add rolling walk-forward folds with frozen configuration per fold.
- [x] Support spread/slippage/fee inputs through `transaction_cost_bps` or `estimated_friction_bps`.
- [ ] Separate pre-market, regular, and after-hours execution assumptions in backtests.
- [ ] Test corporate-action, symbol-change, delisting, and market-holiday behavior end to end.
- [ ] Record decision latency and reject fills that could not realistically occur.

## Evaluation dimensions

- [x] Brier score and calibration error by report horizon.
- [x] BUY/SELL boundary precision and recall.
- [x] Realized return net of estimated trading friction.
- [x] Maximum adverse excursion when available.
- [x] Recommendation stability/churn.
- [x] Profile override rate.
- [x] Aggregation by risk/profile bucket without exposing users.
- [x] Data freshness and fallback/source-mixing rates.
- [ ] Regime, volatility, sector, liquidity, and market-cap slices from point-in-time data.
- [ ] Statistical confidence intervals and multiple-comparison controls for subgroup review.

## Controlled rollout

- [x] Existing normalized REST, WebSocket, and suitability shadow modes remain the first rollout stage.
- [x] Existing deterministic allowlists and percentage cohorts remain available.
- [x] Page 6 generates a machine-readable promotion or `hold_shadow` recommendation.
- [x] Failed blocking gates identify required next steps.
- [ ] Automatically apply rollback after a sustained production gate breach.
- [ ] Require human subgroup review before increasing percentage rollout.
- [ ] Record every rollout change with approver, cohort, policy version, model version, and dataset checksum.

## Default promotion gates

A candidate remains in shadow unless all blocking gates pass:

- Minimum evaluated sample size.
- Brier score below the absolute limit and within baseline regression tolerance.
- Non-negative average return after configured friction.
- Recommendation churn below its limit.
- Stale-data and fallback rates below their limits.
- No unacceptable maximum-adverse-excursion regression when baseline MAE exists.
- Licensing review complete.
- Privacy review complete.

Operational stream reliability, provider cost, subgroup regressions, and licensing terms still require production review before rollout increases.

## Commands and API

```bash
python scripts/page6_historical_validation_report.py \
  --outcomes data/decision_outcomes_snapshot.json \
  --output data/historical_validation_report.json
```

- Protected report: `GET /api/historical-validation` with `X-Daily-Ops-Token`.
- Summary: `GET /api/model-health` → `historical_validation`.
- Weekly automation: `python scripts/run_weekly_model_refresh.py`.

## Exit criteria

1. Massive historical datasets and model artifacts are reproducible by manifest checksum.
2. Point-in-time, survivorship-bias, corporate-action, and leakage controls are verified.
3. Walk-forward results satisfy every automated gate and human subgroup review.
4. Shadow and allowlist rollouts meet production reliability, latency, cost, and notification-volume targets.
5. Automatic rollback is exercised in staging.
6. Production rollout changes record cohort, policy, model, and dataset versions.

## Decision log

- Keep validation metrics independent of the historical data source so current decision outcomes and future Massive flat files share one promotion contract.
- Treat missing evidence as a blocker rather than a pass.
- Keep licensing and privacy as explicit blocking gates, not prose-only checklist items.
- Expose only a summary through model health; require the operations token for the full report.
