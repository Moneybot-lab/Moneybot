# Alpha Atlas V4 ticker/timing-group return regression registration v1

**Status:** `PREPARED_FOR_REVIEW`
**Implementation / training / execution:** `NOT_AUTHORIZED / NOT_EXECUTED`
**Registration JSON SHA-256:** `c3f58f47354f07a5c0aa536344b97fb542fdde0bee1651f86852d3dd69a6469a`

## Hypothesis and positioning

> Training directly on equal-weight ticker/timing-group five-session returns may improve top-five gross endpoint selection relative to the eligible-cohort baseline.

This is an exploratory development screen informed by the unfavorable results in run `35923707545-1` and the alignment review at commit `c3b673c0`. It is not an untouched confirmatory test and does not promise improvement.

## Frozen evidence and membership

The only data source is `track-b-offline-output` run `34689216730-1`, attempt 1, commit `1f8f46db584dff0881273bdeae1c56c1a8a016c5`, artifact `10296724235`, digest `sha256:017cdd8a8b30917e6e2f958e3cba1ae99827e002434b97f9de40c2c4ab871ef9`.

| Input | Exact binding |
|---|---|
| Canonical JSONL | `506073994052be852a5a00fc38e239b85e72a50494c3559640ea980fb6a52d9e` |
| Split-plan bytes | `f11257cff0befc1f0b46e4cab9a64678c766b6fe2abdc50b07861a18f7d6933a` |
| Split semantic hash, embedded and independently recomputed | `bd60055adc6cb1b3f8b143c6c61031bced01c9151fcb3b67c33f75430106e8e8` |
| Frozen manifest | `ea4e55f9faa848219945d7e03c92c7a541645cd4d6df8aa3cfbd0d1334872f15` |

The old OOF capture remains prior evidence only; its predictions are not inputs to this model. Final-holdout membership is metadata-only, and holdout features, labels, prices, returns, and predictions are forbidden.

| Fold | Training rows | Validation rows | Training groups | Validation groups | Purge/embargo boundary |
|---|---:|---:|---|---|---|
| 1 | 11,480 | 4,464 | 34 dates, 2026-04-02–2026-05-21 | 11 dates, 2026-06-01–2026-06-15 | latest train label completion 2026-05-29 20:00Z; earliest validation decision 2026-06-01 13:36:35Z; one session |
| 2 | 17,856 | 2,479 | 46 dates, 2026-04-02–2026-06-09 | 11 dates, 2026-06-17–2026-07-02 | latest train label completion 2026-06-16 20:00Z; earliest validation decision 2026-06-17 15:01:49Z; one session |
| 3 | 11,366 | 3,330 | 32 dates, 2026-05-12–2026-06-26 | 11 dates, 2026-07-07–2026-07-21 | latest train label completion 2026-07-06 20:00Z; earliest validation decision 2026-07-07 13:30:27Z; one session |

Canonical observation-ID vectors in the split plan define membership. No row may move, disappear, or change folds. After grouping, any train/validation crossing, incompatible timing, or failed group-level purge/embargo check blocks the fold.

## One aligned training example

Group key: event date, uppercase ticker, horizon `5`, entry timestamp, and exit timestamp. Ticker is only an operational identifier, not verified security continuity. Stable identity is SHA-256 over the canonical JSON vector of that key plus sorted source canonical IDs.

All members must agree on event date, ticker, horizon, decision time, feature cutoff, entry, and exit. Feature sources must be available by the unanimous feature cutoff; `feature_cutoff_at <= decision_at < entry_at < exit_at` and the established XNYS timing contract must hold. Required conflicts block the fold.

* Numeric features: arithmetic mean of finite member values. Binary indicators therefore become member fractions.
* Missing numeric values: record finite/missing counts; a wholly missing group feature is imputed by the training-fold median.
* Categorical features: none selected. Identifiers and timestamps are never averaged.
* Outcome: equal-observation mean of every member's finite saved gross split-adjusted `return_5d`. Any missing member outcome blocks the fold.
* Weight: exactly `1.0` per group; observation multiplicity and return tails add no weight.
* Provenance: retain every sorted source canonical ID and original fold assignment.

Training and evaluation use the same fixed structural eligibility rules. Old model scores, selections, abstentions, and risk/rule rejection flags are never filters. Validation outcomes remain unread until group membership and predictions are frozen.

## Fixed features and preprocessing

The registration freezes 42 manifest-supported numeric features: the complete existing raw-price-excluding ranking feature set. `feature_close`, identifiers, timestamps, outcomes/buckets, historical scores, predictions, selections, and gates are excluded. The JSON contains the ordered list.

Within each fold only: aggregate groups, fit per-feature median imputation, then mean/population-standard-deviation scaling. A nonfinite or near-zero standard deviation becomes `1.0`. Persist the ordered features and preprocessing statistics with a canonical JSON hash. There are no categorical encodings or unseen-category rules because no categorical input is selected.

## One fixed model

* Family: deterministic ridge linear regression.
* Target: continuous `group_gross_split_adjusted_return_5d` in decimal-return units.
* Score: predicted gross split-adjusted five-session group return—not a buy probability, `daily_top5` probability, or `label_up_5d` probability.
* Objective: mean squared error plus `alpha * sum(beta²)` on standardized features; intercept unpenalized.
* Alpha: `1.0`, defined for this normalized regression objective rather than borrowed from classification.
* Solver: float64 closed-form solve; deterministic SVD pseudoinverse with `rcond=1e-12` only if singular.
* Seed: `0`, serialized but unused because the algorithm has no randomness.
* Fits/search: exactly three fold fits of one configuration; no family, parameter, feature, threshold, or calibration search.

## Frozen selection and evaluation

For each fold/event-date/horizon/entry/exit cohort, rank predicted returns descending and choose `min(5, eligible groups)`. Ties use ticker ascending, then stable group identity. There is no predicted-return cutoff. Selected groups receive equal weights summing to one. Empty cohorts remain explicit with cash weight one and selected return zero. The baseline uses the identical eligible population with equal group weights.

Primary metric: selected equal-weight gross endpoint return minus eligible-cohort equal-weight gross endpoint return. Report selected return, baseline, selected-minus-zero-cash, counts, coverage, empty cohorts, missing evidence, and denominators. Cohorts are equal-weighted within folds; folds are weighted exactly one third only if all three complete. A blocked fold makes the aggregate `NOT_EVALUABLE`; surviving folds are never renormalized. Overlapping endpoints are not compounded, and no uncertainty interval is registered.

Development screen, frozen before scores:

1. Aggregate selected-minus-baseline is positive.
2. Selected-minus-baseline is positive in at least two of three folds.
3. Disclose every fold difference and absolute selected/baseline return, including losses.

Passing supports further research only—not significance, net profitability, external superiority, promotion, or production readiness.

## Future capture contract

A future capture must separately serialize the effective continuous target, predicted-return semantics, evaluation return, group identity/source IDs, fold, decision/cutoff/entry/exit timestamps, and eligibility/selection dispositions. It must persist mappings and predictions before reading validation outcomes. `label_up_5d`, `daily_top5`, buy-probability, and top-five-probability metadata are forbidden as effective target semantics for this model.

## Finite budget and implementation checklist

Budget: one configuration, three fits, at most 24,846 group examples per fold, 42 features, two CPU cores, 4 GiB memory, 30 minutes, zero provider calls, zero holdout fits, and zero search trials.

1. Verify this registration hash and all frozen byte/semantic hashes.
2. Load development membership only; keep holdout content inaccessible.
3. Construct groups and row provenance; run crossing, timing, purge, and embargo gates.
4. Fit training-only preprocessing and the single ridge configuration.
5. Persist pre-outcome development predictions and reproduce mappings.
6. Join outcomes and score only if all fold gates pass.
7. Emit the registered artifacts and no promotion action.

## Limits

Same-security continuity, historical coverage, applicable costs, terminal valuation, broader validation, and promotion readiness remain unresolved. Implementation, training, prediction, and execution are not authorized by this registration.
