# Alpha Atlas V4 target and score alignment review

**Status:** `COMPLETE`
**Scope:** focused development-only investigation; no new performance experiment or model change.

## Completed evidence

Run `35923707545-1` (commit `c1d03575d05a07146f4cf85d35f2087db5259243`) is verified as `COMPLETE`. Artifact `10778740282` is `v4-cohort-relative-ranking-results-35923707545-1`, with artifact digest `sha256:61ac2df3fd4a4302ba88e7d3f75e04dc9b15abfe4560fdcfe0e2c7040ad5917c`. Its internal `SHA256SUMS` verifies; result JSON SHA-256 is `b2621dfe600f399c5de37a6c9b10dd529898a37f9b2468aa32109b058f109776`.

The approved membership remains run `35895423660-1`: audit report SHA-256 `3a71814f1b3ef086a518aa380ce6bf85a65e0bf893d036f788ffc923b1bc79b9`, membership SHA-256 `a5f411fc534cba8a602143c491f6e5e74c5e2280a5dc141a9e1fee8f4c2ab9e0`, and input-provenance SHA-256 `35b7b75d21ef0f12a99fd9f311fbc5dc216cca97c29718bbdf1e68a3e057ed22`.

The completed result is unfavorable: each candidate underperformed the eligible-cohort baseline in every fold. Predictive advantage is `NOT_ESTABLISHED`. These are descriptive five-session endpoint averages, not portfolio returns.

## Compact contract and alignment table

| Candidate | Effective training target | Score meaning and direction | Training recipe | Alignment/classification |
|---|---|---|---|---|
| `challenger-ranking-lane-full-v1` | Observation is among the five highest `return_5d` rows within `event_date` | Logistic sigmoid used as `ranking_score_not_buy_probability`; larger ranks higher | Full window, raw-price-excluding features, tail weights, L2 0.004 | `TARGET_AND_DIRECTION_CONSISTENT`; `OBJECTIVE_ALIGNMENT_LIMITATION`; capture-label metadata defect |
| `challenger-ranking-lane-recent-half-v1` | Same `daily_top5` observation target | Same ranking-score convention; larger ranks higher | Recent half, raw-price-excluding features, tail weights, L2 0.004 | `TARGET_AND_DIRECTION_CONSISTENT`; `OBJECTIVE_ALIGNMENT_LIMITATION`; capture-label metadata defect |
| `challenger-ranking-top5-model-v1` | Same `daily_top5` observation target | Sigmoid is an ordinal probability of weighted `daily_top5`, not a calibrated buy probability; larger ranks higher | Full window, includes raw close, tail weights, L2 0.002 | `TARGET_AND_DIRECTION_CONSISTENT`; `OBJECTIVE_ALIGNMENT_LIMITATION`; capture-label metadata defect |

## Inspected source facts

1. The five-session return column resolves to `return_5d`.
2. `daily_top5` converts that field to numeric, fills missing returns with zero, groups by `event_date` alone, sorts return descending, and labels at most five **observation rows** positive. It has no explicit semantic secondary tie key.
3. Both ranking lanes fit deterministic logistic models to those labels. The full and recent-half lanes differ by training window.
4. The specialized top-five model uses the same target transformation. All three apply weights 4/3/1 to big-gain/big-loss/other rows. The later evaluation economics are unweighted across ticker groups.
5. `predict_proba` applies sigmoid to the fitted positive-class logit. Thus larger scores support descending selection. The two lane scores remain ranking scores; the top-five output is only an ordinal probability of the weighted `daily_top5` target.
6. OOF capture removes holdout rows before fitting or prediction and serializes saved scores directly.

## Target-to-evaluation mapping

| Dimension | Training | Evaluation | Assessment |
|---|---|---|---|
| Unit | Canonical observation | Ticker group after equal observation averaging | Imperfect match; repeated rows can consume multiple training top-five slots but only one evaluation group weight |
| Cohort | `event_date` | Candidate, fold, event date, horizon, entry, exit | Evaluation is more restrictive; timing partitions are absent from target construction |
| Horizon/outcome | `return_5d`, five sessions | Saved gross split-adjusted `return_5d` | Directionally aligned |
| Relative/absolute | Five best rows, even if returns are negative | Selected return and selected-minus-eligible baseline | Relative target cannot guarantee positive returns or baseline improvement |
| Quantity learned | Weighted positive-class order | Equal-weight group return magnitude | Target likelihood/order is not expected-return magnitude |
| Eligibility | Training rows; no later group gate | All-members-must-pass ticker groups | Populations do not exactly match |
| Weighting | Tail-aware 4/3/1 fitting weights | Unweighted group economics | Objective alignment limitation |

This is category **A: correct fitting/score direction with an imperfect objective match**. That mismatch is not automatically a bug and a correct relative-ranking target does not guarantee positive returns.

## Confirmed capture contract defect

There is also a bounded category **B** defect in saved label metadata, separate from the unfavorable return result. Candidate fitting replaces training labels with `daily_top5`, but `_apply_walk_forward_metrics` retains configured `label_up_5d` validation labels. The capture then serializes `target_metadata()` describing `label_up_5d` and writes `record.label` from that configured validation vector.

Earliest affected stage: development OOF capture/serialization at commit `5d360cdbda802ae8527b35fe59f75920b8c827c8`.

Affected: candidate `target_definition`, saved `record.label`, and label-based metrics interpreted as though they represent the effective candidate target. Unaffected: fitted score values, descending direction, approved selection membership, and the gross-return comparison in run `35923707545-1`. Therefore this defect does **not** justify reclassifying the unfavorable comparison as an implementation failure or rerunning performance here.

Smallest future repair: a future capture would need to serialize candidate-specific effective target metadata and independently construct validation `daily_top5` labels under an explicit deterministic tie policy; dependent label evidence would then require regeneration. No repair or regeneration is performed in this review.

## Identical result series

The full ranking lane and top-five model selected the same 390 ticker groups in all 78 cohorts. They provide effectively duplicated **selection evidence in this experiment**. They are not identical models: 1,733 of 1,975 group ranks match, while 242 differ; their L2 values and feature sets also differ. The shared target, full window, tail weights, deterministic logistic family, and mostly shared features plausibly explain the common top-five boundary. This is not a contract violation.

The recent-half lane overlaps each full-window selection in 243 of 390 group selections.

## Deterministic outcome-independent example

The lexicographically first audited cohort—fold 1, event date `2026-06-01`, five-session horizon, entry `2026-06-02T13:30:00+00:00`, exit `2026-06-08T20:00:00+00:00`—was selected without consulting outcomes.

- Full lane top five: `STG`, `DEVS`, `GTM`, `OPTU`, `HUBC`.
- Recent-half lane top five: `HUBC`, `DEVS`, `STG`, `OPTU`, `GTM`.
- Top-five model top five: `STG`, `DEVS`, `GTM`, `OPTU`, `HUBC`.

This illustrates equal-observation group-score averaging followed by descending group ranking. It is not a favorable-return example.

## Conclusion

For all three candidates, target and descending score direction are consistent, while objective alignment is limited. A capture-label metadata defect is confirmed but does not affect this experiment's scores, memberships, or gross returns. The unfavorable result is accepted: the tested frozen selection rule did not improve gross endpoint outcomes on this development sample. Predictive advantage remains `NOT_ESTABLISHED`; broader validation and promotion remain blocked.

## Exactly one recommended next step

If future work is authorized, pre-register—but do not yet build—a development experiment whose target unit and cohort match the evaluated ticker-group/timing-cohort expected five-session return objective and whose capture records the effective target and deterministic tie policy.
