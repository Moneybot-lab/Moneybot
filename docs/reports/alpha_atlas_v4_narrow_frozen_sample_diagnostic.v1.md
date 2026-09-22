# Alpha Atlas V4 narrow frozen-sample diagnostic v1

**Question:** How do the saved frozen OOF predictions compare with simple baselines
on the recorded validation observations?

This is a conditional description of the saved sample. It does not establish
security continuity, unbiased historical-universe coverage, dividend-inclusive or
net profitability, portfolio performance, durable predictive advantage, promotion
readiness, or production fitness. Registration `35755237312-1` remains blocked and
its net-return advantage rule remains unchanged and unevaluated.

## Comparison rules

- Probability candidates: Brier score versus training-fold prevalence is primary;
  lower candidate-minus-baseline is descriptively better. Also report log loss,
  calibration, precision, recall, coverage, and abstention when applicable.
- Classification candidates: report the frozen-threshold precision, recall,
  coverage, and abstention vector. Undefined denominators remain null with reasons.
- Ranking/selection candidates: report gross split-adjusted endpoint-return
  difference versus the equal-weight eligible ticker/date timing cohort. The blocked
  net-primary rule is not replaced.
- Report every fold and signed difference. An equal-fold aggregate and sign count
  are descriptive only; no winner, tuning, two-of-three certification, or threshold
  selection is permitted. Disclose prior development exposure and multiple
  candidate comparisons.

## Weighting

Probability/classification metrics give each canonical validation observation equal
weight. Gross diagnostics first equal-weight repeated canonical observations within
`ticker + date + horizon + entry + exit`, then equal-weight ticker groups within the
matching date/timing cohort. Cohorts are kept separate when horizons or execution
windows differ. Nonempty cohorts receive equal weight within each fold; folds receive
equal weight in the descriptive aggregate.

Abstentions remain in coverage/recall denominators and contribute no selected
position. Empty-selection cohorts remain with zero candidate return. Cash is only a
zero-return arithmetic reference. Group labels are ticker/date timing groups, not
verified securities. Every output must retain a row-to-group mapping whose weights
reconcile to the original validation assignments.

## Uncertainty and execution boundary

If used, uncertainty is a date-block bootstrap that resamples complete cohort dates
within folds. It does not treat repeated observations or overlapping horizons as
independent; insufficient dates produces null. Execution is off by default and must
verify the reviewed specification and all four frozen input hashes. **No scoring was
executed by this specification task.**
