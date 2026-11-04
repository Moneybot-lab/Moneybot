# Portfolio AI latency repair

Status: reviewable implementation and offline evidence; **not deployed and not an incident-resolution claim**.

- Baseline: `42031dab4730d754b764f33c58ba7bb55bce262e`, current `main` after PR #668.
- Repair implementation and benchmark evidence: `8e1a29be81f5c3e49eefcab8fbc78631f86fa5af`.
- Branch: `fix/portfolio-ai-latency`. This report is a subsequent documentation commit.
- Benchmark JSON records the checkout's parent commit and SHA-256 hashes of the modified source files. Measurements were taken before publication; those hashes identify the implementation tested.

## Production evidence and root cause

The Render application log was read without changing the service. Trace `d26f9b838018443c93d5180c6cea2f9b`, service `srv-d6cjebjh46gs73cirb3g`, began **2026-11-03 23:40:15.549640 UTC** and completed **23:42:49.046462 UTC** with HTTP 200.

| Measurement | Observed |
|---|---:|
| Total request | 153,496.29 ms |
| AI enhancement | 147,173.012 ms |
| OpenAI calls | 147,091.998 ms |
| Initial market preparation | 6,283.885 ms |
| Quote / history / sector operations | 2,262.979 / 2,623.894 / 1,325.462 ms |
| Database / deterministic advice / suitability | 5.870 / 2.133 / 1.995 ms |
| Decision logging / serialization | 17.154 / 6.337 ms |
| Request-thread CPU | 3,193.517 ms |
| Positions / HTTP attempts / usable enhancements | 28 / 56 / 28 |
| HTTP-success / read-timeout outcomes | 55 / 1 |
| Malformed/unusable response counter | 27 |

These stages overlap; they must not be added. AI occupied approximately **95.9%** of request wall time. The same trace confirms effective AI enablement, a **6-second Requests timeout**, a **120-second provider cooldown**, and enabled decision logging. Related summary/stable timings (16.836s/16.018s) come from the supplied incident evidence, not newly measured traffic.

**CONFIRMED:** The endpoint awaited every lot's sequential AI enhancement before returning any rows. Its two profiles were 220 output tokens / low reasoning, then 420 / minimal. Every observed enhancement needed a second HTTP attempt. The client subsequently awaited the summary before completing its loading state. A successful retry correctly did not enter the PR #667 outage cooldown; that cooldown was not designed to suppress successful-but-slow retries.

**CONFIRMED code defect, not established as the explanation for those 27 responses:** Missing required fields could still be returned as `ai_enhanced` with defaults. Explicit Responses completion status was ignored. JSON parsing alone was not sufficient validation of a usable portfolio recommendation.

**LIKELY, still unproven:** The first profile's output/reasoning budget contributed to incomplete generation. Both token budget and reasoning effort changed on retry, so their individual effects cannot be separated from this trace.

**UNRESOLVED:** The exact failure mode of the 27 production responses. The existing trace retained neither completion metadata nor token usage. Synthetic incomplete/truncated envelopes demonstrate how these failures arise, but do not recover missing historical evidence. Supported text, parsed-object and nested-content envelopes succeed in deterministic tests; there is no evidence that a valid production response was rejected by the extractor.

## AI request changes

Portfolio inference now starts with the previously successful **420-token / minimal-reasoning** profile. It retains one bounded recovery attempt using the same profile. The prompt, model selection, structured response fields, portfolio rules and investor suitability policy are unchanged. Quick Ask's request profiles are unchanged.

The validator requires an object with the four expected fields, a supported action, a nonblank explanation, and two nonblank strings in each guidance list. Existing harmless action whitespace/case normalization is preserved. Explicit non-completed responses, missing fields, truncated JSON and invalid field types cannot become completed AI advice. Compatible legacy envelopes without a status remain supported.

Two exhausted unusable responses start a **portfolio-only pause on that worker**, using the existing cooldown duration. A single invalid response followed by success never starts this pause. The PR #667 exhausted timeout/5xx handling and existing 4xx behavior remain intact. Quick Ask does not consult the new invalid-response pause. Because this is worker-level provider health, another account on the same worker can temporarily receive fallback advice after a pair of unusable responses; this availability tradeoff needs review.

The existing bounded, opt-in PR #668 sampler now includes safe counters for first/retry validity, incomplete status, exhausted output budget, field/envelope/JSON validation and provider-reported token totals. Reasoning tokens are a subset of output tokens. No prompts, financial inputs, account IDs, raw output, exception text or provider payloads enter timing logs. Sampling defaults, per-worker rate limits and automatic expiration remain unchanged.

## Progressive portfolio architecture

`GET /api/user-watchlist/stream` uses the existing authenticated Flask request and delivers newline-delimited JSON. It needs no worker queue, new service, schema migration, additional thread pool or durable task state.

1. **Snapshot:** Existing market preparation, deterministic serving guards, position calculations and suitability produce complete rows with prices, provenance/freshness flags, quantities, gains/losses, weights and historical charts. Optional AI is explicitly pending. Initial market value, remaining basis and unrealized totals render immediately. Realized/lifetime summary values show loading until the separate summary succeeds; a failure is explicit.
2. **Advice updates:** The same request performs serial AI calls and emits advice for one authenticated lot at a time. The saved deterministic result is reused; no second model forecast is generated. Suitability and action/explanation alignment remain authoritative.
3. **Completion or interruption:** Completed, fallback, failed and expired states are distinct. The reader does not automatically reconnect or replay paid AI work. Refresh is an explicit new observation.

The original `/api/user-watchlist` JSON contract remains available, including `skip_market_data`. It still completes AI before returning for legacy clients. Its new `defer_ai=1` mode supplies full market data without optional AI; the browser uses it when streamed enrichment admission is busy and on market-boundary/bfcache refreshes. This avoids creating an automatic AI retry loop.

The initial payload triggers price streaming and the summary request without waiting for AI. Advice updates change only the affected advice cell and lot state, leaving newer live prices, grouped quantities and charts intact. Repeated-ticker lots retain distinct IDs and cost bases; their individual explanations are accessible in the advice dialog. Snapshot time remains visible, so subsequent live prices do not falsely make old AI inputs current.

### Consistency, admission and lifecycle

- All inputs are constructed on the server for the authenticated session. The client cannot submit another account's holdings or an arbitrary personalized inference prompt.
- A private equality snapshot covers every active lot and all investor-profile fields. It is checked before initial publication and before/after each AI call. New, sold, deleted, resized or repriced lots and profile changes expire the remaining work. The DB transaction is released before remote inference.
- An opaque view identifier, lot ID, browser generation and terminal lot state reject wrong-view, unknown-lot, duplicate and obsolete updates. Financial payload fields are never accepted from an advice update.
- One active enrichment stream per worker, no queue. With the repository defaults of two workers/four threads (`render.yaml`), that permits at most two progressive AI calls at once and leaves other threads available. Existing legacy endpoints are not covered by this new admission limit.
- Nonblocking file locks also fence overlapping views of an account across workers on the existing single instance. There are at most 512 hashed lock files, with no account identifier in their names. Rare shard collisions conservatively report busy. This is not a distributed lock for future multiple instances.
- A 120-second enrichment budget is checked between calls and again before publication. A worker restart discards unfinished work; there is no cross-worker RAM job to recover.
- Navigation aborts the fetch, closes the price stream and clears a scheduled refresh. Restoring a page from bfcache requests data only. Closing a WSGI response, including an unstarted sampled iterator, releases admission and request context.
- A disconnected client can leave the current provider call in flight until it returns and the server detects the closed socket. Requests' timeout is not a hard total-wall deadline against a trickling peer. There are no queued tasks to continue indefinitely; no stronger cancellation guarantee is claimed.

### Logging and prediction evidence

Each lot has one **initial** decision observation and, when enrichment is attempted, one **enriched** observation. These are different served advice phases, not retries of the same event. A view ID, per-view observation index and phase correlate them without creating a prediction identity. A normal 28-lot enriched view therefore writes 56 decision events; the old blocking view wrote 28. AI HTTP retries never write additional decision events.

An interrupted/expired view retains observations already generated and does not synthesize a final event. A new explicit refresh gets a new observation identity. This design does not promise exactly-once network delivery or a durable replay receipt across crashes. The client never resumes/replays stream events automatically.

Both phases retain the `user_watchlist` endpoint and `portfolio_advice` classification. PR #665 excludes these from independent prediction evidence. Genuine forecast metadata, if present, is reused from the initial deterministic result; it is never fabricated or rerun for narrative updates. PR #666's V2/V3.1 guards, stale-data flags, licensing/privacy approvals and model-promotion controls are unchanged.

## Benchmark evidence

Files:

- `portfolio_ai_benchmark_baseline.json` and `portfolio_ai_benchmark_repair.json`: 35 workload combinations, 20 observations each per implementation (1,400 route executions total).
- `portfolio_ai_benchmark_realistic_baseline.json` and `portfolio_ai_benchmark_realistic_repair.json`: one additional 28-lot/10-symbol replay each with production-like delays.
- `portfolio_ai_validation.json`: comparison assertions and final test results.

The harness uses the real routes, SQLite ORM, market caches, guarded deterministic advisor, suitability policy and decision logger with synthetic transports. Outbound socket/HTTP calls are blocked. It tests 5/10/20/28 lots, including 28 lots over 10 tickers; success, profile-sensitive incomplete output, first-attempt malformed output independent of profile, repeated timeout, 5xx, unavailable AI and persistent malformed output. Provider counts, first-valid/invalid/timeout rates, synthetic token usage, CPU, process peak RSS, event counts and financial/advice hashes are included.

**Measurement definition:** Initial latency is arrival of the first complete essential payload at the Flask test client, before optional enrichment; completion includes all AI work. It excludes real network transit and mobile rendering. Browser tests separately execute the actual decoded inline JavaScript and verify render ordering. Neither number is an observed production browser latency.

| Lots / tickers | Scenario | Baseline complete median / P95 (s) | Repair initial median / P95 (s) | Repair complete median / P95 (s) | AI attempts before → after |
|---|---|---:|---:|---:|---:|
| 28 / 10 | success | 0.131 / 0.137 | 0.073 / 0.089 | 0.212 / 0.228 | 28 → 28 |
| 5 / 5 | profile_sensitive | 0.053 / 0.055 | 0.036 / 0.042 | 0.059 / 0.072 | 10 → 5 |
| 10 / 10 | profile_sensitive | 0.096 / 0.100 | 0.060 / 0.063 | 0.105 / 0.108 | 20 → 10 |
| 20 / 20 | profile_sensitive | 0.189 / 0.196 | 0.113 / 0.121 | 0.208 / 0.221 | 40 → 20 |
| 28 / 28 | profile_sensitive | 0.268 / 0.276 | 0.151 / 0.157 | 0.289 / 0.308 | 56 → 28 |
| 28 / 10 | profile_sensitive | 0.192 / 0.200 | 0.072 / 0.080 | 0.210 / 0.226 | 56 → 28 |
| 28 / 10 | first_malformed | 0.188 / 0.195 | 0.081 / 0.095 | 0.300 / 0.335 | 56 → 56 |
| 28 / 10 | timeout | 0.072 / 0.091 | 0.074 / 0.236 | 0.149 / 0.305 | 2 → 2 |
| 28 / 10 | server_error | 0.070 / 0.075 | 0.078 / 0.260 | 0.164 / 0.343 | 2 → 2 |
| 28 / 10 | unavailable | 0.070 / 0.233 | 0.072 / 0.077 | 0.073 / 0.078 | 0 → 0 |
| 28 / 10 | always_malformed | 0.192 / 0.279 | 0.071 / 0.074 | 0.145 / 0.158 | 56 → 2 |

P95 uses nearest rank over 20 fast observations, not a production SLO. Fast fixtures intentionally expose the cost of revision checks, a second suitability pass and additional phase logging. The report does not hide slower full completion when provider delays are only milliseconds.

| 28 lots / 10 tickers, production-like delay replay | Baseline | Repair |
|---|---:|---:|
| First complete essential snapshot | 153.477s | 6.325s |
| Complete AI enrichment | 153.477s | 80.011s |
| HTTP AI attempts / completed enhancements | 56 / 28 | 28 / 28 |
| Quote / history / sector provider calls | 10 / 10 / 10 | 10 / 10 / 10 |
| Decision observations | 28 | 56 |
| Forecasts generated | 0 (V2 serving guard) | 0 (same guard) |

The realistic fixture uses 209ms for each unique quote/history/sector transport and 2.627s per AI HTTP attempt. The profile-sensitive fixture returns incomplete output for 220/low and valid output for 420/minimal. It represents the observed retry pattern conditionally; it does **not** establish why production output failed. No sample-specific read timeout is injected in this replay. There is only one observation per implementation, so no realistic P95 or production latency guarantee is claimed.

Initial 10s and production P95 15–20s remain engineering targets. The replay measures both initial data and full completion; moving work off the critical path is not presented as eliminating that work. The related summary/stable endpoints remain secondary bottlenecks. Starting summary sooner may improve cache reuse, but that benefit was not measured.

The realistic run used 0.077s baseline versus 0.180s repaired process CPU. Process peak RSS was 134.82 versus 135.75 MiB. Across the full fast matrices it was 159.34 versus 159.12 MiB. These are standalone harness high-water measurements, not production memory estimates or incremental allocation measurements. Observed provider concurrency was one in each single-view benchmark; the admission test separately verifies the worker/account limits.

## Provider cost

For the profile-sensitive 28-lot fixture, requests fall from 56 to 28 and synthetic reported input/output totals fall from 22,400/11,200 to 11,200/5,040 tokens. These are fixture values, **not measured billing**. Reasoning counts must not be added to output counts.

The allocated output ceiling for an old two-attempt success was 220+420=640 tokens; the new single-attempt ceiling is 420 (34.4% lower). If both new attempts are needed, their ceiling is 840, **31.25% above** the old two-attempt ceiling. In an already-valid first-attempt workload, call count remains unchanged and the larger first budget does not establish lower cost. Persistent unusable output is bounded by the new portfolio pause. Actual cost and quality under the new first-attempt profile require a small, separately approved production observation using safe completion/usage aggregates.

No instance size, Gunicorn settings, disk, database tier or paid API entitlement changed.

## Validation and remaining limits

**514 tests passed, zero failures or skips, in 19.23 seconds** across the 30 selected modules. The separately captured local Gunicorn smoke used two workers/four threads: initial snapshot 0.057s, full completion 1.344s. A second same-account stream was rejected while lightweight DB-only traffic remained responsive. Closing the next client after its snapshot allowed 1 in-flight synthetic provider attempt to finish, then released the lock and stopped further work. Iterator close, worker/process fencing, expiry, changed lots/profile, unknown/duplicate/out-of-order UI updates, navigation and bfcache restoration have deterministic coverage. The first Unix-socket run was blocked by the sandbox's bind restriction; the successful local check enabled socket access while retaining outbound Python/provider guards.

Financial projection hashes match across every baseline/repair workload and repetition, including missing/stale data tested separately. Final accepted action, reason and suitability hashes also match in the benchmark matrix. Tests preserve authoritative deterministic reasons, policy overrides, original source/freshness metadata, history bars, cost basis, quantities and per-lot isolation. Invalid/incomplete provider objects now explicitly fall back instead of retaining an unsupported AI label; the corresponding old test expectations were updated deliberately.

The V2/V3.1 serving, historical evidence, forecast horizon, decision snapshot, market data, live pricing, suitability, and PR #664 daily/weekly subprocess/lock tests are included. No training, promotion, artifact certification, historical dataset or production workflow was run or changed.

The full repository suite was not run. The previously documented unrelated `test_market_stream.py` hang is excluded; it is not represented as passing or as newly reproduced in this repair. No live OpenAI call, production load test, deployment, iPhone smoke test or Render proxy streaming validation was performed. Old browsers without readable response streams buffer until completion; the standard JSON endpoint remains compatible. The client bounds an individual NDJSON buffer to 8 MiB; very large portfolios need separately designed pagination.

### Alternatives considered and follow-up priorities

| Option | Expected benefit | Risk / cost | Decision |
|---|---|---|---|
| Successful retry profile first | Removes the observed avoidable request pattern if correlation holds | Low implementation scope; actual token/quality impact unmeasured | Implemented |
| Request-owned progressive stream | Initial rows no longer wait for per-lot AI; preserves snapshot context | Moderate protocol/UI change; occupies a bounded request thread | Implemented |
| Follow-up durable enrichment jobs | Resumable results across workers/restarts | Requires receipt storage, lifecycle cleanup and idempotent logging design | Deferred |
| Parallel per-lot AI | Can shorten full completion | More provider concurrency/rate-limit pressure and peak memory | Deferred |
| Multi-lot AI batch | Could reduce prompt overhead | New model response/lot association contract and larger failure blast radius | Deferred |
| Personalized AI cache | Could avoid repeat-view cost | Must include account, exact lots, profile, market snapshot and rules/model revision | Deferred; never cache by ticker alone |
| Summary/stable provider work | May improve secondary cards/endpoints | Requires separate measured provider/cache analysis | Next after controlled validation of primary repair |

### Reproduction

With the existing Python dependencies installed, run the committed harness against the baseline worktree and repair. All provider calls are synthetic and blocked from the network:

```bash
git worktree add ../moneybot-portfolio-baseline 42031dab4730d754b764f33c58ba7bb55bce262e
python scripts/benchmark_portfolio_ai_latency.py --repo ../moneybot-portfolio-baseline --mode baseline --samples 20 --output /tmp/portfolio-baseline.json
python scripts/benchmark_portfolio_ai_latency.py --repo . --mode repair --samples 20 --output /tmp/portfolio-repair.json
```

Add `--realistic --samples 1` for the slower, already-completed production-like replay; repeating it is unnecessary for unchanged source. `portfolio_ai_validation.json` records the completed regression count and comparison assertions. The local WSGI check is `pytest -q -s tests/test_portfolio_stream_gunicorn.py`; its Unix socket requires local bind permission, but no external network connection.

### Human review and rollback

Review the streaming lifecycle, dual-phase decision logging, worker-level malformed-output pause and cost ceilings. If approved for a separate release, perform one authenticated small-portfolio check and then one representative 28-lot check with existing bounded diagnostics. Confirm first-chunk delivery through Render to iPhone, pending/final labels, complete charts/prices, observed full completion, first-attempt validity and real usage totals before calling the incident resolved. This is a recommendation only; no such release or production test is authorized or performed here.

Remaining production verification, requiring separate authorization:

1. Verify the approved deployed commit and use an explicitly bounded diagnostic sampling window. Do not change model, infrastructure or provider-entitlement settings.
2. On an authenticated iPhone, load one small portfolio and one representative 28-lot portfolio. Record actual first snapshot/first usable paint and full enrichment completion separately; verify Render forwards the initial chunk before AI finishes.
3. Compare positions, prices/source/freshness, gains/losses, totals, history charts and suitability-adjusted actions. Confirm pending, completed, fallback and expired labels; later advice must retain its snapshot time and must not replace newer live prices.
4. Refresh or navigate away during one controlled load. Inspect aggregate counts for bounded in-flight work and admission release, with no automatic AI retry storm. Do not modify a real customer's holdings just to exercise a mutation test; those cases already have offline coverage.
5. Inspect first-attempt validity, completion/budget categories, timeout/5xx cooldown behavior, actual token totals, CPU/RSS and phase-event counts. Confirm portfolio observations remain excluded from independent historical prediction evidence.
6. Decide whether the incident is improved from those observations. A production P95 claim needs a separately approved sample of normal traffic; two smoke loads cannot establish it. If correctness, resource use or streaming regresses, arrange an approved rollback.

Rollback by reverting this PR to the baseline behavior. There are no migrations or new infrastructure to undo. Existing JSON consumers remain compatible. An already-running stream finishes or closes normally; its held lock releases on process termination. Observation phases already written remain honest portfolio-advice records and stay excluded from independent prediction evidence.

```text
Production deployed: NO
PR merged: NO
Production environment changed: NO
Model promotion: NO
Model retraining: NO
Historical evidence changed: NO
Decision logging disabled: NO
Suitability enforcement bypassed: NO
Paid API subscriptions changed: NO
Production load test executed: NO
```
