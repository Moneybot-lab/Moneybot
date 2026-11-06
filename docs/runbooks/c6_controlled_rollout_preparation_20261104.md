# C6 controlled rollout and rollback preparation — not authorized for execution

This document grants no production access or action. Local implementation/testing does not authorize publication, hosted SQL, service restart or initialization.

## Prerequisites

- Independently review the exact integrated commit and its fresh synthetic results; confirm all current portfolio features and advice-only delivery boundaries.
- Positively verify branch/PR deployment isolation before publication. Verify Render auto-deploy and preview rules, GitHub triggers, webhooks and external integrations using safe metadata only. Unknown isolation means local-only.
- Obtain the bounded three-service instance/source/lifecycle attestation described by the preserved C6 review (one pass, up to 25 sanitized records). Account for worker, both web services, overlapping instances, Gunicorn children, ops children, in-flight REST/state work and old API/SSE/browser consumers. Labels alone cannot prove termination or drain.
- Resolve actual PostgreSQL/Redis resource and namespace bindings and least-privilege database principals. The reader wrapper is an application capability boundary, not a PostgreSQL permission grant. Independently establish writer exclusion, including existing sessions and external/manual/scheduled clients; prevent reactivation through old binaries, rollback and autodeploy.
- Establish primary routing, logged atomic transactions, acknowledged-write durability, persistence/eviction separation, retention/capacity and restore isolation. A coherent authority rollback must be excluded by independent operational guarantees. If unavailable, stop; neither Redis nor the same rolled-back PostgreSQL instance can supply independent proof.
- Review complete migration history, acknowledged/uncertain chronology, recovery floors, generation, capacity and source selection. Independently verify the source evidence behind all approval/exclusion/history receipt hashes. A syntactically valid receipt cannot manufacture operational truth.

## Proposed transition order

1. Secure explicit owner authorization for an exact operational transition after the prerequisites pass. Record the complete current and intended process/role roster and stop conditions.
2. Establish independently enforced exclusion of old authoritative writers and delayed operations, or positively terminate/drain them with session coverage. Contain legacy reader quality and retained clients separately. Do not silently stop a live service based on this plan.
3. Under separately authorized SQL/migration scope, review/apply the additive authority schema using an administrative principal unavailable to ordinary application readers. Initialize the exact approved selection/history/generation in PREPARING state. Retain conservative bounds for uncertain operations; never infer history solely from expiring Redis price values.
4. Resume interrupted initialization only with the identical approved plan; validate receipts and activate conditionally. Missing/mixed/partial metadata remains closed. Quarantine unexplained inconsistencies rather than resetting floors or generating replacement receipts.
5. Select the same approved authority namespace/endpoint/generation for readers and the intended writer. Reader startup must have only read/demand application capability. The intended writer additionally requires explicit process role, valid selection and successful preflight. Do not reuse production credentials in tests.
6. Verify current executable/image/source, exact confirmed price identity, reader rejection of unconfirmed bytes, quality holds, generation handoff, and old reader/session containment under a separately bounded runtime-verification authorization. Verify existing connections and rollback/resurrection controls, not only fresh requests.

## Stop and rollback rules

Stop if any source/role/instance is unaccounted for; publication triggers are uncertain; authority selection or receipts mismatch; acknowledgment outcome is unknown; chronology/floor is missing; PostgreSQL is unavailable/read-only/restored; or cache/authority payloads diverge. Preserve diagnostic evidence and UNKNOWN/degraded display. Do not repair by erasing bounds, relaxing generations, activating legacy mode or labelling recent REST data LIVE.

Rollback must preserve the independent authority and prevent older binaries from reacquiring write or LIVE-reader capability. Prefer a separately approved forward correction or quarantined/read-only service state. Reverting code to Redis-only authority is not a safe rollback. A same-endpoint database rollback/PITR requires isolation before any reader/writer resumes, an independently reviewed recovery history and a new approved transition; automatic restart must not self-certify the restored authority.

No operational sequence here is executed. The next smallest authorization is independent review of the local commit/evidence, followed by the bounded provider instance/source/lifecycle evidence collection. Deployment, migrations, credentials/grants and runtime observations require their own exact authorization.
