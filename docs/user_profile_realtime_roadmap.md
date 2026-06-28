# User Profile and Real-Time Market Data Roadmap

> **Progress tracking:** Use the [User Profile + Real-Time Setup pages](user-profile-realtime/README.md) for the current status, remaining checklists, and implementation order. This document remains the architectural reference.

## Executive decision

**Build the investor profile first, then add a bounded server-side Massive WebSocket pilot.**

A WebSocket will improve freshness and reduce repeated quote polling, but real-time prices alone will not make Moneybot's recommendations more accurate. The largest immediate accuracy gap is that the current user profile stores identity information, while the advice engines mostly apply the same thresholds to every user. Moneybot should first define what a suitable recommendation means for each user, pass that profile into every decision, and measure personalized outcomes. The streaming layer can then make those profile-aware decisions timely.

The initial stream should subscribe only to symbols that active users currently own, watch, or analyze. Do **not** begin with `T.*`, `Q.*`, or aggregate wildcards for the entire U.S. market. Massive says a full U.S. stock stream averages roughly 2,000 trade messages and 8,000 quote messages per second, with messages potentially containing multiple events. That throughput is unnecessary for Moneybot's first real-time feature and would add cost, backpressure, and reliability risk.

## What the upgraded Massive plan unlocks

Based on the plan screenshot and Massive's official documentation, the upgraded stock plan provides the inputs needed for a materially stronger data layer:

- Real-time stock data rather than 15-minute-delayed data.
- WebSocket trades, NBBO quotes, per-second aggregates, and per-minute aggregates.
- Snapshot trades and quotes for synchronization and recovery.
- More than 20 years of historical data for research and backtesting.
- Corporate actions, reference data, technical indicators, flat files, and financial ratios.

Official references:

- [Stocks WebSocket overview](https://massive.com/docs/websocket/stocks/overview)
- [Stocks REST API overview](https://massive.com/docs/stocks/getting-started)
- [Stocks flat-files overview](https://massive.com/docs/flat-files/stocks/overview)
- [WebSocket throughput guidance](https://massive.com/knowledge-base/article/how-much-data-is-streamed-through-massives-websockets)

Before displaying raw Massive data to anyone other than the licensed individual, confirm the plan's current market-data display and redistribution terms. The screenshot labels the plan for individual, non-professional use; this roadmap therefore assumes a private/personal Moneybot deployment until licensing is verified.

## Current-state findings

### User profile

Moneybot currently stores:

- Name, username, email, password hash, and profile image.
- Portfolio/watchlist positions with symbol, entry price, and share count.
- Sold trades.
- Push-notification and trigger preferences.

The profile does **not** yet store investment suitability and personalization inputs such as goals, time horizon, risk tolerance, liquidity needs, experience, loss capacity, tax-account context, sector restrictions, or preferred recommendation cadence.

### Advice and learning loop

Moneybot already has valuable foundations:

- Deterministic quick and portfolio decision paths.
- Decision logging, outcome materialization, calibration, candidate-model comparison, and promotion scripts.
- Portfolio P&L context and notification triggers.

However, user-profile fields are not part of the model feature contract or decision snapshot. Static portfolio thresholds can therefore produce an action that is technically consistent but unsuitable for a particular user.

### Market data

Moneybot currently:

- Calls Massive's single-ticker snapshot REST endpoint first.
- Falls back to Finnhub, Twelve Data, and yfinance.
- Caches quotes and signals in process for 20 seconds.
- Runs a single Flask web service on Render.

This is good fallback behavior, but a single-process in-memory cache cannot act as a shared real-time source of truth if the application later runs multiple workers or a separate streaming process.

## Target architecture

```text
Massive WebSocket
       |
       v
market-stream worker  ---- health/lag metrics
       |
       +---- normalized latest quote/bar state ---- Redis
       |
       +---- optional durable bars/events -------- PostgreSQL/object storage
                                                   |
Flask API <----- profile + portfolio --------------+
   |
   +---- personalized decision service
   |       - suitability policy
   |       - deterministic/model score
   |       - confidence and data-quality gates
   |       - explanation
   |
   +---- browser updates via SSE first; WebSocket later if bidirectional features emerge
```

### Why this shape

1. **Keep the Massive API key on the server.** The browser must never connect directly to Massive with the private key.
2. **Separate ingestion from request handling.** A long-lived provider connection should not live inside a Flask request or be duplicated unpredictably by web workers.
3. **Use Redis for hot state and coordination.** Store the latest quote/bar, timestamps, subscription demand, and pub/sub events in one shared place.
4. **Persist only what adds learning value.** Keep all profile/decision/outcome records, but downsample market events into bars unless raw trades or quotes are explicitly needed for a tested feature.
5. **Use SSE for the first browser-facing stream.** Moneybot initially needs server-to-browser updates, not bidirectional messaging. SSE is simpler to authenticate, reconnect, and operate behind common proxies. The provider-side connection should still be a WebSocket.

## Profile v1: the first implementation milestone

### Data model

Add a one-to-one `InvestorProfile` model instead of continuing to add columns to `User`.

Recommended fields:

| Field | Type | Purpose |
| --- | --- | --- |
| `user_id` | FK, unique | One profile per user |
| `profile_version` | integer | Supports future questionnaires and model contracts |
| `primary_goal` | enum | Growth, income, preservation, speculation, learning |
| `time_horizon_years` | integer | Distinguishes short- and long-horizon advice |
| `risk_tolerance` | enum | Conservative, moderate, aggressive |
| `loss_capacity_percent` | decimal | Maximum tolerable drawdown, separate from willingness |
| `liquidity_need` | enum | Low, medium, high |
| `experience_level` | enum | Beginner, intermediate, advanced |
| `account_type` | enum | Taxable, IRA, Roth IRA, paper/education, other |
| `position_size_limit_percent` | decimal | Personalized concentration guardrail |
| `sector_limit_percent` | decimal | Portfolio concentration guardrail |
| `excluded_sectors_csv` | text initially | User restrictions; normalize later if needed |
| `penny_stocks_allowed` | boolean | Suitability gate for low-priced momentum names |
| `after_hours_alerts` | boolean | Controls alert timing |
| `recommendation_style` | enum | Conservative, balanced, opportunity-seeking presentation |
| `questionnaire_completed_at` | datetime | Distinguishes defaults from informed settings |
| `created_at`, `updated_at` | datetime | Auditing and cache invalidation |

Also add an append-only `InvestorProfileRevision` record containing the old/new normalized profile and a reason/source. Recommendations must record the `profile_version` used so historical decisions remain reproducible.

### API contract

Add authenticated endpoints:

- `GET /api/me/investor-profile`
- `PUT /api/me/investor-profile`
- `GET /api/me/investor-profile/revisions`
- `POST /api/me/investor-profile/questionnaire-score` if questionnaire scoring is kept server-side

Requirements:

- Use strict allowlists for enum values and numeric bounds.
- Return `profile_complete`, `missing_fields`, and `profile_version`.
- Use optimistic concurrency (`If-Match` or submitted version) to prevent silent overwrites.
- Never expose profile data in public endpoints, logs, analytics payloads, or model prompts unless needed and redacted.

### User experience

Extend Account Settings with a short, progressive questionnaire:

1. Goal and time horizon.
2. Risk willingness and ability to absorb loss.
3. Experience, liquidity, and account context.
4. Concentration and security restrictions.
5. Review screen explaining exactly how answers affect Moneybot.

Use safe defaults until completion. A missing profile must never silently become “aggressive.” Show the active profile next to recommendations with an explanation such as: “Balanced, 7-year horizon; 12% position cap applied.”

### Personalization policy

Do not immediately retrain the prediction model on profile fields. First separate two concerns:

1. **Market forecast:** What is the estimated probability and uncertainty of a market outcome?
2. **Suitability policy:** Given that forecast, the user's holdings and profile, what action is appropriate?

Examples:

- Block or strongly downgrade penny-stock buys when `penny_stocks_allowed` is false.
- Require higher confidence for conservative users.
- Reduce buy strength when a new position would breach position or sector caps.
- Prefer HOLD/trim over BUY when liquidity need is high and the horizon is short.
- Adjust alert urgency and cadence without changing the underlying market forecast.
- Avoid tax-sensitive sell language unless cost-basis and account-type context are sufficient.

Every personalized response should include:

- Base market score and forecast horizon.
- Personalized action.
- Profile rules that changed or constrained the action.
- Data freshness and source.
- Confidence/coverage status.
- Risk notes and the next invalidation checks.

## Real-time market-data implementation

### Phase 1: improve REST correctness before streaming

Create a provider boundary (`MarketDataProvider`) and a normalized market event schema. Move Massive-specific parsing out of the broad `MarketDataService` class.

Normalized quote fields should include:

- Symbol, bid, ask, bid size, ask size, midpoint.
- Last eligible trade price and size.
- Event timestamp, received timestamp, and calculated age.
- Session (`pre`, `regular`, `after`, `closed`).
- Source, source mode (`rest`, `websocket`, `fallback`), and quality flags.
- Sequence number where supplied.

Correctness changes:

- Prefer tradable midpoint or last eligible trade according to the use case; do not label a daily close as a real-time price.
- Expose `as_of`, `age_ms`, `market_session`, `is_stale`, and `quality_flags` on every quote.
- Define staleness thresholds by session and endpoint.
- Add Massive aggregate/history clients so technical indicators use a consistent, licensed source instead of mixing live Massive prices with yfinance history.
- Keep fallbacks, but make source mixing explicit in decision logs.

### Phase 2: bounded provider WebSocket pilot

Run a dedicated `market-stream` worker with one Massive stock WebSocket connection.

Initial subscriptions:

- Per-second aggregates for active symbols (`A.SYMBOL`) for live UI prices and intraday indicators.
- Quotes (`Q.SYMBOL`) only where spread, liquidity, or executable-price quality affects a feature.
- Trades (`T.SYMBOL`) only for features that need tick-level information.
- Per-minute aggregates (`AM.SYMBOL`) for lower-frequency decision refresh and persistence.

Start with a strict symbol budget, for example 100–500 active symbols, driven by:

1. Open portfolio pages.
2. User portfolio holdings.
3. User watchlists/ClearView symbols.
4. Current Quick Ask symbol.
5. A small server-owned scanner universe.

Subscription manager requirements:

- Reference-count symbols requested by active users/features.
- Debounce subscribe/unsubscribe churn.
- Reconcile desired and actual subscriptions after reconnect.
- Authenticate, reconnect with exponential backoff and jitter, and refresh snapshots after gaps.
- Detect stale streams, sequence gaps, malformed events, clock skew, and slow-consumer/backpressure conditions.
- Coalesce high-rate events before Redis/browser publication.
- Publish connection state and last-event age.

### Phase 3: application updates

Add an API quote resolver that reads in this order:

1. Fresh Redis stream state.
2. Massive REST snapshot to synchronize or fill a gap.
3. Existing fallback chain.

Add a user-facing SSE endpoint such as `GET /api/market-stream?symbols=AAPL,MSFT`:

- Require authentication.
- Authorize and cap symbol counts.
- Send coalesced updates (for example, at most 1–2 per second per visible symbol).
- Include heartbeat events and resumable event IDs.
- Fall back to periodic REST refresh when streaming is degraded.

Do not recalculate a full AI narrative on every tick. Recompute inexpensive price/P&L fields continuously, technical features on completed bars, and recommendation state only when a defined trigger occurs, such as:

- A second/minute bar closes.
- Price crosses a user-specific threshold.
- Spread or liquidity quality materially changes.
- A recommendation boundary is crossed and remains crossed for a debounce period.
- New corporate-action/fundamental/news data invalidates the prior snapshot.

### Phase 4: historical accuracy and model research

Use the 20+ year entitlement deliberately:

- Build adjusted daily bars with splits and dividends handled consistently.
- Use point-in-time fundamentals where available; never let future filings leak into past examples.
- Use walk-forward validation and time-based train/validation/test splits.
- Include delisted/failed companies where the dataset supports it to reduce survivorship bias.
- Separate market regimes and report performance by volatility, trend, sector, liquidity, and market cap.
- Add transaction-cost, spread, slippage, and latency assumptions.
- Benchmark personalized policy outcomes separately from forecast accuracy.

Flat files are better than repeated REST calls for broad historical research. Keep a manifest with dataset date, adjustment method, schema version, and checksums so every model artifact is reproducible.

## Delivery sequence

### Milestone 0 — baselines and contracts (1–2 days)

- Record current quote freshness, provider fallback rate, API latency, recommendation churn, calibration, and outcome coverage.
- Define the normalized quote/event schema and profile enums.
- Confirm Massive plan licensing for the intended audience and display model.
- Add feature flags: `INVESTOR_PROFILE_ENABLED`, `MASSIVE_STREAM_ENABLED`, and `MARKET_SSE_ENABLED`.

**Exit criteria:** baselines are saved, contracts are reviewed, and features can be disabled without deployment rollback.

### Milestone 1 — investor profile foundation (3–5 days)

- Add Alembic migrations, `InvestorProfile`, and revision history.
- Add profile API validation and tests.
- Add the Settings questionnaire and completion indicator.
- Add a normalized `UserDecisionContext` object consumed by decision services.

**Exit criteria:** users can create/update a versioned profile; incomplete profiles receive safe defaults; no existing account or portfolio flow breaks.

### Milestone 2 — personalized suitability policy (3–5 days)

- Add a pure, testable policy layer after the base market forecast.
- Apply concentration, risk, horizon, liquidity, experience, and security-type rules.
- Include profile version and applied rules in decision snapshots/logs.
- Add explanation copy and profile-aware notification severity.

**Exit criteria:** deterministic tests prove that identical market inputs can appropriately yield different actions for different profiles, with a traceable explanation.

### Milestone 3 — Massive provider refactor and REST enrichment (3–5 days)

- Add Massive provider/client modules and normalized schemas.
- Add aggregates, trades/quotes snapshots, reference, corporate-action, and ratios clients as needed.
- Add timestamps, freshness, session, and quality fields.
- Replace mixed-source indicator inputs for pilot symbols.

**Exit criteria:** decisions are reproducible from a recorded normalized snapshot, stale data cannot be shown as live, and fallback/source mixing is visible.

### Milestone 4 — server-side WebSocket pilot (4–7 days)

- Add Redis and a separately deployed `market-stream` worker.
- Implement bounded subscriptions, reconnect/recovery, coalescing, metrics, and integration tests with recorded fixtures.
- Shadow the stream without changing recommendations; compare stream state against REST snapshots.

**Exit criteria:** at least 99.9% of pilot events are parsed, p95 ingest-to-cache lag is under two seconds during market hours, reconnect recovery is automatic, and quote discrepancies stay within defined tolerances.

### Milestone 5 — live UI and trigger engine (3–5 days)

- Add authenticated SSE to portfolio and Quick Ask.
- Show source, session, timestamp, and stale/degraded status.
- Trigger inexpensive P&L updates live and recommendation recomputation only at controlled boundaries.
- Deduplicate and cool down notifications.

**Exit criteria:** the UI degrades cleanly to REST, browser reconnects do not leak subscriptions, and notification volume stays within explicit limits.

### Milestone 6 — historical research and controlled rollout (ongoing)

- Materialize adjusted historical datasets and point-in-time features.
- Run walk-forward tests and shadow personalized policies.
- Roll out by user cohort and compare calibration, usefulness, churn, latency, and alert engagement.

**Exit criteria:** promotion requires better out-of-sample metrics without unacceptable drawdown, churn, stale-data incidents, or subgroup regressions.

## Metrics that define “more accurate and efficient”

### Accuracy and usefulness

- Brier score and calibration error by forecast horizon.
- Precision/recall for BUY and SELL boundary events.
- Realized return and max adverse excursion after recommendations.
- Performance net of estimated spread, slippage, and fees.
- Profile-rule override rate and subsequent outcomes.
- Recommendation stability/churn per symbol per day.
- User dismissal, follow-up, and alert-action rates (never treat engagement alone as investment success).

### Data quality

- Quote age p50/p95/p99.
- WebSocket ingest-to-Redis lag.
- Sequence-gap and reconnect counts.
- Stream-versus-snapshot discrepancy rate.
- Fallback and mixed-source rates.
- Percentage of decisions with complete timestamps, sources, and profile versions.

### Efficiency and reliability

- Provider calls avoided by stream/cache hits.
- Messages ingested versus messages published to clients.
- CPU and memory per 100 subscribed symbols.
- API latency and error rate.
- Notification deduplication ratio.
- Recovery time after provider, Redis, or worker interruption.

## Testing strategy

- Unit tests for profile validation, defaults, revisions, and suitability rules.
- Property/boundary tests for risk and concentration thresholds.
- Contract tests for every Massive event type using recorded fixtures.
- Reconnect, duplicate, out-of-order, malformed, stale, and sequence-gap tests.
- Integration tests for Redis latest-state and subscription reconciliation.
- SSE authentication, authorization, symbol-cap, heartbeat, and reconnect tests.
- Timezone/session tests around daylight-saving transitions, market open/close, weekends, and holidays.
- Shadow-mode comparisons between WebSocket state, Massive REST snapshots, and existing fallbacks.
- Load tests at the intended symbol budget before any wildcard subscription.
- Walk-forward model tests with leakage and survivorship-bias checks.

## Immediate first pull requests

1. **Investor profile schema and API** — models, migration, validation, revision audit, and tests.
2. **Investor profile Settings UI** — questionnaire, completion state, and safe-default explanation.
3. **UserDecisionContext and suitability policy** — pure policy module, decision-log integration, and tests.
4. **Normalized Massive REST client** — source/freshness/session/quality contract and aggregate history.
5. **Redis + Massive stream worker in shadow mode** — bounded symbols, recovery, metrics, no UI behavior change.
6. **Authenticated SSE portfolio updates** — coalesced prices, freshness indicators, and REST degradation.

## Recommendation summary

Yes, a Massive WebSocket is worth implementing now, **as a bounded backend ingestion service after the profile contract is underway**. It should improve timeliness and API efficiency, especially for portfolios, Quick Ask, and alerts. It should not directly drive advice on every tick, should not expose the provider key to browsers, and should not subscribe to the entire market initially. The highest-value sequence is:

1. Define the user and suitability rules.
2. Normalize and timestamp the market data.
3. Stream only demanded symbols into shared hot state.
4. Trigger controlled, explainable decision updates.
5. Use the expanded history to validate changes out of sample before promotion.
