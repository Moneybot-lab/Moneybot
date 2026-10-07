# Alpha Atlas V4 Stage B owner-infrastructure binding v1

**Status:** `OWNER CONFIGURATION BOUND OFFLINE / EXPECTED OWNER UNRESOLVED / LIVE NOT AUTHORIZED`

## Bound configuration

The versioned configuration is `docs/reports/alpha_atlas_v4_stage_b_runtime_config.v1.json` (file SHA-256 `860e916a0d2152c603f26f0f0438308539aa4b7ede704639bd731755c9ff9463`; content SHA-256 `4ac339c8caa01c625b54bb53a51b7fef7f294082a2cbe1c52bf9814c7bf03a33`). It binds:

- Render service `moneybot-market-stream`, 3 GB disk mounted `/var/data`, and root `/var/data/moneybot-stage-b`.
- S3 bucket `moneybot-alpha-atlas-backup-20261006`, region `us-east-1`, prefix `stage-b/`.
- Credential-variable names `MONEYBOT_STAGE_B_AWS_ACCESS_KEY_ID` and `MONEYBOT_STAGE_B_AWS_SECRET_ACCESS_KEY`—never their values.
- Owner-reported Versioning, SSE-S3, four public-access blocks, and Governance Object Lock with fixed 180-day default retention.

No verified 12-digit account owner was found in repository evidence. The configuration therefore stores `null` and operational validation stops with `S3_EXPECTED_OWNER_UNRESOLVED`; it accepts neither a wildcard nor a placeholder.

After obtaining the verified ID, create a new uncommitted bound configuration and hashes offline:

```bash
python -m scripts.bind_alpha_atlas_v4_stage_b_owner \
  --config docs/reports/alpha_atlas_v4_stage_b_runtime_config.v1.json \
  --owner-id <verified-12-digit-id> \
  --output /secure/review/alpha_atlas_v4_stage_b_runtime_config.owner-bound.json
```

Review the printed content/file hashes and bind the new content hash in a future execution authorization. This command performs no network or credential lookup.

## IAM/API reconciliation

The installed policy is sufficient for the implemented calls. `HeadBucket` maps to `s3:ListBucket`; configuration calls map to the five supplied bucket-read actions. Object upload uses `s3:PutObject` plus `s3:PutObjectRetention`; exact-version `HeadObject`/read-back/restore use `s3:GetObjectVersion` (with `s3:GetObject` also supplied); explicit retention verification uses `s3:GetObjectRetention`. Every key is validated under `stage-b/`.

No delete, bucket mutation, broad S3, or `s3:BypassGovernanceRetention` permission is required. The same restricted identity currently performs upload, read-back, and restore; separate operational roles have **not** been established.

## Complete backup accounting

A successful expected run freezes 14 objects: ten response/receipt objects, one Massive ledger, one handoff, one outcome, and one inventory. The maximum small-response path freezes 40: 36 attempt body/receipt objects for 18 HTTP attempts plus ledger, handoff, outcome, and inventory. One non-recursive completion manifest is then uploaded and verified. Primary storage therefore has 15 expected or 41 maximum objects; if completion backup itself fails, one append-only `PARTIAL_BACKUP_FAILED` outcome makes 42 local objects and is not falsely claimed by the earlier frozen inventory.

Each frozen object consumes five S3 operations: upload, version/encryption head, retention read, SHA-256 read-back, and exact-version restore. Six configuration calls and four completion-upload verification calls produce **80 expected** and **210 maximum** operations, below the unchanged 512 ceiling. Failed calls consume their reservation and stop; uncertain operations block restart. Transport failures preserve a receipt without inventing response bytes. Partial acquisition produces an outcome, frozen inventory, backup, and restore before the original failure is returned.

The exact endpoint allocation permits at most fifteen 262,144-byte history/split responses plus three 32,768-byte identity responses: 4,030,464 body bytes. Adding the explicit 8,192-byte receipt/failure-manifest reserve for each of 18 attempts yields 4,177,920 bytes, 110,592 above the 4,067,328-byte primary cap before the ledger, handoff, outcome, inventory, or completion. This is a real limit conflict, not omitted evidence: acquisition uses a 3,805,184-byte sublimit that preserves 262,144 bytes for finalization, and before each request reserves that endpoint's maximum body plus receipt. If room is unavailable it stops before the provider call with `STAGE_B_PRIMARY_CAP_CONFLICT`. No cap is increased.

The completion manifest covers the frozen inventory and records every exact version, SHA-256, byte count, retention result, and restore count. The S3 operation ledger and completion manifest are finite checkpoint metadata excluded from the inventory to prevent recursive backup growth; the completion manifest itself is uploaded and read-back/retention verified.

At rates verified 2026-10-01, request-operation cost is approximately `$0.000101` expected and `$0.0002726` maximum. The prior `$0.0007424` allowance remains conservative; the corrected known six-month maximum is `$4.501199950784`, before account-specific pricing, tax, credits, runtime sizes, and failures. No purchase or spending is authorized.

## Offline verification

```bash
python -m scripts.run_alpha_atlas_v4_stage_b_offline \
  --offline-synthetic \
  --output-dir <temporary-output-dir>
```

The current synthetic path produced five synthetic Massive attempts, 14 frozen objects plus one completion object, 80 mocked S3 operations, 47,262 bytes per primary/backup copy, and zero live AWS/Massive requests.

The October 5 fixture remains expired and unchanged. Runtime disk, credentials, bucket ownership/configuration, permissions, provider access, backup, restore, headroom, and interference have not been tested. Stage B remains `NOT_AUTHORIZED / NOT_EXECUTED`; the pilot remains disabled.
