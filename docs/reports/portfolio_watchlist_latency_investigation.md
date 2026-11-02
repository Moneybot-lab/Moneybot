# Portfolio watchlist latency investigation

Investigation date: 2026-11-02. Baseline: `3d5790370ba1888e6d4ed0e04f0240235b0cd685`
(current `main`, including PRs #664–#666). Branch: `fix/portfolio-watchlist-latency`.

## Decision and scope

**A — REPAIR READY FOR REVIEW**, for the reproduced portfolio AI retry storm.
The production incident's exact stage attribution remains unresolved. This is
not a claim that the repair explains or eliminates all observed production latency.

After both AI attempts time out or return HTTP 5xx, portfolio enhancement previously
returned its rule-based fallback without setting the existing provider cooldown.
The next lot immediately repeated both requests. Warm market caches did not help.
At the application default six-second timeout, ten lots can consume a nominal
`10 × 2 × 6 = 120` seconds in this path alone. This is a conditional timeout budget,
not a measurement of those production requests or a hard HTTP wall-clock deadline.

The repair enters the existing, configurable service cooldown **after the second
failed transient attempt**. The first lot retains `reason=provider_error` and later
lots report `reason=cooldown_after_failure`, `mode=rule_based`. The same fallback
calculations run separately for each lot. A successful retry does not set cooldown;
after expiry, ordinary enhancement resumes. The cooldown already existed for other
provider failures, including Quick Ask timeouts; no new cache or user-data storage
is introduced. A bounded diagnostic logs only failure class and cooldown seconds.

No endpoint, calculation, recommendation threshold, provider, model guard,
forecast contract, history response, suitability rule, or logging path was changed.

## Read-only production evidence

Render workspace `My Workspace` was already authorized for read-only diagnostics.
The affected service is `Moneybot` (`srv-d6cjebjh46gs73cirb3g`), host
`moneybotlabs.com`. Its configured branch is `main`, plan is Standard, disk is 1 GB,
and previews are disabled. Service metadata confirms the start command:

```text
gunicorn app:app --workers ${WEB_CONCURRENCY:-2} --threads ${WEB_THREADS:-4} --timeout ${WEB_TIMEOUT:-120} --bind 0.0.0.0:$PORT
```

Startup logs confirm the `gthread` worker type and two worker processes. Environment
overrides for threads, timeout, AI enablement and AI timeout were not retrieved;
four threads and 120 seconds are command defaults, not verified expanded values.
Gunicorn's threaded-worker timeout is not a strict per-request deadline. Long
requests still occupy request threads and expose users to client/proxy timeouts.

Request logs independently confirm the supplied observation (times below are UTC):

| Timestamp | Endpoint | Status | Response time | Bytes |
|---|---|---:|---:|---:|
| 2026-10-31 22:28:14.561 | `/api/user-watchlist` | 200 | 120,402 ms | 28,909 |
| 2026-10-31 22:28:19.151 | `/api/portfolio-summary` | 200 | 4,349 ms | 1,735 |
| 2026-11-02 12:08:24.844 | `/api/user-watchlist` | 200 | 135,375 ms | 28,782 |
| 2026-11-02 12:50:06.985 | `/api/user-watchlist` | 200 | 116,713 ms | 28,914 |
| 2026-11-02 21:27:27.074 | `/api/user-watchlist` | 200 | 149,763 ms | 31,332 |
| 2026-11-02 21:29:02.006 | `/api/user-watchlist` | 200 | 144,372 ms | 31,373 |

Other samples include a 33,502 ms HTTP 200, client-disconnected HTTP 499s, and a
3 ms HTTP 401. The supplied `/portfolio` 16 ms observation is background evidence;
the HTML shell is separate from enrichment. No real user holdings, IP addresses,
user agents, provider responses, or account records are included here.

Around the original request, Render's minute samples show approximately 0.01–0.10
CPU cores and 206–314 MiB resident memory, against an observed 2 GiB memory limit.
There is no evidence of CPU or memory saturation in that window. These are
service-wide samples, not per-request attribution. Queried app logs do not contain
portfolio stage timings or a record of the swallowed timeout/5xx failures. Thus
slow successful AI responses, market-provider fallbacks, sector lookups, database
waiting and request queuing remain possible production contributors.

## Actual request graph

1. Request-ID setup, rate limiting, session authentication/expiration checks.
2. One user-scoped positive-share watchlist query, newest lots first; build base rows.
3. `skip_market_data=1` intentionally returns only base rows and is unchanged.
4. Register unique-symbol stream demand; ensure investor profile; build decision
   context (currently a second profile query). Normal populated-profile fixtures
   execute three SELECTs total. A missing profile may also cause an INSERT/commit.
5. First sequential lot loop: quote → 30-day history → position valuation → sector;
   accumulate portfolio and sector totals. Market inputs are indexed by lot ID.
6. Second sequential lot loop: derive performance and rule-based signal → guarded
   deterministic portfolio attempt → optional remote AI enhancement → pure local
   Quick Ask consistency alignment → authoritative suitability adjustments.
7. Construct each complete row, including histories and source/freshness metadata;
   build decision snapshot and append one decision event per lot.
8. Serialize the entire JSON response and attach request ID.

There is no per-symbol thread pool and no asynchronous enrichment in this path.
The new benchmark wraps the actual functions, observes SQLAlchemy cursor timing
without recording SQL or parameters, and counts the real local decision-log writes.
Authentication, profile preparation, stream registration, quote/history/sector,
deterministic fallback, AI, alignment, suitability, logging, serialization, total
wall time, CPU time, response bytes, cache lookups and provider calls are measured.
Stage totals can nest (e.g. AI includes provider waiting; database time overlaps
profile time). Do not add all stages together as if they were exclusive durations.

## Market data and AI findings

- Service calls repeat for each lot, but existing caches already suppress repeated
  network calls under normal TTLs: quotes 20 seconds, history 300 seconds, sectors
  3,600 seconds. Quote reads re-age independent copies and preserve stale flags.
  Quote single-flight locks exist; history/sector do not have the same protection.
- Quotes can traverse Massive → Finnhub → Twelve Data → yfinance. Massive has
  bounded attempts, negative caching and rate-limit backoff; fallback paths have
  additional waits. Yahoo `Ticker.info` and history calls are outside the service's
  explicit Requests timeout parameter. No timeout or provider changes were made.
- Sector lookup uses yfinance `Ticker.info`; when quotes also fall back to Yahoo,
  independent ticker lookups can duplicate metadata acquisition. The class has
  duplicate sector definitions; the later one is effective. No unrelated cleanup.
- History is used by the portfolio's existing `renderTrend` calls in
  `app_factory.py`; it is preserved in the initial response. Splitting chart loading
  would require a separate UI/API change and was not implemented.
- The live stream is registered for subsequent updates; the endpoint obtains its
  initial quote through the existing market service. Substituting stream state
  would need separate source/freshness/fallback parity evidence.
- The blocked V2 path performs no scorer calls, forecast creation or history
  lookups. Supported full V3 can request 90-day symbol/SPY history; it remains
  unchanged and is not being activated by this task. Unsupported V3.1 stays blocked.
- Portfolio AI makes up to two sequential OpenAI calls per lot. Its separate
  Quick Ask response cache does not cache portfolio advice. Caching portfolio
  advice by ticker alone would incorrectly share entry prices, quantities and
  context across lots/accounts. This repair adds no response cache.
- With successful AI responses the call count remains one per position (two if
  the existing retry is needed). The repair improves persistent timeout/5xx cases,
  not successful-but-slow responses or malformed-response retries.

## Reproduction and benchmarks

`scripts/benchmark_portfolio_watchlist.py` runs the real Flask API blueprint,
session authorization, SQLite ORM, market service caches, synthetic blocked V2
artifact, AI service, enforced suitability and actual temporary decision-log writes.
It uses deterministic synthetic lots: 1, 5, 10, 20 unique positions, plus 20 lots
across 5 symbols, with different entry prices and quantities. Provider transports
are intercepted; outbound sockets and Requests sessions are forbidden. No training,
production request, production workflow, credential or production data is used.

Cold and warm market caches are tested for success, repeated timeout, and HTTP 503.
Each case has 20 observations. AI cooldown is reset before each measured request
in both versions, so the repair's measurement includes the first failed pair.
Controlled transport waits are **1 ms per market call and 10 ms per AI attempt**;
the six-second configured timeout is recorded but not slept in these scaled runs.
These fixtures reproduce the sequential retry multiplication, not real Internet
latency. P95 is nearest-rank over 20 local samples; it is not production P95.

Machine-readable baseline and repaired results are in
`docs/reports/portfolio_watchlist_latency_benchmarks.json`. They include source
hashes, every wall/CPU sample, stage medians/maxima and call/error/timeout counts,
response sizes, semantic hashes, peak RSS and concurrency. Cache hit rate counts
actual TTL lookup hits, including the quote path's double-check on misses; it is
not a provider's reported network cache hit rate. Synthetic provider names identify
the adapter boundary being timed, not measurements of those vendors.

Reproduce from the repair checkout with its installed Python environment:

```bash
REPAIR_ROOT="$PWD"
git worktree add --detach /tmp/moneybot-latency-baseline 3d5790370ba1888e6d4ed0e04f0240235b0cd685
cp scripts/benchmark_portfolio_watchlist.py /tmp/moneybot-latency-baseline/scripts/
"$REPAIR_ROOT/.venv/bin/python" /tmp/moneybot-latency-baseline/scripts/benchmark_portfolio_watchlist.py --samples 20 --output /tmp/watchlist-baseline.json
.venv/bin/python scripts/benchmark_portfolio_watchlist.py --samples 20 --output /tmp/watchlist-repaired.json
```

Run the two benchmark commands sequentially without a concurrent load test. The
benchmark blocks live networking itself. Both committed result sets use these
defaults and the same interpreter/host. Each source hash identifies the AI code
actually loaded; `revision` is the checkout's base commit, since the repair was
benchmarked before its commit was created.

### Controlled timeout workload

Times are **median / P95 / maximum**, in milliseconds. Each row passed semantic equivalence.

| Positions (unique) | Market cache | Baseline latency | Repaired latency | Median improvement | AI attempts before → after | Result |
|---|---|---:|---:|---:|---:|---|
| 1 (1) | cold | 29.2 / 31.5 / 35.7 | 29.0 / 29.9 / 31.5 | 1.0% | 2 → 2 | Equivalent |
| 5 (5) | cold | 131.6 / 137.7 / 150.8 | 48.2 / 52.1 / 57.0 | 63.4% | 10 → 2 | Equivalent |
| 10 (10) | cold | 258.2 / 260.5 / 262.6 | 71.7 / 75.6 / 76.2 | 72.2% | 20 → 2 | Equivalent |
| 20 (20) | cold | 520.5 / 530.1 / 531.2 | 120.4 / 127.3 / 216.8 | 76.9% | 40 → 2 | Equivalent |
| 20 (5) | cold | 457.1 / 462.5 / 464.3 | 57.3 / 58.5 / 61.4 | 87.5% | 40 → 2 | Equivalent |
| 1 (1) | warm | 25.1 / 26.6 / 26.7 | 24.4 / 26.1 / 28.5 | 2.8% | 2 → 2 | Equivalent |
| 5 (5) | warm | 110.5 / 112.1 / 112.8 | 27.8 / 30.1 / 31.6 | 74.9% | 10 → 2 | Equivalent |
| 10 (10) | warm | 218.3 / 219.6 / 219.9 | 30.8 / 34.0 / 35.4 | 85.9% | 20 → 2 | Equivalent |
| 20 (20) | warm | 439.7 / 451.9 / 453.4 | 34.6 / 38.3 / 41.6 | 92.1% | 40 → 2 | Equivalent |
| 20 (5) | warm | 434.9 / 442.2 / 444.9 | 35.4 / 38.9 / 42.0 | 91.9% | 40 → 2 | Equivalent |

### Controlled server_error workload

Times are **median / P95 / maximum**, in milliseconds. Each row passed semantic equivalence.

| Positions (unique) | Market cache | Baseline latency | Repaired latency | Median improvement | AI attempts before → after | Result |
|---|---|---:|---:|---:|---:|---|
| 1 (1) | cold | 29.5 / 31.1 / 32.2 | 29.8 / 31.9 / 32.6 | -0.9% | 2 → 2 | Equivalent |
| 5 (5) | cold | 132.0 / 135.1 / 135.4 | 50.4 / 62.1 / 73.4 | 61.8% | 10 → 2 | Equivalent |
| 10 (10) | cold | 259.6 / 264.4 / 264.6 | 73.2 / 79.4 / 79.6 | 71.8% | 20 → 2 | Equivalent |
| 20 (20) | cold | 516.9 / 535.4 / 562.8 | 123.2 / 135.4 / 255.5 | 76.2% | 40 → 2 | Equivalent |
| 20 (5) | cold | 453.8 / 460.9 / 461.9 | 56.3 / 67.7 / 75.5 | 87.6% | 40 → 2 | Equivalent |
| 1 (1) | warm | 24.2 / 26.3 / 26.9 | 25.9 / 28.8 / 30.1 | -7.3% | 2 → 2 | Equivalent |
| 5 (5) | warm | 111.4 / 115.3 / 116.7 | 29.9 / 34.0 / 37.7 | 73.2% | 10 → 2 | Equivalent |
| 10 (10) | warm | 218.7 / 223.0 / 225.6 | 31.7 / 33.6 / 34.1 | 85.5% | 20 → 2 | Equivalent |
| 20 (20) | warm | 435.1 / 442.4 / 443.9 | 36.6 / 39.3 / 40.1 | 91.6% | 40 → 2 | Equivalent |
| 20 (5) | warm | 432.2 / 442.1 / 442.6 | 35.0 / 37.9 / 40.5 | 91.9% | 40 → 2 | Equivalent |

### Controlled success workload

Times are **median / P95 / maximum**, in milliseconds. Each row passed semantic equivalence.

| Positions (unique) | Market cache | Baseline latency | Repaired latency | Median improvement | AI attempts before → after | Result |
|---|---|---:|---:|---:|---:|---|
| 1 (1) | cold | 18.5 / 22.2 / 65.8 | 18.0 / 20.2 / 22.3 | 2.7% | 1 → 1 | Equivalent |
| 5 (5) | cold | 80.7 / 89.5 / 109.8 | 79.0 / 83.2 / 85.4 | 2.2% | 5 → 5 | Equivalent |
| 10 (10) | cold | 157.7 / 162.4 / 163.0 | 154.7 / 159.5 / 160.8 | 1.8% | 10 → 10 | Equivalent |
| 20 (20) | cold | 313.4 / 342.9 / 408.2 | 310.7 / 326.2 / 388.6 | 0.9% | 20 → 20 | Equivalent |
| 20 (5) | cold | 246.3 / 253.4 / 257.1 | 245.2 / 250.8 / 252.7 | 0.4% | 20 → 20 | Equivalent |
| 1 (1) | warm | 14.2 / 15.1 / 15.3 | 13.3 / 14.4 / 14.8 | 6.5% | 1 → 1 | Equivalent |
| 5 (5) | warm | 59.5 / 61.0 / 61.8 | 58.5 / 60.6 / 60.6 | 1.7% | 5 → 5 | Equivalent |
| 10 (10) | warm | 114.6 / 125.4 / 127.7 | 112.7 / 116.5 / 117.2 | 1.7% | 10 → 10 | Equivalent |
| 20 (20) | warm | 223.0 / 231.4 / 236.8 | 223.7 / 231.7 / 234.1 | -0.3% | 20 → 20 | Equivalent |
| 20 (5) | warm | 226.0 / 230.4 / 231.9 | 225.0 / 243.0 / 247.9 | 0.4% | 20 → 20 | Equivalent |

Successful-response latency is effectively unchanged apart from local measurement variation; there is no successful-path speedup claim. A single-position outage still pays its original two attempts.

### Stage profile and resource evidence

Representative **10 unique positions, cold cache, persistent timeout** stage medians:

| Stage | Baseline ms | Repaired ms | Calls before → after |
|---|---:|---:|---:|
| `authentication` | 0.019 | 0.018 | 1 → 1 |
| `database` | 0.297 | 0.285 | 3 → 3 |
| `database.watchlist` | 0.050 | 0.050 | 1 → 1 |
| `profile_lookup` | 0.851 | 0.848 | 2 → 2 |
| `profile_context` | 0.382 | 0.374 | 1 → 1 |
| `stream_demand` | 0.006 | 0.005 | 1 → 1 |
| `quote` | 12.745 | 12.682 | 10 → 10 |
| `provider.quote.synthetic_massive` | 10.960 | 10.945 | 10 → 10 |
| `history` | 18.287 | 18.495 | 10 → 10 |
| `provider.history.synthetic_massive` | 11.729 | 11.778 | 10 → 10 |
| `sector` | 11.146 | 11.135 | 10 → 10 |
| `provider.sector.synthetic_yfinance` | 10.849 | 10.835 | 10 → 10 |
| `deterministic` | 1.482 | 0.803 | 10 → 10 |
| `ai` | 203.200 | 20.511 | 10 → 10 |
| `provider.ai.synthetic_openai` | 202.445 | 20.242 | 20 → 2 |
| `quick_alignment` | 0.064 | 0.022 | 10 → 10 |
| `suitability` | 0.646 | 0.214 | 10 → 10 |
| `decision_logging` | 2.421 | 1.080 | 10 → 10 |
| `serialization` | 1.586 | 1.483 | 1 → 1 |

Full-process peak RSS: baseline **140.79 MiB**, repair **140.51 MiB**. These are independent Python process high-water marks, not incremental allocation or production capacity. For the representative row, median request CPU was **24.87 → 19.59 ms**. Reducing repeated work reduces CPU; the runtime state remains O(1).

Peak local provider concurrency was **1** for both versions in every case. A cold N-unique portfolio makes N quote, N history and N sector provider calls; a warm portfolio makes zero market-provider calls. Twenty lots across five symbols make five calls of each market type when cold, zero when warm. The existing service is still called per lot; no concurrency or cache TTL change was introduced.

TTL lookup hit rates: unique-symbol cold **0%**, warm **100%**; 20 lots / 5 symbols cold **69.23%**, warm **100%**, identical before/after. During the outage fixture AI attempts change from 2N to 2 while cooldown holds; successful AI attempts remain N. Provider waits dominate the failing baseline. Serialization/database/CPU measurements are local only.


## Functional verification

The initial new regressions failed on baseline for exhausted timeout and HTTP 503.
With the repair they verify cooldown starts after the last failure, resumes at
expiry, does not activate after a successful retry, and does not store lot advice.

Full-route tests compare the same outage responses before/after the correction,
excluding only request IDs, computed quote age and the documented fallback reason.
They assert lot identities, quantities/entry prices, current valuations, returns,
position weights, symbol/history mapping, complete histories, stale flags, source
metadata, suitability authority and final action/reason correspondence. One
snapshot/event is written per position; events remain `portfolio_advice`, carry
no model probability/forecast, and are excluded from historical prediction evidence.
Missing quotes preserve entry-basis provenance and do not corrupt other positions.
Unauthenticated access, the explicit skip route, and two simultaneous accounts
using the same cooldown state are covered. No scorer/forecast calls are allowed
in the fixture. Existing serving-contract tests cover unsupported V3.1 too.

Completed relevant regression run: **443 passed, zero failed, zero skipped, 12.77 s**.
This includes ten new tests and existing portfolio explanation, AI retry, Quick Ask,
serving-contract, historical-evidence, forecast, market-provider/cache/fallback,
live-market API/UI, suitability/profile, decision-log and PR #664 workflow tests.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/workspace/work/alpha-atlas-audit:/workspace/Moneybot .venv/bin/pytest -q \
  tests/test_portfolio_ai_cooldown.py tests/test_portfolio_watchlist_latency.py \
  tests/test_ai_advisor_service.py tests/test_portfolio_advice_explanations.py \
  tests/test_portfolio_explanation_integrity.py tests/test_serving_feature_contract.py \
  tests/test_deterministic_advisor.py tests/test_alpha_atlas_feature_contract.py \
  tests/test_alpha_atlas_v3_features.py tests/test_production_servability.py \
  tests/test_historical_evidence.py tests/test_forecast_horizon.py \
  tests/test_decision_log.py tests/test_decision_snapshot_and_loop_scripts.py \
  tests/test_market_data_service.py tests/test_market_data_providers.py \
  tests/test_live_market.py tests/test_live_market_api.py tests/test_suitability_policy.py \
  tests/test_investor_profile_api.py tests/test_portfolio_ui_offline_investigation.py \
  tests/test_portfolio_resolver_offline_investigation.py \
  tests/test_run_daily_ops_script.py tests/test_run_weekly_model_refresh_script.py
```

The first PYTHONPATH entry is the workspace's offline `sitecustomize.py`, which
raises on outbound `socket.connect`, `connect_ex` and `create_connection`. Use an
equivalent offline test environment when reproducing outside this workspace.

**Additional-suite limitation:** the attempted 476-test expansion including
`tests/test_market_stream.py` passed its first 328 tests, then hung in
`test_worker_does_not_raise_type_error_for_scalar_conditions`. The same test hung
on untouched baseline under the same offline environment; a five-second
faulthandler dump showed the asyncio event loop waiting in `select`. Both stalled
processes were terminated. The 33-test stream module is excluded from the completed
443-test run, not silently marked passed or skipped. Stream code is unchanged;
the cause of that pre-existing/environmental hang was not repaired here.
Hosted CI is not claimed to have passed.

## Resource bounds, compatibility and remaining risks

The runtime change is 11 added lines: one small helper and two calls. It adds no
threads, process pools, queues, stored responses, disk allocation or paid services.
Provider requests decrease during sustained failures; successful-path API use and
market-data entitlements are unchanged. No API fields are added or removed.

The existing cooldown is **per service instance / Gunicorn worker**, shared across
users and AI entry points. It contains only a deadline, not private account data.
Already-running requests are not cancelled; simultaneous requests can each finish
their current pair before observing cooldown. It is not a global single-flight
breaker or an absolute two-call cap across workers. A genuinely slow request can
outlast cooldown; later requests can retry after expiry. Recovery can be delayed
up to the configured cooldown, deliberately using explicit rule-based fallback.
Quick Ask may also use its existing fallback during that shared provider cooldown.
Changing to a distributed circuit breaker or adding bounded concurrency requires
separate throughput/rate-limit and client-thread-safety evidence.

Successful-but-slow AI, serial market fallback waits, repeated-symbol quote TTL
expiry during long loads, large portfolios, provider rate limiting and request
queuing remain risks. SQLite measurements do not represent the production database
or Redis stream registration. The benchmark patches normalized provider transport
boundaries; real Massive rate-limit/fallback behavior is verified by existing unit
tests, not by issuing real provider requests. Full app startup/middleware and
Gunicorn scheduling are outside the isolated blueprint benchmark.

The next engineering step is human review and separately authorized release. Use
the new safe cooldown diagnostic to establish whether this failure path occurs in
production; collect bounded per-stage timings with no account identifiers if slow
requests remain. Confirm effective AI settings and provider latency before changing
parallelism, caching or deadlines. Do not declare the incident resolved from these
local measurements. No 200-user production load test was run.

For this fixture, acceptance is a substantial reduction in multi-position outage
latency, two AI attempts during a sequential load inside cooldown, complete
semantically equivalent rows, and no successful-path call-count increase. A
provisional enriched-route operational goal for up to 20 positions is P95 under
20 seconds (nominal 12-second AI failure budget, five seconds for market data,
three seconds for other work), **only after measuring real market and successful
AI latencies and agreeing those budgets**. This is not a certified SLA or the repository's
separate five-second simulated-load target.

Rollback: revert this focused commit through normal review and a separately
authorized release; this restores the retry storm. Do not disable decision logging,
change rollout settings or bypass serving guards as a latency workaround.

```text
Production deployment performed: NO
Production model changed: NO
Rollout settings changed: NO
Model promotion performed: NO
Training executed: NO
Historical evidence modified: NO
Decision logging disabled: NO
Market-data licensing changed: NO
Production load test executed: NO
Real user financial data exported: NO
PR merged: NO
```
