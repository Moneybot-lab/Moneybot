# Massive WebSocket + Redis Shadow Worker

**Schema:** `market-stream.v1`
**Deployment state:** C1–C5 code repairs under offline review; production validation remains UNKNOWN

## Architecture

The browser never connects to Massive. A single backend `moneybot-market-stream` worker maintains the provider connection, normalizes events, writes expiring latest state to Render Key Value/Redis, and publishes coalesced update notices. The existing `LiveQuoteResolver` reads eligible fresh Redis prices first and uses the REST service when none is usable. `MASSIVE_STREAM_SHADOW_MODE=true` enables worker comparisons; it is not a REST-only API routing gate. These repairs preserve that existing routing.

```text
Massive WebSocket -> parser/order checks -> Redis latest state + coalesced Pub/Sub
                         |                         |
                         +-> bounded REST recovery +-> authenticated Page 5 SSE

Web/API demand -> Redis expiring demand keys -> subscription reconciler
Database portfolio/ClearView demand -----------^
```

## Event scope

The initial worker accepts only these concrete per-symbol channels:

- `A.{symbol}` per-second aggregates for bounded active symbols.
- `AM.{symbol}` per-minute aggregates for feature refresh.
- `Q.{symbol}` only for Quick Ask, ClearView, explicit liquidity demand, and server-owned pilot symbols.
- `T.{symbol}` only for explicit tick/trade demand and server-owned pilot symbols.

Wildcard channels are rejected. The defaults are 250 symbols globally, 100 quote symbols, and 50 trade symbols. The existing WebSocket receive queue default is 1024 frames; this repair does not change it. The frame/message size and write-buffer limits remain bounded. Queue backpressure can pause transport reading; a finite queue is not proof of keepalive reliability or resource compliance.

Massive documents per-minute aggregates as continuously updated bars covering pre-market, regular, and after-hours sessions. The worker uses the same acknowledgement-based authentication/subscription protocol for all configured stock channels. See the official [Massive stock WebSocket documentation](https://massive.com/docs/websocket/stocks/aggregates-per-minute).

## Demand and reconciliation

Demand keys are reference-counted by source during planning:

- `database:portfolio` and `database:clearview` are refreshed by the worker from PostgreSQL.
- `portfolio:{user_id}`, `clearview:{user_id}`, and `quick:{user_or_request}` are refreshed by API activity.
- Browser/request demand expires after 90 seconds by default.
- `MASSIVE_STREAM_SERVER_SYMBOLS` supplies a small server-owned pilot list.

Every reconciliation compares desired and actual subscriptions and sends only the delta. Initial authentication/subscription acknowledgements are verified before active state is reported. Reconnect clears actual state and rebuilds it from current demand. Database demand SQL remains unchanged and offloaded; synchronous Redis work in the worker uses bounded, ordered offloading.

## Reliability and ordering

- WebSocket pings and pong timeouts retain the existing `websockets` client keepalive: 20-second ping interval and 20-second timeout.
- No-message heartbeat timeout defaults to disabled (`0`) because the WebSocket client keepalive already verifies ping/pong liveness and market events can be legitimately sparse.
- Reconnect delay retains nominal 1–30-second exponential backoff with existing jitter.
- Provider event IDs and sequences retain the existing duplicate/out-of-order/discontinuity checks. A numeric discontinuity is not a verified count of lost events. C6 sequence semantics remain deferred pending provider guarantees and separate owner approval.
- Gap/disconnect recovery retains explicit stale marking and bounded REST work. Reconnect no longer waits for every REST repair before attempting the provider connection again. Delayed recovery cannot replace newer state; unavailable prices remain unavailable.
- Shadow comparison runs as at most one background batch with explicit work/time budgets. Comparison, queued recovery and reconnect recovery share bounded REST admission; retries and provider backoff remain intact.
- Redis work, health calculations and large-frame parsing are offloaded through bounded worker-owned execution. Health publication uses a cadence within the existing TTL; large batches yield without dropping events.
- Frame queue, message size, and write buffer settings are bounded as recommended by the [`websockets` asyncio client](https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html).

## Redis keys and delivery

All keys are versioned under `moneybot:market:v1`:

- `latest:{event_type}:{symbol}` — latest normalized event, default TTL 120 seconds.
- `health` — worker health, default TTL 30 seconds.
- `demand:{source}` — expiring demand registry.
- `demand-sources` — known source set.
- `updates` — coalesced Pub/Sub channel.

Pub/Sub is intentionally only a wake-up/coalescing mechanism because Redis Pub/Sub is at-most-once. Consumers re-read latest state; the existing SSE endpoint polls current state and does not replay history from `Last-Event-ID`. This follows Redis guidance on [Pub/Sub delivery semantics](https://redis.io/docs/latest/develop/pubsub/). No durable stream/replay architecture is introduced by these repairs.

## Quote and browser status contract

Price freshness belongs to the selected price event, never an unrelated ticker update or receipt. Cache reads recompute age without fetching or mutating shared cached objects. Stock prices must be finite and positive. Regular-session freshness remains 15 seconds; missing/future timestamps, session/date mismatches and daily-close fallback cannot establish live pricing.

The resolver chooses genuinely fresh eligible T/Q/A/AM candidates before stale channels. REST recovery retains the selected trade/aggregate/quote price's actual provenance and does not rename trade price as NBBO midpoint. Stateful SSE delivery prevents older observations from silently replacing newer delivered prices, while emitting updated stale/unavailable/source diagnostics. Equal-time changed prices without a verified correction contract fail closed; connection ordering does not create global pricing history.

Browser SSE connectivity, timestamp/TTL-verified provider health, row source/freshness and the portfolio's mixed quality are separate facts. Heartbeats establish browser delivery only. Missing/stale provider metadata means UNKNOWN. Status-only and null-price updates remain deliverable; valid retained numbers are labelled last known. Changes in quote status do not initiate advice/narrative refresh.

See [the investigation](reports/moneybot_stream_portfolio_offline_investigation_2026-10-09.md) and [the implementation record](reports/moneybot_stream_portfolio_reliability_repairs_2026-10-09.md) for evidence, budgets, compatibility and rollout prerequisites. No production deployment or operational acceptance is implied.

Render Key Value is Redis-compatible (currently Valkey for new instances), and its internal URL should be used by services in the same region. See [Render Key Value](https://render.com/docs/key-value).

## Persistence decision

Page 4 stores latest second/minute/quote/trade state only. It does **not** durably persist minute bars. Massive REST remains the recovery and historical source. Durable minute datasets, retention, and research reproducibility belong to Page 6.

## Shadow validation gates

Keep the worker in shadow mode until production telemetry demonstrates:

1. At least 99.9% valid-event parse success.
2. p95 provider-event-to-Redis lag below 2 seconds during market hours.
3. Automatic reconnect and REST recovery in controlled failure tests.
4. Stream-versus-REST differences within 50 basis points except documented timing/condition differences.
5. Redis memory and worker CPU/RAM remain within the selected Render plans at the 250-symbol budget.
6. Provider entitlements permit the selected event types and symbol counts.

The authenticated `/api/market-stream-health` endpoint and model-health payload expose connection, subscription, parsing, ordering, lag, Redis, recovery, and shadow-comparison metrics.

## Troubleshooting missing Massive WebSocket activity

The WebSocket connection is owned by the separate `moneybot-market-stream` Render worker, not the web service. The web service intentionally has `MASSIVE_STREAM_ENABLED=false`; the worker must have it set to `true`, share the same `REDIS_URL`, and receive `MASSIVE_API_KEY`.

Check these in order:

1. Confirm the `moneybot-market-stream` service exists and is running in Render.
2. Look for `Starting Massive stream worker`, `authenticated`, and `subscriptions active` in the worker logs.
3. Sign in to MoneyBot and call `GET /api/market-stream-health`. Review `worker_state`, `diagnosis`, `desired_symbols`, `actual_subscription_counts`, `last_message_at`, and `last_error`.
4. If the response says `no_worker_heartbeat`, the worker is not writing health to the shared Redis instance.
5. If it says `idle_no_demand`, add portfolio/ClearView symbols or set `MASSIVE_STREAM_SERVER_SYMBOLS=SPY,QQQ` on the worker.
6. If it says `connected` with no last message, verify the subscription counts and wait for an eligible market event. Massive notes that off-hours update frequency varies, and aggregate bars are not emitted when no qualifying trades occur.
7. If it says `reconnecting`, use `last_error` and the worker logs to identify authentication, entitlement, URL, or network failures.

The worker never logs or returns the API key. Provider-account usage dashboards may also lag application logs, so MoneyBot's worker health and Render logs are the first operational source of truth.
