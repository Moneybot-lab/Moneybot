# Massive REST Contract and Operating Rules

**Schema:** `market-data.v1`  
**Status:** Page 3 foundation complete on June 7, 2026

## Provider boundary

`MarketDataProvider` is the server-side interface for normalized quotes and aggregate bars. `MassiveRestClient` owns Massive authentication, endpoint paths, parsing, timestamp normalization, retries, rate-limit handling, negative caching, and provider metrics. `MarketDataService` remains the orchestration layer and keeps Finnhub, Twelve Data, and yfinance behind an explicitly marked `source_mode=fallback` normalization boundary.

Provider credentials stay on the server. Browser code receives normalized values and diagnostics, never the Massive API key.

## Quote and valuation rules

MoneyBot selects one portfolio/display price in this order:

1. Fresh latest qualifying trade.
2. Fresh midpoint of a valid, non-crossed NBBO.
3. Fresh minute aggregate close.
4. Daily aggregate close only as a visibly stale fallback.

Daily close data is never labeled live. Spread and liquidity analysis must use `bid`, `ask`, sizes, and `midpoint`; it must not infer a spread from the selected valuation price.

Every normalized quote includes the event and receipt timestamps, age, session, source mode, staleness, quality flags, and available provider sequence/event identifiers.

## Sessions and staleness

The bundled exchange calendar uses `America/New_York`, observes major NYSE holidays, and handles daylight-saving changes through `zoneinfo`.

Default staleness limits:

| Session | Default limit |
| --- | ---: |
| Regular | 15 seconds |
| Pre-market / after-hours | 60 seconds |
| Closed | 86,400 seconds |

The limits are configurable with `MASSIVE_REGULAR_STALE_SECONDS`, `MASSIVE_EXTENDED_STALE_SECONDS`, and `MASSIVE_CLOSED_STALE_SECONDS`.

## Historical bars and corporate actions

Massive aggregate requests use `adjusted=true` by default, which means bars are split-adjusted. MoneyBot does not apply split factors a second time. The current historical feature path does not dividend-adjust Massive aggregate closes; responses state `dividend_adjusted=false`.

The new `/stocks/v1/splits` and `/stocks/v1/dividends` endpoints are retained for audit and future total-return features. When a future unadjusted dataset needs both factors, apply split adjustments first and dividend adjustments second. yfinance history is an explicit fallback and is labeled as a mixed source because its auto-adjust behavior includes corporate actions.

Massive does not provide historical per-second stock aggregates through REST. Page 3 therefore rejects `timespan=second` explicitly instead of silently manufacturing bars. Real-time second aggregates belong to the Page 4 WebSocket worker; historical second reconstruction would require raw trades and sale-condition logic.

## Cache and backoff policy

Cache keys include the normalized schema, endpoint, path, and sorted request parameters.

- Snapshot/last quote/trade: 2 seconds by default.
- Minute aggregates: 60 seconds.
- Daily and longer aggregates: 1 hour.
- Reference, corporate-action, and ratios data: 24 hours.
- Provider errors: 30-second negative cache by default.
- HTTP 429: honor `Retry-After` and stop new provider calls during backoff.

`MarketDataService` also keeps its existing request-level cache so one API response does not request the same symbol repeatedly.

## Fallback and reproducibility

Fallback quotes use the same normalized field names and are always marked `source_mode=fallback`. If the fallback does not provide a trustworthy event timestamp, it receives `freshness_unknown`, is marked stale, and is not presented as live.

Quick Ask and portfolio decision snapshots include quote/history sources, schema versions, source modes, staleness, and whether sources were mixed. This allows later outcome analysis to reproduce the inputs used by a decision.

## Endpoint coverage

- Single-ticker snapshot: `/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}`
- Latest trade: `/v2/last/trade/{ticker}`
- Latest NBBO: `/v2/last/nbbo/{ticker}`
- Minute and historical bars: `/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}`
- Ticker details: `/v3/reference/tickers/{ticker}`
- Splits: `/stocks/v1/splits`
- Dividends: `/stocks/v1/dividends`
- Ratios: `/stocks/financials/v1/ratios` (client coverage only; not called until a product feature defines its use)

## Licensing deployment gate

The upgraded plan shown for this project is an **individual, non-professional** plan. MoneyBot currently assumes personal/internal use by the subscribing individual. Do not expose raw feeds publicly, redistribute data, enable unrelated users, or commercialize the data display based on this assumption. Before multi-user production or commercial deployment, obtain written confirmation from Massive that the intended audience, derived displays, storage, alerts, and redistribution model are permitted, and upgrade to an appropriate business/data license if required.

This is an engineering deployment gate, not legal advice.
