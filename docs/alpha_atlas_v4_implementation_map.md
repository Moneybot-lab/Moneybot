# Alpha Atlas V4 implementation map

**Prompt 3 status:** Track B row-construction timing migration is implemented and
research-only. V3/V3.1, production serving, promotion and routing remain unchanged.

## Implemented components

| Concern | Files/functions | Prompt 3 status |
|---|---|---|
| Timestamp normalization | `moneybot/services/outcome_tracking.py`: `normalize_unix_ts`, `event_market_date` | Seconds, milliseconds, microseconds and nanoseconds normalize to UTC; invalid values fail closed; exchange dates use New York. |
| Exchange sessions | `moneybot/services/market_data_providers.py`: `ExchangeCalendar` | Shared `XNYS-rule-calendar.v1` supplies holidays, DST, recurring early closes, previous/next sessions and UTC official opens/closes without a new deploy dependency. |
| Daily availability | `scripts/build_massive_decision_training_rows.py`: `_normalize_market_row`, `_feature_index_for_decision`, `load_market_history` | Prior completed bars use official-close availability; same-session after-hours bars require explicit provider availability. |
| Symbol/context features | `build_training_rows_from_raw_market`, `_market_regime_risk_on`, `_return_volatility`, SPY/sector joins | Symbol, SPY, sector, volatility and regime families share the cutoff, resolve independently and reject missing/stale required sources. |
| Executable labels | `build_training_rows_from_raw_market`, `split_adjusted_forward_return` | Entry is next eligible open; label start equals entry; five/ten-session exits are S4/S9 official closes. |
| Corporate actions | `moneybot/services/corporate_actions.py`, `_feature_safe_splits` | Feature actions must be known by cutoff (or effective before the decision session); future-horizon action IDs/factors remain available only to realized labels. |
| Provenance/schema | `AlphaAtlasV4TimingRecord`, row emission and `write_rows` | Rows emit timing, per-family source/availability, prices, calendar, identity, staleness, actions, commit and manifest identity under `massive-decision-training-rows.v4`. |
| Canonical observations | `moneybot/services/alpha_atlas_v4_canonical_observations.py`; `scripts/canonicalize_alpha_atlas_v4_rows.py` | Raw requests become unit-weight canonical observations plus a request map under `alpha-atlas-v4-canonical-observation.v2` before cleaning or model research. |
| Canonical evaluation | `evaluate_canonical_observations`, `canonical_date_block_bootstrap`, `canonical_top_k`, `score_once_and_fan_out` | Metrics, cutoff-date bootstrap, ranking and research fan-out operate only on unique canonical IDs. |
| Track B orchestration | `.github/workflows/track-b-offline.yml`; `validate_alpha_atlas_v4_workflow_artifacts.py` | Run-scoped raw validation, canonicalization, canonical validation, cleaning and feature materialization execute in contract order; failure evidence uploads unconditionally. |
| Prior-request state | `build_massive_decision_training_rows.py`; canonical request map | V2 removes symbol-global prior model/recommendation history from model features, retains values/source/timestamp per request, and rejects legacy feature names. |
| Purged temporal split | `alpha_atlas_v4_temporal_split.py`; `plan_alpha_atlas_v4_temporal_split.py`; challenger `--split-plan` | V4 groups canonical observations by cutoff exchange date, purges actual entry/exit overlap, embargoes eligible sessions, freezes hashes, and emits a controlled no-candidate outcome when coverage is valid but insufficient. |
| Historical-data entitlement inventory | `scripts/audit_massive_historical_entitlement.py`; `docs/reports/alpha_atlas_v4_massive_historical_inventory.{md,json}` | Read-only, opt-in bounded probes and repository evidence map V4 sources and explicitly leave account entitlement, licensing, point-in-time reference coverage and backfill authorization unverified. |
| Phase 0 evidence | `alpha_atlas_v4_phase0.py`; `verify_alpha_atlas_v4_reconstructability.py`; Phase 0 reports | Fit-only fill policies, the 43+5 feature registry, independent source replay and exact-hash certification fail closed. Phase 0 remains blocked until lineage-complete hosted artifacts and frozen V3.1 hashes exist. |
| Reconstruction lineage | `build_massive_decision_training_rows.py`; `alpha_atlas_v4_reconstruction_lineage_contract.md` | New V4 builds emit deduplicated source objects/rows, request-event identity, corporate-action inspection, canonical-ID lineage, semantic daily-bar availability, hashes, and size metrics under `alpha-atlas-v4-reconstruction-lineage.v1`. A hosted full-artifact certification remains required. |
| V3.1 evidence recovery | `recover_v31_benchmark_evidence.py`; `recover-v31-benchmark-evidence.yml` | Dispatch-only least-privilege recovery targets exact runs #156/#157 and uploads compact extracted hashes/comparison evidence without retraining or committing the historical archives. |

## Compatibility and intentionally deferred work

- V1/V2 Track B rows are not reinterpreted. The cleaner requires V4; the homogeneous
  training reader accepts V2 or V4 but rejects mixed schemas; production comparison
  and promotion evidence remain pinned to V2.
- No V4 dataset was generated, ingested, deduplicated, trained, promoted or routed.
- The V4 cleaner now requires `alpha-atlas-v4-canonical-observations.v2`; raw V4
  request rows cannot bypass canonicalization. Homogeneous V4 training inputs require
  unique canonical IDs and unit model weights.
- Timestamp-safe intraday V4 features and breadth inputs remain deferred because the
  current Track B daily row schema has neither family.
- A maintained external exchange-calendar package can replace the versioned rule
  adapter later; that change requires parity tests and a new calendar identifier.
- Historical backfill remains blocked pending account-specific entitlement and
  retention evidence plus a point-in-time inactive/delisted universe contract. The
  inventory command has no download or backfill mode and is not workflow-scheduled.

## Prompt 8C operational repair

Track B #168 exposed repeated per-observation split adjustment and source-window hashing. The builder now caches economically equivalent adjustment views, row identities, context windows, and duplicate canonical lineage; emits run-scoped performance telemetry; atomically finalizes artifacts; and retains the unchanged fail-closed reconstruction gate. The recovery parser recognizes only the exact V3.1 candidate filename. A hosted rerun is required before changing Phase 0 status.

The subsequent hosted build completed observation and lineage construction but
failed the former whole-directory byte guard at 84,723 selected rows. Evidence v1
now keeps a compact primary manifest and stores every high-cardinality ledger in
deterministic gzip partitions with hash/count/offset indexes. Diagnostics record
the configured limits and per-section sizes before any fail-closed limit error.

## Phase 1 technical readiness

Phase 0 is now closed by scheduled Track B `33960970412-1` (32,619/32,619 rows
reconstructable). Phase 1 uses `alpha_atlas_v4_phase1.py` and
`audit_alpha_atlas_v4_phase1_readiness.py` for a credential-sanitized, bounded,
read-only historical source preflight, effective-dated reference enforcement,
authoritative 43+5 mapping, duplicate comparison, and non-executing controlled
backfill plan. The full-universe backfill remains blocked until inactive/delisted
coverage, identity events, sector history, terminal outcomes, and a common source
date range are technically demonstrated.
