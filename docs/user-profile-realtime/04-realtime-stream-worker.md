# Page 4 — Massive WebSocket Worker

**Status:** Not started
**Goal:** Maintain a bounded, recoverable provider WebSocket and publish normalized latest state to Redis.

[Previous: Massive REST foundation](03-massive-rest-foundation.md) · [Back to dashboard](README.md) · [Next: Live UI and alerts](05-live-ui-and-alerts.md)

## Infrastructure

- [ ] Provision Redis for hot state, pub/sub, subscription demand, and health metadata.
- [ ] Add a separate `market-stream` worker process/service.
- [ ] Keep `MASSIVE_API_KEY` only in backend worker/API environments.
- [ ] Add `MASSIVE_STREAM_ENABLED` and shadow-mode configuration.
- [ ] Define worker resource limits and deployment restart policy.

## Initial stream scope

- [ ] Define a hard initial symbol budget (recommended: 100–500).
- [ ] Subscribe to per-second aggregates for visible/active symbols.
- [ ] Subscribe to per-minute aggregates for feature refresh and persistence.
- [ ] Subscribe to quotes only where spread/liquidity is required.
- [ ] Subscribe to trades only where tick-level behavior is required.
- [ ] Explicitly reject full-market wildcard subscriptions in the first release.

## Subscription manager

- [ ] Track desired symbols by portfolio, watchlist, Quick Ask, and server-owned scanner demand.
- [ ] Reference-count symbol demand.
- [ ] Debounce subscribe/unsubscribe churn.
- [ ] Expire abandoned browser demand.
- [ ] Reconcile desired versus actual subscriptions after reconnect.
- [ ] Enforce per-feature and global symbol caps.

## Connection reliability

- [ ] Authenticate and subscribe with provider acknowledgements checked.
- [ ] Reconnect with exponential backoff and jitter.
- [ ] Add heartbeat/last-message monitoring.
- [ ] Detect malformed, duplicate, out-of-order, and sequence-gap events.
- [ ] Detect slow-consumer and backpressure conditions.
- [ ] Refresh affected symbols from REST after gaps or reconnects.
- [ ] Mark Redis state stale while recovery is incomplete.

## Redis state

- [ ] Store normalized latest quote/trade/bar per symbol.
- [ ] Store event time, receive time, age, session, source mode, and quality flags.
- [ ] Publish coalesced update notifications rather than every raw event.
- [ ] Set TTLs so dead worker state cannot remain indefinitely fresh.
- [ ] Version Redis keys and serialized event schemas.
- [ ] Decide which minute bars are persisted durably.

## Observability

- [ ] Connection state and reconnect count.
- [ ] Desired and actual subscription counts.
- [ ] Messages received by event type.
- [ ] Parse failures, duplicates, sequence gaps, and dropped/coalesced events.
- [ ] Provider-event-to-Redis lag p50/p95/p99.
- [ ] Redis write latency and memory use.
- [ ] REST recovery count and duration.

## Required tests

- [ ] Recorded WebSocket event fixtures for every subscribed event type.
- [ ] Authentication/subscription acknowledgement tests.
- [ ] Reconnect and subscription reconciliation tests.
- [ ] Duplicate, out-of-order, malformed, and gap tests.
- [ ] Redis TTL and stale-state tests.
- [ ] Load test at the intended symbol budget.
- [ ] Shadow comparison against Massive REST snapshots.

## Exit criteria

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

- No additional decisions recorded yet.
