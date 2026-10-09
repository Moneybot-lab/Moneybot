# MoneyBot C1–C5 stream and portfolio reliability repairs — 2026-10-09

**Scope: owner-authorized code-only C1–C5 implementation, synthetic validation and one reviewable PR. C6 excluded.**

This record accompanies the [offline investigation and approved proposal](moneybot_stream_portfolio_offline_investigation_2026-10-09.md). The owner authorized implementation directly; no additional proposal cycle, deployment, merge, live connection, provider/Redis/AWS access, Stage B execution, V4 workload or automatic scheduling is included.

## Source and evidence binding

The checkout began on `codex/v4-worker-identity-source-binding`, commit `6ddb6de3edfbb0de9288673d8bbdd898a59ed6de`. All 85 investigation baseline cases passed before source edits with kernel network blocking (3.28s; zero failures, xfails or skips). Existing uncommitted investigation artifacts were preserved and are included as review context.

Read-only GitHub metadata identifies `main` as `5eb0795e2701d437fa86daca6d2b60dcff39f8dd`. Its Git tree `ecebdc133ea250ed04ef952e7f37cf4ccb39c771` exactly matches the starting local HEAD tree. The earlier investigation could not find that commit as a local object; this later repository-tree comparison establishes matching repository bytes, **not independent verification of the installed worker, runtime settings or production environment**.

Repair branch: `codex/ws-portfolio-reliability-repairs`, targeting `main`. The published PR and its commit list are the authoritative publication record; this report records the tested scope without embedding its own commit hash.

Frozen monitoring evidence and prior Stage B reports are preserved. Original `FEATURE_WINDOW_INVALID` disposition remains unchanged; saved replay PASS is independent. Stage B remains OPEN, and historical/operational UNKNOWN gates remain UNKNOWN. No production timeout repair claim is made.

## Implemented boundaries

### C1 — Worker responsiveness

Worker Redis access and parsing/health work use a worker-owned single-thread executor with ordered admission rather than an unbounded default-executor backlog. Per-frame/event ordering, stale writes, TTLs, keys, schemas, counters, subscriptions and acknowledgement checks remain intact. Health calculations/publication have a cadence inside the existing TTL, and large processed batches yield. Database demand SQL is unchanged. Cancellation does not release admission while an admitted synchronous operation is still running.

### C2 — Comparison and recovery scheduling

Shadow comparisons run outside frame reception with at most one active batch, a default 50-symbol work cap and a 10-second admission/awaiting deadline. Already-admitted synchronous Redis/provider work cannot be forcibly interrupted: it retains its slot and is joined safely, so physical completion can exceed the deadline. No further batch work is admitted after expiry. All comparison, queued recovery and reconnect recovery share bounded REST admission. Reconnection proceeds with explicit stale state instead of awaiting every REST repair. Generation checks prevent queued stale markers or delayed recovery from invalidating newer state. Older persistent-state writes are rejected outside the unchanged sequence classifier; equal-time Q recovery replacements and missing/naive/future recovery timestamps fail closed. Worker-owned tasks and unstarted queue jobs are canceled/cleared, and admitted calls are joined on shutdown. Retry/backoff protections, subscription limits, queue 1024 and ping interval/timeout 20 seconds remain unchanged.

### C3 — Price time and cache correctness

The selected price's timestamp determines freshness; unrelated ticker updates cannot renew an older price. A shared pure freshness calculation ages provider/service/resolver reads. Cache responses are independent copies and require no new fetch merely to update status. Prices must be finite and positive; missing, naive, future or mismatched-session timestamps remain stale/unknown. Daily-close fallback is last known, never live. Unusable snapshot normalization receives a short bounded negative-cache policy while existing transport retry/rate-limit/error caching remains intact.

### C4 — Resolver and connection delivery ordering

Fresh eligible channels are filtered before newest-event selection, preserving T/Q/A/AM compatibility. Recovery serialization preserves actual selected trade/quote/aggregate semantics. Ordering memory belongs to one SSE connection; stateless `/api/quote` has no previous-delivery history. Older or undated incoming values cannot silently replace a newer delivered observation. Incoming status/provenance diagnostics remain visible even while a prior numeric value is retained as stale/last known. An equal-time different-price observation without a trustworthy correction marker is rejected explicitly; the diagnostic is per observation, not a persistent conflict latch. Current-session boundaries invalidate ordering context; price magnitude is never a split/correction ordering rule.

The current contracts do not supply verified corporate-action or correction-event identity. No split/correction signal is fabricated. A genuinely newer timestamped observation is deliverable regardless of numeric direction; ambiguous equal-time corrections remain explicitly unresolved. Operational corporate-action validation remains UNKNOWN.

### C5 — SSE publication and investor-visible status

One delivery fingerprint per permitted symbol detects meaningful price/source/freshness/degraded/flag/unavailable changes independently of provider event IDs; age alone is excluded. Existing SSE event names/IDs, authentication, permissions, symbol limits and demand cleanup remain compatible. Status-only deliveries do not initiate advice/narrative work.

Provider health is additive, based only on the existing health record's schema, timestamp, state and TTL. Missing/stale/invalid health means UNKNOWN; no INFO or provider call is added. Browser connection state, provider state, each row's source/freshness and the portfolio's full mixed-quality summary are separate. Heartbeats cannot erase stale rows or imply provider health. Null-price metadata is applied while valid prior numbers are explicitly last known. Initial enrichment maps existing quote metadata without fetching an extra quote. Grouped lots, realized/unrealized/lifetime P&L, advice and trigger safeguards remain protected.

## Session and correction policy

Regular-session freshness remains 15 seconds; existing extended 60-second/closed 24-hour limits are not increased. Freshness uses the stricter event/current threshold and fails closed across local exchange date/session mismatch. Missing/future source time cannot establish freshness or newer ordering. The current observation's exchange session is exposed separately from actual source-event time.

This conservative boundary can show last known until a valid new-session event arrives. It is an honest absence-of-current-evidence state, not proof of provider disconnect. No extra REST fetch is added merely to compare every healthy stream value, and no threshold is loosened to make the UI green.

## Request and resource budgets

The following comparisons are code-derived defaults/bounds, not measured production usage. Transport retries and rate-limit backoff remain unchanged.

| Path | Before | After |
| --- | --- | --- |
| Successful primary quote through service cache | 20-second outer cache, approximately 3 initial requests/minute/symbol/process under continuous access | Same cache/coalescing; age/status recalculated on independent copies without a fetch |
| Direct unusable-snapshot polling | Raw snapshot expiry could repeat normalization failures after 2 seconds | Default 5-second negative cache, clamped to 0.1–30 seconds; direct initial-request ceiling approximately 30 to 12/minute before unchanged retries and outer-cache effects |
| Worker REST work | Recovery concurrency 2 plus independent serial shadow/reconnect paths | One shared concurrency limit 2, symbol in-flight/cooldown claims, queue 512; one shadow batch, default 50 candidates and existing 30-second cadence |
| Worker health INFO/SET | Could run per received frame | Default 5-second cadence inside the 30-second TTL, plus necessary forced lifecycle updates |
| Latest-state SSE polling | 4 latest-key GETs/symbol/iteration | Same 4; no REST comparison for every healthy stream quote |
| Provider health for SSE | No separate verified browser contract | At most one existing health-key GET per heartbeat/connection (default 15 seconds), reused and TTL-revalidated for price envelopes |
| Safe worker persistence | No timestamp guard against delayed replacement | One initial existing-state GET per accepted symbol/channel, then a cached timestamp; up to 4 guard reads for a recovery store, with no extra GET on each stream tick |

Synthetic regressions verify shared REST concurrency, deduplication/cooldown, cache hits without refetch, negative-cache recovery, health cadence and initial API enrichment without an additional quote request. Browser freshness rechecks use existing heartbeats/rendering and local elapsed time, adding no provider calls, timers or workflows.

Retries and client backoff remain bounded. Background comparison/recovery must share an aggregate executor/admission limit, not independent multiplied budgets. Local queue/task/executor bounds are synthetic acceptance targets, not sustained production capacity certification.

## Validation record

All tests use the project `.venv` Python and the inherited kernel filter in `tests/offline_investigation_runner.py`. Non-AF_UNIX socket creation and all connect syscalls are denied; Node children inherit the filter. The environment's local socket-send restriction is handled only for asyncio's AF_UNIX wake-up pipe via local `os.write`, without enabling provider/network access. Market transports, clocks, credentials, Redis and database inputs are synthetic or in-memory.

| Run | PASS | FAIL | XFAIL | SKIPPED |
| --- | ---: | ---: | ---: | ---: |
| Original 85-case baseline before implementation | 85 | 0 | 0 | 0 |
| C1 focused delayed-operation stage | 6 | 0 | 0 | 0 |
| C2 existing + retained investigation + initial new worker regressions | 58 | 0 | 0 | 0 |
| C3 provider/service stage | 93 | 0 | 0 | 0 |
| C4 resolver stage | 98 | 0 | 0 | 0 |
| C5 API stage after initial-cost metadata fix | 41 | 0 | 0 | 0 |
| Final UI review, including fallback/cost-basis/elapsed-age cases | 53 | 0 | 0 | 0 |
| Final worker review, including unsafe recovery timestamps/overflow | 70 | 0 | 0 | 0 |
| Final integrated run | **366** | **0** | **0** | **0** |

The final integrated run completed in 7.18 seconds with no deselections and both kernel/local wake-up guards PASS. Counts above describe overlapping individual runs; they are not summed. The original 85-case investigation coverage is retained within the expanded files. Characterization assertions now require corrected behavior; the original report and baseline result preserve the pre-repair reproduction evidence.

Intermediate worker validation identified two obsolete existing expectations (accepting an older recovery and awaiting REST before reconnect), and one delayed-recovery fixture needed an explicit entered/release gate. Those assertions/fixtures were corrected to exercise the approved contract; the final worker run passed all 70 cases. A closed-session fixture was corrected from premarket to closed time. The ordinary full suite was not executed; no broader or production acceptance claim is made.

Final exact command from repository root:

```sh
.venv/bin/python -B tests/offline_investigation_runner.py -q \
  --junitxml=work/c1_c5_integrated.xml \
  tests/test_market_stream.py tests/test_stream_worker_offline_investigation.py \
  tests/test_stream_worker_reliability_repairs.py \
  tests/test_market_data_providers.py tests/test_market_data_service.py \
  tests/test_price_freshness_repairs.py tests/test_portfolio_resolver_offline_investigation.py \
  tests/test_live_market.py tests/test_live_quote_reliability_repairs.py \
  tests/test_live_market_api.py tests/test_live_sse_reliability_repairs.py \
  tests/test_portfolio_ui_offline_investigation.py tests/test_live_ui.py \
  tests/test_dashboard_api.py::test_user_watchlist_exposes_quote_source_diagnostics \
  tests/test_dashboard_api.py::test_user_watchlist_returns_rows_with_quote_and_history_enrichment \
  tests/test_dashboard_api.py::test_user_watchlist_duplicate_symbol_creates_distinct_acquisition_lot \
  tests/test_dashboard_api.py::test_specific_lot_multiple_sales_and_accounting_invariants \
  tests/test_dashboard_api.py::test_closed_then_rebought_preserves_realized_and_lifetime_pnl \
  tests/test_dashboard_api.py::test_full_lot_loss_is_closed_and_historical_sale_date_is_retained \
  tests/test_dashboard_api.py::test_sale_is_lot_owner_scoped \
  tests/test_dashboard_api.py::test_user_watchlist_uses_ai_portfolio_advice_when_available \
  tests/test_dashboard_api.py::test_user_watchlist_includes_deterministic_portfolio_advice_when_available \
  tests/test_dashboard_api.py::test_user_watchlist_keeps_deterministic_portfolio_advice_when_ai_is_enabled
```

Integrity PASS: all 178 frozen reports, 151 protected tracked files (including 91 scripts and 30 workflows), and the 3 saved-monitor checksum files are unchanged. The 347-file starting snapshot contains 6 authorized source changes; generated bytecode was restored to original bytes. `_accept_event`, both parser methods, database-demand SQL, sequence-gap recovery invocation, queue 1024 and keepalive 20/20 match the starting source. No frozen evidence or Stage B disposition changed.

**NOT_EXECUTED: 8 operational categories:** installed-source/settings verification; live provider/worker continuity; live Redis performance; measured provider costs/sequence guarantees; native-browser network outage/reconnection; sustained CPU/RSS compliance; deployment/restart validation; Stage B/V4 execution/acceptance. These are intentionally withheld categories, not skipped pytest cases.

## Exact changed-file inventory

Production source (6):

```text
moneybot/services/market_stream.py
moneybot/services/market_data_providers.py
moneybot/services/market_data.py
moneybot/services/live_market.py
moneybot/api.py
moneybot/app_factory.py
```

Tests and network-isolated runner (11):

```text
tests/offline_investigation_runner.py
tests/test_market_stream.py
tests/test_stream_worker_offline_investigation.py
tests/test_stream_worker_reliability_repairs.py
tests/test_portfolio_resolver_offline_investigation.py
tests/test_price_freshness_repairs.py
tests/test_live_quote_reliability_repairs.py
tests/test_live_market_api.py
tests/test_live_sse_reliability_repairs.py
tests/test_portfolio_ui_offline_investigation.py
tests/test_live_ui.py
```

Documentation (5):

```text
docs/massive_stream_shadow_worker.md
docs/user-profile-realtime/04-realtime-stream-worker.md
docs/user-profile-realtime/05-live-ui-and-alerts.md
docs/reports/moneybot_stream_portfolio_offline_investigation_2026-10-09.md
docs/reports/moneybot_stream_portfolio_reliability_repairs_2026-10-09.md
```

## Compatibility, rollback and owner review

Review worker repairs separately from provider/resolver and API/UI changes. Preserve the existing repository, normalized quote, SSE and grouped-portfolio contracts through additive metadata where possible. Selected-price timestamp correctness can intentionally change stale labels; downstream consumers of event time need manual review before deployment. Session/date and ambiguous-correction handling is deliberately conservative.

Rollback the isolated worker change set to restore scheduling; rollback provider/resolver changes together when reverting selected-price timestamp/cache semantics; rollback API and UI together when reverting last-known/health metadata and fingerprinting. No database migration, secret change, runtime queue/keepalive relaxation, routing switch or global pricing state is introduced.

Before any separately authorized rollout, review correction/session semantics, request budgets, initial metadata, authentication/authorization and grouped-lot accounting. Operational validation requires exact deployed source/config binding, loop-lag and command durations, frame queue/transport pauses, ping/PONG timing, comparison/recovery/cache diagnostics, high-volume/thin-symbol cases and resource samples within explicit owner-approved boundaries. Existing production keepalive causation remains unverified.

**Next owner action: review the repair PR.** No merge, deployment, restart or live validation is authorized by implementation completion. C6 requires verified provider sequence guarantees and separate approval.
