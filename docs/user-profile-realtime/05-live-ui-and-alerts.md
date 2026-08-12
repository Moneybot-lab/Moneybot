# Page 5 — Live UI and Alert Triggers

**Status:** Implementation complete; production validation pending
**Goal:** Deliver useful real-time updates to authenticated users without recalculating expensive advice on every tick.

[Previous: Stream worker](04-realtime-stream-worker.md) · [Back to dashboard](README.md) · [Next: Historical validation](06-history-validation-rollout.md)

## Delivered in this page

### API quote resolution

- [x] Read fresh Redis stream state first.
- [x] Use normalized Massive REST data when stream state is absent or stale.
- [x] Retain the existing provider fallback chain through `MarketDataService`.
- [x] Return freshness, session, source mode, quality, and degraded/stale state.
- [x] Prevent stale Redis state from outranking REST.

The shared `LiveQuoteResolver` is used by both `/api/quote` and browser delivery. Its response contract is versioned as `live-market.v1` and includes a stable event ID.

### Browser delivery

- [x] Add authenticated `GET /api/live-market-stream` SSE delivery.
- [x] Authorize symbols from the user's portfolio/ClearView set; Quick Ask may request one explicit symbol.
- [x] Cap symbols per connection (`LIVE_SSE_SYMBOL_CAP`, default `25`).
- [x] Send event IDs, reconnect retry guidance, heartbeats, and the received `Last-Event-ID` in the ready event.
- [x] Coalesce browser-visible reads to `LIVE_SSE_INTERVAL_SECONDS` (default one second).
- [x] Refresh Redis demand TTL while connected and clear demand on generator teardown.
- [x] Preserve the last known value and expose REST fallback/degraded state when streaming fails.

### User experience

- [x] Live portfolio current price and unrealized P&L.
- [x] Live Quick Ask price and freshness header.
- [x] Visible market session and “as of” timestamp.
- [x] Clear stale, degraded, and reconnecting states.
- [x] Show when profile suitability changes the base Quick Ask or portfolio action.
- [x] Link profile adjustments to Settings.

The UI does not generate an AI narrative on each market event. A controlled boundary emits `recommendation_refresh`; the portfolio performs a bounded refresh while Quick Ask asks the user to rerun analysis.

### Controlled recommendation triggers

- [x] Minute-bar close trigger.
- [x] Price-threshold crossing with hysteresis support.
- [x] Suitability/concentration boundary input.
- [x] Debounce before firing.
- [x] Material spread/liquidity-change input.
- [x] Snapshot invalidation input for news, corporate actions, or fundamentals.
- [x] Deduplicate by user, symbol, rule, and recommendation state.
- [x] Enforce cooldowns, after-hours preferences, and a global emergency disable.
- [x] Record trigger reason plus profile/data schema versions in the trigger payload.
- [x] Export fired/suppressed trigger counters through model-health diagnostics.

## Configuration

| Variable | Default | Purpose |
| --- | ---: | --- |
| `LIVE_SSE_SYMBOL_CAP` | `25` | Maximum visible symbols on one authenticated connection |
| `LIVE_SSE_INTERVAL_SECONDS` | `1.0` | Browser update coalescing interval |
| `LIVE_SSE_HEARTBEAT_SECONDS` | `15.0` | Heartbeat and demand-refresh interval |
| `LIVE_TRIGGER_DEBOUNCE_SECONDS` | `15.0` | Required persistence before a controlled trigger fires |
| `LIVE_TRIGGER_COOLDOWN_SECONDS` | `300.0` | Minimum time between trigger deliveries |
| `LIVE_ALERTS_EMERGENCY_DISABLED` | `false` | Immediately suppress every live recommendation trigger |

## Tests completed

- [x] SSE authentication and symbol authorization.
- [x] Connection cleanup and demand removal.
- [x] Heartbeat and resume metadata.
- [x] Redis-first and stale-stream-to-REST behavior.
- [x] Trigger debounce, hysteresis, deduplication, cooldown, after-hours, and emergency-disable behavior.
- [x] Frontend stale/reconnect and suitability-adjustment surfaces.
- [x] Per-connection symbol-cap behavior.

## Production exit gates

These remain operational checks rather than code-complete claims:

1. Verify portfolio and Quick Ask updates against the deployed Redis worker.
2. Confirm disconnects do not leave demand beyond the configured TTL.
3. Measure SSE connection count, delivery latency, reconnect rate, and REST fallback rate.
4. Confirm notification volume and recommendation churn stay within explicit limits.
5. Add durable sent/failed/opened/acted-on alert analytics before enabling push delivery from live triggers.
6. Keep `LIVE_ALERTS_EMERGENCY_DISABLED=true` until the Page 4 shadow-data gates pass in production.

## Decision log

- Use server-sent events instead of a second browser WebSocket. The backend owns the provider connection and credentials; browsers receive a constrained, authenticated, one-way stream.
- Poll shared Redis latest-state at a bounded visible rate rather than forwarding every provider event.
- Stream prices continuously, but refresh recommendations only at controlled boundaries.
- Keep durable notification engagement analytics and broad push delivery as a production rollout follow-up, not as a prerequisite for live prices.
