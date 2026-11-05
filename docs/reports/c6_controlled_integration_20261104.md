# Controlled C6 integration into current MoneyBot

Base: `62dd5640dea5547cf88617a7860d5ac9435bf5a8`, verified through the GitHub main ref at execution. Branch: `c6/postgres-authority-controlled-integration-20261104`. This is an isolated local implementation; publication is blocked pending positive deployment isolation. No deployment or hosted initialization is authorized.

The frozen candidate archive is `1479f7c05f74a25708543a6401da8a4f591dcae279ea83a64179cc0012a577d3`, from validation freeze `64012083b864c480eda50273bda9f6f4c6543e68`. Historical run `41165580975`, attempt 1, and artifact SHA-256 `0cad5133b07fa30bbb9a41e3bb014694943c4f04a188f8c7ed87bbbd0453113b` are preserved. Fresh results for this integration are recorded separately in the accompanying evidence package.

## Integration and compatibility decisions

The frozen changes were merged against their actual original base, `726ddc185ce0201e3775bc7c1a88a546e841e64c`, into the verified current source. Current files were not replaced wholesale. Five authority modules (`stream_postgres_authority`, `stream_postgres_state`, `stream_postgres_connection`, `stream_integrity`, `stream_initialization`) and the additive proposed SQL schema retain their frozen bytes. The existing worker ordering, Q/T identity/session/freshness, memory retention, recovery and latency repairs were carried forward.

PR #669's request-owned progressive snapshot, portfolio lot/revision checks, deterministic decisions, AI enrichment, advice-only patches and browser generation/view/lot guards remain. Existing financial, notification, watchlist, chart and signal paths are preserved. `trade_signal.py`, `portfolio_enrichment.py`, AI implementation, ops modules, workflows and Render configuration remain at the current base. No Alpha Atlas scoring change was made.

Deliberate integration changes beyond the frozen code:

1. `create_stream_state` defaults to a nonpublishing inactive authority, never legacy Redis authority. Explicit `legacy`/invalid modes reject. A worker requires `postgres_option_a`, a Redis cache configuration, a validated selection file and an independent `MASSIVE_STREAM_PROCESS_ROLE=writer` setting. Enablement or shadow mode alone cannot grant authority.
2. Factory calls from web/app startup return `ReaderAuthorityState`, even inside a writer-configured process. It delegates reads and demand only, rejecting publication, stale marking, reconciliation and raw-client access. This limits application interfaces; it is not a database ACL or proof against hostile code possessing database credentials.
3. The worker calls `create_stream_state(..., writer=True)` and performs authority preflight before creating provider clients or the application. Invalid/incomplete selection/root evidence fails closed before ingestion.
4. Independent `MarketDataService.get_quote` values retain numeric/provider availability for accounting and analysis, but explicitly carry UNKNOWN admission and degraded quality. `live_data_available` remains the pre-existing provider-availability field; it is not an authority claim. The protected API/SSE resolver is the authoritative price path.
5. A REST fallback in an authority-required resolver is stale/degraded/UNKNOWN, even if recent. Missing authority cannot silently restore Redis-only or REST LIVE. Initial portfolio snapshots preserve the frozen conservative behavior: independent values remain degraded/UNKNOWN until the separate confirmed API/SSE path supplies current quality. Initial AI advice never confirms storage.

New activation and progressive-endpoint tests cover these decisions. Existing frozen test inputs and assertions were ported with the implementation; original evidence and earlier review assertions were not edited. No assertion was weakened or converted to XFAIL.

## Authority boundary

The original sequence is preserved: durable PostgreSQL reservation → bounded Redis staging → PostgreSQL conditional confirmation. Staging is not authoritative publication. A reader samples the two bounded cache slots, then rereads PostgreSQL and checks exact confirmed payload/ticket/generation. Payload substitution, a recent legacy Redis SET, missing metadata, expired selection, uncertain outcome or superseded confirmation cannot establish LIVE. Health and freshness checks add further conservative admission conditions.

Lost acknowledgments retain uncertainty and bounds. Reconciliation restages an exact authority operation and confirms conditionally; it does not infer acknowledgment from Redis visibility. Cross-channel recovery floors, retirement bounds, generation checks and stale-marking compare-and-set remain intact. Ordinary construction performs no authority-table initialization. The supplied SQL is a proposed additive schema for separately controlled execution.

Unchanged legacy repository classes remain available for historical tests and explicit offline/admin tooling; the application factory no longer selects them. An old independently running binary can still physically write legacy Redis keys. Integrated readers cannot promote those writes to authoritative LIVE, but actual production process/session exclusion remains UNKNOWN. Safe deployment still requires positive exclusion and role/session controls.

## Limits and release gates

The local test evidence does not establish hosted PostgreSQL durability, permissions, capacity or restore isolation. Coherent same-endpoint PostgreSQL rollback/PITR remains unsafe under the retained design. WAL and a normal container restart do not exclude that failure. A separately reviewed infrastructure guarantee and fail-closed recovery process are required; otherwise hosted execution remains blocked.

No source or configuration was published. Current preview/external deployment-trigger isolation has not been positively established. The old Render records are historical observations and cannot authorize a branch push. See the external deployment-isolation assessment and rollout preparation; neither executes a service change.

The independent review must assess application capability boundaries, rollout roles, exact source integration and initial-snapshot availability. This implementation intentionally favors UNKNOWN quality when confirmation is absent. It does not claim positive runtime exclusion.

`C6_DISPOSABLE_POSTGRES18_INTEGRATION = VERIFIED_OFFLINE — CLOSED`

`C6_HOSTED_OPERATIONAL_READINESS = BLOCKED`

`C6_LEGACY_RUNTIME_WRITER_EXCLUSION = UNKNOWN`

`C6_PRODUCTION_ACCEPTANCE = NOT_ESTABLISHED`

`STAGE_B = OPEN/UNKNOWN`

## Completed fresh validation

- PostgreSQL 18: 71 PASS; all four acknowledgment-fault cases PASS; normal restart/reconciliation and post-restart network isolation PASS.
- Authority: 178 PASS, zero remaining skips after Node became available.
- Frozen compatibility against integrated source: 287 PASS.
- Current worker/portfolio: 192 PASS.
- Activation/adversarial: 127 PASS.
- Broader application: 229 PASS / 25 FAIL initially. The exact unchanged base reproduced all 25 failures. Corrected only the modal harness to include the existing rendered status helper and its AI fixture to meet PR #669's two-item schema; all 25 assertions now PASS. Latest result across 254 broader identities: 254 PASS, 0 FAIL. This is an evidence union, not a fabricated full-suite rerun. All original assert ASTs are unchanged.

Suites overlap; do not add counts as unique tests. Exact commands, XML/logs, earlier incomplete/error attempts, matrix and custody hashes are in `/workspace/c6_controlled_integration_20261104/evidence/`. Kernel network denial protects offline runs. Real PG tests used UID 10001, read-only source/root, dropped capabilities and network-none disposable database namespace with no host ports. No hosted endpoints or credentials were used.

C6_CURRENT_CODEBASE_INTEGRATION = SYNTHETIC_VALIDATED for this bounded synthetic scope. Hosted gates remain blocked/unknown. Publication is prohibited pending separate positive isolation evidence. Local commit identity is recorded externally to avoid a self-referential report hash.
