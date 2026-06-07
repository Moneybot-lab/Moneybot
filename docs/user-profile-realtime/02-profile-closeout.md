# Page 2 — Profile Integration Closeout

**Status:** Complete — June 7, 2026
**Goal:** Make the investor profile a consistent, observable contract across MoneyBot before introducing streaming complexity.

[Previous: Current state](01-current-state.md) · [Back to dashboard](README.md) · [Next: Massive REST foundation](03-massive-rest-foundation.md)

## Why this comes next

The profile database, API, questionnaire, and first portfolio policy are working. The remaining profile work is integration: every personalized path should consume the same context, produce the same explanation shape, and record the same profile-version metadata.

## Work checklist

### Configuration and rollout safety

- [x] Add `INVESTOR_PROFILE_ENABLED` configuration.
- [x] Add `SUITABILITY_POLICY_ENABLED` configuration.
- [x] Add a shadow mode that calculates personalized advice without changing the displayed action.
- [x] Add deterministic cohorting or an allowlist for controlled rollout.
- [x] Document how to disable enforcement without reverting a deployment.

### Shared decision contract

- [x] Move profile loading/context construction out of the watchlist endpoint into a reusable service.
- [x] Define one `PersonalizedDecision` response contract for portfolio, Quick Ask, and triggers.
- [x] Include base action, personalized action, applied rules, profile version, completion state, and forecast horizon.
- [x] Define stable public rule codes and user-facing messages.
- [x] Add a policy-schema version for historical reproducibility.

### Recommendation-path coverage

- [x] Apply the context to Quick Ask without changing the objective forecast fields.
- [x] Apply the context to ClearView decisions.
- [x] Apply the context to notification generation.
- [x] Use `after_hours_alerts` to suppress or defer after-hours notifications.
- [x] Use recommendation style only for presentation and thresholds—not to alter raw market facts.
- [x] Confirm SELL behavior explicitly before adding profile-based SELL modifications.

### Portfolio correctness

- [x] Define how cash is represented before treating position weights as final allocation percentages.
- [x] Decide whether concentration rules block only incremental BUY or also suggest trimming.
- [x] Record sector-source normalization as a Page 3 dependency; current policy treats unknown sectors as unavailable rather than compliant.
- [x] Handle unknown sectors without falsely reporting compliance.
- [x] Add tests for zero-value positions, missing quotes, duplicate sectors, and partial holdings.

### User experience

- [x] Show profile-adjustment rule labels in the portfolio advice modal.
- [x] Show whether the displayed action differs from the base market action.
- [x] Add a direct link from adjusted advice to Account Settings.
- [x] Add an optional revision-history panel in Settings.
- [x] Explain that changes affect future recommendations and do not rewrite historical decisions.

### Metrics and privacy

- [x] Count complete versus incomplete profiles.
- [x] Count policy evaluations and action overrides by rule code.
- [x] Measure recommendation churn before and after policy enforcement.
- [x] Track outcomes by policy version without storing unnecessary profile details.
- [x] Add a retention policy for profile revision history.
- [x] Review logs to ensure profile answers are not emitted accidentally.

## Required tests

- [x] Unit tests for every rule boundary and combination priority.
- [x] Contract tests showing identical forecasts produce different suitable actions for different profiles.
- [x] Tests proving the policy cannot create BUY or SELL.
- [x] Tests for feature-flag off, shadow, and enforced modes.
- [x] Tests for Quick Ask, portfolio, and notification response consistency, including after-hours suppression.
- [x] Tests proving historical decision snapshots retain the original profile and policy versions.

## Exit criteria

This page is complete when:

1. One reusable decision-context service is used by all personalized recommendation paths.
2. Policy enforcement can be disabled or run in shadow mode through configuration.
3. Quick Ask and portfolio responses use the same personalization contract.
4. Notification timing respects after-hours preferences.
5. Profile/policy metrics are visible without leaking sensitive answers.
6. All focused tests pass and existing non-profile behavior remains compatible.

## Suggested pull requests

1. **Profile feature flags and reusable context service**
2. **Quick Ask and ClearView personalization contract**
3. **Profile-aware notification timing and severity**
4. **Advice-modal explanations and revision-history UI**
5. **Profile/policy metrics and privacy audit**

## Decision log

- **June 7, 2026:** Added `off`, `shadow`, and `enforce` modes with deterministic user rollout and an explicit allowlist.
- **June 7, 2026:** Standardized personalization as `suitability.v1`, preserving the base action, policy action, displayed action, profile version, completion state, rules, cohort, mode, and forecast horizon.
- **June 7, 2026:** Confirmed the first policy version never creates or modifies SELL actions; it only softens unsuitable BUY actions to HOLD.
- **June 7, 2026:** Defined portfolio weights as invested positions only, excluding unknown cash balances.
- **June 7, 2026:** Deferred authoritative sector sourcing to Page 3 Massive reference normalization; unknown sectors do not generate a false compliance rule.
- **June 7, 2026:** Added a configurable revision-retention window with a default of 2,555 days and kept full questionnaire answers out of personalization telemetry.
