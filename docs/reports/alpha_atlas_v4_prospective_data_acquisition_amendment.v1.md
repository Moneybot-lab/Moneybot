# Alpha Atlas V4 prospective data-acquisition amendment v1

**Status: `PREPARED_FOR_REVIEW_NOT_AUTHORIZED`. Recommendation: a separate,
bounded Massive REST pre-assembly process preserves immutable raw responses and
receipts on a single-writer Render persistent disk; the existing assembler stays
cache-only.** This appears technically feasible, but is not supported within
currently evidenced resources. It requires account entitlement/cost confirmation,
a reviewed stock/sector manifest, and a configured 180-day primary-plus-backup
store.

The proposed finite allowance is **743 expected / 2,271 maximum provider request
attempts** over bootstrap plus ten sessions. Expected retained storage including
one backup is **30.53 MiB**; the fail-closed maximum is **501.75 MiB**. Therefore
this amendment explicitly proposes a **512 MiB cap**, replacing—not silently
reinterpreting—the original 250 MiB/zero-request limits if approved. Provider,
storage, and egress cost are **UNKNOWN** until the actual Massive plan and Render
quote are checked. No request, collection, purchase, deployment, or infrastructure
change is authorized by this document.

## One concrete architecture and schedule

1. After manifest approval, a separate acquisition command fetches 60 eligible
   sessions of adjusted daily data for at most 50 stocks plus shared SPY and at
   most 11 sector ETFs (62 unique daily symbols). It also fetches dated ticker
   details for the 50 stocks and a globally date-bounded split feed filtered to
   those 62 symbols.
2. It writes raw bytes first, then an immutable receipt, under proposed
   `/var/data/moneybot/alpha_atlas_v4/acquisition/v1`. Objects are append-only;
   retry, failure, pagination, and correction records count against budgets.
3. On each pilot session, it runs separately during 06:30–07:30 New York and
   fetches the prior completed daily bar for 62 symbols plus incremental splits.
   It must finish before 07:45 universe binding. Acquisition failure cannot move
   07:45, the inclusive 08:40 readiness deadline, or the frozen 08:45 boundary.
4. The snapshot assembler reads only the completed immutable store through a
   no-fallback interface. It never invokes acquisition or provider clients.
5. The recommended disk does not exist today. Render's documented daily disk
   snapshots remain available for at least seven days, so a separately verified
   backup copy and 180-day lifecycle are required.

## Reusable ingestion review

| Existing job/path | Request/scope/frequency | Execution and present retention | Smallest prospective preservation change | Before deadline? |
|---|---|---|---|---|
| `.github/workflows/track-b-offline.yml`; `scripts/ingest_massive_flatfiles.py:ingest_massive_flatfiles`; `build_massive_decision_training_rows.py:load_market_history` | Scheduled 06:15 UTC `day_aggs_v1` prefix; builder later selects observed symbols, SPY and sector ETFs over 120 calendar days | Configured and prior hosted Track B evidence exists. Raw files and an ingest manifest are runner-local; builder hashes objects, but this is not durable pilot storage | Before runner cleanup, preserve only approved 62-symbol rows with original object hash and a true transfer receipt. This preserves an existing scheduled response; it is not another market request | **UNKNOWN**: source publication and transfer completion by 07:30 New York are not evidenced |
| `.github/workflows/track-b-offline.yml`; `scripts/fetch_massive_splits.py:fetch_pages/materialize` | Global execution-date query, 1,000/page, filtered to decision symbols plus SPY/11 ETFs; scheduled with Track B | Hosted use is evidenced historically. Filtered normalized rows retain retrieval time, page count and hash, but not immutable page bytes, per-page receipts, or retry bodies | Extract a separate bounded stage and persist every sanitized page/attempt before normalization | **UNKNOWN**; current workflow continues into unauthorized training and cannot be reused wholesale |
| `MarketDataService.get_price_history_data`; `MassiveRestClient.get_aggregates` | On-demand per-symbol adjusted daily aggregates, usually 30 bars; 300-second process cache | Implemented, not scheduled for 62 symbols. Normalization omits raw response and receipt; misses can fall back to yfinance | Separate caller requests 60 sessions, forbids fallback, and preserves raw response/receipt before normalization | **No current evidence** |
| `scripts/run_market_stream.py`; `MassiveWebSocketWorker` | Continuous latest A/AM/Q/T for demanded symbols; default server symbols SPY/QQQ | Render configuration enables the worker, but current connection is unobserved. Redis retains only latest state with TTL/eviction | Do not use for daily bootstrap; retaining stream messages still would not supply completed daily history | **Insufficient fields/lookback** |

No current response is proven complete and durable enough for direct reuse. Track B
flat-file and split responses are category **B: existing responses that could be
preserved prospectively** if timing, licensing, and transfer are confirmed.
Category **C** is the bounded bootstrap below. Category **D** is the recurring
allowance. If verified flat-file responses replace REST daily retrieval, the REST
requests are not also issued or counted as “zero incremental”; one source path is
selected before authorization.

## Minimum feature-to-source dependencies

| Family and computation | Minimum source requirement | Timing, adjustment, and provenance |
|---|---|---|
| Symbol daily: SMA50, returns, RSI, ATR, MACD, volume/VWAP, slopes and volatility | **50 valid completed-session OHLCV+VWAP bars** per stock. Twenty returns require 21 bars; MACD's registered window is 35 and VWAP slope is 29. Request 60 eligible sessions to provide a ten-session missing-bar/warm-up margin | `adjusted=true`, with separately retained split lineage. Latest source is at most three XNYS sessions stale. Retain raw response, request ID, bar dates, actual receipt, hash, bytes and page completion |
| SPY context: 1d/5d returns, symbol-relative return, 20-return beta, SMA20 regime, 20-return volatility | **21 valid bars**; reuse one shared 60-session SPY object | Same adjustment/freshness/provenance; never retrieve SPY per stock |
| Sector context: stock minus sector five-return value | **6 valid bars** per sector ETF; reuse the shared 60-session objects for at most 11 ETFs | Requires a separately approved effective-dated stock-to-sector mapping. A mapping observed today is not historical proof |
| Identity and split lineage | Dated ticker details for 50 stocks; split ID, ticker, execution date, type, ratios and historical factor when supplied for all 62 symbols | Details are true only as of their query date. Split pages begin one session before the history window. Unknown identity/mapping fails closed; continuity is never inferred |

The date window is calculated using the repository XNYS calendar: from the 60th
previous eligible session through the immediately previous completed session.
It is not “about 90 calendar days.” Missing observations do not reduce registered
lookbacks or silently drop features.

## Bounded bootstrap and recurring plan

* Aggregates: `GET /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}` with
  `adjusted=true`, ascending order and limit 50,000; at most one page/symbol.
* Identity: dated `GET /v3/reference/tickers/{ticker}`; one page/stock.
* Splits: `GET /stocks/v1/splits`, globally date-bounded, sorted ascending, limit
  5,000; at most five bootstrap pages and two recurring pages.
* Deduplication key: provider + sanitized parameters + response SHA-256. SPY and
  sector objects are shared. Concurrency is one, each page gets at most three
  total attempts, bootstrap/recurring acquisition gets 45 minutes, and raw page
  caps are 256 KiB aggregates/splits and 32 KiB identity.
* Partial response, missing bar, HTTP entitlement error, page-cap exhaustion,
  checksum/storage failure, or runtime exhaustion stops acquisition. All attempts
  remain evidence; the later assembler emits MISSING/UNKNOWN and makes no fallback
  request.

Newly retrieved historical data receives its actual future receipt time. A bar's
source date is never used as receipt evidence, and retrieval does not prove that
the data was available at an earlier decision.

## Request budget

| Stage | Expected | Maximum including pagination and two retries | Formula |
|---|---:|---:|---|
| Bootstrap | 113 | 351 | Expected: 62 aggregate + 50 identity + 1 split page. Maximum: `(62 + 50 + 5 pages) × 3 attempts` |
| Each session | 63 | 192 | Expected: 62 aggregate + 1 split page. Maximum: `(62 + 2 pages) × 3 attempts` |
| Ten sessions | 630 | 1,920 | Per-session values × 10 |
| **Total** | **743** | **2,271** | Bootstrap + ten sessions |

The **2,271-attempt cap** is prepared for review and would replace the original
zero-incremental-request rule only if explicitly approved. Public plan material
is not proof that the configured account supports these endpoints, history depth,
rate, retention, or maximum attempts.

## Storage and 180-day budget

Expected values are planning estimates, not measurements. Maximum values are
uncompressed hard response/record caps and include failed attempts. Retention is
a duration, not a new daily copy.

| Stored class | Expected | Maximum |
|---|---:|---:|
| Bootstrap raw responses + receipts | 1.97 MiB | 55.62 MiB |
| Recurring acquisition, each session | 0.20 MiB | 13.50 MiB |
| Recurring acquisition, ten sessions | 1.98 MiB | 135.00 MiB |
| Snapshots/manifests/mappings, ten sessions | 10.31 MiB | 41.25 MiB |
| Corrections/failed-attempt reserve | 0 | 15.00 MiB |
| Operational manifests/checksums | 1.00 MiB | 4.00 MiB |
| **Primary** | **15.26 MiB** | **250.87 MiB** |
| **Primary + one verified backup through 180 days** | **30.53 MiB** | **501.75 MiB** |

The maximum primary alone exceeds 250 MiB, so the original cap is insufficient.
The proposed **512 MiB cap includes primary plus one backup copy**. It is not an
authorization to provision storage.

## Receipt and revision contract

Each receipt records provider, endpoint, credential-free parameters, actual UTC
request/response times, source-event dates, HTTP status, provider request ID,
response SHA-256/bytes, page ordinal, `next_url` presence, pagination completion,
adjustment mode, action-lineage IDs, attempt ID, and optional superseded object.
Raw received bytes are immutable. Corrected bars, new split factors, or provider
revisions append new objects and references; they never rewrite a frozen source
or snapshot. Sector and identity observations are explicitly “observed as of” and
are not projected backward.

## Existing storage evaluation

| Option | Evidence and access | Durability/expiry/backup | Decision |
|---|---|---|---|
| Render Key Value `moneybot-market-state` | Exists; private `REDIS_URL`; exact quota and persistence mode unknown | `allkeys-lru`, mutable keys and 120-second stream TTL; restore evidence absent | Reject as evidence store |
| Render persistent disk | **Does not exist**; would be accessible to one service at runtime | Atomic filesystem layout fits; encrypted at rest; automatic daily snapshots are documented for at least seven days, not 180 | Recommended conditional path plus separate 180-day backup |
| Actions runner/artifacts | Exists only in workflow context | Runner ephemeral; artifact URL/retention is not application durability or restore proof | Reject |
| `DATABASE_URL` | Credential is configured for runtime; provider/quota unknown | No immutable acquisition schema, lifecycle, blob quota or restore evidence | Unsupported absent account evidence |

## Cost and official documentation

Incremental provider, disk, backup, and egress cost are **UNKNOWN**. Massive's
public pricing lists plan-level rate/history features, not the configured account's
entitlement, remaining quota, overages, or retention rights. Render pricing/account
configuration is likewise not available here. Before approval, an operator must
confirm the Massive plan can cover the endpoints, 60-session depth and 2,271
attempt ceiling without purchase, and obtain the actual storage/backup quote.

Official pages reviewed 2026-09-29:

* [Massive custom bars](https://massive.com/docs/rest/stocks/aggregates/custom-bars): adjustment flag, 50,000 maximum limit, result fields, request ID and `next_url`.
* [Massive splits](https://massive.com/docs/rest/stocks/corporate-actions/splits): 5,000 maximum, pagination, split identity/ratios/factor.
* [Massive ticker overview](https://massive.com/docs/rest/stocks/tickers/ticker-overview): dated details interface.
* [Massive pricing](https://massive.com/pricing): public plan rate/history differences only.
* [Render disks](https://render.com/docs/disks): ephemeral default, runtime-only single-service disk, encryption, snapshots and restore limitations.
* [Render Key Value](https://render.com/docs/key-value): eviction and persistence modes.

## Universe and exact next decision

QQQ/SPY remains `PROPOSED_NOT_APPROVED` and can test plumbing only; selecting both
is the entire eligible baseline and cannot test top-five stock selection. The
repository contains no reviewed, static, outcome-independent source for up to 50
common stocks. Watchlists and decision logs are dynamic/user/observation driven;
examples are not authorization; no suitable listing snapshot is saved.

The missing input is one user-approved, effective-dated common-stock source
snapshot with typed identifiers and subscription authorization. Once supplied,
the deterministic proposed rule is: retain eligible US common stocks, sort by
permanent typed identifier then ticker, and take the first 50—without scores,
returns, request success, or later eligibility. This rule and any resulting roster
remain separately reviewable and do not replace the current manifest silently.

Exact implementation points after approval are a new standalone acquisition CLI,
a raw-response seam beside `MassiveRestClient` normalization, the existing
`ExchangeCalendar` and sector ETF map, an append-only acquisition sibling of the
snapshot root, and an explicit no-fallback reader. No production job is modified
by this amendment.

**Approval required:** approve or reject (1) a supplied stock/sector/identity
manifest, (2) the 2,271-attempt finite request cap, (3) the 512 MiB primary-plus-
backup cap/path after account quotes and retention proof, and (4) a later separate
implementation/execution authorization. Collection stays disabled. The current
readiness result remains `COMPLETE — BLOCKED_STORAGE_AND_CACHE`; all completed
historical, timing, identity, regression, and scoring findings remain closed.
