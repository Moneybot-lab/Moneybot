# Page 1 — Current State

**Status:** Operational foundation
**Purpose:** Record what exists today so remaining work does not duplicate or accidentally bypass it.

[Back to progress dashboard](README.md) · [Completed: Profile closeout](02-profile-closeout.md) · [Next: Massive REST foundation](03-massive-rest-foundation.md)

## Completed capabilities

### Versioned investor profile

- [x] One profile per user.
- [x] Goal, horizon, risk tolerance, loss capacity, liquidity, experience, and account context.
- [x] Position and sector concentration limits.
- [x] Excluded sectors, penny-stock permission, after-hours alerts, and recommendation style.
- [x] Profile completion state and conservative effective defaults.
- [x] Optimistic concurrency through `profile_version`.
- [x] Append-only profile revision records.
- [x] Authenticated read, update, and revision-history APIs.

Primary implementation:

- `moneybot/models.py`
- `moneybot/services/investor_profile.py`
- `moneybot/api.py`
- `migrations/versions/20260607_01_add_investor_profiles.py`

### Settings experience

- [x] Responsive investor questionnaire.
- [x] Completion progress and profile-version display.
- [x] Safe-default explanation for incomplete profiles.
- [x] Stale-version conflict handling.
- [x] Account identity and avatar editing retained.

Primary implementation:

- `moneybot/templates/settings.html`
- `moneybot/static/js/settings.js`

### First suitability integration

- [x] Objective `base_advice` is kept separate from personalized `advice`.
- [x] Suitability rules can soften BUY to HOLD but cannot manufacture BUY or SELL.
- [x] Penny-stock, excluded-sector, position-limit, and sector-limit rules.
- [x] Profile confidence thresholds for risk, liquidity/horizon, experience, preservation goals, loss capacity, and recommendation style.
- [x] Position and sector weight included in portfolio responses.
- [x] Applied rule codes and profile version included in decision telemetry.
- [x] Full profile answers are excluded from decision-log personalization metadata.

Primary implementation:

- `moneybot/services/suitability_policy.py`
- `moneybot/services/decision_snapshot.py`
- `moneybot/api.py` (`GET /api/user-watchlist`)

## Page 2 closeout now completed

- [x] Portfolio, Quick Ask, ClearView-trigger, and notification paths use the shared personalization contract.
- [x] Profile collection and suitability enforcement have independent feature flags, shadow mode, and deterministic rollout controls.
- [x] After-hours notification delivery respects the profile preference.
- [x] Settings exposes revision history and explains that edits affect only future recommendations.
- [x] Model-health reporting includes completion, evaluation, override, rule, mode, and churn metrics without full questionnaire answers.
- [x] Revision history has a configurable retention window.

## Remaining limitations

- Market data is still primarily request/REST driven with a process-local cache.
- Massive prices and yfinance history can still be mixed in the same analysis path.
- There is no shared Redis quote state or dedicated streaming worker.

## Current risk posture

Until the remaining pages are complete:

- Treat profile-aware advice as a portfolio-only first release.
- Keep incomplete-profile defaults conservative.
- Do not expose the Massive API key to browser code.
- Do not add full-market wildcard WebSocket subscriptions.
- Do not recompute narratives or model decisions on every tick.
- Keep existing REST/provider fallbacks enabled.

## Decision log

- **June 7, 2026:** Chose forecast/policy separation instead of training profile answers directly into the first model iteration.
- **June 7, 2026:** Chose a bounded backend Massive WebSocket plus browser SSE rather than direct browser-to-provider connections.
