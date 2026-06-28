# Page 4 — Massive WebSocket Worker

**Status:** Shadow-ready — production validation pending
**Goal:** Maintain a bounded, recoverable provider WebSocket and publish normalized latest state to Redis.

[Previous: Massive REST foundation](03-massive-rest-foundation.md) · [Back to dashboard](README.md) · [Next: Live UI and alerts](05-live-ui-and-alerts.md)

## Infrastructure

- [x] Provision Redis for hot state, pub/sub, subscription demand, and health metadata.
- [x] Add a separate `market-stream` worker process/service.
- [x] Keep `MASSIVE_API_KEY` only in backend worker/API environments.
- [x] Add `MASSIVE_STREAM_ENABLED` and shadow-mode configuration.
- [x] Define worker resource limits and deployment restart policy.

## Initial stream scope

- [x] Define a hard initial symbol budget (recommended: 100–500).
- [x] Subscribe to per-second aggregates for visible/active symbols.
- [x] Subscribe to per-minute aggregates for feature refresh and persistence.
- [x] Subscribe to quotes only where spread/liquidity is required.
- [x] Subscribe to trades only where tick-level behavior is required.
- [x] Explicitly reject full-market wildcard subscriptions in the first release.

## Subscription manager

- [x] Track desired symbols by portfolio, watchlist, Quick Ask, and server-owned scanner demand.
- [x] Reference-count symbol demand.
- [x] Debounce subscribe/unsubscribe churn.
- [x] Expire abandoned browser demand.
- [x] Reconcile desired versus actual subscriptions after reconnect.
- [x] Enforce per-feature and global symbol caps.

## Connection reliability

- [x] Authenticate and subscribe with provider acknowledgements checked.
- [x] Reconnect with exponential backoff and jitter.
- [x] Add heartbeat/last-message monitoring.
- [x] Detect malformed, duplicate, out-of-order, and sequence-gap events.
- [x] Detect slow-consumer and backpressure conditions.
- [x] Refresh affected symbols from REST after gaps or reconnects.
- [x] Mark Redis state stale while recovery is incomplete.

## Redis state

- [x] Store normalized latest quote/trade/bar per symbol.
- [x] Store event time, receive time, age, session, source mode, and quality flags.
- [x] Publish coalesced update notifications rather than every raw event.
- [x] Set TTLs so dead worker state cannot remain indefinitely fresh.
- [x] Version Redis keys and serialized event schemas.
- [x] Decide which minute bars are persisted durably: none in Page 4; REST remains historical truth and Page 6 owns durable research datasets.

## Observability

- [x] Connection state and reconnect count.
- [x] Desired and actual subscription counts.
- [x] Messages received by event type.
- [x] Parse failures, duplicates, sequence gaps, and dropped/coalesced events.
- [x] Provider-event-to-Redis lag p50/p95/p99.
- [x] Redis write latency and memory use.
- [x] REST recovery count and duration.

## Required tests

- [x] Recorded WebSocket event fixtures for every subscribed event type.
- [x] Authentication/subscription acknowledgement tests.
- [x] Reconnect and subscription reconciliation tests.
- [x] Duplicate, out-of-order, malformed, and gap tests.
- [x] Redis TTL and stale-state tests.
- [x] Load test at the intended symbol budget.
- [x] Shadow comparison against Massive REST snapshots.

## Exit criteria

Implementation is complete. These production shadow gates remain to be measured after deployment:

1. At least 99.9% of valid pilot events parse successfully.
2. p95 provider-event-to-Redis lag is under two seconds during market hours.
3. Reconnect and snapshot recovery are automatic.
4. Stream-versus-REST discrepancies stay within defined tolerances.
5. The stream can be disabled without affecting REST functionality.
6. The worker remains within resource limits at the chosen symbol budget.

## Suggested pull requests

1. **Redis hot-state repository and event schema**
2. **Massive WebSocket client and parser**
3. **Subscription manager and bounded demand registry**
4. **Reconnect, gap recovery, and staleness controls**
5. **Shadow metrics and load-test tooling**

## Decision log

- **June 7, 2026:** Chose one bounded worker connection, a 250-symbol global cap, 100 quote cap, 50 trade cap, and no wildcard subscriptions.
- **June 7, 2026:** Use per-second and per-minute aggregates for all accepted symbols; quotes and trades require feature-specific demand or a server-owned pilot symbol.
- **June 7, 2026:** Version Redis keys under `moneybot:market:v1`, expire latest state after 120 seconds, and treat Pub/Sub only as a coalesced wake-up signal.
- **June 7, 2026:** Keep REST as the user-facing source in shadow mode; reconnects and sequence gaps mark state stale and trigger REST snapshot recovery.
- **June 7, 2026:** Do not persist minute bars durably in Page 4. Durable historical datasets remain Page 6 work.
- **June 7, 2026:** Added Render Key Value plus a starter background worker declaration; keep exactly one worker instance during initial entitlement and resource validation.
- **June 7, 2026:** Detailed the architecture, keys, reliability model, and rollout gates in [`docs/massive_stream_shadow_worker.md`](../massive_stream_shadow_worker.md).
