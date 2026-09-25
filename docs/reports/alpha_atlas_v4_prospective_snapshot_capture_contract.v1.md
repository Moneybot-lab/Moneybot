# Alpha Atlas V4 prospective snapshot-capture contract v1

**Status: `PREPARED_FOR_REVIEW`. Implementation, collection, training, and
scoring are `NOT_AUTHORIZED / NOT_EXECUTED`.** This is a new prospective
convention. It does not reconstruct historical evidence or change the completed
shared-decision, timing, regression, or prior-scoring conclusions.

## 1. Existing path and concrete reuse

The smallest safe implementation is a new metadata-only capture stage beside,
not inside, production decisions or the outcome-bearing training builder:

| Concern | Verified repository reuse point | What is verified vs not verified |
|---|---|---|
| Scheduler | `.github/workflows/moneybot-daily-ops.yml` uses cron, single concurrency, a 15-minute job timeout, secret validation, and a token-authenticated application endpoint. | Configuration is verified in Git. GitHub start-time punctuality, hosted duration, and deployed endpoint behavior are not demonstrated guarantees. The existing 13:00 UTC schedule is not the proposed schedule and must not be repurposed silently. |
| Run orchestration | `scripts/run_daily_ops.py:build_daily_ops_commands` sequences bounded scripts and records per-command logs under the runtime directory. | Reusable orchestration pattern only; current commands include outcome/calibration work and must not be invoked by snapshot capture. |
| Calendar | `moneybot.services.market_data_providers.ExchangeCalendar` supplies `America/New_York`, holidays, early closes, session open/close, and previous/next sessions. | Rule implementation exists; extraordinary future closures still require a versioned calendar update rather than inference. |
| Source receipt/cache | `NormalizedQuote.received_timestamp`, `ProviderResult.received_timestamp`, `MarketDataService` TTL caches, and `market_stream` latest-state/Redis implementations expose receipt or cache state. | Receipt fields are concrete. In-memory TTL caches (20-second quotes, 300-second history, 600-second company snapshots, 3,600-second sector cache) are not durable provenance and cache completion timing is not currently captured as a cohort snapshot. |
| Daily-source normalization | `build_massive_decision_training_rows._normalize_market_row`, `_bar_availability`, and `_feature_index_for_decision` distinguish source dates, explicit availability, cutoff, and staleness. `DEFAULT_MAX_STALENESS_SESSIONS` is 3. | These semantics and default exist. The current fallback from missing availability to official close is not receipt proof for this contract and must yield unknown availability unless separate receipt evidence exists. |
| Feature assembly | `build_training_rows_from_raw_market` and its feature cache compute the existing V4 families; `emit_phase0_evidence_bundle` hashes source rows and records provenance. | Reuse feature definitions and pure calculations only. The function also constructs future labels and requires forward windows, so calling it unchanged would violate outcome isolation. Extract a label-free adapter; do not run the training-row builder. |
| Universe input | `_market_load_window` derives symbols from saved decision events and adds SPY/sector benchmarks; `DecisionLogger` appends `decision_events.jsonl`. | This is observed-event coverage, not a complete prospective universe. It cannot define the pilot universe retrospectively. A reviewed static pilot manifest is a prerequisite. |
| Persistence | `runtime_paths.resolve_runtime_dir` supports `MONEYBOT_PERSISTENT_DATA_DIR`; `is_durable_runtime_configured` distinguishes explicit configuration. `decision_log_export.commit_export` demonstrates temp-write, flush/fsync, `os.replace`, and directory fsync. | Atomic-write primitives are reusable. The fallback `data/` path is explicitly best-effort/ephemeral, and no 180-day retention, quota, backup, or read-back guarantee is presently verified. |
| Artifact retention | Several research workflows set `retention-days: 30`; Track B upload does not state a retention duration. | Actions is useful transport, not established durable storage. An artifact URL or successful upload is not preservation proof. |

Required instrumentation is therefore limited to: a new runtime path helper, a
label-free adapter around existing feature calculations, immutable universe and
session manifests, receipt/assembly/persistence clocks, atomic snapshot writes,
reconciliation, and read-back verification. No provider client, prediction
endpoint, production decision path, or training function needs modification.

## 2. Frozen operational schedule

For each official XNYS session, freeze the universe at **07:45
America/New_York**, assemble from **07:45 through 08:30**, require complete
persistence by the **08:40 snapshot deadline**, and use **08:45** as the fixed
scheduled decision boundary. Intended entry is that session's official 09:30
open; the five-session horizon exits at the official close of the fifth eligible
session, counting entry as session one.

The named IANA timezone controls DST. A future GitHub workflow may declare both
11:45 and 12:45 UTC cron candidates, but an application guard must execute only
at 07:45 New York time, on an eligible session, once per cohort/session ID.
Holidays and unscheduled closures create no cohort. Early closes use the
calendar's real close if they are the fifth session. Delayed starts do not move
the window or boundary. Completion after 08:40 is late; readiness after 08:45
is after-boundary. Neither may be backdated. Ambiguous clock ordering is
`UNKNOWN_CLOCK`.

This window intentionally leaves at least five minutes between the persistence
deadline and decision boundary and uses prior completed-session data. It does
**not** assume an official close is provider-ready at the closing bell; actual
receipt evidence remains mandatory.

## 3. Immutable snapshot and timing semantics

The companion JSON Schema defines the record. Every expected ticker carries a
cohort/session ID, typed point-in-time identifier when available, universe
manifest identity/hash, attempt and snapshot IDs, ordered feature-vector hash,
feature/provenance versions, code/configuration hashes, per-family source object
and content hashes, missingness, errors, freshness, lateness, and eligibility
reason codes.

`source_event_at` is when an upstream event occurred. `source_received_at` is
when this collector actually received/read it and includes an evidence kind.
`assembly_started_at` and `assembly_completed_at` bracket computation.
`persistence_completed_at` follows flush, file fsync, atomic rename, and
directory fsync. `ready_for_use_at` is durable local completion, or a later
required replica verification. `feature_cutoff_at` is fixed at 08:40;
`scheduled_decision_at` is fixed at 08:45. None substitutes for another. Hashes
establish content identity, never independent historical timing proof.

Outcomes, future prices, predictions, scores, ranks, and selections are forbidden
inside the snapshot. The schema's example is synthetic and is not evidence that
capture has occurred.

## 4. Universe, eligibility, duplicates, and cohorts

Before pilot authorization, review and commit one versioned manifest of at most
50 lexically ordered tickers already covered by the existing subscription. Keep
it unchanged for all ten sessions. Every member is written to the session
manifest before assembly; missing, halted, delisted, ambiguous-identity, and
failed members remain expected.

For each ticker/cohort choose the latest `COMPLETE`, durably ready snapshot with
`ready_for_use_at <= scheduled_decision_at` and cutoff no later than 08:40;
ties choose the lexically greatest `snapshot_id`. Same-content retries remain
append-only attempt references. Corrections create a new snapshot with
`supersedes_snapshot_id` and cannot rewrite the original or become eligible
unless independently ready by the original boundary. Never average information
boundaries or replace a miss with a later snapshot.

Exactly one final disposition—`ELIGIBLE`, `MISSING`, `LATE`, `STALE`, `INVALID`,
or `UNKNOWN`—must reconcile every expected member; overlapping diagnostics are
separate. The existing supported daily-family freshness limit is three XNYS
sessions. A family without a supported limit is `UNKNOWN_FRESHNESS`, not
eligible. Report zero, one, two-to-four, exactly five, and more-than-five cohort
sizes. With five or fewer eligible tickers, `min(5, eligible)` is the entire
eligible baseline and cannot demonstrate selection differentiation.

## 5. Separate outcome attachment

A later, separately authorized process may join by immutable `snapshot_id`,
cohort/session ID, and typed security identity. It must use the same-session
regular open strictly after the decision and the fifth eligible-session official
close. Missing prices, corporate actions, halts/delistings, identity uncertainty,
terminal events, and unverified availability remain explicit failures. They do
not change original membership or snapshot eligibility. Replacement prices,
inferred proceeds, forward fill, and dropping failures are forbidden.

This contract authorizes no outcome retrieval, model fitting, scoring, selection,
portfolio calculation, or predictive claim.

## 6. Storage and finite pilot

Proposed storage is
`$MONEYBOT_PERSISTENT_DATA_DIR/alpha_atlas_v4/prospective_snapshots/v1/<cohort_session_id>`.
Writes are same-filesystem temp files followed by flush/fsync, `os.replace`, and
directory fsync. Each session contains the frozen universe, append-only attempts,
immutable snapshots, dispositions, a manifest, byte sizes, and `SHA256SUMS`.
Secrets and private request bodies are excluded. Read back and hash every listed
file within 24 hours.

The pilot is capped at **10 sessions, 50 expected tickers/session, 500 total
assignments, 45 collection minutes and 55 hard runtime minutes/session, one
concurrent run, and 250 MiB uncompressed total**. Incremental provider-request
budget is **zero**: only data already supplied by the existing authorized path
may be consumed. A cache miss is evidence of missing/unknown availability, not
permission to query.

Minimum retention is proposed as 180 days after session ten. This is an
**unresolved prerequisite**: before implementation/collection authorization, an
operator must verify that the configured persistent mount provides sufficient
quota, lifecycle, access control, backup/retrieval, and 180-day retention. If
`is_durable_runtime_configured()` is false or that verification is absent, stop.
Also stop on universe/hash drift, any incremental request requirement, budget
exhaustion, atomic-write/read-back failure, unknown calendar/clock ordering, or
forbidden/secret fields.

## 7. Exact implementation plan (not authorized here)

1. **`moneybot/services/runtime_paths.py`** — add
   `prospective_snapshot_root()` that requires explicit durable configuration;
   never fall back to `data/`.
2. **New `moneybot/services/alpha_atlas_v4_prospective_snapshot.py`** — implement
   canonical hashes/IDs, timestamp validation, append-only attempts, atomic
   persistence, deterministic selection, disposition reconciliation, budgets,
   and forbidden-field validation.
3. **`scripts/build_massive_decision_training_rows.py`** — extract/reuse a pure,
   label-free feature-family assembler from `build_training_rows_from_raw_market`;
   preserve `_feature_index_for_decision` provenance but reject official-close
   fallback as receipt proof. Do not alter current builder behavior in place.
4. **New `scripts/capture_alpha_atlas_v4_prospective_snapshots.py`** — load the
   pre-reviewed universe, consume existing cached/source objects without network
   fallback, capture wall/monotonic times, write session evidence, and exit
   nonzero on stop conditions.
5. **New workflow, separate from `moneybot-daily-ops.yml`** — two DST-covering
   cron candidates plus strict New York/session/idempotency guard, 55-minute
   timeout, concurrency one, no training steps, and compact failure reporting.
6. **Pilot configuration** — add only the reviewed universe manifest and hashes;
   no credentials. Large snapshots remain under durable runtime storage and
   outside Git.

Focused synthetic tests must cover deadline equality versus late completion;
source event versus receipt/ready time; missing/stale/unknown families; retries,
duplicates, corrections, and partial atomic writes; EST/EDT, holidays, early
closes, delayed jobs, and clock uncertainty; universe/disposition reconciliation;
deterministic selection; budget stops; forbidden outcomes; no provider calls;
and unchanged production/daily-ops behavior.

## Review outcome and next authorization

This package is implementation-ready but not implemented. The precise next step
is a separate **implementation-and-synthetic-testing authorization**, contingent
on (1) approval of the static <=50-ticker universe manifest and (2) verified
180-day durable-storage lifecycle/quota/retrieval. That authorization must still
exclude collection and provider requests. A later authorization would be needed
to run the finite pilot.

Nothing here establishes predictive advantage or closes historical coverage,
terminal valuation, net-return validation, holdout, promotion, or production
gates.
