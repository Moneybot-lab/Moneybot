# Page 6 — Historical Validation and Rollout

**Status:** Not started
**Goal:** Prove that fresher data and personalized actions improve decisions out of sample before broad promotion.

[Previous: Live UI and alerts](05-live-ui-and-alerts.md) · [Back to dashboard](README.md)

## Historical dataset

- [ ] Use Massive flat files for broad historical ingestion where practical.
- [ ] Build adjusted daily and intraday bars with explicit split/dividend treatment.
- [ ] Add point-in-time reference and fundamental fields where used.
- [ ] Include delisted/failed securities where available to reduce survivorship bias.
- [ ] Store dataset date, schema version, adjustment method, and checksums.
- [ ] Make model artifacts reference an immutable dataset manifest.

## Leakage and realism controls

- [ ] Ensure filings/fundamentals are not visible before their historical availability time.
- [ ] Use time-based train, validation, and test partitions.
- [ ] Use walk-forward evaluation.
- [ ] Model spread, slippage, fees, and decision latency.
- [ ] Separate pre-market, regular, and after-hours assumptions.
- [ ] Test corporate-action and symbol-change handling.

## Evaluation dimensions

- [ ] Brier score and calibration error by horizon.
- [ ] BUY/SELL boundary precision and recall.
- [ ] Realized return and maximum adverse excursion.
- [ ] Results net of estimated trading friction.
- [ ] Recommendation stability/churn.
- [ ] Profile override rate and subsequent outcomes.
- [ ] Results by risk profile, without exposing individual users.
- [ ] Results by regime, volatility, sector, liquidity, and market cap.
- [ ] Data freshness and fallback/source-mixing rates.

## Controlled rollout

- [ ] Establish baseline metrics before stream enforcement.
- [ ] Run normalized REST changes in shadow mode.
- [ ] Run WebSocket state in shadow mode against REST.
- [ ] Run profile policy changes in shadow mode by cohort.
- [ ] Roll out to an allowlist first.
- [ ] Add percentage-based cohorts with deterministic assignment.
- [ ] Define automatic rollback gates.
- [ ] Review subgroup regressions before increasing rollout.

## Promotion gates

A candidate should not be promoted unless:

- [ ] Calibration improves or stays within an approved tolerance.
- [ ] Net outcomes improve for the intended horizon.
- [ ] Drawdown and adverse excursion do not regress beyond limits.
- [ ] Recommendation churn remains acceptable.
- [ ] Stale-data and stream-recovery incidents stay below limits.
- [ ] No material subgroup regression is found.
- [ ] Operational cost remains within budget.
- [ ] Licensing and privacy reviews are complete.

## Operations after launch

- [ ] Daily stream/data-quality report.
- [ ] Weekly profile-policy and recommendation-churn report.
- [ ] Weekly model/outcome materialization.
- [ ] Monthly provider-cost and Redis-capacity review.
- [ ] Incident runbooks for Massive, Redis, API, and notification outages.
- [ ] Periodic review of profile questions and suitability thresholds.

## Exit criteria

1. Historical datasets and model artifacts are reproducible.
2. Walk-forward results satisfy promotion gates.
3. Shadow and allowlist rollouts meet data-quality and reliability targets.
4. Rollback controls are tested.
5. Daily/weekly operational reports identify regressions quickly.
6. Production rollout is documented with cohort and policy versions.

## Suggested pull requests

1. **Massive flat-file ingestion and dataset manifest**
2. **Point-in-time feature and adjustment pipeline**
3. **Walk-forward evaluation with transaction costs**
4. **Shadow/cohort rollout controls and promotion gates**
5. **Daily/weekly operational reporting and incident runbooks**

## Decision log

- No additional decisions recorded yet.
