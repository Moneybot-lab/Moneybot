# Alpha Atlas V4 Phase 0 closeout

**Schema:** `alpha-atlas-v4-phase0-closeout.v1`
**Executive result:** `PHASE_0_BLOCKED_HOSTED_RUN_REQUIRED`
**Starting checkout commit:** `e3ba5eb4e42bfa0182dd3686f8338b6bd4744f3b`

Phase 0 remains open. The production V4 builder now emits a compact, deduplicated, hash-bound `alpha-atlas-v4-reconstruction-lineage.v1` bundle and a production-path local fixture passes full exact-artifact verification. A post-lineage hosted Track B artifact and recovered V3.1 #156/#157 hashes are still required before Phase 0 can pass.

## Checklist

| Item | Status | Evidence / blocker |
|---|---|---|
| 0.1 V3.1 benchmark | BLOCKED | Exact run/artifact IDs and GitHub digests are frozen; authenticated recovery must run. |
| 0.2 prediction/execution clock | PASS | Existing timing contract and tests remain intact. |
| 0.3 daily/context cutoff | PASS | Completed-session semantic availability is independently derived; existing cutoff tests pass. |
| 0.3 fit-only filling | PASS | Existing fit-period-only policy remains unchanged. |
| 0.4 adversarial leakage tests | PASS | Existing fail-closed suites remain intact. |
| 0.5 canonical observations | PASS | Canonical IDs and unit weights remain enforced; lineage joins by canonical ID. |
| Exit: 100% row timestamp invariant | BLOCKED | Requires a new hosted exact artifact. |
| Exit: same-day close leakage | PASS | Adversarial tests pass. |
| Exit: duplicate economics canonicalized | PASS | Existing canonicalization evidence. |
| Exit: every feature/label reconstructable | PASS locally / BLOCKED hosted | Production builder fixture replays 43/43 features, execution, target, identity, source hashes, and actions; hosted evidence is pending. |
| Exit: no unsupported manifest claim | PASS | Certification remains exact-hash/full-artifact only. |

## Implementation readiness

The builder records Massive source objects once, deduplicates selected adjusted-basis rows, records request-event identity as ineligible for historical-universe certification, and records relevant actions or an explicit inspected-source no-action disposition. Canonicalization, cleaning, and feature materialization preserve compact lineage references. Evidence size limits fail closed rather than truncate.

## Benchmark and hosted evidence

Direct V3.1 recovery could not be performed because `gh auth status` reports no logged-in GitHub host. The dispatch-only recovery workflow is therefore required. No post-lineage hosted Track B run has occurred, so no hosted artifact SHA, hosted row count, or `VERIFIED_FOR_THIS_ARTIFACT` claim is made.

## Next permitted step

After merge, run exactly **Recover V3.1 Benchmark Evidence**, then **Track B Offline Challenger**. Inspect `alpha-atlas-v31-benchmark-recovery` and the Track B `phase0/source_evidence`, `reconstructability_report.json`, and `temporal_safety_certification.json`. Historical backfill, V4 tuning, promotion, routing, and customer-facing changes remain prohibited.
