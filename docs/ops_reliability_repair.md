# Daily Ops and Weekly Refresh reliability repair

## Root cause from code inspection and offline reproduction

The previous JSONL reader retained raw tail strings while decoding complete nested decisions. During scan expansion, the caller also retained its prior decoded batch until the next assignment completed. Calibration rebuilt its provider/cache and repeated downloads on each expansion. The synthetic benchmarks below reproduce this avoidable memory growth without production data.

Daily Ops stopped at the first failed child; Weekly Refresh's checked subprocess propagated that failure. HTTP retry loops could repeat expensive work after HTTP 500 responses. Raw report bodies could enter operational logs, and artifact-existence fields did not establish freshness.

This public runbook documents implementation and synthetic validation only. Production incident identifiers, service configuration, observed live metrics, and model identity are retained in the owner's separate completion report.

## Changed files

| File | Purpose / behavior |
| --- | --- |
| `moneybot/services/decision_log.py` | Select newest physical lines using byte offsets, then decode one record at a time. Preserve blank/malformed-line counting, order, and the existing list-returning API for other callers. |
| `scripts/day13_calibration_report.py` | Discard snapshots immediately after extracting calibration/segmentation fields; release old compact batches; keep one provider and scalar lookup cache across expansion; plan lazy eligible-only history; reject nonfinite probabilities; avoid duplicated fitted-row dictionaries; emit bounded operational metrics and atomically publish completed reports. Provider exceptions produce a separate `.failed.json` diagnostic and nonzero exit, preserving the prior canonical report. |
| `moneybot/services/outcome_tracking.py` | Bound cached history to 256 entries and 100,000 close points; keep only earliest preload dates; lazily extend ranges for older evidence. Preserve invalid close positions so missing data cannot shift a horizon; reject invalid/nonpositive prices instead of deriving observations from them. |
| `moneybot/services/ops_runtime.py` | Atomic replacement, peak RSS, bounded subprocess output, deadlines, process-group cleanup, and safe status loading. |
| `moneybot/services/ops_supervisor.py` | A small standard-library process owns the outer deadline even if the HTTP worker dies. Children retain the execution-lock descriptor. |
| `moneybot/services/ops_execution.py` | Shared cross-worker `flock`, persistent seven-day SQLite idempotency receipts, truthful exit/status handling and stale-status rejection. |
| `moneybot/api.py` | Keep token authentication; require `Idempotency-Key`; use shared execution service for both endpoints; return structured stage/freshness data and bounded, non-private diagnostics. |
| `scripts/run_daily_ops.py` | Explicit stage results, dependency-aware skips, continuation of independent stages, atomic status, failure/signal identification, and no report-body logging. |
| `scripts/run_weekly_model_refresh.py` | Reuse its single baseline refresh; run one Daily Ops bundle; request up to 200 visible outcome rows rather than the previous 20-row ceiling; require fresh prerequisites and historical validation; expose nested failed stages and blocked validation gates. |
| `scripts/day7_decision_log_summary.py` | Atomic summary publication. |
| `scripts/day12_materialize_outcomes.py` | Atomic outcome snapshot publication. |
| `scripts/day13_recalibrate.py` | Atomic proposal publication; no change to canary, rollout, or activation logic. |
| `scripts/autofill_daily_report.py` | Atomic markdown publication. |
| `scripts/page6_historical_validation_report.py` | Atomic validation-report publication; no change to validation mathematics or promotion gates. |
| `.github/workflows/moneybot-daily-ops.yml` | Shared workflow concurrency group, bounded curl/body output, durable idempotency key, explicit transport/application errors, required successful response body, and no automatic POST retries. Existing schedule and dispatch retained. |
| `.github/workflows/moneybot-weekly-refresh.yml` | Same diagnostics and safeguards, with weekly-specific deadline. Existing schedule and dispatch retained. |
| `tests/test_dashboard_api.py` | Authentication, new execution seam, required key, timeout/failure/freshness contracts, and privacy assertions. Four previously duplicate timeout function names now all execute. |
| `tests/test_run_daily_ops_script.py` | Both workflow shell scripts exercised with fake curl; success, HTTP 500, transport failure, bounded diagnostics, privacy, and no retry assertions. |
| `tests/test_ops_reliability.py` | Large-log, math/segmentation, cache/provider, invalid-price, artifact, subprocess, worker-death, concurrency, receipt, recovery, and orchestration regressions. |
| `scripts/benchmark_calibration_memory.py` | Reproducible synthetic offline memory benchmark; temporary data only. |
| `docs/ops_reliability_repair.md` | Evidence, contracts, measurements, validation and deployment runbook. |

## Calibration memory and integrity

The initial 5,000-line daily scan, doubling sequence, 50,000-line default cap, chronological order, and stop-on-first-window-with-fitted-rows behavior remain intact. The cap is not reduced: sparse valid evidence can occur behind many quote-only decisions. The reader's cap counts physical lines, including malformed/blank lines, exactly as before. Each expansion still scans the file, trading bounded memory for modest additional I/O; history/provider reconstruction and repeated identical outcome lookups are removed.

Only required metadata and derived signal-completeness values survive JSON decoding. History is fetched only for mature, correctly targeted, probability-bearing, non-portfolio-quote-only events. Planned symbol ranges retain the original earliest-event-through-now history semantics. Older expanded ranges invalidate the corresponding history entry, while already evaluated scalar outcomes remain reusable. The history budget bounds retained entries/points; the scalar cache holds at most 50,000 results. One unusually large record or provider response still determines transient allocation size.

Brier/Platt calculations, forecast-horizon isolation, segmented diagnostics, and the prior finite-probability clipping behavior are preserved. Nonfinite probabilities and invalid prices are excluded. Missing prices are never converted to zero or moved to a different trading session. No model promotion/activation code, active model artifact, portfolio/profile, secret, database, or production decision log was changed.

Linux process peak RSS (`getrusage(RUSAGE_SELF)`), Python 3.11, deterministic fixtures, no live market data:

| Case | JSONL size | Peak RSS | Stage/read time |
| --- | ---: | ---: | ---: |
| Legacy reader, 20,000 events | 166,660,000 B | 620.66 MiB | 1.829 s |
| Repaired full stage, 20,000 events, no eligible rows | 166,660,000 B | 100.77 MiB | 2.733 s |
| Legacy reader, 50,000 events | 416,650,000 B | 1,325.12 MiB | 3.328 s |
| Repaired full stage, 50,000 events, no eligible rows | 416,650,000 B | 117.44 MiB | 4.913 s |
| Repaired full stage, 50,000 events, 12 mature rows | 416,649,988 B | 119.45 MiB | 4.559 s |
| Same sparse case with 512 MiB virtual-address limit and BLAS threads=1 | 416,649,988 B | 118.34 MiB | 5.075 s |

Every event contains 8 KiB of unused nested snapshot text. The sparse case makes one mocked provider request, retains 20 close points, fits exactly 12 observations and reports insufficient evidence. Legacy measurements reproduce the previous reader/expansion allocation pattern with the same imports, not the entire legacy calibration computation. The repaired measurement includes the complete stage. The memory-limited case used `ulimit -v 524288`, `OPENBLAS_NUM_THREADS=1`, and `OMP_NUM_THREADS=1`; these are benchmark conditions, not production configuration changes.

This validates substantial standalone-stage headroom for the reported failure pattern. It does **not** establish a maximum for arbitrary real records, unique-symbol counts, provider response sizes, all-eligible fitting workloads, Gunicorn workers, or aggregate live service memory. Production verification remains required. Large numbers of distinct eligible symbols may cause cache eviction/refetch and deadline exhaustion; this fails explicitly rather than silently reducing evidence.

Reproduce:

```bash
python scripts/benchmark_calibration_memory.py --events 20000 --legacy-reader
python scripts/benchmark_calibration_memory.py --events 50000 --legacy-reader
python scripts/benchmark_calibration_memory.py --events 20000
python scripts/benchmark_calibration_memory.py --events 50000
python scripts/benchmark_calibration_memory.py --events 50000 --mature-rows 12
```

## Execution and artifact contracts

All Daily Ops stages are operationally required. Summary, outcome materialization, and calibration are independent; a failed stage does not prevent later independent stages. Recalibration requires the current calibration report. Markdown assembly requires all of its current input reports. Failure, missing/stale output, or skipped dependencies produce a nonzero Daily Ops exit. A completed calibration diagnostic with fewer than 30 valid observations is `insufficient_data`, not a fabricated fitted success; Daily Ops may complete successfully in that diagnostic state. Existing recalibration minimum-row/canary safeguards still apply.

Weekly runs the baseline once, Daily Ops once, then validation only when the current bundle succeeded. It reads the validation report's actual minimum-row and promotion-gate outcomes. Insufficient evidence or blocked validation produces a nonzero weekly exit, even when report generation itself succeeded. `artifact_status=fresh` is separate from validation success. Thus weekly can remain red for legitimate evidence, quality, or required review gates; this repair does not waive them to make CI green. No model is automatically promoted.

Every report is written to a same-directory temporary file, flushed/fsynced, then atomically replaced. Stage status records distinguish success, failure, skipped, insufficient data, stale artifact, and overall degraded execution. Successful independent artifacts remain available. Canonical failed calibration output is preserved; `.failed.json` carries the failed attempt's diagnostics. A preserved file's existence never means it was refreshed. Replayed receipts explicitly report no newly refreshed calibration/plan artifact.

Both protected APIs now require an `Idempotency-Key` of 1–200 characters, in addition to `X-Daily-Ops-Token`. Their responses retain `data.success`, exit code fields, command/path metadata, and request ID, and add stage/freshness/status information. Raw child output is deliberately absent. A shared disk-backed advisory lock covers daily and weekly across threads and workers. A different request while busy receives 409. Exact retries return stored terminal results, including failures, for seven days. A worker interrupted after recording a request leaves a 409 interrupted receipt: the same key is not executed again. Use a new, intentional dispatch ID to recover after diagnosis. After seven days keys may execute anew; callers must use unique IDs for intended new runs.

The lock descriptor is inherited by supervisor and children so abrupt HTTP-worker death does not release execution exclusivity prematurely. The independent supervisor still enforces its deadline. Standard output/error are continuously drained with at most 256 KiB retained per stream; only allowlisted numeric stage metrics and structured results enter operational logs/API responses.

| Boundary | Daily | Weekly |
| --- | ---: | ---: |
| Stage subprocess | 600 s | Daily wrapper 660 s; other stages 660 s |
| API subprocess | 660 s | 1,020 s |
| Independent orphan supervisor | API limit + 1 s | API limit + 1 s |
| curl request | 720 s | 1,080 s |
| GitHub job | 15 min | 20 min |

Schedules are unchanged: daily 13:00 UTC, weekly Sunday 12:00 UTC (7 AM / 6 AM MDT on the investigation date). Both workflows share `moneybot-production-ops` concurrency. There are no automatic POST retries: neither HTTP 500 nor a lost response proves that a request is safe to rerun. Actions reruns reuse the run ID and receipt; a fresh workflow dispatch intentionally creates a new request. Curl bodies are limited to 64 KiB; displayed responses contain only selected operational fields, redact configured secret values, and are capped at 4 KiB. HTTP 2xx with `success=false` is a failure.

## Validation

The focused regression suites exercise empty/20,000/50,000 logs, newest physical-line semantics, sparse/mixed/probability-null/immature events, original calibration mathematics and segments, horizon isolation, Massive-first/fallback diagnostics, cache bounds/reuse/older-range extension, invalid prices, real SIGKILL, nonzero exits, output bounds, timeout descendant cleanup, HTTP-worker death with retained lock/deadline, cross-workflow concurrency, duplicate/failure receipts, interrupted receipts, recovery under a new ID, stale status, partial stages, atomic artifact preservation, validation gates, API auth, and both shell workflows.

Validation completed with **224 distinct focused tests passing** across the following runs:

- `pytest -q tests/test_run_daily_ops_script.py`: 15 passed (0.51 s).
- `pytest -q tests/test_dashboard_api.py`: 100 passed (8.21 s) before the last storage-error regression was added.
- Final `pytest -q tests/test_dashboard_api.py tests/test_ops_reliability.py`: 131 passed (14.82 s), comprising 101 API tests and 30 reliability tests.
- The related suite covering calibration, outcome/history providers, decision logs, weekly wrapper, materialization, historical validation/API, recalibration, baseline refresh, production servability, runtime paths and report assembly: 106 passed (8.29 s), including 29 reliability tests already counted above; the other 77 are distinct.
- A final preload-eviction regression additionally verifies that eviction preserves the original full history range; the outcome/reliability suites passed together (48 tests). This adds one distinct regression to the counts above.
- All 18 changed/new Python files parsed successfully; `git diff --check` passed.

 No repository lint, formatting, or type-check command/configuration was found (no pyproject/setup/tox/Makefile checks and none in the workflow suite); no tool-specific checks are claimed. Changed Python syntax compilation and `git diff --check` are performed. Tests use deterministic fixtures and market-data mocks, without production credentials.

## Production rollout, verification and rollback

No merge, deploy, production workflow rerun, service-plan change, cron reactivation, or paid service creation is authorized by this repair. Confirm deployment and preview policy before merging; merging may itself initiate a deployment.

After explicit deployment approval:

1. Review this PR and its gate/API contract changes. Confirm all callers send an idempotency key; deploy the API and workflow changes together. Preserve the existing runtime directory, reports, currently promoted model, production logs and data.
2. Confirm the existing disk is writable and the service remains a single instance. `flock` is a host/shared-disk boundary, not a distributed multi-host lock. Do not scale to separate disks without replacing it with a durable shared execution fence.
3. At low traffic, explicitly authorize one Daily Ops dispatch. Observe RSS for the **whole service**, stage metrics, fitted/eligible/scanned counts, provider requests and cache points. Check fresh reports, proposal-only/canary behavior, and unchanged active model identity. Do not consider a replayed receipt a fresh execution.
4. Explicitly authorize one Weekly Refresh dispatch only after the daily result is understood. Verify one baseline refresh, one daily bundle, current outcome input, validation-gate results, and truthful non-success for insufficient/blocked validation. Review the relevant evidence/review gates rather than bypassing them.
5. Confirm both next scheduled runs retain the existing schedules, logs identify any failed stage, and aggregate memory remains comfortably below the configured service memory limit under normal web traffic. Stop additional manual dispatches if memory approaches the limit.

Rollback: with approval, revert this focused code/workflow commit and deploy the prior known code revision. Preserve runtime artifacts, `.failed.json`, receipts, locks, decision logs and active models; do not restore or reset the promoted model. Let any existing supervisor finish or reach its deadline before a replacement run. Reverting removes the new reliability safeguards and can reintroduce the original memory/retry failure, so diagnose before retrying.

Longer term: move execution to a durable queue/worker or dedicated job with job IDs and status polling; use a durable distributed lock/fencing scheme and explicit memory budget. This separates expensive Python work from Gunicorn and HTTP timeouts. It needs its own reviewed deployment and cost/data-access plan. No new service or infrastructure change was made here.
