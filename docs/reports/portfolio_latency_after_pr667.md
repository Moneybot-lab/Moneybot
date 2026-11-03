# Persistent portfolio latency after PR #667

Investigation: 2026-11-02 America/Denver. All production timeline timestamps below
are UTC. Examined `main`: `03f8a39f94d5019ea7c75e73b2046a9d790309ee`.
Diagnostics branch: `diagnostics/portfolio-request-timing`.

**The incident is not resolved.** Existing production logs cannot establish the
dominant request stage. This change adds disabled-by-default instrumentation and
does not change recommendations, provider selection, timeouts, cooldown, model
settings, prices, histories, suitability, or decision-event behavior.

## Production evidence and confidence

| Classification | Finding | Evidence / limit |
|---|---|---|
| **CONFIRMED** | PR #667 code was deployed | Render deploy `dep-dbki0vnf3r2c73cfp790` is live, commit `03f8a39f...`, finished 23:46:35.012. The merged source contains the exhausted-timeout/5xx cooldown calls. |
| **CONFIRMED** | Slow watchlist response persisted | Request `74ddfbbb-2eaa-4d4e`, HTTP 200, completed 23:50:05.743438 in 146,333 ms. Estimated edge start 23:47:39.410438. |
| **CONFIRMED** | Summary was a subsequent request | HTTP 200 at 23:50:18.564912, 12,601 ms; estimated start 23:50:05.963912, about 220 ms after watchlist completion. It did not cause the preceding 146-second response. |
| **CONFIRMED** | Stable-watchlist client disconnected earlier | HTTP 499 at 23:47:39.104671, 1,607 ms; estimated start 23:47:37.497671. A disconnect does not prove that server-side processing stopped. |
| **CONFIRMED** | Serving guard still emitted a warning | V2 warning at 23:47:51.384258. It has an instance label but no request ID or worker PID. |
| **CONFIRMED** | No portfolio cooldown warning in queried window | Filtered app logs for 23:46–23:55 returned no `AI portfolio provider cooldown` line, with no further page. Absence is not proof of a broken cooldown. |
| **CONFIRMED** | No resource saturation shown in minute samples | At 23:48–23:50, CPU was 0.0472, 0.0233, 0.0186 cores; memory 296.8, 299.4, 299.5 MiB. The 23:51 sample was 313.5 MiB. These are service-wide, not request metrics. |
| **LIKELY** | Waiting dominates the long response | Low CPU plus synchronous remote work is consistent with I/O or queuing. It does not distinguish OpenAI, market providers, connection locks or DB waits. |
| **LIKELY** | Neighboring Yahoo warnings belong to signal enrichment | News/company warnings at 23:47:40–47 fit stable-watchlist work continuing after 499. Similar warnings at 23:50:07–18 fit summary enrichment. These endpoints call `get_signal`; watchlist uses its own quote-only signal. No correlation IDs prove attribution. |
| **UNVERIFIED** | Successful slow AI or repeated malformed responses dominate | Both bypass the PR #667 outage cooldown, but production AI enablement, attempts, outcomes and timings are not visible. |
| **UNVERIFIED** | Yahoo sector/info, history or quote fallback dominates | Those are synchronous watchlist dependencies with cache/provider waits; existing logs have no durations for successful calls. |
| **UNVERIFIED** | DB, suitability, logging or serialization dominates | Local fixtures make these inexpensive; that cannot establish production disk/database behavior. |

The V2 warning occurred about 12 seconds after the estimated watchlist start, but
it is once-per-advisor and can originate from another endpoint. It **cannot** prove
that market retrieval finished in 12 seconds or that the remaining 134 seconds
were spent in AI. No holdings, user records, IPs, prompts or provider bodies were
exported into this report. Yahoo warning symbols are deliberately omitted.

## Effective settings: known versus unknown

Source defaults in `app_factory.py` are `AI_ENABLED=false`,
`AI_TIMEOUT_SECONDS=6.0`, `AI_FAILURE_COOLDOWN_SECONDS=120`, and `AI_MODEL=gpt-5-mini`.
The application passes these settings into its AI service. **These are not
verified effective production values.** The connected Render tools expose no
read-only environment-variable getter, and this workspace has no configured
Render CLI/API credential. A full environment dump was not requested. The user
was asked for only the effective nonsecret values; they were unavailable when
this report was prepared.

Sampled diagnostic records will read the actual AI service's `enabled`,
`timeout_s`, and `failure_cooldown_s` attributes, without exposing API keys or
model/prompt strings. This closes the configuration gap after a separately
reviewed activation; it does not retroactively establish past settings.

## Request-stage analysis

`/api/user-watchlist`: authentication → user-scoped watchlist SELECT → stream demand
registration → investor-profile queries → **sequential market loop** (quote,
30-day history, sector, portfolio/sector valuations) → **sequential advice loop**
(guarded deterministic attempt, AI, Quick Ask alignment, suitability, row/snapshot
construction and one decision-log append per lot) → JSON serialization.

The market loop finishes before the advice loop. Repeated lots call the service
again, although quote/history/sector caches can avoid new provider calls. Quote TTL
is 20 seconds, history 300 seconds, sector 3,600 seconds; cold caches, different
worker caches and quote expiry during a long load can cause additional work.
Cached quotes are re-aged without changing provenance. Massive has its own cache,
negative-cache and retry/backoff behavior. Quote fallback can traverse Massive,
Finnhub, Twelve Data and Yahoo; history falls back from Massive to Yahoo. Sector
lookup uses Yahoo `Ticker.info`. Yahoo library calls can include multiple internal
HTTP operations and are not bounded by the market service's Requests timeout.

V2/V3.1 guards still reject unsupported scoring. No history or scorer is required
for the blocked V2 path. The remaining deterministic computation is local.
Suitability remains authoritative and logging retains PR #665 evidence exclusions.
No timing information in existing logs permits production stage-duration estimates.

`/api/portfolio-summary` separately reloads lots, calls quote, **full signal** and
sector for each lot, reads sold trades and builds totals. Full signal can request
Yahoo history/info/news, company snapshots and Massive technical history. Its
score is not interchangeable with watchlist's quote-only score.

`/api/stable-watchlist` performs quote/full-signal enrichment of a static candidate
list. It may run while a user navigates to the portfolio, including after a client
disconnect. There is no proof that this consumed enough shared capacity to explain
the watchlist delay; telemetry must separate requests.

The frontend's `load()` awaits watchlist, renders its rows, awaits summary, then
starts live updates and clears loading state. The observed pair therefore implies
roughly **159 seconds** before that sequence can finish, excluding browser overhead.
Charts are already returned in watchlist and must remain available.

## PR #667 cooldown behavior

| AI path | Existing behavior, preserved here |
|---|---|
| Disabled / unsupported provider | Per-lot explicit rule-based fallback; no OpenAI call |
| Cooldown active | Per-lot fallback with `cooldown_after_failure`; no new attempt |
| Valid successful response | Enhanced advice; no cooldown, even if slow |
| First timeout / HTTP 5xx | Retry once using the existing second payload |
| Second timeout / HTTP 5xx | Set existing service-instance cooldown and return fallback |
| HTTP 4xx (including rate limiting) | Existing immediate cooldown and fallback |
| Empty, truncated, non-object or invalid model JSON | Existing retry/fallback; no outage cooldown |
| Invalid HTTP response JSON / other exception | Existing exception fallback; no new cooldown |
| Missing required model fields in a parsed object | Existing default/post-processing behavior; diagnosed but not changed |

Tests confirm the cooldown starts after exhausted transient attempts, skips later
lots, resumes after expiry and remains inactive after a successful retry. It is
shared across AI entry points in one worker, not across Gunicorn workers, and does
not cancel in-flight calls. Requests timeouts are connect/read timeouts, not a
universal total-request deadline. The missing warning is compatible with several
of these paths, especially successful or malformed responses, or disabled AI.

## Minimal diagnostic instrument

`moneybot/services/request_timing.py` installs Flask hooks before the API blueprint
and SQLAlchemy cursor hooks. It is **off by default**. It samples only the three
named endpoints; query parameters, request headers and session/account identifiers
are never included. A generated random trace ID appears in one aggregate log and
the sampled response's `X-Portfolio-Timing-ID` header. This ID is independent of
client-supplied request IDs. All timing names and counter names are allowlisted.

Each completed sample records:

- UTC start, endpoint, HTTP status, response bytes, total app wall time and request
  thread CPU time; no response body or account details.
- Count, total and maximum duration, and bounded outcome categories for auth,
  profile, DB queries, stream-demand registration, quote/history/sector/signal,
  deterministic advice, AI, alignment, suitability, decision logging and JSON.
- Transport-attempt counts/timings for OpenAI, Massive, Finnhub, Twelve Data and
  news HTTP, plus Yahoo library info/history/news/financials operations.
- Connect/read/generic timeout, HTTP 4xx/5xx and generic error categories, without
  exception text; malformed/missing-field/other-parse outcomes and cooldown skips.
- Service TTL lookup hit/miss counts, actual decision-log writes/errors and two
  request-relative milestones: market loop complete and advice loop complete.
- Actual nonsecret AI timeout/cooldown/enabled values and logging-enabled flag.

Durations are **inclusive**: provider waits nest inside AI/market spans and DB
queries nest inside profile/view spans. They must not be summed as exclusive time.
Yahoo counts are library operations, not a guarantee of one billable HTTP request.
Service TTL counters do not represent every internal provider/library cache.
No symbols or distinct-symbol set is retained, so repeated-lot attribution still
needs synthetic testing or separate approved aggregate analysis. Provider counts
can establish whether repeated service calls hit the transport.

Sampling controls (configuration only; none were changed in production):

| Setting | Default | Bound |
|---|---:|---|
| `PORTFOLIO_TIMING_SAMPLE_RATE` | 0 | 0–1; 0 disables |
| `PORTFOLIO_TIMING_MAX_PER_MINUTE` | 2 | Hard clamp at 5 starts per worker per fixed minute bucket |
| `PORTFOLIO_TIMING_DURATION_SECONDS` | 900 | Automatically expires; maximum 3,600 seconds after app initialization |

An approved diagnostic window might use rate `1`, cap `2`, duration `900` for a
small number of normal user loads, not a load test. Across two workers this permits
at most four starts per minute at those settings, shared across the three endpoints.
Sampling may therefore miss a particular request. Fixed bucket boundaries can
produce adjacent bursts; the hard maximum is per bucket, not a sliding window.
Expiry prevents new samples, while already-started traces finish normally. A
restart resets the window. Runtime dictionaries have fixed key bounds; no per-lot
arrays, user-response cache, background task, thread pool, provider call or service
is added. Disabled mode performs cheap context checks and emits no diagnostic log.

The log's wall time ends before diagnostic emission, excludes proxy/Gunicorn queue
time and response transmission, and does not measure total CPU of other threads.
Database spans cover cursor execution, not every ORM fetch/hydration operation;
profile/view spans also contain that work. Library-internal errors swallowed by
Yahoo are not observable as raised exceptions at its public method boundary.
Records are emitted at request completion; killed workers can lose incomplete
traces. Logging failures are contained, exceptions/results propagate unchanged,
and ContextVar state is cleared on teardown. Selected responses add only the trace
header; JSON schemas and normal unsampled responses are unchanged.

### Validation and overhead

**464 tests passed, zero failures/skips, 14.01 seconds.** This includes 19 new timing tests plus the existing AI, cooldown, full-route portfolio, pricing/chart/provenance, account-isolation, suitability, decision logging, historical-evidence, fail-closed serving and PR #664 workflow regressions. The complete repository suite was not run; the unrelated market-stream async test hang documented in the PR #667 investigation remains a known local limitation. Hosted CI is not claimed to have passed.

Local overhead fixture: 20 lots across five synthetic symbols, successful AI, zero configured provider delay, warm caches, 100 observations per mode after five warmups. A log sink records byte size without writing to an external transport. All three modes have the same semantic response hash. These are not production latency measurements.

| Mode | Median ms | P95 ms | Maximum ms | Peak process RSS MiB | Timing records | Largest record bytes |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 14.823 | 19.405 | 24.015 | 133.54 | 0 | 0 |
| disabled | 13.541 | 14.969 | 18.854 | 133.57 | 0 | 0 |
| sampled | 16.647 | 20.542 | 24.202 | 133.69 | 105 | 1818 |

Sampled median overhead was about 1.8–3.1 ms relative to the baseline/disabled measurements; disabled differences are measurement noise, not a speedup. The measured log size was under 2 KB for this fixture, not a universal maximum. RSS is a full-process high-water mark, not incremental allocation. Logging transport backpressure and production DB/Redis/Gunicorn behavior are outside this local measurement.

Machine-readable results and final source hashes: `docs/reports/portfolio_timing_validation.json`. Reproduction (using an installed local Python environment):

```bash
git worktree add --detach /tmp/moneybot-timing-baseline 03f8a39f94d5019ea7c75e73b2046a9d790309ee
cp scripts/benchmark_portfolio_timing.py /tmp/moneybot-timing-baseline/scripts/
"$PWD/.venv/bin/python" /tmp/moneybot-timing-baseline/scripts/benchmark_portfolio_timing.py --mode baseline --output /tmp/timing-baseline.json
.venv/bin/python scripts/benchmark_portfolio_timing.py --mode disabled --output /tmp/timing-disabled.json
.venv/bin/python scripts/benchmark_portfolio_timing.py --mode sampled --output /tmp/timing-sampled.json
```

Run these sequentially. The synthetic scenario blocks outbound sockets and real Requests sessions. Sampled-mode benchmarking resets only the local sampling budget to measure every synthetic request; production budget/expiry behavior is independently regression-tested.

Regression command used (the first PYTHONPATH entry supplies the workspace socket-denying `sitecustomize.py`; use equivalent offline isolation elsewhere):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/workspace/work/alpha-atlas-audit:/workspace/Moneybot .venv/bin/pytest -q \
  tests/test_portfolio_request_timing.py tests/test_portfolio_ai_cooldown.py \
  tests/test_portfolio_watchlist_latency.py tests/test_ai_advisor_service.py \
  tests/test_portfolio_advice_explanations.py tests/test_portfolio_explanation_integrity.py \
  tests/test_serving_feature_contract.py tests/test_deterministic_advisor.py \
  tests/test_alpha_atlas_feature_contract.py tests/test_alpha_atlas_v3_features.py \
  tests/test_production_servability.py tests/test_historical_evidence.py \
  tests/test_forecast_horizon.py tests/test_decision_log.py \
  tests/test_decision_snapshot_and_loop_scripts.py tests/test_market_data_service.py \
  tests/test_market_data_providers.py tests/test_trade_signal.py \
  tests/test_live_market.py tests/test_live_market_api.py \
  tests/test_suitability_policy.py tests/test_investor_profile_api.py \
  tests/test_portfolio_ui_offline_investigation.py tests/test_portfolio_resolver_offline_investigation.py \
  tests/test_run_daily_ops_script.py tests/test_run_weekly_model_refresh_script.py
```

## Proposed fixes, ranked for review after measurement

| Rank | Proposal | Expected benefit | Risk / cost |
|---:|---|---|---|
| 1 | Activate this bounded diagnostic window after separate approval | Establishes the actual bottleneck and effective settings; avoids another speculative repair | Low implementation risk; no paid requests added; small sampled logging cost |
| 2 | Progressive portfolio delivery: complete market/valuation/chart snapshot first, then per-lot AI explanations and final suitability-adjusted advice | High initial-load benefit **if AI dominates**; retains charts and explanations | Medium–high API/UI work. Explicit pending states, user-scoped snapshot/profile revision, stale/provenance semantics, stable lot IDs and exactly-once final decision events are required. No new paid service inherently needed. |
| 3 | Separate summary analytics from the gate for live updates/loading completion; reuse a consistent validated market snapshot | Removes the observed subsequent 12.6-second gate and possible duplicate work | Medium. Preserve full-signal score semantics, totals, cost basis and timestamp consistency; do not substitute quote-only scores. May reduce calls, no inherent paid increase. |
| 4 | Harden/reuse sector and history metadata caches and deduplicate unique-symbol work within the request | High if Yahoo/market loop dominates, modest if existing caches hit | Low–medium after freshness and cache-thread-safety evidence. Preserve all charts/provenance. Never mark old quotes fresh; keep failed/missing data explicit. |
| 5 | Limited parallel independent provider/AI work under a shared bounded admission budget | Potentially high for independent network waits | Medium–high. Verify provider entitlements, rate limits, mutable clients/caches, cancellation, per-worker aggregate caps, ordering, retries and per-lot logging before implementation. No increased infrastructure is authorized. |
| 6 | Review AI response contract/retry strategy if malformed outputs dominate | Avoids repeated failed generations | Medium recommendation-integrity risk. Validate model/payload compatibility with frozen fixtures; changing budgets/fallbacks can change advice availability. No model/settings change in this PR. |

The safest architecture is a versioned, user-scoped **market snapshot plus explicit
advice enrichment**, with accurate prices/charts immediately available and AI
explanations visibly pending until ready. It must preserve final suitability and
source attribution and reject responses for an obsolete lot/profile revision.
This is a proposal, not an implemented redesign. First measure whether the initial
market snapshot itself is slow; splitting AI alone would not fix that.

Next step: review this diagnostics-only PR. A separately authorized release and
short normal-traffic diagnostic window are needed to identify the production
bottleneck. Match the generated response header to its aggregate record, compare
app wall time with edge timing, and classify the dominant inclusive stage before
choosing a behavior-changing repair. Stop if provider-call counts or resource use
increase unexpectedly. Roll back diagnostics by disabling sampling or reverting
the PR through normal review; neither action was performed here.

No production deployment/configuration change, paid provider request, load test,
model change/promotion/training, licensing change, decision-logging disablement,
historical-evidence alteration or PR merge was performed in this investigation.
