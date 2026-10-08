# Alpha Atlas V4 Stage B replay closure v1

## Assessment

- `PROVIDER_NORMALIZATION_REPAIR: CLOSED`
- `OFFLINE_SAVED_EVIDENCE_REPLAY: PASS`
- `ISOLATED_REPLAY_HANDOFF: ELIGIBLE`
- `ORIGINAL_LIVE_EXECUTION: FAILED_UNCHANGED`
- `PREMARKET_TIMING: NOT_TESTED`
- `PROSPECTIVE_ELIGIBILITY: NOT_TESTED`
- `PILOT_CONSUMPTION: PROHIBITED`
- `OVERALL_STAGE_B: OPEN`

The Massive abbreviated-field root cause, shared canonical-history repair, saved
feature-window processing, offline replay, saved accounting, saved backup/restore
validation, and isolated handoff are closed within the reported replay scope.
No other acceptance item closes merely because that handoff is eligible.

## Provenance and scope

The owner supplied the Render command result and inspected full report results in
the task request. This is **owner-reported runtime evidence**, not an independently
retrieved Render artifact or a newly generated attestation. The runtime root
`/var/data/moneybot-stage-b` is unavailable in this checkout. No download, remote
retention inspection, runtime SHA256SUMS verification, or original-evidence
mutation is claimed. Owner reports PR #656 merged and repair commit `e7f6d14`
deployed. This checkout has merge `996efb9c` for #656, but `e7f6d14` is not a local
Git object; deployment identity remains owner-reported.

The original committed [repair report](alpha_atlas_v4_stage_b_provider_normalization_repair.v1.md),
JSON, and SHA256SUMS remain historical evidence, including their then-pending replay
statement. This closure supersedes that pending statement only. Synthetic tests
are separate from the actual saved-input replay.

Owner executed `python -m scripts.replay_alpha_atlas_v4_stage_b_saved_evidence`
with root `/var/data/moneybot-stage-b` and output
`/var/data/moneybot-stage-b/replay/alpha-atlas-v4-stage-b-normalization-repair.v1`.
Reported schema is `alpha-atlas-v4-offline-saved-evidence-replay.v1`, purpose
`OFFLINE_SAVED_EVIDENCE_REPLAY`, status `PASS`, time
`2026-10-08T01:47:36.193443+00:00`, and live provider/AWS requests both zero.
All hashes below are **reported values**, not independently verified runtime bytes.

| Evidence | Reported result / SHA256 |
|---|---|
| Report content | `02ea4d3d8604f2afdf5deb5ca1e0878828fda7f7da823049f6c6c54e73d5c854` |
| AAPL normalized history | 75 rows; `a9eca8dfecf654bbb453e8fe940fd0896c753e973432bc73f5b76f02a7d275bc` |
| SPY normalized history | 75 rows; `780e775c796fb39e84cefc86ca625d8296f2106e184882464af92cee726c0a49` |
| XLK normalized history | 75 rows; `d1ab6470cb4a37e9f3b4c0c1620df444bd9b855b4bd6f016154d09cf9aadf6f6` |
| Massive ledger | 5 attempts, 15 states: RESERVED 5, TRANSMITTING 5, PERSISTED 5, FAILED 0, UNCERTAIN 0; head `78bf02dded9c1d89eaffea607bb769ce10759b03037d80c141cdd093297bde30` |
| S3 ledger | 91 operations, 273 states: RESERVED 91, TRANSMITTING 91, SUCCEEDED 91, FAILED 0, UNCERTAIN 0; head `87bed57b4468da5ce32246a036a1d3abab0799b8382720e0efb13fa63564eb87` |
| Backup/restore | 15 objects, 169053 bytes, 15 receipts, 15 recorded restored objects, 15 locally restored files |
| Replay handoff | `ISOLATED_REPLAY_ONLY`, eligible true, reason codes []; `2ec1cc429f37f15de32d60bd419d16edb0ee2190d07dc381bd07d362caea9f85` |

Backup status is `SAVED_VERIFICATION_EVIDENCE_ONLY_NO_FRESH_AWS_CHECK`. The replay
validates saved inventory, completion, receipts, local objects, and restored files.
It does not prove ongoing remote retention or fresh AWS read-back.

Split evidence has 557 **source rows**, not 557 applied split events. Application
is `EXACTLY_ONCE_FROM_PROVIDER_UNADJUSTED`, basis `2026-10-06`. Existing split
provenance and separate AAPL/SPY/XLK adjustment bindings remain authoritative and
unchanged; no new split counts or per-symbol binding hashes are inferred.
Reported identity: composite FIGI `BBG000B9XRY4`, share-class FIGI `BBG001S5N8V8`.
The original result remains `FAILED_FEATURE_WINDOW_INVALID_UNCHANGED`.

## Remaining-readiness matrix

The [setup acceptance schema](alpha_atlas_v4_stage_b_setup_package.v1.json) requires
every required item PASS; UNKNOWN, NOT_TESTED, or FAIL prevents acceptance. Later
operational-purpose and experimental-proxy clarifications govern timing and proxy
scope; the old constituent-membership gate is not reinstated.

| Requirement | Current evidence and remaining limit |
|---|---|
| Original execution / operational completion | Failed unchanged; derived replay is not a completed prospective operational execution. No continuation or fresh run authorized here. |
| Authorization/manifest and accepted proxy | Historical approvals/bindings remain immutable. Replay eligibility is not a new executable authorization; dates cannot roll or be backdated. Experimental context proxy makes no membership claim. |
| Typed identity, endpoint access, pagination, response limits, receipt provenance, 75-session window, split consistency | Reported replay validates saved inputs and normalization; no new provider capability or prospective availability demonstrated. |
| Attempt budget | Saved Massive/S3 chains validated as reported. Prior usage remains charged; no reset, fresh allowance, or retry permission. |
| Evidence budget, storage capacity, worker CPU/RSS headroom and interference | 169053 covered bytes are not complete primary/backup cap, free-space, runtime, or stream-impact measurements. Required runtime measurements remain unverified by supplied report. |
| Deadline / premarket timing | Operational-only scope cannot prove prospective 07:30 compliance. Premarket timing NOT_TESTED; no prospective observation supplied. |
| Primary read-back / backup version lock / isolated restore | Saved bytes/receipts/restores validated within stated scope; fresh remote read-back, current bucket/version/lock configuration and ongoing retention remain unestablished. |
| Cache-only handoff / prospective snapshot eligibility | Isolated replay only; prospective eligibility NOT_TESTED, pilot input false. No pilot consumption. |
| Secret redaction / retention terms | Existing design and reviewed limited personal-use terms preserved; report does not independently establish runtime redaction or full operational lifecycle. No credentials inspected. |
| Cleanup/deletion lifecycle | Existing narrow-guidance gate remains: end-to-end deletion and Render snapshot removal unverified. No deletion or Governance bypass authorized. |
| Pilot manifest, collection and Stage C | Manifest approval and separate collection authorization remain open; QQQ/SPY stays PROPOSED_NOT_APPROVED. Training, fitting, scoring, trading, promotion, routing and Stage C remain disabled/unauthorized. |

## Single recommended next verification gate

**Reconcile saved worker resource/headroom and stream-interference evidence** against
`worker_interference` and the resource/deadline requirements already registered in
[setup package](alpha_atlas_v4_stage_b_setup_package.v1.md). The supplied replay
summary establishes none of these observations, so they remain open. This is the
smallest non-destructive next check: inspect evidence already created before
considering any new provider acquisition or AWS verification.

Existing `RuntimeResourceGuard`, `telemetry`, and the operational runner in
`moneybot/services/alpha_atlas_v4_stage_b.py` produce RSS/free-space phase samples
and runtime/CPU data on the applicable paths. Inspect the actual failed-run
outcome/phase report and contemporaneous stream-worker logs if available; a
failure may not retain the complete success-path measurements. The replay does
not synthesize missing telemetry, and process RSS alone does not prove acceptable
stream interference. There is no dedicated standalone saved-log acceptance CLI;
read-only reconciliation using existing evidence needs no new implementation.

Acceptance requires source/revision/time-bound actual worker observations,
coverage of the observed initialization/acquisition/backup/restore phases,
RSS and free-space compared with the exact bound guard limits, measured runtime
compared with the applicable 55-minute scope (preserving disclosed unknown legacy
elapsed time), and contemporaneous stream behavior sufficient to assess the
registered unacceptable-interference stop criterion. Do not invent a numeric
interference threshold or infer PASS from absence of errors. Missing phases,
limits, baseline, or interference observations remain UNKNOWN. Failed-run
measurements may close only their demonstrated scope, never whole Stage B.

Required evidence is the original outcome/phase data, exact configuration and
approved scope references, available timestamped resource samples and worker
logs/baseline, plus a derived read-only comparison identifying each source,
criterion, result and gap. Record source hashes only when actual bytes are
available; preserve originals and all ledgers. No substitute runtime artifact is
created by this documentation task.

The saved-evidence review can be entirely offline with zero provider/AWS access,
no scheduling and no deployment. It is a recommendation only and has not been
executed here. If existing records cannot establish the requirement, closure needs
separately authorized prospective worker observation with a defined measurement
scope and acceptance bounds. Merely observing worker resources need not require
provider or AWS requests; workload-specific missing phases cannot be certified by
idle observations. Do not use the claimed run, continuation, or old authorization
to generate new workload. Any necessary operational execution would need separate
owner approval, an exact revision/window/hash binding, cumulative budget review,
and explicit provider/AWS permissions for its scope. This task authorizes none
of those actions and does not establish that another acquisition is unavoidable.

Premarket timing, prospective eligibility and fresh AWS retention/read-back remain
separate later gates. No new Codex implementation is necessary for this next saved
record review. Stop at the owner-authorization boundary for any new observation
or operational execution; do not reopen normalization investigation or start Stage C.

## Focused checkout verification

- `tests/test_alpha_atlas_v4_stage_b_replay.py`: **5 passed**. Repeated with
  Python socket connection and name-resolution calls blocked through inherited
  `sitecustomize` (including CLI subprocesses): **5 passed**. These are synthetic
  fixtures, not the owner runtime files.
- Original normalization-repair v1 SHA256SUMS: JSON and Markdown both **OK**.
- `git diff --check`: passed. Only the checklist and this closure report are
  deliverable changes; operational code, original reports, ledgers, and hashes
  remain unchanged.
- Actual Render report files and their SHA256SUMS were not available or verified.

## PR summary

Close the provider-normalization repair and owner-reported offline saved-evidence
replay from PR #656. Update the existing checklist milestone, preserve the original
failed live execution and immutable repair evidence, and document the reported
history/accounting/restore/handoff results with explicit provenance limits.

Stage B remains open: isolated replay eligibility cannot authorize pilot use or
prove prospective timing, current remote retention/read-back, resource headroom,
stream interference, or deletion lifecycle. Recommend one offline saved-resource
review before any separately authorized new observation. Validation: five focused
replay tests pass with network access blocked, original repair checksums pass,
and documentation diff checks pass. No operational code or live execution changes.
