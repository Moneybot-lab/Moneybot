# Alpha Atlas V4 Stage B owner execution approval v1

**Decision:** the owner approved exactly one `OPERATIONAL_VERIFICATION_ONLY` Stage B run using the worker-generated prepared v2 authorization. The unchanged limits are 18 maximum Massive attempts, the registered primary/backup/S3 limits, and 55 minutes. The approval expressly excludes the pilot, training, scoring, and trading.

No executable authorization was fabricated in Git. The exact prepared v2 bytes and their complete-canonical pin exist only on the owner worker and were not supplied to this repository task. The new offline approval command therefore requires that independent pin, verifies the prepared document's internal hash, scope, frozen fixture, setup/config/proxy bindings, budgets, deployed revision, source hashes, and unexpired interval, and writes a new no-clobber approved file.

The approved output remains `AUTHORIZED_NOT_EXECUTED`. Existing execution-claim and persistent-ledger safeguards enforce one run and prevent a replacement document from resetting allowances.

## Network-free finalization on the normal worker

```bash
python -m scripts.approve_alpha_atlas_v4_stage_b_authorization \
  --prepared /var/data/moneybot-stage-b/authorization/stage-b-operational-2026-10-06.prepared.v2.json \
  --prepared-external-sha256 <PREPARED_V2_EXTERNAL_COMPLETE_CANONICAL_SHA256> \
  --output /var/data/moneybot-stage-b/authorization/stage-b-operational-2026-10-06.approved.v1.json
```

This command does not execute Stage B, read credentials, create clients, reserve attempts, or make provider/AWS requests. Its reported `external_complete_canonical_sha256` is the independent runner pin for the approved document. Stage B was not run in this task.
