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
- [x] **DELISTED_SECURITY_AVAILABILITY: CLOSED / VERIFIED — bounded historical access for provider-ended ticker listings.**
- [ ] Historical-universe completeness for all securities eligible during the research interval demonstrated.
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

### Delisted-security availability closure criteria

The original requirement is not satisfied by `active=false`, a missing bar, or
the existing `STATIC_ONLY` reports. Closure requires a real, read-only Massive
run which completely paginates the existing `market=stocks`, `type=CS`,
`active=false` discovery query; separately counts records with an explicit
provider `delisted_utc` field and other inactive listings; and successfully
retrieves point-in-time reference data plus historical aggregate bars for a
deterministic, date-spanning sample of at least three confirmed delistings. The
run must preserve sanitized request provenance and response hashes and must have
no failed selected probe. This closes only demonstrated historical-data
availability for confirmed delisted securities.

It does **not** establish complete historical-universe coverage, effective-dated
identity/ticker-event chains, or terminal-price/delisting valuation treatment.
Those remain separate open checklist items below. The previously reported 6,629
records are inactive listings, not 6,629 proven delistings, and must be reconciled
against the newly paginated population rather than assumed correct.

The manual **Alpha Atlas V4 Delisted Coverage Verification** workflow performs
this bounded live check using the existing `MASSIVE_API_KEY`, inputs
`research_start=2018-01-01`, `research_end=2026-09-15`, `max_pages=20`, and
`max_probes=12`. It uploads
`alpha_atlas_v4_delisted_coverage_verification-<run-id>-<attempt>` even on
failure. Workflow code or fixture tests are not provider evidence.

Hosted run `35123217191-1` at commit
`fd517cf5b752df85ed981d980ac6f832be64c904` is retained as failed evidence:
pagination completed in seven pages with 6,607 inactive listings, but only 10 of
12 probes returned bars. AGU and HLS ended on `2018-01-02`; clipping their query
to `research_start=2018-01-01` produced a holiday-only one-day window. The
corrected verifier uses XNYS sessions, preserves both as explicit follow-ups, and
permits at most ten sessions/21 calendar days before the research start solely to
test availability. Such bars are labeled outside-interval and cannot establish
in-interval coverage or authorize backfill. A new hosted run is required.

The provider `delisted_utc` field is recorded as an **ended ticker listing**, not
proof of company or security termination. Shared CIK, composite FIGI, or share-
class FIGI values create typed investigation candidates only; issuer CIK reuse is
never treated as a verified same-security ticker chain. The 6,607-versus-6,629
population discrepancy remains unresolved because the earlier response snapshot
is unavailable for identity-level reconciliation.

Hosted run `35125664186-1`, source commit
`205529d612c1ac2a3497a07f5cb6151d2eef62f4`, artifact
`alpha-atlas-v4-delisted-coverage-verification-35125664186-1`, closed the narrow
availability subitem: status `VERIFIED`; all six criteria passed; 12/12
representative probes and 2/2 AGU/HLS follow-ups verified across 12 distinct
tickers; pagination completed in seven pages with 6,607 inactive listings; and
no availability check failed. AGU and HLS each returned ten bars for
`2017-12-15` through `2017-12-29`, explicitly outside the research interval.
The evidence remained `research_only=true`, `automatic_promotion=false`,
`ready_for_live_routing=false`, and `full_backfill_authorized=false`.

This proves bounded historical access for provider-ended ticker listings only.
It does not prove company/security termination, complete delisted-security or
historical-universe coverage, complete daily-price histories, an effective-dated
identity chain, or terminal valuation. The source report and original GitHub
artifact archive are preserved unchanged; the manual **Alpha Atlas V4 Historical
Coverage Diagnostics** workflow records their separate SHA-256 values and emits a
derived corrected summary without live requests or source mutation. The original
human summary's combined `14 / 12` display is superseded by derived counts:
representative `12 / 12`, follow-ups `2 / 2`, distinct tickers `12`.

The next bounded diagnostic targets the reported daily gaps for GSS, SWCH, KAII,
and MGI, typed identity investigation candidates, and the unresolved population
comparison. Until its hosted evidence is reviewed, daily-price completeness,
effective-dated identities, terminal valuation, historical-universe completeness,
and `UNRESOLVED_EARLIER_SNAPSHOT_UNAVAILABLE` remain open.

Diagnostic run `35174216390-1` at commit
`b736f2cb387525175eb57141f0170e2c919cb6c6` remains `BLOCKED`. Its first targeted
provider request returned HTTP 404, and the initial diagnostic implementation let
that per-case error abort the complete report; the outer wrapper then omitted the
already validated source-report hash and request provenance. This was not a new
inactive-ticker discovery failure—the workflow had already retrieved, validated,
hashed, and summarized source run `35125664186-1`. The repaired diagnostic records
the sanitized failing URL, HTTP status, response hash/size, and ticker while
continuing the other bounded cases; any request failure still leaves the run
`BLOCKED`. The exact failed-run artifact is retained in the next audit bundle.

Diagnostic run `35176245513-1` at execution commit
`79b51d02e454dac6dae1c3660d7ddd8761bbab79` confirmed the request-isolation
repair and all six date-specific references, but exposed an identity-date defect.
All 20 historical-identity requests (BWINA, BWINB, PTVCA, PTVCB, KHD, MFCB,
MIL, TRY, TRY.B, FITBM, FITBO, HUB.A, HUB.B, ANDV, TSO, TSOw, FRM, XNR, KV.A,
and KV.B) incorrectly inherited `research_end=2026-09-15` and returned HTTP 404.
Those failures are preserved; they are not evidence that records for long-ended
tickers are unavailable. The corrected diagnostic first requests listing metadata,
selects the final exchange session inside each ticker's `list_date`/`delisted_utc`
interval, and emits `HISTORICAL_IDENTITY_DATE_UNRESOLVED` without making a dated
reference request when that interval cannot be established. Identifier types stay
typed; CIK reuse cannot verify continuity and classes, units, and warrants are not
collapsed.

The same run confirmed the exact aggregate gaps and successful point-in-time
references: GSS `2022-01-28`, SWCH `2022-12-06`, KAII `2023-01-19`,
`2023-02-17`, and `2023-02-24`, and MGI `2023-06-01`. Bounded primary-source
review now explains GSS by the effective acquisition and SWCH/MGI by documented
pre-open merger halts. These are nontrading explanations, not retrieved prices or
terminal-value policy. KAII remained the separately registered Class A share
ticker until the documented KAII/QDRO change at the `2023-02-27` market open;
no primary event found explains its three missing aggregates, so those sessions
remain `TRADING_ELIGIBLE_GAP_UNRESOLVED` pending venue trade/quote or halt records.
Publication and effective dates, source URLs, conclusions, and whether evidence
was available at decision time are emitted per case.

The manual **Alpha Atlas V4 Historical Coverage Diagnostics** workflow now
automatically downloads the exact unchanged `35176245513-1` artifact, validates
the accepted source report against SHA-256
`896a61604ae6fc8ba16821c0bf4d609b0e48efe275bd066318acc243351d0d7d`,
and always uploads `alpha-atlas-v4-historical-coverage-diagnostics-<run-id>-<attempt>`.
Dispatch it with no inputs. The next exact evidence action is to obtain Nasdaq
trade/quote or halt records for KAII on the three dates above. Historical-universe
completeness, terminal valuation, effective-dated transition verification, and the
6,607-versus-6,629 population reconciliation remain blocked. The earlier 6,629
snapshot has not been located and is not reconstructed.

Evidence extraction run `35180206136` preserved the top-level JSON from
diagnostic run `35178703375-1` (commit
`0dc495973f7b2bb2892c6e78ae0d933d475fe80d`) with SHA-256
`78ee7d3afe8434ff952b46b2899d529c37d4864cfe3f30ada61467bd48fb7081`.
That evidence corrects the prior progress description: all 20 identity records
had `list_date=null`, a populated `delisted_utc`, `query_date=null`, and
`reference_request_issued=false`. Zero request failures therefore established
diagnostic execution only; it verified no historical identity transition.

The focused repair now treats the timezone-normalized ended-listing timestamp as
a bounded investigation anchor when `list_date` is absent. It attempts no more
than five preceding exchange sessions per preserved identity record and retains
every unsuccessful response and response hash. It also performs exact before/after
dated-reference checks for the class-specific BWINA→PTVCA and BWINB→PTVCB mappings
documented in the issuer's August 1, 2018 Form 8-K, plus separate KAII→QDRO,
KAIIU→QDROU, and KAIIW→QDROW checks around the February 27, 2023 effective time.
The primary filings make these defensible transition cases, but the transitions
remain unverified until a hosted run returns and validates both dated references.
FITBM/FITBO `type=CS` versus preferred/depositary-share descriptions is now an
explicit metadata conflict, not accepted identity evidence. Older cases remain in
the output as possible pre-research predecessor links and are not counted as
verified or silently discarded.

The workflow preserves run `35178703375-1` unchanged, validates the extracted
JSON hash above, and publishes both the readable report and literal diagnostic
JSON to GitHub step summaries. Bounded availability remains closed. KAII's three
missing-price dates, terminal valuation, historical-universe completeness,
population reconciliation, and any transition lacking both dated responses remain
open. The next action is **GitHub → Actions → Alpha Atlas V4 Historical Coverage
Diagnostics → Run workflow** with no inputs.

Run `35182066580-1` at commit
`f106608775410c02a312ac01438d2ccabf5e6fbf` failed with
`IDENTITY_INVESTIGATION_RECORD_LIMIT_EXCEEDED`. The failure was in local
candidate processing, not a provider request or pagination call: one global
counter covered every ticker occurrence in every shared-identifier candidate
group and raised as it encountered occurrence 33 against a configured limit of
32, before issuing that ticker's listing-metadata request. The first 32 had been
entered for processing, but the outer exception report discarded their partial
results. The source remained the completely paginated seven-page, 6,607-record
`market=stocks`, `type=CS`, `active=false` enumeration. Because the failed report
did not record candidate-group or occurrence totals, the exact total above 32,
duplicates, unrelated tickers, and remaining cases cannot be recovered from that
artifact and are not guessed.

The repaired scope is the explicit 20-ticker investigation list already preserved
by the prior evidence. Its calculated caps are 20 target records, at most two
narrow exact-ticker listing pages and 20 listing rows per ticker, at most five
historical dates per target (100 attempts), and ten before/after transition
requests. Exact duplicates are removed, unrelated source candidates are counted
but excluded, and ambiguous metadata remains unresolved. Every new report records
the source filters and pagination, received/unique/duplicate/unrelated counts,
configured versus observed limits, completed records, and remaining cases. Limit
exhaustion returns a partial `BLOCKED` report rather than throwing away evidence.
No hosted result exists yet for this repair; the verified bounded availability
closure and all other open items above are unchanged.

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
- [x] Six-model real-artifact reload verification completed and permanently frozen.
- [x] Reload verifier repaired to reuse the exact diagnostic capture's persisted fold-local fill policies after registration/candidate/fold/ID checks.
- [x] Source verification `34872687197-1` blocker resolved by accepted hosted sources `34876711068-1` and `34906607045-1`.
- [x] Fold fill-policy evidence generator implemented and fixture-tested without fitting or holdout access.
- [x] Hosted `weighting_model_fill_policy_verification.json` generated, independently reviewed, and permanently frozen.
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

The evidence-only fill-policy certification records the persisted per-fold
policies used by the successful replay: coercion to numeric, replacement of
`+/-inf` with missing, feature-specific training-fold medians (or zero only when
the training fold has no finite value), applied before model scaling. It records
affected rows/cells, feature-specific values, matrix hashes, training-only
provenance, and corrected-state before/after hashes. The existing manual reload
workflow now consumes the already successful reload artifact and generates only
`weighting_model_fill_policy_verification.json`; it does not rerun replay or fit.

## Permanent weighting-model certification freeze

- [x] The one-time archival workflow is implemented and locally fixture-tested.
- [x] The permanent tag, release, and release assets were created by the manual
  hosted workflow.

**INDEPENDENT_WEIGHTING_MODEL_CERTIFICATION: CLOSED / VERIFIED / FROZEN**

Immutable release identity:

- Tag: `v4-weighting-model-certification-2026-09-14`.
- Release: **V4 Weighting Model Independent Certification — Frozen Evidence**.
- Final fill-policy source: **V4 Verify Weighting Model Reload**, run
  `34906607045`, attempt `1`, head SHA
  `e2ef0ad0adfb270fea3446e10d54b44bacb524e8`.
- Earlier reload/corrected-state source: **V4 Verify Weighting Model Reload**,
  run `34876711068`, attempt `1`, head SHA
  `38731eabaf2d179c8e3347769eda949ff678cd41`.
- Original `v4-weighting-model-reload-34876711068-1.zip` SHA-256:
  `63fdf075318d6d81c88ff43769c9699d464316b7c8dd546f4fb7b0c84bb8cdbf`.
- Original `v4-weighting-model-reload-34906607045-1.zip` SHA-256:
  `e63d7ed2c57c7b03045ed27d5254001cd6d48d79fbe8701f292d808fc19be00d`.
- `corrected_weighting_model_states.json` SHA-256:
  `f5d7e92e510a5d154a2e0b47ae1c36f317991624af3b9adda0b73bea78cb3b9a`.
- Freeze manifest SHA-256:
  `112885d5878caa861f770683d54d201e45e22466951a2fbb4bec7f436cc25adb`.

The immutable release assets are `corrected_weighting_model_states.json`,
`fill_policy_sources.json`, `reload_sources.json`, `SHA256SUMS.txt`, both original
run ZIPs named above, `V4_WEIGHTING_MODEL_CERTIFICATION_MANIFEST.json`,
`weighting_model_fill_policy_verification.json`, and
`weighting_model_reload_verification.json`. They prove all six arm/fold replays,
with zero missing or duplicate prediction IDs, zero decision mismatches, and zero
scores outside tolerance; establish the authoritative 43-feature order; and show
that corrected states remained immutable. They independently prove the fold fill
policy, training-fold-only fill statistics, and exact reconstructed validation
execution matrices. No model refit, prediction/score/decision change, final
holdout access, automatic promotion, or live routing occurred. This certification
must not be reopened or its release assets altered.

**Freeze V4 Weighting Model Certification** is fixed to the two accepted source
artifacts: reload/corrected-state run `34876711068-1` and fill-policy run
`34906607045-1`. It validates both exact sources, downloads both original ZIPs
without repackaging them, hashes them into a composite audit manifest, and targets tag
`v4-weighting-model-certification-2026-09-14` at the source run's `head_sha`.
The tag target is specifically the final fill-policy run's `head_sha`. The release
attaches both unchanged ZIPs, the manifest, checksums, and byte-identical reviewer
copies. A rerun verifies an existing freeze byte-for-byte rather than overwriting
it. The workflow performs no model computation, refitting, holdout access, or
promotion.

## Hosted Portfolio Verification

**HOSTED_PORTFOLIO_VERIFICATION: CLOSED / VERIFIED / FROZEN**

The exact source is **Track B Offline Challenger** run `35002276286`, attempt `1`,
head SHA `7da77897e10f228d2b60bedfbeb3c983836de5c6`, artifact
`track-b-offline-output`, downloaded ZIP SHA-256
`e7039b26a535e9399540b4a9e1033c85811091afc4a889efe77232c5287fc7f3`.
That historical job correctly remains recorded as **failed**: its first-version
verifier incorrectly scoped unrelated diagnostic rows as portfolio holdings. The
artifacts—not the failed conclusion—were subsequently accepted by **V4 Verify
Hosted Portfolio Evidence** run `35040195995-1`, head SHA
`3fac3df92c0bcfe5966f27481cc15c518aa3eb86`, artifact
`v4-hosted-portfolio-verification-35040195995-1` (ZIP SHA-256
`0132182c58fb48082897f27e7c517a796fc51e0af330527662930050047e3abd`).
The accepted `VERIFIED` result has no failures; its JSON SHA-256 is
`bdb57d0f692c7f32fd3a14b5de288a8c113a2294087a5cd35dcfb446ec80c693`,
source-manifest SHA-256 is
`c8fbe9b0760e4caf515d203bc05f7433879fe895c4fcc9de3fa91d8d5e682279`,
and source-checksum file SHA-256 is
`e4e2cf402c287ae9799a4fd5c14649986b0670f1a1d9962d284cd963a4f19ac6`.

Its core Phase 0 reconstruction passed all 34,446 rows,
while four non-portfolio `OBSERVATION_VALUATION_DIAGNOSTIC_ONLY` rows remain
visibly incomplete. None intersects the 25 actual selected holdings; all 25
selected holding valuations independently verified. Candidate
`challenger-stump-05-sma-50-v1` produced 1,919 orders, 50 fills, 1,894 explicit
rejections, 25 unique securities, and zero duplicate violations. All 28 equity
sessions reconciled exactly (maximum difference `0.0`, tolerance `1e-6`); maximum
drawdown `-0.06326763145155956` ran from `2026-08-14` to `2026-09-01` without
recovery. Economic gates remained separate, no model fitting or holdout access
occurred in verification, and promotion and live routing remained disabled.

The canonical `challenger_suite/portfolio_path/` output currently contains six
files (the five portfolio path/accounting payloads plus the separately added
selected-portfolio valuation certificate): `execution_policy.json`,
`execution_ledger.json`, `daily_portfolio_equity.json`,
`valuation_evidence_manifest.json`, `portfolio_metrics.json`, and
`selected_portfolio_valuation_certification.json`. The Phase 0 valuation output is
`phase0/reconstructability_report.json`, bound by
`phase0/temporal_safety_certification.json`.

The workflow now runs `scripts/verify_v4_hosted_portfolio.py` only after completed
hosted artifacts exist. It emits `v4_hosted_portfolio_source_manifest.json` and
`v4_hosted_portfolio_verification.json`, binds all inputs to workflow/run/attempt/
head SHA and SHA-256, and fails closed on source, valuation, candidate, ledger,
equity, cost, drawdown, economic-gate, promotion, or routing discrepancies. It is
evidence-only and cannot fit, predict, repair artifacts, promote, or route.

The permanent archive is assigned tag
`v4-hosted-portfolio-verification-2026-09-15` and release **V4 Hosted Portfolio
Verification — Frozen Evidence**, targeting the exact verifier-run head SHA. The
manual **Freeze V4 Hosted Portfolio Verification** workflow creates or verifies
that immutable release without regenerating evidence and never moves a conflicting
tag or overwrites conflicting assets.

## Next ordered open audit item

The first unresolved item still ordered by this authoritative checklist is
**historical-universe completeness for all securities eligible during the research
interval**, followed by effective-dated ticker identity—not Hosted V4 Holdout
Isolation Verification. The bounded diagnostics workflow is ready, but requires
the live manual run described above. Do not skip or reorder it. Hosted holdout-isolation remains
implemented and locally verified in
`tests/test_alpha_atlas_v4_holdout_isolation.py`, but its hosted verification must
not be started as the next checklist item until the earlier ordered Phase 1 items
are resolved or the checklist is explicitly reordered by an authorized review.
