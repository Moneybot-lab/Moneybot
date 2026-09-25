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

## Evidence packaging and retrieval

The complete, row-level `timing_compatibility_audit.json`, resolved-input
metadata, execution provenance, summary, and checksums were deliberately kept
out of Git and uploaded as GitHub Actions artifact
[`v4-group-return-regression-36022025311-1`](https://github.com/Moneybot-lab/Moneybot/actions/runs/36022025311/artifacts/10816618256)
by [run `36022025311`, attempt 1](https://github.com/Moneybot-lab/Moneybot/actions/runs/36022025311/attempts/1).
The artifact service recorded bundle SHA-256
`19b68b9eed3962dccbc5dde119f2e792f017f9a1c94f4ba5d8c54fc4574f5cc4`
and artifact ID `10816618256`.

The bundle's byte size was not captured in the committed run record. This
recovery environment has neither a GitHub remote nor an authenticated `gh`
session, so it cannot query the private artifact API to recover that value or
currently verify retention. The compact JSON records the size as `null` rather
than inventing it. The artifact was durable Actions storage when uploaded, but
its current retrievability must therefore be verified by an authorized
reviewer. No claim is made that an ephemeral local copy preserves the evidence.

The reviewable Git delta against actual base `196c9b8` contains only the
focused implementation/tests, checklist, compact reports, and checksum file.
The largest resulting files are the pre-existing checklist (95,541 bytes),
implementation (16,853 bytes), runner (11,048 bytes), and test (8,538 bytes);
the compact JSON and Markdown reports are 5,368 and 5,035 bytes respectively
after this retrieval annotation. Generated row/group mappings and downloaded
source artifacts are not Git additions. Those payloads—not the focused source
change—were the material that exceeded diff extraction limits when included
inline.

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
