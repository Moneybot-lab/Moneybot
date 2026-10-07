# Alpha Atlas V4 Stage B operational-verification review package v1

## Purpose

This replacement is **`OPERATIONAL_VERIFICATION_ONLY`**. It verifies bounded retrieval, dated security identity, split adjustment, private feature/handoff validation, persistent accounting, primary persistence, S3 backup/read-back/exact-version restore, and runtime resource measurements.

It may run outside premarket hours. A successful check leaves each of the following **`NOT_TESTED`**:

- premarket source availability;
- compliance with the prospective 07:30 acquisition deadline;
- prospective snapshot eligibility; and
- pilot readiness.

It supersedes the proposed prospective-timed execution path, not the October 5, October 20, or October 26 historical evidence. Prospective mode retains the 07:30 cutoff and all later binding, assembly, readiness, and decision boundaries.

## Frozen historical window

Preparation occurred on 2026-10-07 before the October 7 XNYS session completed. The last fully completed eligible session was therefore **2026-10-06**.

- Historical data-as-of: **2026-10-06**.
- Inclusive definition: exactly 75 XNYS sessions from **2026-06-22 through and including 2026-10-06**.
- Excluded: 2026-10-07 and every later session.
- Ordered-session-list SHA-256: `6b1ad71f708834049f89f168bff3650c6b74dc15664111bc944e832e5343b663`.
- History requests: AAPL, SPY, and XLK daily bars for 2026-06-22–2026-10-06, `adjusted=false`.
- Identity request: AAPL dated 2026-10-06.
- Split lineage: 2026-06-22–2026-10-06, filtered locally to AAPL/SPY/XLK, at most two pages.

The dates are immutable. Runtime cannot roll or extend the window, substitute a symbol, or make discovery requests. Historical event dates never become receipt times; all requests, receipts, assembly, persistence, backup, and restore timestamps record the actual execution time.

## Proxy acceptance

The owner separately accepts AAPL→XLK solely as `EXPERIMENTAL_CONTEXT_PROXY` for this operational check. It makes no ETF-membership or verified sector-classification claim. Six aligned stock/context observations and the formula remain unchanged. Request 4 must still resolve security-level identity; CIK alone is insufficient, and all history remains quarantined until identity resolution.

## Execution validity and authorization

The proposed execution-validity interval is **2026-10-13 14:00 UTC through 2026-10-15 22:00 UTC**. It is independent of the historical window, finite, not automatically extended, and **not owner-approved for execution**.

The proposed authorization remains `PREPARED_FOR_EXECUTION_APPROVAL_NOT_APPROVED`, with `execution_gate_usable=false`, `owner_approval=NOT_GIVEN_FOR_EXECUTION`, and Stage B `NOT_AUTHORIZED_NOT_EXECUTED`.

The operational handoff is marked `ISOLATED_STAGE_B_INSPECTION_ONLY`, `pilot_input_allowed=false`, and is rejected by the prospective cache-only consumption boundary.

## Budgets and safeguards

Each invocation has a 55-minute hard runtime, additionally bounded by the authorization interval. Budgets remain 5 expected/18 maximum Massive attempts, cumulative cap 3,789; 512 S3 operations; 4,067,328 bytes each for primary and backup; and 8,134,656 bytes combined. An immutable execution claim blocks repeated invocation without creating a fresh allowance. Existing retry, pagination, response, finalization-reserve, identity-quarantine, crash-conservative, single-writer, immutable-receipt, split/VWAP, and exact-version restore behavior remains in force.

The execution claim adds one retained object to the complete inventory: 15 expected and 41 maximum frozen objects. The corresponding maximum S3 path is 215 operations, still within the unchanged 512-operation ceiling.

## Deployment handoff

After merging and deploying the reviewed immutable revision to the normal `moneybot-market-stream` worker, prepare the revision-bound, still-unapproved authorization without network or credential access:

```text
python -m scripts.prepare_alpha_atlas_v4_stage_b_post_deploy_authorization --deployed-commit "$(git rev-parse HEAD)" --output /var/data/moneybot-stage-b/authorization/stage-b-operational-2026-10-06.prepared.json
```

The binder verifies the replacement fixture, purpose clarification, new proxy acceptance, owner configuration, accepted proxy configuration, observed Git revision, implementation hashes, purpose, interval, and budgets. It avoids circular hashing because it runs after deployment and its output is not committed into the revision it describes.

## Future operational command — NOT TO RUN

Only after a separate owner execution approval and independent authorization-hash pinning:

```text
python -m scripts.run_alpha_atlas_v4_stage_b_operational --authorization <SEPARATELY_APPROVED_AUTHORIZATION.json> --approved-authorization-sha256 <EXTERNALLY_PINNED_SHA256> --sector-context docs/reports/alpha_atlas_v4_stage_b_operational_sector_proxy_binding.accepted.v1.json --config docs/reports/alpha_atlas_v4_stage_b_runtime_config.owner-bound.v1.json --fixture docs/reports/alpha_atlas_v4_stage_b_operational_verification_manifest.v1.json --fixture-file-sha256 de6c1b13f978421947eb93ed64439fd50780ca20faa1741a10af4786b99dbb4e --fixture-content-sha256 579013c85367c75f8a821aa30c75fe9cd261080a9c8060cfcd94af3d958499e9 --session 2026-10-06 --output /var/data/moneybot-stage-b/run/stage-b-operational-result.json
```

This command was not run.

## Next action

Merge/deploy this reviewed revision to the normal worker, then use the network-free binder to prepare its final execution authorization. Do not execute Stage B yet.
