# Page 3 — Massive REST Foundation

**Status:** Complete — June 7, 2026
**Goal:** Make real-time and historical market inputs consistent, timestamped, and testable before opening a long-lived WebSocket.

[Previous: Profile closeout](02-profile-closeout.md) · [Back to dashboard](README.md) · [Next: Stream worker](04-realtime-stream-worker.md)

## Architecture deliverables

- [x] Create a `MarketDataProvider` interface.
- [x] Move Massive request and parsing logic out of the broad `MarketDataService`.
- [x] Add a Massive client with centralized authentication, timeout, retry, and error mapping.
- [x] Keep Finnhub, Twelve Data, and yfinance behind explicit fallback adapters.
- [x] Add provider contract fixtures for successful, partial, stale, forbidden, and rate-limited responses.

## Normalized quote contract

Every quote should expose:

- [x] `symbol`
- [x] `bid`, `ask`, `bid_size`, `ask_size`, and `midpoint`
- [x] `last_trade_price` and `last_trade_size`
- [x] Selected display/valuation `price` and why it was selected
- [x] `event_timestamp` and `received_timestamp`
- [x] `age_ms`
- [x] `market_session` (`pre`, `regular`, `after`, or `closed`)
- [x] `source` and `source_mode` (`rest`, `websocket`, or `fallback`)
- [x] `is_stale`
- [x] `quality_flags`
- [x] Sequence or provider identifiers when available

## Correctness tasks

- [x] Stop labeling daily close data as real-time.
- [x] Define price-selection rules for portfolio valuation versus spread/liquidity analysis.
- [x] Define staleness thresholds by market session.
- [x] Add exchange-calendar/holiday awareness.
- [x] Normalize nanosecond/millisecond timestamps to timezone-aware UTC values.
- [x] Add split/dividend adjustment rules for historical bars.
- [x] Make mixed-source decisions explicit in snapshots and logs.
- [x] Record the current individual/non-professional licensing assumption and require provider approval before multi-user or commercial deployment.

## Massive endpoint coverage

- [x] Single-ticker snapshot.
- [x] Latest trade.
- [x] Latest quote/NBBO.
- [x] Explicitly reject historical REST second aggregates and route real-time second aggregates to the Page 4 WebSocket design.
- [x] Minute aggregates.
- [x] Historical aggregate bars.
- [x] Ticker reference details.
- [x] Corporate actions.
- [x] Financials/ratios only where a defined feature uses them.

## Efficiency tasks

- [x] Add request-level and process-shared client cache policy definitions; reserve cross-worker Redis state for Page 4.
- [x] Add cache keys that include provider/schema version where necessary.
- [x] Add negative caching and rate-limit backoff.
- [x] Avoid duplicate quote/history requests inside a single API response.
- [x] Record provider-call, fallback, latency, and stale-response metrics.

## Required tests

- [x] Parsing tests using recorded Massive fixtures.
- [x] Price-selection tests for trade, midpoint, aggregate close, and stale inputs.
- [x] Market-session and daylight-saving tests.
- [x] Corporate-action adjustment tests.
- [x] Provider fallback and mixed-source diagnostics tests.
- [x] Contract tests proving decisions can be reproduced from a normalized snapshot.

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

- **June 7, 2026:** Standardized REST quotes and bars as `market-data.v1` and preserved legacy compatibility fields while consumers migrate.
- **June 7, 2026:** Selected fresh trade, then fresh NBBO midpoint, then fresh minute close for valuation; a daily close is only a stale fallback and is never labeled live.
- **June 7, 2026:** Set default staleness limits to 15 seconds in the regular session, 60 seconds in extended sessions, and 24 hours while closed.
- **June 7, 2026:** Use split-adjusted Massive aggregates by default and do not apply split factors twice; dividend adjustment remains explicit and disabled for Massive price-history features.
- **June 7, 2026:** Confirmed historical per-second stock aggregates are not a Massive REST product. Real-time seconds move to Page 4 WebSockets; historical reconstruction would require raw trades.
- **June 7, 2026:** Recorded the individual/non-professional license as personal/internal-use only and made commercial or multi-user deployment contingent on written provider approval.
- **June 7, 2026:** Detailed contracts, endpoint paths, cache policy, fallback rules, and licensing assumptions in [`docs/massive_rest_contract.md`](../massive_rest_contract.md).
