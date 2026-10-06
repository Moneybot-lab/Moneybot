# Alpha Atlas V4 prospective pilot readiness v1

**Decision: `BLOCKED` — technical readiness is blocked by runtime storage and
cache-only feature availability.** Manifest preparation is complete but
`PROPOSED_NOT_APPROVED`; collection remains `NOT_AUTHORIZED`.

## Hosted synthetic check

Run `36457965993-1` was `PASS / SYNTHETIC_ONLY`: zero provider requests,
`real_collection_available=false`, and one synthetic assignment. Uploaded
`report.json` SHA-256 was
`7cb11d7d811bf1b3248dae0919a18620d45fc61f0059a7637c52a1957616c9b4`.
This is a workflow smoke test, separate from unit tests and from operational
readiness. Its historical artifact was not changed or rerun.

The future packaging fix now writes `report.json` (artifact-root relative) in
`SHA256SUMS`, rather than `evidence/report.json`; a focused test copies the
members as uploaded and verifies the manifest.

## Proposed static universe

**QQQ, SPY** (2 members; proposed manifest SHA-256
`a5439b1fd85f4247ffc4b21f91ba1a3176e4839fc86c459daa70d6718cf0d56b`).

The deterministic source is the repository's server-owned stream default
`MASSIVE_STREAM_SERVER_SYMBOLS=SPY,QQQ`; `render.yaml` does not override it. The
rule takes every default member, uppercases, removes duplicates/wildcards, and
sorts lexically. It does not use scores, outcomes, returns, observed capture
success, or data availability. The same expected list remains fixed for ten
sessions even if a member becomes missing, halted, delisted, or ineligible.
Typed point-in-time identifiers are unavailable and remain null; continuity is
not inferred. **Preparation is not approval: user review is pending.**

## Requirement decision table

| Requirement | Evidence source | Status at 2026-09-28 UTC | Remaining limitation | Smallest next action |
|---|---|---|---|---|
| Hosted synthetic smoke | Run `36457965993-1` report hash | VERIFIED_SMOKE_ONLY | Not runtime/cache/retention evidence | Preserve unchanged |
| Static universe | `worker_config_from_env`, `render.yaml`, exact file hashes | PREPARED_NOT_APPROVED | User has not approved QQQ/SPY; typed IDs absent | User approves or rejects manifest |
| Explicit root and 250 MiB | Repository Render blueprint and environment-name inspection | BLOCKED | No disk or `MONEYBOT_PERSISTENT_DATA_DIR`; no authenticated runtime | Runtime owner configures persistent disk and supplies read-only quota evidence |
| Research namespace writable | No authenticated application-runtime shell | UNKNOWN | Isolated runner storage is irrelevant | Run isolated probe only after real root exists |
| Atomic publish/read-back | Focused synthetic tests | SYNTHETIC_ONLY | Not observed on intended filesystem | Later run one small isolated runtime probe |
| Access, backup, restore, retention | Repository configuration | UNKNOWN | No policy, 180-day lifecycle, or restore evidence | Supply policy and bounded restore evidence |
| Cache-only feature families | Feature registry, process caches, Redis latest-state implementation | BLOCKED | Required windows and provenance do not exist in a durable no-fallback cache | Review minimal read-only saved-object integration |
| 07:45–08:30 availability | TTL/config inspection | UNKNOWN | No in-window logs; midday access would not prove readiness | Inspect premarket metadata only after interface exists |
| Timing/budgets | Bound clarification and constants | PRESERVED | Collection still unauthorized | No change |

## Runtime and storage

The blueprint deploys a Render web service, a stream worker, and a Key Value
service configured with `allkeys-lru`. It declares no persistent disk or
`MONEYBOT_PERSISTENT_DATA_DIR`. This environment has neither a Render shell nor
that configured variable, so **no runtime probe occurred**. Writing to this
container or an Actions runner would not test application storage.

Configuration was inspected; runtime behavior was not observed. Writable
namespace and quota are unknown. Atomic publication and immediate read-back are
synthetically tested only. Backup/retrieval was not tested. No 180-day retention
or cleanup guarantee is evidenced. Redis's eviction policy and 120-second
stream TTL expressly cannot provide snapshot retention. Artifact URLs do not
prove durable preservation.

## Cache-only family readiness

| Family | Existing path | Need | Status and reason |
|---|---|---|---|
| Symbol daily | Process `history_cache` (300 s); Redis latest A/AM/T/Q (120 s) | Split-consistent OHLCV/VWAP through 50 completed sessions; event and genuine receipt evidence | **UNAVAILABLE**: latest events/five-minute cache cannot provide the window; process insertion/access time is not receipt provenance; ordinary miss paths can fetch |
| SPY context | Same history/latest-state paths for SPY | 21 sessions for returns, beta, regime, volatility | **UNAVAILABLE**: latest state is insufficient and historical receipt lineage is not persisted |
| Sector context | Process `sector_cache` (3600 s) plus absent ETF history | Effective-dated sector mapping and at least 5 ETF sessions | **UNAVAILABLE**: mapping, history, and receipt evidence are not jointly cached; miss paths may fetch |
| Identity/splits lineage | Historical builder inputs; no prospective runtime cache | Point-in-time typed identity and split events known by cutoff | **UNAVAILABLE**: proposed IDs are null and no receipt-bearing prospective source exists |

The bounded inspection read repository configuration and code only. It did not
invoke normal accessors because cache misses, expiry, reconnect recovery, and
provider fallback can mutate state or issue requests. No runtime credentials
were available. No cache was warmed, refreshed, exported, or mutated. Scheduled
07:45–08:30 availability therefore remains unverified. The smallest potential
cache integration is a read-only, no-fallback view over already-received daily
objects plus immutable receipt manifests; implementing or populating it is not
authorized here.

## Preserved scope and next action

All reviewed timing and pilot limits remain unchanged: New York/XNYS timing,
07:45 binding, 07:45–08:30 assembly, inclusive 08:40 readiness, frozen 08:45
cohort, ten sessions, at most 50 tickers, 500 assignments, 250 MiB, one run, 55
minutes, and zero incremental requests. Shared-decision feasibility remains
`COMPLETE — NOT_SUPPORTED_BY_SAVED_EVIDENCE`; the regression remains
`NOT_EVALUABLE — INCOMPATIBLE_SAVED_TIMING_EVIDENCE` with zero fits; run
`35923707545-1` remains `COMPLETE — UNFAVORABLE FINDINGS`; completed timing,
identity, and historical investigations remain closed.

**Single smallest next action:** the runtime owner attaches/configures the
intended Render persistent disk with `MONEYBOT_PERSISTENT_DATA_DIR` and supplies
read-only evidence of mount, quota, access, backup, restore, cleanup, and
retention settings. Only then should the already-authorized isolated storage
probe run. This report makes no predictive claim and authorizes no collection,
training, or scoring.
