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

Hosted run `35183625727-1` at commit
`5dcd7d8c99cbbc652f637dbb6b35dabcd62c66d9` completed the repaired bounded
scope and verified five **retrospective, class-specific** ticker transitions:
BWINA→PTVCA Class A and BWINB→PTVCB Class B effective `2018-08-01`, and
KAII→QDRO Class A ordinary shares, KAIIU→QDROU units, and KAIIW→QDROW warrants
effective `2023-02-27`. These results rely on the retained SEC filings and both
dated provider references for each mapping. They do not merge the three KAII
security types, prove every historical identity, or close broader historical-
identity coverage. The original artifact
`alpha-atlas-v4-historical-coverage-diagnostics-35183625727-1` is preserved
unchanged; the workflow validates run/attempt/commit, artifact ID `10480794049`,
and GitHub digest
`sha256:2b42f9aa9fa0a62197b96220fe4d18148325c4adc3bbd628bf7224c6d8251ebf`,
then records the exact extracted report SHA-256 in derived evidence.

Decision-time availability is corrected separately. The Protective filing was
SEC-accepted at `2018-08-01T16:14:57-04:00`, after the `09:30:00-04:00` effective
market open; the Quadro filing was accepted at
`2023-02-24T16:15:38-05:00`, before the `2023-02-27T09:30:00-05:00` effective
market open. Those comparisons establish public knowability relative to the
transition instants only. Run `35183625727-1` contains no exact `decision_at` or
`feature_cutoff_at` for an affected model decision, so all five decision-time
states are `UNVERIFIED/UNKNOWN`, the compatibility boolean is `null`, and feature
use is unauthorized. Retrospective transition verification remains valid.

KAII daily bars for `2023-01-19`, `2023-02-17`, and `2023-02-24` remain open.
The next bounded hosted run will recheck the exact day plus adjacent daily controls,
minute aggregates, trades, quotes, and trade-condition reference data using KAII
Class A only. Per date it permits four endpoint queries and at most six paginated
HTTP requests: one page per aggregate endpoint, two
trade pages, two quote pages, and 5,000 records per endpoint; report samples are
capped at 100 records while exact response hashes and counts are retained. An
entitlement denial, request failure, empty response, quote-only result, and budget
exhaustion remain distinct. No returned data replaces a bar or authorizes valuation.
Universe completeness, terminal valuation, population reconciliation, and remaining
identity coverage stay open.

Run `35233428008-1` is the pinned source for the next evidence-only KAII review.
The manual **Evaluate KAII Trade Conditions** workflow validates its successful
run metadata, commit `bf2776e0943b3c0cd392015da4005391d30b15da`, artifact ID
`10502208555`, name
`alpha-atlas-v4-historical-coverage-diagnostics-35233428008-1`, and GitHub digest
`sha256:082092918781f45dc61b57b12128903b9b4e0837ee86f391336bc5d72f6f3786`.
It preserves the original ZIP and exact top-level reports, records source and
derived report hashes, and does not rerun identity diagnostics.

For KAII `2023-01-19`, the derived evaluator treats the two saved size-one trades
individually: conditions `[17,37,41]` and `[16]`. It uses each saved condition's
separate consolidated and market-center open/close, high/low, and volume flags.
Massive human support has now confirmed that a restrictive condition takes
precedence over permissive co-conditions and that any odd-lot trade is excluded
from aggregation. The `[17,37,41]` record is therefore excluded under that
confirmed combination rule because condition 37 is odd lot. Support also confirms
that condition 41 updates price and volume, correcting the earlier AI-support
statement; condition 41 alone does not explain the absent bar. The separate `[16]`
record's interpretation remains attributed only to the saved condition table, not
independently verified by the correspondence. The evaluator may record the
narrow explanation `NO_OHLC_ELIGIBLE_RECORDS_IN_RETRIEVED_REGULAR_SESSION_EVIDENCE_UNDER_CURRENT_RULES`
only if both records are consolidated-OHLC-ineligible and the saved trade response
is pagination-complete. Current condition metadata has no demonstrated 2023
effective version, so historical applicability remains `UNVERIFIED/UNKNOWN`.
Even that scoped explanation is not a retrieved price or valuation evidence.

For `2023-02-17` and `2023-02-24`, saved complete regular-session empty trade
responses plus quotes prove only what Massive returned inside those request bounds;
quotes do not prove trades and empty responses do not prove venue-wide nontrading.
The smallest non-duplicative query is one full Eastern calendar-day KAII Class A
trade request per date, capped at two pages and 5,000 records. If those requests
remain empty, the exact remaining evidence is authoritative SIP/venue coverage or
provider clarification that the saved request hashes represent complete historical
SIP trades (including late reports) and whether condition/correction processing
excluded records from trades or aggregates. No provider contact or upgrade is
required by the workflow. All three price gaps, terminal valuation, full historical
coverage, and population reconciliation remain open pending the derived hosted
result.

The first **Evaluate KAII Trade Conditions** job failed before evaluation because
the workflow set up Python 3.11 but omitted the repository dependency installation;
the evaluator's existing bounded-request helper import reaches the Phase 0 module
and therefore the pinned `numpy==2.4.3` dependency. The workflow now follows the
repository-supported `pip install -r requirements.txt` pattern rather than adding
an unpinned package or chasing transitive imports individually. It separately
publishes source-validation status and evaluation status, preserves evaluator
stdout/stderr and traceback on failure, and leaves the job failed when evaluation
does not complete. No KAII evidence conclusion or checklist closure is attributed
to that dependency-failed run.

The repaired **Evaluate KAII Trade Conditions** execution completed successfully
as run `35255222521-1` at commit
`bed6a95b0b4fafa9f8fd18a8c9e33346fc83fddc`. Its artifact is
`kaii-trade-condition-evaluation-35255222521-1` (artifact ID `10511084736`, GitHub
digest `sha256:cd734e934fea432c65f6dda64df40e82bbd4817bc37f1ab488ecfa269e5d68cb`).
This evaluation run is distinct from source diagnostic run `35233428008-1`. The
manual **Record KAII Condition Findings** workflow validates those exact identities,
preserves the original archive and report bytes, and publishes exact SHA-256 hashes
for `derived/kaii_trade_condition_evaluation.json` and `.md` before creating any
derived correction.

- [x] KAII `2023-01-19` condition analysis completed within the saved, complete
  regular-session response: both size-one records are consolidated-OHLC-ineligible
  under the evaluated current rules. Massive human support confirms that
  `[17,37,41]` is excluded by odd-lot condition 37 despite permissive
  co-conditions and that condition 41 updates price and volume. `[16]` updates
  neither consolidated OHLC nor volume under the saved condition table; support
  did not separately verify condition 16. Historical 2023 applicability remains
  `UNVERIFIED/UNKNOWN`, the scope remains the saved regular-session response, and
  this does not retrieve a price.
- [x] KAII `2023-02-17` full-day bounded query analyzed: it returned two
  extended-hours odd-lot records (2 shares at `$10.19`, 3 at `$10.20`) with
  conditions `[14,12,37,41]`. Human support's confirmed combination rule excludes
  both because each contains odd-lot condition 37, regardless of permissive
  co-conditions. This is consistent with, but does not certify, the absent bar;
  historical rule applicability remains open.
- [ ] KAII `2023-02-24` price gap remains unresolved. The completed full-day query
  returned zero records with pagination complete, but that does not prove venue-wide
  zero trading or exclude retention, entitlement, correction, ticker-mapping, or
  provider coverage limitations. Human support separately reports a full-day KAII
  query with 52 quotes and no trades, but the preserved correspondence does not name
  a date, so its status is `DATE_ATTRIBUTION_UNCONFIRMED` and it is not attributed
  to February 24.

Human response received; scoped findings recorded in
[`alpha_atlas_v4_kaii_massive_human_support_supplement.json`](reports/alpha_atlas_v4_kaii_massive_human_support_supplement.json)
and its readable companion. The correspondence is preserved verbatim as
user-supplied evidence attributed to Massive human support; response timestamp,
agent, and ticket ID remain unknown. New-evidence hashes are recorded in
[`alpha_atlas_v4_kaii_massive_human_support_supplement.SHA256SUMS`](reports/alpha_atlas_v4_kaii_massive_human_support_supplement.SHA256SUMS).
The supplement links to, but does not modify, the original diagnostic/evaluation
reports or their hashes. Support confirms the mixed-condition/odd-lot rule, but
does not explicitly version it or establish historical applicability to the
currently served 2023 aggregates. Provider testimony remains distinct from saved
API responses and supplies no query bounds, timezone, entitlement, pagination,
response hash, or venue-completeness proof.

The corrected summary is derived from the preserved evaluation JSON and does not
repeat either full-day request. A factual Massive support draft and technical
evidence attachment retain sanitized bounds, provenance, response hashes, and any
provider request IDs actually present; absent IDs are not invented. The three gaps
remain unavailable as prices, historical rule applicability and valuation readiness
remain open, and terminal valuation, universe completeness, broader identity
coverage, and population reconciliation are unchanged.

The first **Record KAII Condition Findings** execution (`35552582183-1`) retrieved
the pinned source successfully but failed before preparation because direct file
execution set `sys.path[0]` to `scripts/`, so the namespace import
`scripts.evaluate_kaii_trade_conditions` could not resolve from the repository
root. The workflow now uses the repository-root module entry point
`python -m scripts.prepare_kaii_support_inquiry`. A preparation failure is reported
explicitly and cannot present preserved source evidence as a newly generated
finding; the always-upload step continues to preserve whatever source material was
retrieved. This execution-path repair changes no evidence conclusion or open item.

## Track B certification and diagnostics handoff

- [x] Certification separation and hosted portfolio accounting are the completed engineering baseline (hosted runs `34675971440-1` and `34689216730-1` are reproducibility checks, not independent performance samples).
- [x] Development-only signal-coverage, probability-calibration, and bounded threshold-sensitivity diagnostics implemented and locally fixture-verified.
- [x] Bounded development-only frozen-fold OOF capture and capture-to-report workflow implemented and fixture-tested.
- [x] Hosted development capture and basic diagnostic execution completed (`34769717178-1`, source Track B run `34689216730`).
- [x] Development-only concentration and score-stability analysis implemented and fixture-tested.
- [x] Development-only concentration and score-stability analysis completed locally in the prior review.
- [x] Two-arm current-versus-uniform weighting comparison protocol pre-registered in code and documentation.
- [x] Immutable artifact-bound weighting registration materialized and reviewed (`34796621288-1`; canonical registration SHA-256 `4112f445d8ef079550f02185e4592c05aff9c0321ee4bfa615b632360af4c5c3`).
- [x] Weighting-comparison runner implemented and deterministic-fixture verified.
- [x] Manual **V4 Register Weighting Experiment** workflow implemented and locally tested.
- [x] Hosted artifact-bound weighting registration completed (`34796621288-1`).
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

## Dependency review and reprioritized next work

This review changes **priority only**, not completion. It supersedes the former
strict ordering that put every historical-coverage question before any further
development measurement. The next comparison uses only already frozen development
OOF observations and predictions; it is not a backfill, refit, holdout evaluation,
or production test. Historical issues remain required before claims whose scope
actually depends on them.

### Evidence retained and evidence access boundary

The following completed evidence remains closed within its stated scope:

- bounded ended-listing availability from `35125664186-1` at
  `205529d612c1ac2a3497a07f5cb6151d2eef62f4`: 12/12 representative probes,
  2/2 follow-ups, and 12 distinct tickers; it is not complete historical coverage;
- the independently frozen weighting-model reload/fill-policy certification and
  its immutable release assets described above; and
- hosted portfolio accounting/valuation verification `35040195995-1` for all 25
  selected holdings, including costs, slippage, exact equity reconciliation, and
  drawdown. This certifies that historical result's accounting, not predictive
  advantage or promotion readiness.

Repository reports inspected in this review include
[`docs/reports/alpha_atlas_v4_phase1_readiness.json`](reports/alpha_atlas_v4_phase1_readiness.json),
[`docs/reports/alpha_atlas_v4_phase1_terminal_price_discovery.json`](reports/alpha_atlas_v4_phase1_terminal_price_discovery.json),
the canonical observation and execution contracts, and the implementation for
development diagnostics, cohort economics, and portfolio reconstruction. Hosted
ZIP contents not present in this checkout are prior reported evidence, not newly
re-inspected bytes; their local absence is not evidence that a completed item
failed.

### Open-item priority and dependency matrix

`DEFERRED_NONBLOCKING` means unresolved and unverified, never resolved. A deferred
item immediately reactivates if the artifact-bound materiality scan described
below finds that it intersects the next comparison.

| Open item | Priority | Affected decision/result/reliability requirement | Evidence/dependency | Claim or experiment blocked | Smallest sufficient acceptance criterion | Next action/reactivation condition |
|---|---|---|---|---|---|---|
| Artifact-bound materiality and comparability registration for the next development comparison | **BLOCKING_NEXT_EXPERIMENT** | Which canonical rows, securities, dates, prediction semantics, costs, and baselines may be compared | Canonical key and one-observation weighting are fixed by [`alpha_atlas_v4_canonical_observation_contract.md`](alpha_atlas_v4_canonical_observation_contract.md); development diagnostics currently state execution evidence may be unavailable | Any new claim of predictive advantage, net economic advantage, stability, or calibration | Before metric computation, hash-bind the exact canonical input, split plan, manifest, and OOF capture; prove zero holdout overlap; inventory every row intersecting unresolved identities, KAII dates, FITBM/FITBO, missing exits, or unavailable valuation paths; pre-register baseline and metric semantics; fail rather than drop a row | Perform the single task specified below. If an affected row exists, stop and promote only that concrete dependency to a repair blocker |
| Historical-universe completeness | **REQUIRED_BEFORE_BROADER_VALIDATION_OR_USE** | Universe membership, survivorship bias, denominator for stock-selection and historical claims | Phase 1 readiness is `BLOCKED_FULL_UNIVERSE_BACKFILL`; bounded availability proves access only | Full-universe backfill, broad historical-performance/generalization claims, and later training on a reconstructed universe | Effective-dated eligible population and exclusions reconcile for the claimed interval with no current-ticker projection or availability-based dropping | Resume before broader validation/backfill, or immediately if the materiality scan shows the next frozen input was constructed from the unresolved universe |
| Effective-dated identity coverage beyond the five verified transitions | **REQUIRED_BEFORE_BROADER_VALIDATION_OR_USE** | Stable security identity, corporate actions, feature/label joins | Five retrospective transitions are scoped evidence; shared CIK is not continuity | Claims spanning unresolved aliases; any experiment containing one of those predecessor/successor securities | Dated, class-specific continuity/separation evidence for every identity actually consumed by the claim | First intersect the exact experiment rows; investigate only intersecting identities, preserving shares/units/warrants separately |
| FITBM/FITBO `CS` versus preferred-depositary-share description conflict | **DEFERRED_NONBLOCKING** | Security-type eligibility and universe inclusion | Repository search finds these tickers only in historical-diagnostic/checklist logic; the hosted canonical input is not locally available for an independent membership check | Nothing yet; it blocks the next comparison only if either typed identity occurs in its exact input, eligible universe, or holdings | Artifact-bound count by ticker plus stable typed identifier equals zero; if nonzero, resolve type from effective-dated primary/provider evidence before computation | Include the exact membership count in the next registration; do not start a classification repair on zero intersection |
| KAII `2023-01-19`, `2023-02-17`, and `2023-02-24` historical price/condition questions | **DEFERRED_NONBLOCKING** | Price availability and any return/valuation path crossing those sessions | Human response received; scoped findings recorded. Restrictive/odd-lot precedence is confirmed, while historical 2023 applicability remains unknown; February 24's saved full-day response is empty and the support-reported 52 quotes/no trades has `DATE_ATTRIBUTION_UNCONFIRMED` | Any result that consumes a KAII price/valuation on an affected date | Authoritative historical-applicability evidence and, where valuation is required, a retrieved/certified price; date-specific attribution is also required before applying the 52-quote testimony to February 24 | Do not rerun or send another inquiry. Reactivate on clarifying human evidence or exact frozen-input intersection |
| 6,607-versus-6,629 inactive-listing population reconciliation | **DEFERRED_NONBLOCKING** | Population completeness and provenance of the unavailable earlier snapshot | Earlier 6,629 snapshot is unavailable; 6,607 is the later paginated population | Reproduction/comparison of the earlier population and broad completeness certification | Locate the original immutable 6,629 snapshot and reconcile identities; no reconstruction from fresh queries | Retain as unavailable until that exact snapshot is found; do not repeat discovery queries to manufacture a comparison |
| Common technically supported historical interval | **REQUIRED_BEFORE_BROADER_VALIDATION_OR_USE** | Valid common date range for features, context series, universe, and labels | Readiness report records `common_supported_interval=null` | Full backfill, retraining, and claims across a common historical interval | Intersection derived from complete required-source inventories with explicit family-specific gaps and no silent row loss | Defer execution until backfill/broader validation is authorized; reactivate if next frozen input falls outside already certified source bounds |
| Terminal/missing-exit policy and incomplete terminal valuation | **REQUIRED_BEFORE_BROADER_VALIDATION_OR_USE** | Executable labels, realized proceeds, portfolio equity, and drawdown | Static terminal discovery has no approved final policy; hosted portfolio verification covers its 25 holdings only | Any broader portfolio result or training/evaluation row with an unresolved exit | Every selected/consumed row has verified executable exit/consideration and timing, or the predeclared experiment fails; never infer zero/stale proceeds or drop the row | Materiality-scan exact next rows. If zero intersections, broader policy stays open; if nonzero, stop before comparison and resolve those events |
| Backfill feasibility from observed size/request/runtime samples | **REQUIRED_BEFORE_BROADER_VALIDATION_OR_USE** | Operational feasibility and boundedness of a future historical build | Representative access is not a full inventory or runtime estimate | Approval of a controlled full-universe backfill | Versioned object/request/byte/runtime estimate over the approved universe and interval with finite caps and failure behavior | Do only after universe, interval, identity, and terminal requirements are defined |
| Controlled backfill approval | **DEFERRED_NONBLOCKING** | Authorization boundary | Checklist explicitly authorizes no backfill | Any backfill execution | Separate explicit approval identifying frozen plan, sources, interval, budgets, and outputs | Reactivate only after prerequisite evidence and an explicit user authorization |
| Historical backfill execution | **DEFERRED_NONBLOCKING** | Data needed for later broad training/validation | Not authorized and prerequisites remain open | New broad-data training or historical certification | Approved plan completes with immutable provenance and no dropped observations | Only after controlled-backfill approval |
| Challenger training authorization | **DEFERRED_NONBLOCKING** | Any refit/new model state | Existing next task is measurement of frozen development predictions, not fitting | Training, tuning, candidate selection | Separate explicit authorization after trustworthy baseline evidence and required data gates | Do not train in this review or the next evidence-only task |
| Phase 1 exit gate | **REQUIRED_BEFORE_BROADER_VALIDATION_OR_USE** | Overall readiness for broader V4 validation/use | Depends on the preceding Phase 1 requirements | Holdout evaluation, promotion readiness, or production consideration | All required Phase 1 gates completed and evidence-bound review passes | Keep open; a development baseline comparison cannot close it |
| Model improvement and promotion readiness | **REQUIRED_BEFORE_BROADER_VALIDATION_OR_USE** | Whether V4 has trustworthy advantage and may proceed toward holdout/promotion | Registered weighting ablation was unfavorable (`+0.006319421301219023`; one of three folds improved); portfolio accounting certification is not an advantage test | Holdout evaluation, automatic promotion, and live routing | Pre-registered development evidence supports advantage and stability; then separately satisfy Phase 1, holdout, economic, and routing gates | First run only the bounded frozen-OOF comparison below after its registration is reviewed |

### Materiality conclusions for known historical issues

- **FITBM/FITBO:** no repository-resident consumed dataset or evaluated-holding
  evidence references either ticker outside the diagnostic/checklist code. Because
  the hosted canonical bytes are absent locally, materiality is **unknown**, not
  zero. The next registration must count ticker and typed-identifier intersections;
  only a nonzero result warrants classification repair.
- **KAII:** human response received; scoped findings recorded. Odd-lot precedence
  is confirmed, but historical 2023 applicability remains unverified and the
  support-reported 52 quotes/no trades cannot be assigned to February 24 from the
  response text. The unresolved dates do not block a frozen-input experiment if
  the immutable scan proves no affected row, price, or valuation path is consumed.
  No diagnostic rerun or second inquiry is requested.
- **Remaining identities and terminal valuation:** the correct unit of work is the
  exact frozen experiment input/selection, not the whole historical discovery
  population. Any intersection fails the comparison before metrics; zero
  intersection defers, but does not close, broader coverage.
- **Historical universe and point-in-time reference:** complete effective-dated
  membership is mandatory for a new reconstructed-universe experiment. It is not
  retroactively required to describe an already immutable canonical development
  sample, provided that sample's limitations and selection provenance are reported
  and no full-universe/generalization claim is made.

### Exactly one recommended next task

**Task: register and implement an evidence-only, development-OOF frozen-model
baseline comparison; do not execute it until the registration and materiality
report are reviewed.** This is the shortest valid route to determine whether the
already frozen predictions show predictive advantage, instead of continuing
nonmaterial historical edge-case work.

Copy/paste-ready implementation brief:

```text
Implement “Alpha Atlas V4 Development OOF Baseline Comparison” as research-only,
with no fitting, tuning, threshold selection, provider access, backfill, final-
holdout access, promotion, or production routing.

Inputs (exact immutable hosted sources already used by development diagnostics):
- canonical observations from Track B run 34689216730, expected SHA-256
  506073994052be852a5a00fc38e239b85e72a50494c3559640ea980fb6a52d9e;
- chronological split plan SHA-256
  bd60055adc6cb1b3f8b143c6c61031bced01c9151fcb3b67c33f75430106e8e8;
- frozen challenger manifest from the same source artifact (record its exact hash;
  fail if unavailable or mismatched);
- development OOF capture from diagnostics run 34769717178-1, SHA-256
  454febde2e14ca8a916222d86a6872db1c5e790a29429f2db6393d828e87e434.

Interval:
- derive and report the minimum/maximum feature-cutoff exchange dates and each
  fold's validation dates from the immutable split plan/capture; do not type a
  presumed interval or read final-holdout rows.

Before metrics, emit an immutable registration plus materiality report that:
- verifies all hashes, IDs, candidate/fold membership, purge/embargo, one-row-per-
  canonical-ID accounting, and zero final-holdout overlap;
- counts exact intersections with FITBM, FITBO, KAII on 2023-01-19/02-17/02-24,
  every unresolved historical identity, and every missing/uncertified exit or
  valuation path; never silently exclude a match; stop with BLOCKED if any match
  affects a required metric;
- records prediction semantics and uses calibration metrics only for candidates
  explicitly certified as probabilities.

Baselines and comparable assumptions:
- probability lane: training-fold prevalence applied unchanged to that fold's
  validation rows (Brier/log loss/calibration), versus frozen OOF probabilities;
- ranking/selection: equal-score/equal-weight date-cohort universe and cash/no-
  selection baselines, versus the frozen configured selection; no random baseline;
- economics: reuse the frozen execution-cost policy when present. Report gross,
  net after transaction costs/slippage, excess versus equal-weight date cohort,
  turnover/coverage, and stock-selection precision/recall. If only endpoint returns
  exist, label total return and drawdown NOT_EVALUABLE rather than manufacturing an
  equity path. Use a certified existing valuation path only if already present.
- report by fold and pooled descriptively, by time subperiod and available declared
  market-regime field; do not infer regimes retrospectively if absent. Include
  confidence calibration, decision coverage, abstention/risk/rule rejection, and
  concentration.

Pre-registered claim rule (development evidence only):
- do not claim advantage unless the frozen model beats its appropriate simple
  baseline on the primary metric in the aggregate and in at least 2 of exactly 3
  chronological folds, while reporting all secondary metrics and any sign reversal;
- never convert this result into holdout access, candidate selection, promotion, or
  live advice. A completed report may conclude “no demonstrated advantage.”

Outputs:
- registration.json/.md, materiality_report.json/.md,
  baseline_comparison.json/.md, per-fold rows, input manifest, and SHA256SUMS;
- always-uploaded artifact and literal GitHub summary, with execution status
  separate from evidence conclusion.
```

Until that task is separately authorized and its registration reviewed, the next
experiment remains unexecuted. V4 stays research/shadow-only, the final holdout
remains frozen, and automatic promotion/live routing remain disabled.

### Frozen-input baseline registration implementation and evidence status

- [x] Registration-only validator, materiality scanner, readable reports, checksum
  manifest, and manual read-only workflow implemented and synthetic-failure tested.
- [x] First real artifact-bound registration executed as `35659754050-1`; its
  evidence status is `REGISTERED_BLOCKED`, not ready.
- [x] Narrow validator/reporting correction for that run's evidence gaps implemented
  and synthetic-failure tested.
- [ ] **CORRECTED REAL ARTIFACT-BOUND REGISTRATION EVIDENCE: NOT YET EXECUTED.**
  Local GitHub authentication is unavailable and the exact source artifacts are not
  present in this checkout. Fixture results are not live evidence and do not supply
  corrected counts or a reviewed replacement registration hash.

The implementation is `scripts/register_alpha_atlas_v4_baseline_comparison.py`,
called only by manual workflow **V4 Register Development Baseline Comparison**.
The workflow has no inputs, `actions: read`/`contents: read` permissions, installs
`requirements.txt`, validates source metadata, downloads existing artifacts, and
uploads partial reports even when evidence readiness is blocked. It never computes
model-versus-baseline metrics.

Pinned source identities for the hosted check are:

- **Track B Offline Challenger** `34689216730-1`, commit
  `1f8f46db584dff0881273bdeae1c56c1a8a016c5`, artifact ID `10296724235`,
  `track-b-offline-output`, GitHub digest
  `sha256:017cdd8a8b30917e6e2f958e3cba1ae99827e002434b97f9de40c2c4ab871ef9`;
- **V4 Development Diagnostics** `34769717178-1`, commit
  `5d360cdbda802ae8527b35fe59f75920b8c827c8`, artifact ID `10321991290`,
  `v4-development-diagnostics-34689216730`, GitHub digest
  `sha256:e341166155b819dd98e3c9a82c2be1d260ba549d637e5fb7ed1f086f21d5b4d4`.

Repository expectations remain canonical input
`506073994052be852a5a00fc38e239b85e72a50494c3559640ea980fb6a52d9e`,
split-plan embedded content hash
`bd60055adc6cb1b3f8b143c6c61031bced01c9151fcb3b67c33f75430106e8e8`,
manifest byte hash
`ea4e55f9faa848219945d7e03c92c7a541645cd4d6df8aa3cfbd0d1334872f15`,
and OOF capture byte hash
`454febde2e14ca8a916222d86a6872db1c5e790a29429f2db6393d828e87e434`.
The hosted report must newly compute every consumed file's byte size/hash and label
comparison to these independent repository expectations separately.

The validator derives the interval and actual usable-fold count from source bytes;
it does not assume three folds. It blocks the documented two-of-exactly-three rule
if the observed count differs. It validates canonical IDs, candidate/fold pairs,
OOF membership and record cardinality, labels/returns/security/session joins,
threshold/target/horizon semantics, temporal purge and embargo metadata, and zero
holdout-ID overlap while retaining only holdout IDs—not holdout row content.

Materiality reports direct ticker/date and dependency-window intersections,
missing prices, uncertified valuation paths, and typed-identifier evidence status.
No affected row is removed. A direct unresolved intersection is `BLOCKED`; an
unavailable typed-identity mapping is `UNKNOWN`, never zero. The KAII supplement's
odd-lot finding is preserved, while its 52-quotes/no-trades date attribution and
historical-2023 rule applicability remain unresolved.

The frozen registration specifies training-fold prevalence, equal-weight eligible
date-cohort, and zero-return/no-cost cash baselines; frozen timing, threshold,
selection, abstention, weighting, and candidate semantics; endpoint gross/net,
calibration, precision/recall, coverage, concentration, fold/time stability, null
handling, and non-overlapping horizon-block uncertainty. Numeric costs may be used
only when present in frozen policy evidence. Total return and drawdown remain
`NOT_EVALUABLE` without certified portfolio paths/capital accounting, and
endpoint returns may not be compounded into a portfolio curve. The predeclared
advantage rule remains aggregate improvement plus improvement in at least two of
exactly three folds, scoped only to a development screen. Prior development-result
exposure and the unfavorable weighting ablation are disclosed.

Smallest current blocker: rerun the manual workflow against its authenticated,
pinned saved artifacts. Even a successful execution may report evidence readiness
`BLOCKED` or `UNKNOWN`; registration becomes ready only if the real integrity and
materiality reports pass and the generated registration hash is reviewed.

#### Run 35659754050-1 preservation and correction scope

The original **V4 Register Development Baseline Comparison** run `35659754050-1`
at commit `c9993aaed0822d16215a7bb443e7923d839e0b00` completed execution but correctly
reported `REGISTERED_BLOCKED`. Its artifact
`v4-development-baseline-registration-35659754050-1` is artifact ID `10666961124`,
GitHub digest
`sha256:c88ce85414d1859ca01253ee9a3c1639eaf9bd03fbc3b6828c971578d62bb0a2`,
and its internal registration hash is
`019fb1387941858c4588f19bb89b8acd34144487025f0233d6e20cb4bfafb107`.
The corrected workflow downloads and preserves that exact `registration.json`,
records its byte hash, and links it from—rather than overwriting it with—the new
registration.

The original report established 24,846 development rows, 41 candidates, three
usable folds, 123 candidate/fold pairs, 421,193 OOF assignments, zero holdout
overlap, and zero direct historical ticker/date intersections. Those are retained
as prior-run findings pending corrected revalidation. It also reported typed
identity `UNKNOWN`, all 24,846 rows missing entry/exit prices, no cost-policy
versions, and zero affected rows in the short summary.

The demonstrated reporting defects are narrow:

- typed identity was forced to unavailable by workflow provenance instead of
  inventorying canonical identifier fields and consuming the saved historical
  diagnostic mappings;
- price detection inspected only top-level `entry_price`/`exit_price` and ignored
  the documented `reconstruction_lineage.execution.entry_price/exit_price` source;
- frozen endpoint returns, raw execution prices, daily valuation paths, and
  certification were not reported separately;
- cost reporting looked only for a policy-version field and did not distinguish
  missing components, explicit zero, numeric one-way components, or returns already
  net of costs; and
- the short summary displayed zero direct matches without surfacing the typed,
  price, cost, and valuation evidence gaps.

The corrected validator now emits an identifier-type coverage table for
point-in-time symbol ID, share-class FIGI, composite FIGI, and CIK; consumes saved
diagnostic mappings with source hashes; treats CIK only as an investigation flag;
and separates confirmed share-class matches from temporally/share-class ambiguous
composite matches. Missing mapping coverage remains `UNKNOWN` for the affected
subset, not zero.

Price reporting now resolves documented nested execution lineage and reports
mutually exclusive `both_present`, `entry_only`, `exit_only`, `both_missing`,
`invalid`, and `unresolved_join` counts with representative canonical-ID examples.
It never reconstructs prices from returns. Return availability and gross/net status,
cost component availability/applicability, explicit valuation-path inspection, and
metric-family eligibility are separate report sections. Probability/classification,
gross endpoint, net endpoint, and portfolio metrics each receive `EVALUABLE`,
`NOT_EVALUABLE`, or `UNKNOWN` status and reason codes; zero direct intersections or
zero detected uncertified paths cannot imply complete coverage or certification.

No corrected real counts or new registration hash are claimed until the same manual
workflow is rerun. It is now additionally pinned to and retrieves source diagnostic
`35233428008-1` for typed mappings and prior registration `35659754050-1` for
immutable provenance. Performance comparison execution remains pending review.

### Supplemental source-resolution repair after run 35665126250-1

- [x] **Implementation — deterministic supplemental evidence resolution.** Run
  `35665126250`, attempt `1`, stopped in **Resolve exact files and write provenance**
  with `prior=1 identity=3 costs=1`; the registration step was skipped. This is a
  source-selection failure, not a new split-integrity or materiality result. The
  recursive basename uniqueness check has been replaced with pinned
  artifact-relative paths: `reports/registration.json` in registration run
  `35659754050-1`, top-level
  `alpha_atlas_v4_historical_coverage_diagnostics.json` in identity run
  `35233428008-1`, and the pinned nested selected-portfolio policy in Track B run
  `34689216730-1`. The latter remains explicitly different-scope evidence and does
  not establish cost applicability to development OOF results.
- [x] **Failure evidence — preserved and actionable.** The resolver inventories
  every same-basename candidate with artifact-relative path, byte size, and newly
  computed SHA-256; validates pinned run/artifact metadata and JSON schema; copies
  the intended source byte-for-byte; and writes JSON plus Markdown resolution
  reports before failing for a missing intended path, schema mismatch, or expected
  report-hash mismatch. A calculated file hash is not represented as an
  independently expected hash.
- [ ] **Evidence — corrected real registration remains pending.** Local GitHub
  authentication is unavailable, so the three report sizes/hashes inside artifact
  `10502208555` were not independently inspected here and no corrected registration,
  materiality result, split-integrity result, or new registration hash is claimed.
  After merge, run **V4 Register Development Baseline Comparison** with no inputs;
  review `supplemental-resolution.{json,md}` first, then the registration outputs.

### Track B cost-policy source-path repair after run 35686093927-1

- [x] **Implementation — pinned nested policy path.** The hosted resolver confirmed
  that prior-registration and identity selection completed, then failed only with
  `COST_POLICY_EVIDENCE_INTENDED_PATH_MISSING`. The Track B policy is now pinned to
  `track_b/runs/34689216730-1/challenger_suite/portfolio_path/execution_policy.json`
  relative to the validated `track-b-offline-output` artifact root. There is no
  recursive or root-level fallback.
- [x] **Recorded failed-run evidence.** Run `35686093927`, attempt `1`, recorded the
  selected file as 525 bytes with SHA-256
  `477293feee5fa31ab876b9db589b2c7be6b34d81ccf893f4d2032c2d6c62b2ef`.
  The resolver enforces that value while labeling its provenance as recorded by
  that hosted failure; this documentation does not claim a second independent hash
  verification. The policy remains `LOCATED_DIFFERENT_SCOPE` and cannot establish
  development-OOF cost applicability.
- [ ] **Evidence — corrected registration still pending.** This path repair does
  not verify comparison readiness. Rerun **V4 Register Development Baseline
  Comparison** with no inputs after merge and review the resolver, materiality, and
  split-integrity reports before any comparison execution.

### Development return and symbol-lineage trace after run 35692705575-1

- [x] **Retain completed evidence.** Registration `35692705575-1`, internal hash
  `f2eb532c9c050977197c5a3306b1948e2d9a397bc7dd31f7c7efda7255332c3c`,
  completed source resolution and verified split integrity. It reported entry/exit
  prices for all 24,846 development rows and endpoint returns for all 10,273 unique
  validation rows. Portfolio return/drawdown remains `NOT_EVALUABLE`.
- [x] **Implementation — return semantics cannot be inferred from presence.** Gross
  endpoint economics is now `EVALUABLE` only when source provenance binds Track B
  commit `1f8f46d...`, diagnostics commit `5d360cd...`, and every validation return
  matches `round(exit / (entry * split_factor) - 1, 6)` within `5e-7`. The traced
  formula is a decimal split-adjusted price return with no cost/slippage term;
  dividend adjustment is not established. Missing or mismatching trace evidence
  yields `GROSS_RETURN_SEMANTICS_UNVERIFIED`. Net economics still requires the
  applicable development policy and cannot borrow the selected-portfolio policy.
- [x] **Implementation — symbol-ID guarantees narrowed.** Pinned producer code uses
  `event.point_in_time_symbol_id` when supplied and otherwise falls back to
  `symbol:event_day`. The validator now counts fallback-pattern rows and cross-symbol
  collisions and labels the fallback a ticker/date observation key, not proof of a
  listing, security, ticker-change chain, or share-class continuity. Canonical/OOF
  join integrity remains separately verified.
- [x] **Pinned-code supplement.** The source-only trace is preserved at
  `docs/reports/alpha_atlas_v4_development_semantics_source_trace.json`, SHA-256
  `9337c5b2b9e1fdaf78e47f098175d030d172dcd8bd48c21d690206797d618a30`.
  It records code behavior, not yet the real saved-row consistency counts.
- [ ] **Evidence — authenticated consistency rerun pending.** Run **V4 Register
  Development Baseline Comparison** with no inputs. The smallest remaining evidence
  is the real formula match/mismatch/unsupported counts plus saved-ID fallback,
  collision, and affected-scope counts. Until then, typed-identity sufficiency stays
  `PARTIAL_UNKNOWN`, gross semantics are not newly certified here, and comparison
  readiness is not approved.

### Exact frozen identity dependencies and scope decision after run 35741072083-1

- [x] **Retain completed evidence.** Registration `35741072083-1`, internal hash
  `9c573b0c75b97dbbe9a1b3b17c694425fd647e6fcbd600d2fd2c636d51203608`,
  verified split integrity, all 24,846 development entry/exit prices, and formula
  matches for all 10,273 validation gross split-adjusted price returns. Dividend
  adjustment remains unestablished; no applicable development cost policy exists;
  portfolio total return/drawdown remains unavailable.
- [x] **Implementation — dependency scope is explicit.** Registration now emits
  `identity_dependencies.{json,md}` with mutually exclusive `SUPPORTED`,
  `CONFLICTING`, and `UNRESOLVED_IDENTITY` row classifications; canonical IDs;
  validation membership; feature-history, label/execution, corporate-action,
  fold/OOF, and date-cohort dependency windows; separate occurrence counts; and
  canonical, mapping, and pinned-code provenance. A saved latest feature-source date
  is not mislabeled as the full lookback start; absent exact lineage remains
  `UNKNOWN` rather than zero.
- [x] **Implementation — metric impact separated from arithmetic.** Probability,
  classification, and verified-gross endpoint metrics may be mechanically
  computable while security-continuity uncertainty limits interpretation. Net
  economics remains blocked by the missing applicable cost policy. Portfolio and
  terminal-valuation claims remain `NOT_EVALUABLE`.
- [x] **Scope proposal prepared, not activated.** Registration emits
  `comparison_scope_decision.{json,md}` recommending review of Option A only: the
  exact frozen candidates and three OOF folds, registered probability/classification
  metrics, and a gross split-adjusted endpoint diagnostic under the unchanged
  comparison rule. It expressly excludes net profitability, portfolio performance,
  durable advantage, and promotion claims. Option B is inactive and would require a
  new pre-results cost-policy registration plus the same identity prerequisites.
- [x] **Review companion preserved.** The pre-execution decision is recorded in
  `docs/reports/alpha_atlas_v4_identity_scope_decision.{json,md}` with SHA-256
  `e88b9bb38f3d5968b39629b9343d7e8a14c06761320a000e16d13f1f2b83fc86`
  (JSON) and `9a9314c951e666c3c150d89f724d5e32ddce2af2b1565c08f06585011855d720`
  (Markdown). Unknown artifact-bound counts remain explicitly pending.
- [ ] **Evidence readiness — hosted dependency counts pending.** Local artifact
  authentication is unavailable. Run **V4 Register Development Baseline
  Comparison** with no inputs and review the identity-dependency and scope-decision
  reports. This is the single next action; it is a review step, not authorization to
  score the comparison. No broad identity backfill is requested.

### Narrow frozen-sample diagnostic v1 after run 35755237312-1

- [x] **Evidence retained.** Registration `35755237312-1`, internal hash
  `aacb8521f35ce37e5beed7ea1376c8a963be8fb79667e5c97e2180a5ce960a06`,
  reports 24,846 development rows, 10,273 validation rows, 515 tickers, and
  3,777 ticker/date observation keys. Split and canonical/OOF joins and the gross
  split-adjusted formula are verified. Continuity and the full lookback start remain
  unresolved; no identity conflict was confirmed; applicable development costs are
  unavailable; the registered net-primary experiment remains blocked.
- [x] **Specification prepared, not approved or executed.** The separately versioned
  `alpha-atlas-v4-narrow-frozen-sample-diagnostic.v1` asks only how saved frozen OOF
  predictions compare with simple baselines on recorded validation observations.
  JSON SHA-256 is
  `f55e749a6616506b1e227ddebb96dad569b33cd3c56599fe189803dc68173e0f`;
  Markdown SHA-256 is
  `f7452cee7eb57cc8bd1bca1785c594d770616381f90ad50ec968f897db66559a`.
- [x] **Comparison rules disambiguated.** Probability candidates use Brier versus
  training-fold prevalence; classification reports frozen-threshold precision,
  recall, coverage, and abstention; gross selection diagnostics use an equal-weight
  eligible ticker/date timing cohort and cash only as zero-return arithmetic. Signed
  fold differences and equal-fold aggregates are descriptive. The two-of-three
  registered advantage criterion is not reused as certification, candidates are not
  ranked or removed, and inapplicable metrics remain explicit.
- [x] **Weighting fixed before scoring.** Observation metrics weight canonical rows
  equally. Gross diagnostics first equal-weight repeated observations within ticker,
  date, horizon, entry, and exit; then equal-weight ticker groups within matching
  timing cohorts. Incompatible horizons/windows remain separate. Abstentions and
  empty-selection cohorts remain in denominators; row mappings and weights must
  reconcile. These are ticker groups, not verified securities.
- [x] **Audit implementation complete.** The existing registration workflow now
  runs a hash-bound membership/grouping/weight audit after registration, even while
  registration remains blocked, and uploads the audit without scoring. Specification
  or input hash changes fail closed.
- [ ] **Review/real audit pending; diagnostic execution not authorized.** After
  merge, run **V4 Register Development Baseline Comparison** with no inputs and
  review `narrow_diagnostic_grouping_audit.{json,md}` plus the specification. Do not
  execute scoring until the specification hash is explicitly reviewed and approved.

### Narrow diagnostic split-plan hash correction after run 35758737998-1

- [x] **Cause confirmed.** Registration completed, but the audit compared semantic
  split content hash `bd60055a...` to the saved plan's file bytes and failed with
  `FROZEN_INPUT_HASH_MISMATCH:plan`. No grouping audit or scoring completed.
- [x] **Specification v1 preserved; v1.1 corrects hash semantics only.** The exact
  2,505,426-byte plan is pinned by file SHA-256
  `f11257cff0befc1f0b46e4cab9a64678c766b6fe2abdc50b07861a18f7d6933a`.
  The producer's `canonical_json_hash` is recomputed after removing `plan_sha256`
  and must equal both the registered and embedded content hash
  `bd60055adc6cb1b3f8b143c6c61031bced01c9151fcb3b67c33f75430106e8e8`;
  the embedded declaration is not trusted alone. Canonical, manifest, and capture
  checks remain exact byte hashes.
- [x] **Corrected specification hashes.** v1.1 JSON SHA-256 is
  `206e86dcfd2fa25edfbb8b3201e84e342da55ace668b4e0c6d4d31a4132912b2`;
  Markdown SHA-256 is
  `225b87df9134eff3eacad1fa4c29eb0da10670c56253b2cfa0a99013174f86c1`.
  Dataset, folds, candidates, weighting, metrics, claim limits, and blocked net rule
  are unchanged.
- [x] **Failure evidence hardened.** Audit failures now write JSON and Markdown with
  role, hash type, expected/actual values, and explicit grouping/scoring flags, and
  preserve the exact reviewed specification before exiting nonzero.
- [ ] **Real grouping audit still pending.** After merge, run **V4 Register
  Development Baseline Comparison** with no inputs. Review only the grouping audit;
  this correction does not approve performance scoring.

### Reviewed narrow diagnostic execution authorization after audit 35788348286-1

- [x] **Grouping audit complete within scope.** Run `35788348286`, attempt `1`,
  produced `AUDIT_COMPLETE_NO_SCORING` under specification v1.1 hash
  `206e86dcfd2fa25edfbb8b3201e84e342da55ace668b4e0c6d4d31a4132912b2`:
  10,273 unique validation observations and 421,193 assignments for 41 candidates
  across three folds, reconciled cohort weights, and no holdout-content access.
- [x] **Narrow scoring implementation prepared.** A separate manual workflow,
  **V4 Execute Narrow Frozen Sample Diagnostic**, validates the exact specification,
  four frozen inputs, and approved audit identity before scoring. It preserves frozen
  thresholds, abstention/rejection flags, training-fold-only prevalence, stable
  manifest roster order, row/cohort weights, empty-selection cash, and equal-fold
  descriptive aggregation. It never invokes training, tuning, or holdout content.
- [x] **Interpretation remains bounded.** Calibration intervals/bins and bootstrap
  intervals are emitted as not evaluated because v1.1 does not fix bin count,
  replicate count, confidence level, or seed. Same-security continuity, full
  lookback start, dividends, applicable development costs, and certified portfolio
  paths remain unresolved. The net-primary registration stays blocked regardless of
  descriptive results.
- [x] **Real narrow scoring execution completed.** Run `35795768048-1` produced the
  saved conditional descriptive results. Completion is execution evidence only and
  does not establish predictive advantage or broader validation readiness.

### Narrow execution histogram serialization repair after run 35792997101-1

- [x] **Cause confirmed as representation-only.** Execution stopped before scoring
  at `groups_by_row_multiplicity`: the approved JSON necessarily reloaded histogram
  keys as strings while the independently reproduced in-memory `Counter` used
  integers. Direct dictionary equality therefore failed even when every bin/count
  was identical. No descriptive finding was produced by run `35792997101-1`.
- [x] **Strict normalization implemented.** Approved and reproduced histograms are
  separately normalized to positive-integer multiplicities and nonnegative-integer
  counts, with booleans, fractional counts, malformed keys, and normalization
  collisions rejected. Equality remains exact after normalization; missing, extra,
  or changed bins remain blocking. All other grouping and input checks are unchanged.
- [x] **Failure evidence improved.** Results now preserve the implementation commit,
  already validated input hashes, grouping/scoring stage flags, the exact differing
  field, normalized expected/actual summaries, key types, missing/extra bins, and
  changed counts. A successful result also records that the mismatch was only JSON
  representation.
- [x] **Authorized narrow scoring subsequently completed.** Run `35795768048-1`
  completed against the pinned inputs after this serialization repair. This closes
  execution only; its descriptive output does not close the broader net-return or
  predictive-advantage gates.

### Ranking zero-selection investigation after run 35795768048-1

- [x] **Narrow diagnostic execution is complete within its descriptive scope.** Run
  `35795768048`, attempt 1, completed under specification v1.1
  (`206e86dcfd2fa25edfbb8b3201e84e342da55ace668b4e0c6d4d31a4132912b2`).
  Its three ranking-lane zero-exposure results are arithmetic descriptions, not
  evidence of stock-selection skill or broader predictive advantage.
- [x] **Evidence-bound investigation tooling is implemented without rescoring.** It
  validates the completed result and approved audit, consumes the exact frozen
  manifest and OOF capture, keeps top-level producer predictions distinct from
  scores and record-level fields, and emits per-candidate/fold gate counts,
  mutually exclusive dispositions, deterministic canonical-ID examples,
  provenance, and checksums. Missing/null selection evidence is never treated as
  explicit false.
- [x] **Real cause classification completed.** Investigation run `35815996007-1`
  reconciled 30,819 assignments (10,273 observations for each of three candidates
  across three folds) as `EXPECTED_FROZEN_ZERO_SELECTION`: producer and diagnostic
  selections were both zero, with no missing/uninterpretable selection evidence and
  no abstention, risk, or rule rejection. No diagnostic interpretation defect was
  established and no corrected scoring is required. The prior gross difference is
  zero exposure against a negative baseline, not successful stock selection.
- [x] **Manual-workflow YAML startup repair completed after commit `b5aa219`.**
  GitHub rejected the workflow before creating a job because the upload step used a
  flow-style `with` mapping whose unquoted artifact-name expression contains `{`
  characters. The upload inputs now use an equivalent block mapping. Pinned source
  identities, read-only permissions, manual-only dispatch, investigation logic,
  failure-summary publication, and unconditional evidence upload are unchanged.
  This repaired workflow parsing only; the investigation subsequently completed in
  run `35815996007-1` as recorded above.
- [x] **Producer-code provenance startup repair completed after run
  `35812443361-1`.** Git-tree inspection establishes that commit
  `5d360cdbda802ae8527b35fe59f75920b8c827c8` contains both
  `scripts/capture_alpha_atlas_v4_development_oof.py` and its imported producer
  `scripts/train_challenger_suite.py`; the failed job's default shallow checkout did
  not contain that historical commit. The workflow now fetches the exact diagnostic
  and Track B commits before validating paths. The investigation records Git blob
  IDs and file-byte SHA-256 values and emits a specific partial report if a required
  historical reference remains unavailable; the generic workflow fallback does not
  overwrite an existing detailed report.
- [x] **Supplemental closure recorded without rewriting historical evidence.** The
  original report's generic corrected-scoring next action was conditional on an
  interpretation defect; that condition was not met. The closure note preserves the
  distinction between `ranking_score_not_buy_probability` for the two ranking-lane
  candidates and `probability` for `challenger-ranking-top5-model-v1`.
- [x] **Cohort-relative ranking proposal is `PREPARED_FOR_REVIEW`.** Version 1 asks
  whether the exact saved development scores order ticker/date/timing groups under
  one fixed top-five rule justified by the frozen `daily_top5` training target. It
  freezes inputs, roster, semantics, cohorts, deterministic ties, missing-evidence
  blocking, equal selected-group weights, baselines, and fold aggregation. Proposal
  JSON SHA-256: `000a0f8e2ba0fbaf563eb64dc3589fe4b2ae0695fd6c63fa8c536675d9b26f4d`.
- [ ] **New experiment is `NOT_AUTHORIZED / NOT_EXECUTED`.** Review must decide
  whether fixed top five with weights renormalized equally across selected ticker
  groups is the acceptable exploratory contract. This is a new post-diagnostic rule,
  not a correction to v1.1; no implementation or execution exists in this task.
- [ ] **Broader gates remain open.** Applicable development costs, net-return
  validation, terminal valuation, complete historical coverage, and promotion
  readiness are unchanged and unresolved.
