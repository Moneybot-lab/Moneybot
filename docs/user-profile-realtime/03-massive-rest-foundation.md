# Page 3 — Massive REST Foundation

**Status:** Not started
**Goal:** Make real-time and historical market inputs consistent, timestamped, and testable before opening a long-lived WebSocket.

[Previous: Profile closeout](02-profile-closeout.md) · [Back to dashboard](README.md) · [Next: Stream worker](04-realtime-stream-worker.md)

## Architecture deliverables

- [ ] Create a `MarketDataProvider` interface.
- [ ] Move Massive request and parsing logic out of the broad `MarketDataService`.
- [ ] Add a Massive client with centralized authentication, timeout, retry, and error mapping.
- [ ] Keep Finnhub, Twelve Data, and yfinance behind explicit fallback adapters.
- [ ] Add provider contract fixtures for successful, partial, stale, forbidden, and rate-limited responses.

## Normalized quote contract

Every quote should expose:

- [ ] `symbol`
- [ ] `bid`, `ask`, `bid_size`, `ask_size`, and `midpoint`
- [ ] `last_trade_price` and `last_trade_size`
- [ ] Selected display/valuation `price` and why it was selected
- [ ] `event_timestamp` and `received_timestamp`
- [ ] `age_ms`
- [ ] `market_session` (`pre`, `regular`, `after`, or `closed`)
- [ ] `source` and `source_mode` (`rest`, `websocket`, or `fallback`)
- [ ] `is_stale`
- [ ] `quality_flags`
- [ ] Sequence or provider identifiers when available

## Correctness tasks

- [ ] Stop labeling daily close data as real-time.
- [ ] Define price-selection rules for portfolio valuation versus spread/liquidity analysis.
- [ ] Define staleness thresholds by market session.
- [ ] Add exchange-calendar/holiday awareness.
- [ ] Normalize nanosecond/millisecond timestamps to timezone-aware UTC values.
- [ ] Add split/dividend adjustment rules for historical bars.
- [ ] Make mixed-source decisions explicit in snapshots and logs.
- [ ] Verify Massive market-data licensing for the intended deployment and display audience.

## Massive endpoint coverage

- [ ] Single-ticker snapshot.
- [ ] Latest trade.
- [ ] Latest quote/NBBO.
- [ ] Second aggregates.
- [ ] Minute aggregates.
- [ ] Historical aggregate bars.
- [ ] Ticker reference details.
- [ ] Corporate actions.
- [ ] Financials/ratios only where a defined feature uses them.

## Efficiency tasks

- [ ] Add request-level and shared cache policy definitions.
- [ ] Add cache keys that include provider/schema version where necessary.
- [ ] Add negative caching and rate-limit backoff.
- [ ] Avoid duplicate quote/history requests inside a single API response.
- [ ] Record provider-call, fallback, latency, and stale-response metrics.

## Required tests

- [ ] Parsing tests using recorded Massive fixtures.
- [ ] Price-selection tests for trade, midpoint, aggregate close, and stale inputs.
- [ ] Market-session and daylight-saving tests.
- [ ] Corporate-action adjustment tests.
- [ ] Provider fallback and mixed-source diagnostics tests.
- [ ] Contract tests proving decisions can be reproduced from a normalized snapshot.

## Exit criteria

1. All quote consumers use the normalized contract.
2. Stale data cannot be presented as live.
3. Technical indicators for pilot symbols use a consistent Massive history source.
4. Provider fallback and source mixing are visible.
5. REST snapshot data is reliable enough to recover a future WebSocket gap.
6. Licensing assumptions are recorded.

## Suggested pull requests

1. **Provider interface and normalized quote schema**
2. **Massive snapshots/trades/quotes client**
3. **Massive aggregates and corporate-action handling**
4. **Freshness, session, quality, and fallback diagnostics**
5. **Provider metrics and recorded contract fixtures**

## Decision log

- No additional decisions recorded yet.
