# Alpha Atlas V4 Stage B owner-review package v1

## Sector-gate decision

**The prior gate unnecessarily conflated experimental proxy selection with dated classification and ETF membership.**

The registered feature needs six aligned dates from the stock and its configured context ETF for a five-session relative-return comparison. The planner treats the mapping value as a history dependency; the validator checks date alignment. Neither calculation tests whether AAPL is an XLK constituent or derives AAPL's sector classification.

These remain distinct:

- **Classification:** a descriptive fact about AAPL, not an input to this Stage B calculation.
- **Constituent membership:** a dated fact about XLK holdings, not required by the calculation and not claimed for the proposed date.
- **Experimental proxy:** the fixed, owner-approved choice of XLK as AAPL's context series. XLK history, alignment, provenance, and the feature formula remain required.
- **Security identity:** a separate gate. Request 4 must resolve an unambiguous security-level identity; CIK alone remains insufficient.

The versioned clarification permits only the third relationship. It does not fabricate an effective membership/classification interval, change the formula, remove XLK history, or weaken identity, timing, provenance, or budget rules. Operational use requires a final `APPROVED` authorization that binds the proxy contract and explicitly sets `owner_accepts_sector_proxy_clarification=true`.

## Single proposed session

- **Session:** Tuesday, 2026-10-20.
- **Acquisition cutoff:** 07:30 America/New_York / 05:30 America/Denver / 11:30 UTC.
- **History window:** exactly 75 prior eligible XNYS sessions, 2026-07-06 through 2026-10-19.
- **Reason:** selected on October 6, it leaves thirteen full calendar days for review and deployment. No data dependency requires waiting until October 26.
- **No rolling:** if review, deployment, or approval misses the fixed cutoff, do not run and do not substitute a date automatically.

The new v3 fixture supersedes only the unapproved October 26 proposal. The expired October 5 fixture and October 26 v2 proposal remain unchanged as historical records.

## Package state

- Clarification: `alpha_atlas_v4_stage_b_sector_proxy_clarification.v1.json` — `PREPARED_FOR_REVIEW_NOT_ACCEPTED`.
- Fixture: `alpha_atlas_v4_stage_b_verification_manifest.v3.json` — `PREPARED_FOR_REVIEW_NOT_APPROVED`.
- Proxy binding: `alpha_atlas_v4_stage_b_sector_proxy_binding.proposed.v1.json` — `PROPOSED_NOT_ACCEPTED`.
- Authorization: `alpha_atlas_v4_stage_b_execution_authorization.proposed.v2.json` — `PREPARED_FOR_REVIEW_NOT_APPROVED`, `owner_approval: NOT_GIVEN`, `execution_gate_usable: false`.
- Owner configuration file SHA-256: `0b7ef94a1a3ff023f01466a94e1b53d321608ff6c5e968e4a802d246b03c0f28`.

Budgets remain 5 expected/18 maximum Massive attempts, 3,789 cumulative Massive attempts, 512 S3 operations, 4,067,328 bytes per primary and backup copy, and 8,134,656 bytes combined. The configured service, root, bucket, owner, region, prefix, credential-variable names, receipt semantics, quarantine, and fail-closed storage behavior are unchanged.

## Revision binding without a circle

The reviewed package records source-file hashes. After that exact immutable revision is deployed, the owner creates a separate authorization outside the deployed repository revision. That authorization records the already-existing deployed Git commit and source hashes and is passed with an independently pinned authorization hash. The authorization is therefore not a file inside the commit it authorizes.

## Owner-review sequence

1. Review the clarification, v3 fixture, proxy binding, implementation source hashes, budgets, and command.
2. Deploy the reviewed immutable revision to the normal `moneybot-market-stream` worker without changing its normal start command.
3. Separately create and approve the final authorization, binding the deployed revision and package hashes and explicitly accepting the proxy clarification.
4. Only then manually run once before the fixed cutoff. This task performs none of these operational steps.

## Future manual command — not authorized now

Run only on the normal `moneybot-market-stream` worker with its mounted `/var/data` disk:

```text
python -m scripts.run_alpha_atlas_v4_stage_b_operational --authorization <SEPARATELY_APPROVED_AUTHORIZATION.json> --approved-authorization-sha256 <EXTERNALLY_PINNED_SHA256> --sector-context docs/reports/alpha_atlas_v4_stage_b_sector_proxy_binding.proposed.v1.json --config docs/reports/alpha_atlas_v4_stage_b_runtime_config.owner-bound.v1.json --fixture docs/reports/alpha_atlas_v4_stage_b_verification_manifest.v3.json --fixture-file-sha256 c7d06f54aa18d4c3bd52c29e9c7423605e6919a076f29ba24dcfa7e61eecf113 --fixture-content-sha256 b22907c9036e3f7476ffa35e6601caaefb810e311050f6457d7d5ff4b3041885 --session 2026-10-20 --output /var/data/moneybot-stage-b/run/stage-b-result.json
```

## Remaining blockers and next action

Owner review/acceptance, deployment, and a separately created approved authorization remain required. Runtime storage, provider, AWS, backup/restore, capacity, and headroom remain unverified until that separately authorized bounded check.

**Single next action:** owner reviews this exact package and either accepts or rejects the experimental XLK proxy clarification. No credentials were read and no network request, deployment, approval, Stage B run, training, scoring, or pilot activity occurred.
