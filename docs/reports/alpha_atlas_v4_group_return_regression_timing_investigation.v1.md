# Alpha Atlas V4 group-return regression timing investigation

## Result

Run `36022025311-1` correctly blocked before fitting. The mismatch is not a
formatting alias or parser mapping error: the saved `decision_at` and
`feature_cutoff_at` values are genuinely different UTC instants inside the
registered ticker/timing groups. Registration v1 requires unanimity, so the
existing experiment remains blocked.

The failed attempt started and completed zero fits, wrote no predictions, ran
no evaluation, and accessed no holdout content. Historical run `35923707545-1`
remains `COMPLETE — UNFAVORABLE FINDINGS`.

## Focal group

The three reported IDs are training members in both folds 1 and 2. Their group
key is `2026-04-03 / T / 5 / 2026-04-06T13:30:00Z /
2026-04-10T20:00:00Z`. Their respective decision and cutoff instants are
`02:03:21Z`, `02:03:46Z`, and `02:03:51Z` on 2026-04-04. All required values
are present, ISO-8601 timezone-aware, and already use UTC offsets. Each row's
`originating_decision_at_min` and `originating_decision_at_max` corroborate its
canonical `decision_at`. Label start equals entry at `2026-04-06T13:30:00Z`.
The five saved feature-family source timestamps are unanimously
`2026-04-02T20:00:00Z` for this focal group.

The parser maps `Z` to `+00:00`, requires an explicit UTC offset, parses with
`datetime.fromisoformat`, converts to UTC, and then compares instants. It does
not infer from `event_date`. Equivalent offset representations are therefore
accepted; naive, missing, invalid, and genuinely unequal timestamps block.

## Complete metadata-only scope

| Fold | Partition | Rows | Registered groups | Affected groups | Affected rows |
|---:|:---|---:|---:|---:|---:|
| 1 | train | 11,480 | 2,241 | 1,627 | 10,866 |
| 1 | validation | 4,464 | 773 | 487 | 4,178 |
| 2 | train | 17,856 | 3,396 | 2,379 | 16,839 |
| 2 | validation | 2,479 | 499 | 335 | 2,315 |
| 3 | train | 11,366 | 2,222 | 1,455 | 10,599 |
| 3 | validation | 3,330 | 703 | 385 | 3,012 |

The scan used authorized development identity and timestamp metadata only. It
did not read or calculate outcomes, average features, create predictions, fit a
model, or decode holdout rows. The implementation now persists every affected
group assignment and timestamp value in `timing_compatibility_audit.json`
before any fit can start.

Although the focal rows point to the same saved feature-family source instants,
their contractual information boundaries differ. Across the full scope,
averaging first and validating later would combine rows admitted at different
decision/cutoff instants. That violates the approved unanimity rule even where
individual source timestamps happen to coincide.

## Reviewable amendment—not authorized

The accompanying amendment proposes adding normalized `decision_at` and
`feature_cutoff_at` to both group and evaluation-cohort keys. It keeps every
temporal order, source cutoff, purge, embargo, and split check. It would,
however, turn every row in each scanned partition into a distinct training
unit and frequently create singleton evaluation cohorts. This materially
changes the approved experiment, so it requires explicit review and a new
authorization. No amended training or evaluation was executed.
