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
- [x] Bounded development-only frozen-fold OOF capture and capture-to-report workflow implemented and fixture-tested.
- [x] Hosted development capture and basic diagnostic execution completed (`34769717178-1`, source Track B run `34689216730`).
- [x] Development-only concentration and score-stability analysis implemented and fixture-tested.
- [x] Development-only concentration and score-stability analysis completed locally in the prior review.
- [x] Two-arm current-versus-uniform weighting comparison protocol pre-registered in code and documentation.
- [ ] Immutable artifact-bound registration JSON materialized (pending the certified input, manifest, plan, and capture files).
- [x] Weighting-comparison runner implemented and deterministic-fixture verified.
- [x] Manual **V4 Register Weighting Experiment** workflow implemented and locally tested.
- [ ] Hosted artifact-bound weighting registration completed.
- [x] Manual **V4 Execute Weighting Experiment** workflow implemented and locally tested against deterministic workflow contracts.
- [x] Registered weighting experiment executed (`34797622678-1`); registered uniform-minus-current endpoint was unfavorable (`+0.006319421301219023`, one of three folds improved).
- [x] Saved weighting-model feature metadata defect identified as legacy 10-name metadata attached to 43-dimensional fitted state.
- [x] Metadata-only repair and reload verification implemented and fixture-tested.
- [ ] Six-model real-artifact reload verification completed (manual verification workflow pending).
- [x] Reload verifier repaired to reuse the exact diagnostic capture's persisted fold-local fill policies after registration/candidate/fold/ID checks.
- [ ] Source verification `34872687197-1` blocker resolved in a new hosted verification run.
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

If a compatible capture is not already present, generate it without invoking the
full challenger orchestration or its final-holdout scoring:

```bash
python scripts/capture_alpha_atlas_v4_development_oof.py \
  --canonical-input data/track_b/runs/RUN-ATTEMPT/flat_feature_store/all.jsonl \
  --split-plan data/track_b/runs/RUN-ATTEMPT/challenger_suite/challenger_split_plan.json \
  --manifest data/track_b/runs/RUN-ATTEMPT/challenger_suite/challenger_suite_manifest.json \
  --output data/track_b/runs/RUN-ATTEMPT/development_walk_forward_predictions.json \
  --provenance-output data/track_b/runs/RUN-ATTEMPT/development_oof_capture_provenance.json
```

The manual **V4 Development Diagnostics** workflow accepts a successful source
Track B run ID, verifies the downloaded artifact hashes, reuses a compatible
capture or performs only this bounded development-fold refit, and uploads one
`v4-development-diagnostics-<source-run-id>` artifact. The recorded hosted run
establishes capture/basic-report execution, not concentration-analysis execution.

Concentration and score-stability reports consume the downloaded diagnostic ZIP
directly; this command cannot fit models, regenerate predictions, evaluate the
holdout, or access a provider:

```bash
python scripts/analyze_alpha_atlas_v4_development_concentration.py \
  --artifact v4-development-diagnostics-34689216730.zip \
  --output-dir data/track_b/development_concentration \
  --expected-capture-sha256 454febde2e14ca8a916222d86a6872db1c5e790a29429f2db6393d828e87e434 \
  --expected-input-sha256 506073994052be852a5a00fc38e239b85e72a50494c3559640ea980fb6a52d9e \
  --expected-split-plan-sha256 bd60055adc6cb1b3f8b143c6c61031bced01c9151fcb3b67c33f75430106e8e8 \
  --expected-diagnostic-code-sha 5d360cdbda802ae8527b35fe59f75920b8c827c8 \
  --expected-workflow-run 34769717178-1 \
  --expected-source-run 34689216730
```

The three reporting views retain every canonical observation: equal observation
weight, equal total weight per symbol/original-event-date group, and equal total
weight per original event date. They are descriptive reporting estimands only.
The observer field named `session` contains the unmodified source `event_date`;
it is not silently shifted or asserted to be an exchange session. The proposed
next experiment is one pre-registered development-only current-versus-uniform
objective-weight ablation with features, folds, recipe family, purge, and embargo
held fixed. It is not implemented here, and holdout evaluation/promotion remain
unchanged.

## Registered development-only weighting comparison

The only experiment is `alpha-atlas-v4-big-loss-weight-ablation.v1` for the
frozen `challenger-big-loss-avoider-v1`. First materialize its immutable
artifact-bound registration—before execution—using the certified source files:

```bash
python scripts/run_alpha_atlas_v4_weighting_experiment.py register \
  --input <flat_feature_store/all.jsonl> \
  --split-plan <challenger_split_plan.json> \
  --manifest <challenger_suite_manifest.json> \
  --capture <development_walk_forward_predictions.json> \
  --registration weighting_experiment_registration.json \
  --expected-input-hash 506073994052be852a5a00fc38e239b85e72a50494c3559640ea980fb6a52d9e \
  --expected-plan-hash bd60055adc6cb1b3f8b143c6c61031bced01c9151fcb3b67c33f75430106e8e8 \
  --expected-manifest-hash <SHA256_FROM_VERIFIED_SOURCE_ARTIFACT> \
  --expected-capture-hash 454febde2e14ca8a916222d86a6872db1c5e790a29429f2db6393d828e87e434 \
  --original-capture-code-sha 5d360cdbda802ae8527b35fe59f75920b8c827c8
```

Review and commit/archive that generated JSON and readable `.md` companion,
then use the same arguments with mode `execute` plus
`--output-dir <development-weighting-output>`. Execution rejects any registration
or artifact drift. The primary paired difference is symbol/date-balanced Brier
loss, uniform minus current, with an equal-fold mean; favorable descriptive
evidence requires a negative mean and improvement in at least two of exactly
three folds. No outcome can select or promote an arm or alter threshold 0.60.

To materialize the hosted registration after merge: **GitHub → Actions → V4
Register Weighting Experiment → Run workflow**, leaving the reviewed defaults
`source_track_b_run_id=34689216730` and
`source_diagnostics_run_id=34769717178` unchanged. Download
`v4-weighting-registration-<registration-run-id>-<run-attempt>` and review the
registration before separately authorizing any experiment execution.

After the reviewed registration artifact exists, **V4 Execute Weighting
Experiment** is the only hosted execution entry point for this registration. It
has no dataset or hash inputs: this version is fixed to registration run
`34796621288-1`, canonical registration hash
`4112f445d8ef079550f02185e4592c05aff9c0321ee4bfa615b632360af4c5c3`,
Track B run `34689216730`, and diagnostics run `34769717178-1`. It runs only the
existing `execute` subcommand and uploads
`v4-weighting-execution-<workflow-run-id>-<run-attempt>`. It never creates or
amends a registration and does not evaluate the final holdout.

The numerical experiment path selected the registered 43 columns explicitly for
both training and validation, but the shared fitter returned its legacy ten-name
default metadata. Future experiment states now replace that metadata with the
exact registered order before serialization; learned numbers and the unfavorable
registered result are unchanged. **V4 Verify Weighting Model Reload** downloads
the exact reviewed execution and sources, creates metadata-only corrected copies,
and replays without fitting. It fails closed if the original fold-local fill state
is needed but unavailable, and never accesses final-holdout rows.
The verifier now separates feature-metadata correction, prediction replay, and
saved-result arithmetic. It may classify the defect as metadata-only only after
all six reloaded score vectors pass the fixed tolerance; otherwise replay remains
explicitly unresolved.
