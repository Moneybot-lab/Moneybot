# Page 2 — Profile Integration Closeout

**Status:** Next
**Goal:** Make the investor profile a consistent, observable contract across MoneyBot before introducing streaming complexity.

[Previous: Current state](01-current-state.md) · [Back to dashboard](README.md) · [Next: Massive REST foundation](03-massive-rest-foundation.md)

## Why this comes next

The profile database, API, questionnaire, and first portfolio policy are working. The remaining profile work is integration: every personalized path should consume the same context, produce the same explanation shape, and record the same profile-version metadata.

## Work checklist

### Configuration and rollout safety

- [ ] Add `INVESTOR_PROFILE_ENABLED` configuration.
- [ ] Add `SUITABILITY_POLICY_ENABLED` configuration.
- [ ] Add a shadow mode that calculates personalized advice without changing the displayed action.
- [ ] Add deterministic cohorting or an allowlist for controlled rollout.
- [ ] Document how to disable enforcement without reverting a deployment.

### Shared decision contract

- [ ] Move profile loading/context construction out of the watchlist endpoint into a reusable service.
- [ ] Define one `PersonalizedDecision` response contract for portfolio, Quick Ask, and triggers.
- [ ] Include base action, personalized action, applied rules, profile version, completion state, and forecast horizon.
- [ ] Define stable public rule codes and user-facing messages.
- [ ] Add a policy-schema version for historical reproducibility.

### Recommendation-path coverage

- [ ] Apply the context to Quick Ask without changing the objective forecast fields.
- [ ] Apply the context to ClearView decisions.
- [ ] Apply the context to notification generation.
- [ ] Use `after_hours_alerts` to suppress or defer after-hours notifications.
- [ ] Use recommendation style only for presentation and thresholds—not to alter raw market facts.
- [ ] Confirm SELL behavior explicitly before adding profile-based SELL modifications.

### Portfolio correctness

- [ ] Define how cash is represented before treating position weights as final allocation percentages.
- [ ] Decide whether concentration rules block only incremental BUY or also suggest trimming.
- [ ] Resolve sectors from normalized Massive reference data rather than yfinance where possible.
- [ ] Handle unknown sectors without falsely reporting compliance.
- [ ] Add tests for zero-value positions, missing quotes, duplicate sectors, and partial holdings.

### User experience

- [ ] Show profile-adjustment rule labels in the portfolio advice modal.
- [ ] Show whether the displayed action differs from the base market action.
- [ ] Add a direct link from adjusted advice to Account Settings.
- [ ] Add an optional revision-history panel in Settings.
- [ ] Explain that changes affect future recommendations and do not rewrite historical decisions.

### Metrics and privacy

- [ ] Count complete versus incomplete profiles.
- [ ] Count policy evaluations and action overrides by rule code.
- [ ] Measure recommendation churn before and after policy enforcement.
- [ ] Track outcomes by policy version without storing unnecessary profile details.
- [ ] Add a retention policy for profile revision history.
- [ ] Review logs to ensure profile answers are not emitted accidentally.

## Required tests

- [ ] Unit tests for every rule boundary and combination priority.
- [ ] Contract tests showing identical forecasts produce different suitable actions for different profiles.
- [ ] Tests proving the policy cannot create BUY or SELL.
- [ ] Tests for feature-flag off, shadow, and enforced modes.
- [ ] Tests for Quick Ask, portfolio, and notification response consistency.
- [ ] Tests proving historical decision snapshots retain the original profile and policy versions.

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

- No additional decisions recorded yet.
