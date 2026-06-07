# Page 5 — Live UI and Alert Triggers

**Status:** Not started
**Goal:** Deliver useful real-time updates to authenticated users without recalculating expensive advice on every tick.

[Previous: Stream worker](04-realtime-stream-worker.md) · [Back to dashboard](README.md) · [Next: Historical validation](06-history-validation-rollout.md)

## API quote resolution

- [ ] Read fresh Redis stream state first.
- [ ] Use Massive REST to synchronize or fill gaps.
- [ ] Retain the existing provider fallback chain.
- [ ] Return freshness, session, source mode, and degraded/stale state.
- [ ] Prevent stale Redis state from outranking a fresher REST response.

## Browser delivery

- [ ] Add authenticated SSE endpoint for permitted symbols.
- [ ] Cap symbols per connection and per user.
- [ ] Send heartbeat events.
- [ ] Add resumable event IDs where useful.
- [ ] Coalesce updates to a visible rate, such as one or two updates per second per symbol.
- [ ] Clean up symbol demand promptly after disconnect.
- [ ] Fall back to periodic REST refresh if SSE or Redis is degraded.

## User experience

- [ ] Live portfolio current price and P&L.
- [ ] Live Quick Ask price/freshness header.
- [ ] Visible market session and “as of” timestamp.
- [ ] Clear stale/degraded/reconnecting status.
- [ ] Preserve the last known value without pretending it is current.
- [ ] Show when profile suitability changed the base market action.
- [ ] Link profile adjustments to Settings.

## Controlled recommendation triggers

Recompute cheap values continuously, but recompute recommendations only when:

- [ ] A second/minute bar closes according to the feature contract.
- [ ] Price crosses a user-specific threshold.
- [ ] A suitability/concentration boundary is crossed.
- [ ] A recommendation boundary remains crossed for a debounce period.
- [ ] Spread/liquidity quality materially changes.
- [ ] News, corporate actions, or fundamentals invalidate the snapshot.

Do not:

- [ ] Generate a new AI narrative on every tick.
- [ ] Send an alert for every intermediate threshold crossing.
- [ ] Trigger after-hours alerts when the user disabled them.

## Notification controls

- [ ] Deduplicate by user, symbol, rule, and recommendation state.
- [ ] Add cooldowns and hysteresis.
- [ ] Apply after-hours preferences and quiet-time policy.
- [ ] Record why an alert fired and which data/profile versions were used.
- [ ] Track sent, suppressed, failed, opened, and acted-on events.
- [ ] Add a global emergency disable switch.

## Required tests

- [ ] SSE authentication and symbol authorization.
- [ ] Connection cleanup and symbol-demand expiry.
- [ ] Heartbeat, reconnect, and resume behavior.
- [ ] Redis-to-REST degradation behavior.
- [ ] Trigger debounce, hysteresis, deduplication, and cooldown tests.
- [ ] After-hours and market-holiday tests.
- [ ] Frontend stale-state and reconnect rendering tests.
- [ ] Load tests for expected concurrent users and visible symbols.

## Exit criteria

1. Portfolio and Quick Ask visibly update from the stream.
2. UI always exposes freshness and degraded state.
3. Browser reconnects do not leak subscriptions.
4. REST fallback works automatically.
5. Notification volume stays within explicit limits.
6. Recommendation churn does not materially increase.

## Suggested pull requests

1. **Redis-first quote resolver**
2. **Authenticated SSE endpoint and connection registry**
3. **Portfolio/Quick Ask live update UI**
4. **Controlled recommendation trigger engine**
5. **Notification deduplication, cooldowns, and after-hours policy**

## Decision log

- No additional decisions recorded yet.
