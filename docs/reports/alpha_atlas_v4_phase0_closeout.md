# Alpha Atlas V4 Phase 0 closeout

**Schema:** `alpha-atlas-v4-phase0-closeout.v1`  
**Executive result:** `PHASE_0_BLOCKED`  
**Starting repository commit:** `c5812e70ac237a20328b2be5d3ea5c6ba2777bc9`

Phase 0 is not closed. The engineering repairs add fit-only filling, a complete feature registry, an independent reconstruction verifier, and artifact-bound certification. Current evidence still cannot prove that every hosted observation is reconstructable because existing rows do not persist immutable source-object/selected-row/corporate-action lineage, and the hosted artifact is not mounted here.

## Checklist

| Item | Status | Evidence / blocker |
|---|---|---|
| 0.1 V3.1 benchmark-only record | BLOCKED | Version and feature contract are repository-verified; model/metadata hashes and runs #156/#157 are `UNVERIFIED_EXTERNAL_ARTIFACT`. |
| 0.2 prediction/execution clock | PASS | Existing timing contract and adversarial tests. |
| 0.3 daily/context cutoff | PASS | Existing same-day and context cutoff tests. |
| 0.3 fit-only filling | PASS | V4 frozen-plan and each walk-forward fold now fit their own median policy from fit rows only. |
| 0.4 adversarial leakage tests | PASS | Existing normal-passing suites remain intact. |
| 0.5 canonical observations | PASS | Canonical IDs and unit weights remain enforced. |
| Exit: 100% row timestamp invariant | BLOCKED | No exact hosted artifact hash was inspected in this task. |
| Exit: same-day close leakage | PASS | Adversarial tests pass. |
| Exit: duplicate economics canonicalized | PASS | Existing canonicalization evidence. |
| Exit: every feature/label reconstructable | BLOCKED | Existing rows lack `reconstruction_lineage`; current-artifact result is not available. |
| Exit: no unsupported manifest claim | PASS for new V4 artifacts | Builder now emits `NOT_EVALUATED`, not `leakage_safe=true`; only independent exact-hash verification can certify. |

## 43 versus 48

`48 = 43 intended numeric model inputs + 5 provenance columns`: `feature_cutoff_at`, `feature_family_available_at`, `feature_family_source_at`, `feature_market_asof_date`, and `feature_split_ids`. The prefix-based feature-store manifest counted these five, while numeric model selection did not. No prior-request field is a V4 model input.

## Fit-only filling

Previously `train_challenger_suite` called `_fill_feature_gaps` over the entire prepared V4 frame before applying its frozen split, so future holdout values could affect medians. V4 now fits `alpha-atlas-v4-feature-fill-policy.v1` on frozen-plan training IDs only and applies that immutable policy to holdout rows. Every walk-forward fold fits a separate policy on that fold's purged training rows. Legacy V2 behavior is unchanged.

## Reconstruction and certification

The verifier hashes every referenced source before use, checks source event/availability timestamps and required symbol/SPY/sector families, replays registered calculations and executable returns, and fails closed on contract, action, fill, feature, execution, target, or canonical-ID disagreement. Shared source documents are cached. It supports full input or bounded samples.

A certification uses `alpha-atlas-v4-temporal-safety-certification.v1` and binds the exact artifact SHA-256, verification-report SHA-256, full-artifact scope, per-observation identities, result count, result hash, and recomputed failure count. A fabricated, partial, zero-row, stale, or failed report cannot become `VERIFIED_FOR_THIS_ARTIFACT`; legacy `leakage_safe=true` is explicitly ignored. A deterministic synthetic lineage-bearing observation replayed all 43 builder formulas successfully (1 checked, 1 reconstructable, 0 failed), while adversarial source, timing, action-availability, fill, feature, execution, target, and stale-certification mutations failed closed. With no current lineage-complete hosted artifact, artifact certification remains `NOT_EVALUATED`, hosted rows checked 0, and no hosted artifact hash is claimed.

## Next permitted step

Only Phase 0 evidence work may continue: persist immutable market-source and corporate-action lineage for each row, recover immutable V3.1 run #156/#157 artifacts, and rerun exact-artifact verification. Historical backfill, V4 tuning, promotion, routing, and customer-facing changes remain prohibited. Phase 1 also remains independently blocked by entitlement/licensing and inactive/delisted reference-data questions.
