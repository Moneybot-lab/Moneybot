# Alpha Atlas V4 cohort-relative ranking experiment proposal v1

**Status:** `PREPARED_FOR_REVIEW`; execution is `NOT_AUTHORIZED / NOT_EXECUTED`.

## Question

Do the saved ranking scores order the recorded development observations usefully when evaluated under an explicit cohort-relative selection rule?

This is exploratory and post-diagnostic because development results have already been viewed. It is not an untouched confirmatory test.

## Candidate treatment

| Candidate | Saved semantics | Proposed eligibility |
|---|---|---|
| `challenger-ranking-lane-full-v1` | `ranking_score_not_buy_probability` | Eligible as an ordinal ranking score. |
| `challenger-ranking-lane-recent-half-v1` | `ranking_score_not_buy_probability` | Eligible as an ordinal ranking score. |
| `challenger-ranking-top5-model-v1` | `probability` | Eligible with qualification: rank the probability of its `daily_top5` target; never call it a buy probability. |

The producer used `daily_top5` as the training target: within each event date, the five highest saved training returns were labeled positive. The frozen 0.60 value was a pointwise decision threshold, and the completed investigation confirms it selected nothing. “Top5” was a training-target definition, not a cohort-relative position-taking implementation. This proposal introduces one fixed top-five selection rule because the existing target establishes five; it does not search k.

## Proposed rule

1. Preserve exact development membership, folds, candidates, saved scores, and input hashes. Never load final-holdout content.
2. Define compatible cohorts by candidate, fold, event date, horizon, entry timestamp, and exit timestamp.
3. Within a cohort, group observations by ticker. These are **ticker groups, not verified securities**.
4. Give each observation equal weight when calculating its ticker group's arithmetic-mean score. Any missing/nonfinite score or required grouping/return evidence blocks that candidate/fold result; nothing is silently removed.
5. Rank ticker groups by descending score. Break ties by ticker ascending and then the sorted canonical-ID vector. Outcomes never enter ranking or tie-breaking.
6. Apply saved abstention/risk/rule gates before ranking. Missing gate evidence blocks evaluation. Select `min(5, eligible groups)`.
7. Give selected ticker groups equal weights summing to 100%. If no group is eligible, retain the cohort as 100% zero-return cash. This differs deliberately from v1.1, which retained full-universe weights and assigned unselected weight to cash.
8. Compare selected gross split-adjusted endpoint return with the equal-weight full eligible ticker-group cohort and with zero cash. Aggregate cohorts equally within fold and folds equally. Do not compound endpoint returns.

## Scope decision

Supported: a conditional ordinal-ranking description and gross endpoint arithmetic on the exact saved development sample.

Blocked: out-of-sample predictive advantage, net profitability/costs, portfolio return/drawdown, verified security continuity, universe completeness, terminal valuation, promotion, and production readiness.

No training, calibration, feature change, threshold search, k search, tuning, or scoring is authorized here. The old v1.1 result and blocked net-return rule remain unchanged.

## Review boundary

The sole review decision is whether to approve **fixed top five with equal weights renormalized across selected ticker groups** as the exploratory development-only selection contract. Implementation and execution require a later authorization.
