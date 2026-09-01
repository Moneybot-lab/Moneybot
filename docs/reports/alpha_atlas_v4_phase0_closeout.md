# Alpha Atlas V4 Phase 0 closeout

**Schema:** `alpha-atlas-v4-phase0-closeout.v1`
**Executive result:** `PHASE_0_BLOCKED_EVIDENCE_BUNDLE_HOSTED_RERUN_REQUIRED`
**Starting checkout commit:** `ec9485d88c3df91b297acd4e7ab5dc703b7add26`

Track B #168 (`33463597909`) timed out after 600 seconds in `build_raw`. A later hosted build completed optimized observation and lineage construction (50,000 processed, 41,268 emitted, 84,723 selected evidence rows) but failed the former whole-directory 1 GiB evidence guard. The evidence is now deterministically gzip-partitioned behind a compact hash index; Phase 0 remains blocked until a hosted run reaches full-artifact reconstruction.

## Current evidence

- Production builder lineage, timing, canonicalization, fit-only filling, and exact-artifact verifier remain fail closed.
- The local production-path fixture still certifies all 34 cleaned rows with zero failures.
- A 10,000-event scale fixture completes in 21.191 seconds, hashes/parses three files once, reuses 9,960 duplicate lineages, and selects 365 rows.
- V3.1 #156 candidate SHA-256 is `f702a4267895c1b65a6bf432cd675f761197e61e29d51780401a5a34aae69531`.
- V3.1 #157 candidate SHA-256 is `9da4fb4135fdf6de546172465ee8f32b0813122a671bf489e823aa8fbd9c05b4`.
- The corrected exact-filename recovery parser must be rerun to freeze additional artifact hashes and verify metrics.

## Safety

No historical backfill, model tuning, retraining, promotion, routing, production-model, licensing, or customer-facing behavior changed. The workflow still stops before challenger training when Phase 0 reconstruction fails.

## Next action

After merge, rerun **Recover V3.1 Benchmark Evidence** once, then dispatch exactly one **Track B Offline Challenger**. The Track B run must at minimum reach `Evaluate V4 Phase 0 reconstructability gate`; completion alone is not Phase 0 proof.
