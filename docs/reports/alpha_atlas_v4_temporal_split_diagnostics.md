# Alpha Atlas V4 challenger temporal-split repair

## Hosted evidence availability

The hosted bundle is not accessible from this checkout because it has no Git remote or
GitHub credentials. Hosted row/date/horizon distributions therefore cannot be reported
truthfully here. The reported hosted stage progression establishes that raw,
canonical, cleaned, and feature-store stages succeeded; exact counts remain pending the
next artifact download.

## Confirmed code-path defect and invocation

The workflow passed `flat_feature_store/train.jsonl` to `train_challenger_suite.py` with
`--min-rows $TRACK_B_MIN_ROWS` and no other split flags. Thus hosted defaults were
`train_ratio=0.8`, `horizon_days=5`, and `embargo_days=1`. The feature store had already
taken a chronological 80% split, after which the trainer performed a second 80% row
split. Its legacy purge then read `event_date`, subtracted five calendar days from the
first test timestamp, and removed one calendar day of test rows. It did not read V4
`entry_at`, `exit_at`, mixed horizons, or exchange sessions. This double partitioning
and calendar-day approximation can empty a short canonical test date range; the hosted
counts are still needed to distinguish that mechanism from genuinely insufficient
coverage.

## Repaired invocation and evidence

The preflight reads `flat_feature_store/all.jsonl` once with ratio `0.8`, one XNYS
embargo session, five minimum training dates, two minimum test dates, and the unchanged
`TRACK_B_MIN_ROWS`. It writes `challenger_split_diagnostics.json` and
`challenger_split_plan.json`. A feasible training invocation consumes the same `all`
file and exact plan with `horizon_days=5`; both input and plan hashes are verified.

Diagnostics report raw canonical counts, unique symbols and dates, entry/exit sessions,
horizons, labels, missing timing fields, timestamp ranges, multiplicity, and every
candidate boundary's pre-purge rows, purged rows, embargoed rows, final rows/dates, and
rejection reasons. No request metadata is included.

## Compatibility and controlled HOLD

V3 and V3.1 artifacts use frozen benchmark semantics and are not trained from V4
executable-open labels. The workflow writes explicit compatibility-boundary evidence
instead. A valid but infeasible preflight writes
`NO_CANDIDATE_INSUFFICIENT_TEMPORAL_COVERAGE` and skips challenger training/backtesting;
malformed data still exits nonzero. Both outcomes remain research-only, cannot promote,
and cannot route live.
