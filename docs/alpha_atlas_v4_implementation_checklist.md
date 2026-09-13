# Alpha Atlas V4 implementation checklist

> Alpha Atlas V4 is currently a private personal-use model for the owner’s
> investment research and personal account management. A future commercial MoneyBot
> Labs model is outside this checklist and will require a separate project, dataset,
> validation process, deployment plan, and licensing decision.
>
> This personal-use Phase 1 audit requires no vendor licensing confirmation,
> subscription change, service purchase, business-plan approval, or commercial
> launch approval. None is a technical blocker or checklist gate here.

## Phase 0 — complete

- [x] Phase 0 exit gate passed.
- [x] Export completeness verified: 50,219 of 50,219 records; not truncated.
- [x] Durable continuity verified: `PREFIX_VERIFIED`.
- [x] Duplicate audit complete: 4,775 exact duplicates in 1,082 groups.
- [x] Conflicting decision IDs and immutable duplicate identities absent.
- [x] Full-artifact reconstruction: 32,619 of 32,619 rows.
- [x] V4 remains research/shadow-only; automatic promotion and live routing disabled.

## Phase 1 — In progress: technical historical coverage and backfill readiness audit underway

- [x] Historical source inventory schema and deterministic report.
- [x] Read-only, bounded technical-data preflight implementation.
- [x] Manual-only GitHub preflight workflow with sanitized always-uploaded evidence.
- [x] Bounded representative live preflight executed through the configured credential path (10/10 probes, 2,946 bytes, zero writes).
- [x] Representative active, TWTR inactive, split, SPY, sector ETF, regular-session, and early-close access demonstrated.
- [x] Live evidence propagation repaired and cross-artifact consistency validated.
- [x] Manual bounded coverage-discovery tooling and workflow added.
- [ ] Delisted-security coverage demonstrated.
- [ ] Effective-dated ticker-change and permanent identity chain demonstrated.
- [x] Survivorship protection fails closed and forbids projecting current tickers backward.
- [x] Point-in-time reference checks reject current-state metadata.
- [x] Corporate-action parity implementation and deterministic fixtures.
- [x] Authoritative 48-field mapping: 43 model inputs plus 5 provenance fields.
- [x] Deterministic exact-duplicate collapse and weight 1.0 contract.
- [ ] Common technically supported date range measured.
- [ ] Terminal/missing-exit policy approved for inactive securities.
- [ ] Backfill feasibility confirmed using observed object/request/runtime samples.
- [ ] Controlled backfill explicitly approved in a later prompt.
- [ ] Historical backfill executed.
- [ ] Challenger training authorized.
- [ ] Phase 1 exit gate complete.

No full historical backfill is authorized by this checklist update.

## Track B certification and diagnostics handoff

- [x] Certification separation and hosted portfolio accounting are the completed engineering baseline (hosted runs `34675971440-1` and `34689216730-1` are reproducibility checks, not independent performance samples).
- [x] Development-only signal-coverage, probability-calibration, and bounded threshold-sensitivity diagnostics implemented and locally fixture-verified.
- [ ] Real-data development diagnostic execution completed (intentionally not run by this change).
- [ ] Model improvement and promotion readiness demonstrated.

The diagnostic command consumes the frozen plan, certified canonical input,
frozen challenger manifest, and the existing chronological walk-forward observer
capture. It performs no fitting, provider access, candidate selection, portfolio
valuation filtering, or final-holdout backtest:

```bash
python scripts/generate_alpha_atlas_v4_development_diagnostics.py \
  --canonical-input data/track_b/canonical_observations.jsonl \
  --split-plan data/track_b/alpha_atlas_v4_temporal_split_plan.json \
  --manifest data/track_b/next_generation/next_generation_challenger_manifest.json \
  --oof-predictions data/track_b/development_walk_forward_predictions.json \
  --output-dir data/track_b/development_diagnostics \
  --baseline-sha 1f8f46db584dff0881273bdeae1c56c1a8a016c5
```

The observer capture must use the `records` emitted by
`train_challenger_suite(..., walk_forward_observer=...)`; the generator fails
closed unless its complete candidate roster and every fold's train/validation
membership exactly match the frozen manifest. Ranking-lane scores remain ranking
scores rather than buy probabilities. Safety remains `research_only=true`,
`automatic_promotion=false`, and `ready_for_live_routing=false`.
