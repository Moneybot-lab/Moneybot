# Alpha Atlas V4 purged temporal split contract

**Contract:** `alpha-atlas-v4-purged-temporal-split.v1`  
**Plan schema:** `alpha-atlas-v4-split-plan.v1`  
**Diagnostic schema:** `alpha-atlas-v4-split-diagnostics.v1`  
**Scope:** V4 research-only canonical observations

## Split unit and ordering

The split unit is the XNYS exchange-session date containing `feature_cutoff_at`.
`feature_cutoff_at`, `decision_at`, `entry_at`, `label_start_at`, and `exit_at` must be
explicit UTC timestamps satisfying the V4 timing contract. Every canonical observation
on one split-unit date stays together. Dates are ordered chronologically using
`XNYS-rule-calendar.v1`; request order, file order, legacy `event_date`, labels, returns,
utility, and model performance cannot influence the boundary.

## Deterministic boundary planner

The planner evaluates every boundary between eligible split-unit dates. It retains only
boundaries meeting frozen minimum train/test dates and canonical-observation counts,
then selects the feasible boundary closest to the configured date ratio. Ties select
the earlier boundary. Every rejected boundary and its non-performance reason is written
to diagnostics.

## Purging, embargo, and mixed horizons

The first test split-unit session and the configured number of eligible exchange
sessions are embargoed; weekends and exchange holidays do not count. A training
observation is purged unless its actual `exit_at` is strictly before the earliest
retained test `entry_at`. This uses each observation's real execution interval, so five-
and ten-session horizons can coexist without a fixed-day approximation. Purging is not
reapplied to the frozen outer holdout inside challenger training; nested calibration and
walk-forward splits retain their separate leakage controls.

## Minimums and insufficient coverage

Workflow defaults are five independent training dates, two test dates, one embargo
session, and the configured `TRACK_B_MIN_ROWS` divided by the configured train ratio.
No boundary is selected using outcomes. If no boundary meets these requirements, the
valid dataset receives `NO_CANDIDATE_INSUFFICIENT_TEMPORAL_COVERAGE`; training,
backtesting, comparison, promotion, and routing are not attempted. Malformed timing,
missing canonical IDs, duplicate canonical IDs, mixed contracts, non-unit weights, or
hash mismatch are data errors and still fail nonzero.

## Frozen plan and evidence

The plan records the input SHA-256, selected canonical IDs, boundary, calendar,
contract, embargo, safety flags, and its own canonical-JSON SHA-256. The trainer must
verify both hashes and exact membership. Diagnostics report input/canonical counts,
dates, symbols, entry/exit sessions, horizons, timestamp ranges, missing fields,
multiplicity, every candidate boundary, purge/embargo removals, and the final status.
No private request metadata is emitted.
