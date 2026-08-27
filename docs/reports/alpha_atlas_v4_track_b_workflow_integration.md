# Alpha Atlas V4 Track B workflow integration repair

## Confirmed root cause

The workflow built raw rows at
`data/track_b/decision_training_snapshot_massive.jsonl` with schema
`massive-decision-training-rows.v4`, then passed that exact raw path directly to
`clean_training_snapshot.py`. The workflow contained no invocation of
`canonicalize_alpha_atlas_v4_rows.py`. The cleaner correctly required
`alpha-atlas-v4-canonical-observations.v2` and rejected the raw manifest; no stale
fallback filename or independent unadjusted-price flag caused the observed error.

## Repaired run-scoped sequence

Each GitHub run/attempt now owns `data/track_b/runs/<run-id>-<attempt>/` and uses:

1. `raw/decision_training_snapshot_massive.jsonl` plus its raw V4 manifest.
2. Raw schema/timing validation.
3. `canonical/canonical_observations.jsonl`, request map, diagnostics, and manifest.
4. Canonical schema/contract/hash/unique-ID/unit-weight validation.
5. `training_quality/cleaned_all.jsonl` and quality report.
6. `flat_feature_store/` and research-only challenger/evaluation artifacts.

The cleaner input is the canonicalizer's declared observation path. A former
repository-level raw snapshot cannot be selected as a fallback. Canonical and cleaning
manifests carry raw/canonical SHA-256 lineage.

## Safety and remote verification

The V2 production comparison remains pinned to V2 and is not run with V4 evidence.
The V4 summary explicitly records `automatic_promotion: false`,
`ready_for_live_routing: false`, and `v2_production_comparison_performed: false`.
Artifact upload remains unconditional and includes available raw, canonical, cleaning,
and sanitized failure evidence. Local tests exercise the real build → canonicalize →
clean → feature-store sequence. A GitHub Actions dispatch is still required to verify
runner credentials, Massive access, and downstream hosted execution.

## Prior-state conflict repair

The hosted V1 canonicalization reached its intended fail-closed boundary and exposed
request-order-dependent prior-model state. V2 preserves the complete family in the
request map, excludes it from canonical observations, and emits bounded diagnostics for
all unresolved material conflicts before failing. The run-scoped stage order and upload
behavior are unchanged.
