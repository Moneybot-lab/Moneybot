# Alpha Atlas V4 Stage B narrow guidance and execution package v1

## Decision

**`SUPPORTED_BY_AVAILABLE_TERMS_AND_OFFICIAL_GUIDANCE_FOR_LIMITED_STAGE_B_SCOPE`.**

The implementation was inspected, not merely its descriptions. Stage B performs bounded AAPL/SPY/XLK history, dated identity and split retrieval; private feature/handoff validation; immutable local persistence; private S3 backup, read-back and isolated restore; and resource measurements. It contains no model fitting, prediction scoring, automated trading, public display, redistribution, customer access, or pilot execution.

Massive's official [Individual-plan guidance](https://massive.com/knowledge-base/article/which-plan-do-i-need-to-show-massive-data-in-my-app) says an Individual license covers the account holder's own research, scripts, and trading, while access by another person requires Business coverage. Its official [AI guidance](https://massive.com/blog/give-your-ai-live-market-data-with-massive) illustrates subscription-backed historical-price analysis, split retrieval/processing, and derived calculations. These are usage guidance, not contractual amendments.

Read together with the current [Market Data Terms](https://massive.com/legal/market-data-terms-of-service):

- §1's personal, non-business grant matches this owner-only check.
- §2's publication, redistribution, third-party, and commercial restrictions are not implicated.
- §5(d), read alone, requires a license for non-display/derivative uses. The evidenced Individual subscription and the official descriptions of own scripts and subscription-backed automated historical/split analysis support this limited private feature-validation use.
- §8 requires cessation and deletion after termination, restriction, or suspension.
- Under §9, the terms control over guidance if a conflict exists. None was identified for this narrow scope.

This conclusion does **not** cover model training, an investment product, automated trading, commercial reuse, customer access, the pilot, or an undisclosed account addendum. No separate permission letter is required merely because the design uses 180-day retention or a private S3 backup.

## Retention and deletion

Governance Object Lock prevents deletion during its retention period; it does not automatically delete an object afterward. With versioning, deleting the current object only creates a delete marker and does not erase prior versions. See AWS's [Object Lock considerations](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock-managing.html) and [Deleting object versions](https://docs.aws.amazon.com/AmazonS3/latest/userguide/DeletingObjectVersions.html).

The acquisition identity remains intentionally unable to delete or bypass Governance retention. A separately authorized owner/admin process must:

1. stop acquisition and use;
2. delete all local primary, receipt, ledger, handoff, outcome, inventory, completion, and isolated-restore copies;
3. after retention expires, list and delete every exact S3 version and delete marker—not only the current key;
4. address Render's service-managed disk snapshots and every other retained copy; and
5. retain deletion evidence and failures.

If deletion must occur before Governance retention expires, an owner/admin identity—not the acquisition user—would need version-list/delete permissions plus `s3:BypassGovernanceRetention` and must explicitly request bypass. No such process or role is established, Render snapshot removal is unverified, and deletion has not been tested.

## Proposed execution package

- **Status:** `PREPARED_FOR_REVIEW_NOT_APPROVED_BLOCKED_PENDING_SECTOR_APPLICABILITY`.
- **Proposed XNYS session:** 2026-10-26.
- **Acquisition cutoff:** 07:30 America/New_York / 05:30 America/Denver / 11:30 UTC.
- **Fixed history window:** exactly 75 prior XNYS sessions, 2026-07-10 through 2026-10-23.
- **Fixture:** `docs/reports/alpha_atlas_v4_stage_b_verification_manifest.v2.json`.
- **Prepared authorization:** `docs/reports/alpha_atlas_v4_stage_b_execution_authorization.proposed.v1.json`; it is not approved and cannot open the runtime gate.
- **Configuration:** owner-bound configuration file SHA-256 `0b7ef94a1a3ff023f01466a94e1b53d321608ff6c5e968e4a802d246b03c0f28`.

The dated 2026-10-05 AAPL-in-XLK evidence is not extrapolated. Before approval and before request 1, authoritative evidence applicable to 2026-10-26 must be preserved and hash-bound. If it does not support that date, this proposal must expire without execution.

All budgets remain unchanged: 5 expected/18 maximum Massive attempts, cumulative cap 3,789; 512 S3 operations; 4,067,328 bytes for each primary and backup copy; 8,134,656 bytes combined; and a 07:30 New York hard stop.

## Manual command — not authorized now

Run only from the normal `moneybot-market-stream` worker runtime with its mounted `/var/data` disk, after the exact revision is deployed, fresh sector evidence is bound, and the owner separately approves a final authorization:

```text
python -m scripts.run_alpha_atlas_v4_stage_b_operational --authorization <OWNER_APPROVED_AUTHORIZATION.json> --approved-authorization-sha256 <EXTERNALLY_PINNED_SHA256> --sector-evidence <DATE_APPLICABLE_SECTOR_EVIDENCE.json> --config docs/reports/alpha_atlas_v4_stage_b_runtime_config.owner-bound.v1.json --fixture docs/reports/alpha_atlas_v4_stage_b_verification_manifest.v2.json --fixture-file-sha256 f2d1ed7817123656930a4d36560f5e2c6f9d9a87e70cd22d4aeaabeca045ef88 --fixture-content-sha256 f9bea723771f2156674c45a7e035a2dae1b70f53324128ec7092f9aa9f30ebd7 --session 2026-10-26 --output /var/data/moneybot-stage-b/run/stage-b-result.json
```

Do not use GitHub Actions or a diskless Render one-off job.

## Smallest next owner action

Obtain and preserve authoritative AAPL→XLK applicability evidence for 2026-10-26. If it supports that date, review and explicitly approve a final authorization binding that evidence and the deployed revision. No live request, credential read, deployment, execution, or authorization approval occurred here.
