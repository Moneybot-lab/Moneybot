# Alpha Atlas V4 Massive historical inventory and entitlement audit

**Schema:** `alpha-atlas-v4-massive-historical-inventory.v1`  
**Audit date:** 2026-08-27 UTC  
**Starting commit:** `35e88ac7d4ba82ee307c8873706536276adb8f74`  
**Scope:** read-only inventory; no backfill, model training, promotion, or routing

The deterministic companion report is
[`alpha_atlas_v4_massive_historical_inventory.json`](alpha_atlas_v4_massive_historical_inventory.json).
Every material claim uses the evidence taxonomy requested by the audit.

## Executive finding

- **`VERIFIED_REPOSITORY`:** this checkout does not contain a Massive historical price
  corpus beginning in 2022. It contains no downloaded Massive flat-file objects or ingest
  manifest. The only `2022` source literal enables the Juneteenth exchange holiday from
  2022 onward; it is not a data start date.
- **`VERIFIED_REPOSITORY`:** flat-file ingestion synchronizes the configured prefix into
  an immutable ingest-date directory without a start-year filter. The V4 builder then
  deliberately narrows discovery to symbols in the decision log and dates from the
  earliest decision minus 70 calendar days through the label horizon. That explains the
  short 2026 Track B build, not a provider entitlement cutoff.
- **`UNVERIFIED`:** any asserted repository/runtime history beginning in 2022 is external
  to this checkout. It may reflect a persistent disk, old artifact, first manual ingest,
  or provider object selection, but no local manifest proves which.
- **`UNVERIFIED`:** exact Massive plan, dataset-specific earliest dates, flat-file depth,
  rate limits, retention rights, internal-training rights, derived-data rights, inactive
  ticker coverage, and delisted support. No credentials are configured and official
  documentation access was blocked in this environment.

The user-reported hosted Track B run is kept separate: 37,051 raw requests, 31,861
canonical observations, and an approximate 2026-04-03–2026-08-19 observation span are
recorded as `UNVERIFIED` because the hosted artifacts are not mounted here.

## Repository historical-data inventory

| Dataset | Evidence | Location/pattern | Dates/rows/bytes | Adjustment and PIT status | Consumer/status |
|---|---|---|---|---|---|
| Decision-log archive | `VERIFIED_REPOSITORY` | `data/decision-log-archive/*.jsonl` | one file, 88 rows, 17,729 bytes; filename date 2026-03-14 | Request events, not market prices; no corporate-action lineage | Manual research archive |
| Outcomes snapshot | `VERIFIED_REPOSITORY` | `data/decision_outcomes_snapshot.json` | one file, 7,902 bytes; date extent not established by bounded metadata | Legacy/mixed adjustment and PIT status unverified | Legacy outcome support |
| Massive flat-file ingest | `VERIFIED_REPOSITORY` configuration; data `UNVERIFIED` | `data/raw/massive_flatfiles/<ingest-date>/us_stocks_sip/day_aggs_v1/**` | zero objects/manifests in checkout | Flat adjustment semantics unverified; separate split cache mandatory | V4 runtime research input |
| Track B run-scoped data | `UNVERIFIED` hosted summary | `data/track_b/runs/<run>-<attempt>/**` in GitHub artifacts | user-reported 31,861 canonical rows, 2026-04-03–2026-08-19; bytes unavailable | User-reported event-time split adjustment and 145 split events | Research-only; not repository history |
| Tracked model/calibration JSON | `VERIFIED_REPOSITORY` | `data/day*.json`, baseline/history files | tracked `data/` totals about 35 KB | Derived artifacts, not reusable market history | Production/legacy support |

Known discovery constraints are repository policies rather than verified provider limits:
120-minute Track B job timeout, 600-second builder timeout, 50,000 decision-event limit,
decision-log symbol universe, and a 70-calendar-day feature-history buffer.

## Account entitlement matrix

No account-specific entitlement was verified. A successful result for one family must not
be generalized to another. All rows below are currently `UNVERIFIED` for entitlement,
earliest/latest access, rate/data-delay limits, retention, and delisted support.

| Dataset family | REST | Flat file | Earliest confirmed | PIT/inactive support | Remaining blocker |
|---|---|---|---|---|---|
| Daily stock aggregates | Unverified | Configured prefix, access unverified | — | Delisted/universe support unverified | Execute bounded account probe and inspect listing metadata |
| Minute/other intraday aggregates | Unverified | Unverified | — | Unverified | Dataset-specific plan/depth and object metadata |
| Trades | Unverified | Unverified | — | Security identity unverified | Entitlement, size, retention |
| Quotes | Unverified | Unverified | — | Security identity unverified | Entitlement, size, retention |
| Ticker details/history/events | Unverified | Unverified | — | Critical PIT fields unverified | Stable issuer identity and effective/available timestamps |
| Inactive/delisted reference | Unverified | Unverified | — | Critical survivorship blocker | Account-specific inactive and delisted probe |
| Splits/reverse splits/stock dividends | Repository client exists; account unverified | Cache is REST-derived | — | Availability retained when supplied | Depth, publication timestamps, retention |
| Cash dividends/spinoffs/M&A/cash-outs | Unverified | Unverified | — | Unsupported in V4 normalization | Entitlement plus outcome/terminal-value contract |
| IPO/listing dates, holidays, exchanges | Unverified provider access | Unverified | — | Local XNYS rule calendar is not provider entitlement evidence | Reference parity and official history |
| Financials/SEC/news | Unverified | Unverified | — | Not current V4 requirements | Entitlement, licensing, PIT availability |
| WebSocket | Repository client exists; historical depth not applicable | n/a | — | Live transport only | Account entitlement unverified |

## Safe bounded probe

The new command is dry-run by default and has no backfill/download mode:

```bash
python scripts/audit_massive_historical_entitlement.py \
  --start-year 2004 --max-requests 12 --max-bytes 1048576 \
  --timeout-seconds 15 --max-retries 1
```

Execution requires adding `--execute`; credentials are read only from
`MASSIVE_API_KEY`/`POLYGON_API_KEY`. Hard ceilings are 24 requests and 2 MiB. Planned
families are daily aggregates, minute aggregates, splits, and ticker reference, with
bounded dates 2004, 2006, 2010, 2015, 2020, 2022, and 2026 where caps allow.

**Actual result:** dry-run only; zero requests and zero bytes. All Massive credential
environment variables were unset. Official documentation retrieval also failed due to
network controls, so documentation and account-specific conclusions remain
`UNVERIFIED`. The command distinguishes access, entitlement denial, authentication,
missing date, empty valid response, rate limiting, timeout, network error, provider error,
and unclassified responses.

## Corporate actions and adjustment audit

### Verified repository behavior

- Flat-file OHLCV is treated as raw/unadjusted by the V4 builder.
- `forward_split`, `reverse_split`, and ratio-bearing `stock_dividend` are the only
  normalized action types.
- Feature bars are converted to the share basis effective at the feature as-of date;
  prices are multiplied by the cumulative factor and volume is inversely adjusted.
- Feature-safe actions require point-in-time availability or the conservative existing
  policy; actions during the future label interval may adjust the realized outcome but
  are not input features.
- Entry is the official S0 open and exit is the S4/S9 official close. Realized split
  factors adjust the denominator consistently.
- REST live history explicitly requests split-adjusted aggregates, while flat-file V4
  applies local split normalization. Passing already-adjusted REST bars into this local
  adjustment path would risk double adjustment and is prohibited until source parity is
  proved.
- Cash dividends and total-return labels are absent.

### Unsupported/blocking actions

Cash dividends, spinoffs, ticker changes, mergers, acquisitions, cash buyouts,
delistings, bankruptcy, reorganizations, and warrant/unit conversions have no complete
V4 outcome or terminal-value policy. Missing post-entry terminal prices are rejected,
which is fail-closed but can bias any universe assembled only from surviving/current
symbols.

## Survivorship-bias and reference-data finding

**`VERIFIED_REPOSITORY`:** the current V4 universe begins with symbols present in the
request log, then adds SPY and sector context. The fallback point-in-time identity is
`ticker:event-date`, not a stable issuer identifier reconstructed from historical
reference data. The repository does not reconstruct a complete daily universe of active
and inactive securities, listing/delisting dates, ticker histories, security types, or
terminal corporate events.

A historical V4 universe is therefore **blocked** until the account can supply or another
licensed source can establish: stable issuer/security ID, ticker and effective interval,
exchange, active status, listing type, listing date, delisting date/reason, symbol-change
events with availability, and complete terminal corporate-action history. Current active
tickers must not substitute for that universe. ETFs, ADRs, preferreds, warrants, units,
OTC securities, and ordinary equities require explicit eligibility rules.

## Adjusted versus unadjusted source matrix

| Values | Current policy | Dividend adjusted | Knowledge rule | Main risk |
|---|---|---:|---|---|
| Flat OHLC/VWAP | Raw source, locally split-normalized | No | Feature actions bounded at cutoff | Source semantics unverified; double adjustment if source was already adjusted |
| Flat volume/dollar volume | Volume inversely split-normalized; dollar volume derived | No | Same action basis as prices | Vendor volume semantics and stock-dividend parity |
| REST live OHLCV | `adjusted=true`, reported split-adjusted | No in repository diagnostics | Provider response-time data | REST/flat parity unverified |
| SMA/EMA/MACD/RSI/ATR/volatility/gaps | Derived from cutoff-safe split-basis bars | No | Only permitted bars/actions | Future action leakage if availability is missing or incorrectly supplied |
| Entry/exit and labels | Raw official open/close retained plus split-adjusted economic return | No | Future actions allowed only for realized outcome | Delisting/M&A terminal value and dividends unsupported |

## Licensing, retention, and derived-data rights

All contractual conclusions are `UNVERIFIED`: persistent local/cloud storage, backups,
internal training, derived features, derived scores, backtest retention, redistribution,
customer display, delayed/real-time display, raw API exposure, cancellation deletion,
team access, and audit logging. Technical access does not establish these rights. Legal
review of authenticated terms is required before any historical download or durable
training corpus is created.

## Storage and processing estimates

These are `INFERRED`, deliberately coarse planning bounds—not provider measurements.
Assumptions: 5,040 sessions (~20 years), 10,000 securities, 32 compressed bytes/daily
row, 20/minute row, 2,500 trades and 7,000 quotes per symbol-session, 18/trade and
16/quote compressed bytes. Working space is modeled at 4× compressed, peak temporary at
6×, and retained source+working+backup headroom at 12×.

| Scenario | Compressed/network | Working | Peak temp | Recommended with backup |
|---|---:|---:|---:|---:|
| Daily only / minimum viable price corpus | 1.6 GB | 6.5 GB | 9.7 GB | 19.4 GB |
| Daily + minute | 394.7 GB | 1.58 TB | 2.37 TB | 4.74 TB |
| Daily + trades | 2.27 TB | 9.08 TB | 13.61 TB | 27.23 TB |
| Daily + trades + quotes | 7.91 TB | 31.66 TB | 47.49 TB | 94.97 TB |

At 10/100/500 Mbps, transfer time is `bytes × 8 / rate`; for daily-only this is roughly
22 minutes / 2.2 minutes / 26 seconds before protocol overhead, while the base
minute scenario is roughly 88 / 8.8 / 1.8 hours. Provider object count, compression,
actual symbol history, transformation throughput, and feature-store expansion remain
unverified. A conservative daily-first pilot should reserve at least 50 GB, but no disk
should be provisioned or data downloaded until entitlement, retention, and historical
universe blockers clear.

## V4 field source mapping

The JSON companion contains one stable, field-level record for all 43 model features and
17 timing/execution/target/provenance fields. Key families are:

- Traded-symbol daily aggregates: close, returns, momentum, SMA/EMA, slopes, RSI, MACD,
  ATR, drawdown, gap, volatility, volume, VWAP, beta, and dollar-volume features.
- SPY daily aggregates: SPY returns, symbol-minus-SPY, beta, volatility proxy, and regime.
- Sector benchmark aggregates plus point-in-time sector classification: sector-relative
  return.
- Execution/timing: decision/cutoff, S0 entry, S4/S9 exit, session identities, raw and
  adjusted prices.
- Targets: executable five-session return/label; ten-session is contractually defined but
  not materialized by the current five-session workflow.
- Provenance: stable point-in-time security identity, per-family source/availability,
  contracts, calendar, split IDs/factors, provider, commit, and manifest hashes.

For every mapped field, earliest historical availability, delisted compatibility, and
full train/serve parity remain unverified pending account and reference probes. The
legacy `feature_probability_up_delta_from_last_signal` and its prior-request family are
confirmed absent from the V4 model vector and retained only in request audit metadata.

## Pre-backfill decision matrix

| Dataset | Entitled | Earliest | Required | PIT safe | Delisted | Adjustment | Retention | Priority/blocker |
|---|---|---|---:|---|---|---|---|---|
| Daily aggregates | Unverified | — | Yes | Conditional on availability proof | Unverified | Flat/REST parity unverified | Unverified | Required before any build; bounded probe first |
| Historical/inactive ticker reference | Unverified | — | Yes | Unverified | Unverified | n/a | Unverified | Required before any build; survivorship blocker |
| Splits/reverse splits/stock dividends | Unverified account | — | Yes | Partial repository policy | Unverified | Local policy verified | Unverified | Required before any build; depth/availability blocker |
| Ticker events/symbol changes | Unverified | — | Promotion-quality | Unverified | Unverified | n/a | Unverified | Stable identity blocker |
| Cash dividends/M&A/cash-outs/delistings | Unverified | — | Promotion-quality | Unverified | Unverified | Unsupported | Unverified | Terminal-value/total-return blocker |
| Minute aggregates | Unverified | — | No for daily-first | Unverified | Unverified | Unverified | Unverified | Later challenger; storage ~0.4 TB compressed inferred |
| Trades/quotes | Unverified | — | No for daily-first | Unverified | Unverified | n/a | Unverified | Not recommended until licensing/storage reviewed |
| Financials/SEC/news | Unverified | — | No current V4 | Unverified | Unverified | n/a | Unverified | Optional/later policy |

## Staged recommendation — not authorization

1. Verify authenticated terms and account plan per dataset.
2. Execute only the bounded audit probes and bounded flat-file metadata listing.
3. Prove inactive/delisted point-in-time reference and stable identity coverage.
4. Version a universe and terminal-value/dividend contract.
5. Only then design a daily-aggregate-first pilot manifest with a small date/symbol slice.
6. Defer minute, trade, quote, news, and financial backfills until daily V4 is safe.

No phase is marked complete and no historical backfill is authorized by this report.
