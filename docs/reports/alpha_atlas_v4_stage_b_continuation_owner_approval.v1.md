# Alpha Atlas V4 Stage B continuation owner approval v1

The owner approved one continuation of the same Stage B operational verification and expressly accepted that the failed startup's elapsed time is unknown. The continuation may use at most 55 minutes and must end before `2026-10-09T04:00:00Z`, which is 10:00 p.m. MDT in Utah on October 8, 2026.

The approval preserves the six already-consumed S3 operations, the 512-operation ceiling, 5 expected/18 maximum Massive attempts, persistent 3,789-attempt cap, and all evidence-byte limits. It does not create a second run or authorize the pilot, training, scoring, or trading.

No executable approval artifact is fabricated in Git. The actual deployed-revision-bound prepared recovery can only exist after the repaired revision is deployed and the preserved runtime evidence passes the read-only inspector. The offline finalizer requires the independently reported complete-canonical pin of those exact prepared bytes, validates the approved cutoff and scope, and writes a new no-clobber artifact in `AUTHORIZED_CONTINUATION_NOT_EXECUTED` state.

```bash
python -m scripts.approve_alpha_atlas_v4_stage_b_recovery \
  --prepared-recovery /var/data/moneybot-stage-b/authorization/stage-b-operational-2026-10-06.recovery.prepared.v1.json \
  --prepared-recovery-sha256 <PREPARED_RECOVERY_EXTERNAL_COMPLETE_CANONICAL_SHA256> \
  --output /var/data/moneybot-stage-b/authorization/stage-b-operational-2026-10-06.recovery.approved.v1.json
```

This command is network-free and does not execute the continuation or read credentials.
