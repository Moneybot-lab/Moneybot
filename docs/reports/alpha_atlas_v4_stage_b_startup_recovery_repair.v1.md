# Alpha Atlas V4 Stage B startup/recovery repair v1

## Decision

The demonstrated run attempted Stage B but stopped during initialization: its immutable execution claim exists and the owner reports six completed S3 configuration operations, while the deployed source failed constructing the Massive transport before `_acquire`. The repository did not receive the raw runtime files, so the six-operation statement remains owner-reported until the new read-only inspector validates the actual operation IDs, transitions, and hash chain.

`ALPHA_ATLAS_V4_MASSIVE_API_KEY` is now owner-reported as configured. Its presence and value were not inspected here, and no secret value, fragment, or hash is persisted.

## Repair

Local document/purpose/authorization/revision/fixture/configuration/interval gates still precede credential access. The runner then checks all three explicit Stage B variables before publishing any claim, reserving an operation, or constructing a client. Phase-aware failures now preserve the last completed and failing phases, claim/acquisition state, readable Massive/S3 counts, original error code, and a separate preservation error without serializing exception text that could contain secrets. Result files are no-clobber.

The recovery inspector is read-only. It requires the pinned original authorization, bound claim, exact legacy failure, a valid 18-record S3 ledger representing six completed configuration calls, no Massive activity, and no later-phase evidence. Inconsistency fails closed.

A continuation preserves the original claim and ledger. It creates a separate atomic one-use continuation claim, rejects any prior Massive activity or uncertain/duplicate state, retains the six S3 operations against the 512 ceiling, and charges repeated configuration checks normally. It is the same single operational check, not a second allowance.

## Expired interval and remaining runtime

The original interval ended at `2026-10-08T04:00:00Z` exclusive. It is not extended or backdated. The legacy result did not record enough phase timestamps to reconstruct total active elapsed time, so the recovery document labels that value `UNKNOWN_LEGACY_NOT_RECORDED`. A replacement interval and acceptance of the disclosed remainder rule require a separate, exact continuation approval. The prepared binder is not approval and cannot execute.

## Commands

Read-only inspection on the normal worker:

```bash
python -m scripts.inspect_alpha_atlas_v4_stage_b_recovery \
  --root /var/data/moneybot-stage-b \
  --authorization /var/data/moneybot-stage-b/authorization/stage-b-operational-2026-10-06.approved.v1.json \
  --failure-report /var/data/moneybot-stage-b/stage-b-operational-2026-10-06.result.v1.json \
  --output /var/data/moneybot-stage-b/authorization/stage-b-operational-2026-10-06.recovery-inspection.v1.json
```

Network-free recovery binding after deploying the repaired revision and after choosing an explicit replacement interval:

```bash
python -m scripts.prepare_alpha_atlas_v4_stage_b_recovery \
  --inspection /var/data/moneybot-stage-b/authorization/stage-b-operational-2026-10-06.recovery-inspection.v1.json \
  --execution-valid-from <TIMEZONE_AWARE_TIMESTAMP> \
  --execution-valid-until <TIMEZONE_AWARE_TIMESTAMP> \
  --output /var/data/moneybot-stage-b/authorization/stage-b-operational-2026-10-06.recovery.prepared.v1.json
```

Output state: `PREPARED_FOR_CONTINUATION_APPROVAL_NOT_APPROVED`; `execution_gate_usable=false`. Neither command reads credentials or performs AWS/Massive requests.
