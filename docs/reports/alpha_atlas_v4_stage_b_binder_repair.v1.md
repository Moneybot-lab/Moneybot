# Alpha Atlas V4 Stage B post-deployment binder repair v1

## Defect and correction

The prior binder omitted `setup_package_sha256`. The live consumer requires that field to equal `SETUP_PACKAGE_FILE_SHA256`; consequently, approving the old prepared record would still have failed with `AUTHORIZATION_BINDING_MISMATCH`.

The repaired binder now verifies the exact setup-package file bytes against registered file SHA-256 `3524360467fbd47a3eeb7dfbd4252d82dfda9b407354a2807268d6acb5ba5a79` and emits that value as `setup_package_sha256`. It also checks every authorization field consumed by live preflight: approval state, purpose, fixture, setup, configuration, proxy, deployed revision and source hashes, budgets, historical scope, and the 55-minute runtime.

The existing runtime file `/var/data/moneybot-stage-b/authorization/stage-b-operational-2026-10-06.prepared.json` was neither read nor changed. The observed deployed revision `e45184577233a2f5fdf4f825d50f58c8a445e407` is recorded as context only; the repaired binder must run on the newly deployed checkout and binds its actually observed `HEAD`.

## Explicit interval and output safety

Preparation now requires `--execution-valid-from` and `--execution-valid-until`. Both must be timezone-aware, normalize to UTC, form a positive interval, and end after preparation time. The binder does not choose, derive, roll, or extend an interval. The runner rejects execution before `valid_from` and at or after `valid_until` before credentials or clients.

The binder rejects an existing output destination and emits only `PREPARED_FOR_EXECUTION_APPROVAL_NOT_APPROVED`, `execution_gate_usable=false`, and `owner_approval=NOT_GIVEN_FOR_EXECUTION`.

## Approval-state schema

The only live approved-but-not-executed state accepted by the consumer is:

- `status: APPROVED`
- `mode: LIVE`
- `execution_gate_usable: true`
- `owner_approval: APPROVED_FOR_SINGLE_STAGE_B_OPERATIONAL_VERIFICATION`
- `stage_b_execution: AUTHORIZED_NOT_EXECUTED`
- `owner_accepts_sector_proxy_clarification: true`

The preparation CLI never emits that state. Test-only approved records remain isolated in offline tests.

## Hash semantics

1. **Internal `content_sha256` (A):** SHA-256 of canonical JSON after removing `content_sha256`.
2. **External approved-authorization pin (B):** SHA-256 of the complete canonical authorization including `content_sha256`. This is the value compared by `StageBRunner`.
3. **File-byte SHA-256 (C):** SHA-256 of the exact saved bytes. Formatting changes C but not A or B.

Approval changes the document; A, B, and C must therefore be recomputed, and the new B must be independently pinned. The network-free inspector reports all three without modifying, approving, or executing the authorization.

## Offline regression result

The focused regression invoked the real binder, verified its actual output remained unapproved and failed live preflight, then created a clearly test-only approved copy, recomputed A and B with repository canonical serialization, and passed that copy through `StageBRunner`'s real live-mode preflight. No validation function was mocked. Storage/resource probing was isolated; no credentials, clients, execution claim, ledger reservation, provider call, or AWS call occurred.

## Post-deploy preparation command

Use a **new filename** on the actual worker after deploying the repaired revision, with an owner-selected future interval:

```text
python -m scripts.prepare_alpha_atlas_v4_stage_b_post_deploy_authorization --deployed-commit "$(git rev-parse HEAD)" --execution-valid-from <TIMEZONE_AWARE_TIMESTAMP> --execution-valid-until <TIMEZONE_AWARE_TIMESTAMP> --output /var/data/moneybot-stage-b/authorization/stage-b-operational-2026-10-06.prepared.v2.json
```

This prepares only an unapproved record. It does not execute Stage B.
