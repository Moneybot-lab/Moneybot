# Alpha Atlas V4 shared-decision cohort feasibility

## Classification: `INCOMPLETE_EVIDENCE_UNAVAILABLE`

This is a new common-boundary review, not a reopening of the completed
within-group timing investigation. The timing result and `ZERO_FITS` status are
preserved. Different original decision timestamps do not determine whether a
snapshot was assembled and available at an earlier, shared boundary.

## Proposed common boundary

For each five-session execution window, use the official close of the XNYS
session immediately preceding `entry_at`, calculated with
`XNYS-rule-calendar.v1` and stored in UTC. This is a **proposed operational
convention**, not verified historical behavior. It is strictly before entry,
shared across competing tickers, leaves the five-session entry/exit window
unchanged, and does not depend on scores, returns, record counts, or cohort size.
It must not be moved later to improve coverage.

The existing contract says that source data must be proven available by the
feature cutoff and that unknown availability rejects a row. The saved producer
sets `feature_cutoff_at` to the original event decision time, stores separate
per-family source and availability timestamps, and describes a snapshot as
fresh. Neither a source event time nor a per-family bar availability timestamp
proves when the complete feature snapshot was constructed or captured.
Therefore eligibility requires explicit snapshot construction/capture
availability, every feature-family availability at or before the boundary, and
a supported freshness status. Equal-boundary values qualify; later values do
not. Among multiple qualifying records, choose the latest cutoff and then the
lexically greatest canonical ID. Original IDs and timestamps remain unchanged.

## Evidence-access boundary

Local inspection verified the registration file itself at SHA-256
`c3f58f47354f07a5c0aa536344b97fb542fdde0bee1651f86852d3dd69a6469a`
and reused its recorded artifact identities and frozen file hashes. No exact
canonical JSONL, frozen split plan, row mapping, or downloaded archive exists in
this checkout or the searched local workspace. GitHub is unauthenticated and no
remote is configured. Consequently remote metadata was not checked, current
expiration/retention is `UNKNOWN`, no archive was retrieved, no downloaded bytes
were verified, and no internal checksum was verified during this task.
Recorded provenance is not a claim of current retrievability:

| Artifact | ID | Recorded digest | Recorded bytes | Retrieval |
|---|---:|---|---:|---|
| `track-b-offline-output` | 10296724235 | `sha256:017cdd8a8b30917e6e2f958e3cba1ae99827e002434b97f9de40c2c4ab871ef9` | unknown | [recorded Actions location](https://github.com/Moneybot-lab/Moneybot/actions/runs/34689216730/artifacts/10296724235) |
| `v4-group-return-regression-36022025311-1` | 10816618256 | `sha256:19b68b9eed3962dccbc5dde119f2e792f017f9a1c94f4ba5d8c54fc4574f5cc4` | unknown | [recorded Actions location](https://github.com/Moneybot-lab/Moneybot/actions/runs/36022025311/artifacts/10816618256) |

The precise missing inputs are `flat_feature_store/all.jsonl` (registered
SHA-256 `506073…2d9e`) and `challenger_split_plan.json` (`f11257…6933a`).
The timing audit artifact alone records source timestamps, not complete snapshot
construction/capture availability, so it cannot substitute for the canonical
rows.

## Findings that cannot be invented

The six fold/partition opportunity dispositions, cohort-size counts and
proportions, unique-window reconciliation, and frozen-split conflict counts are
`null`, not zero. Computing them requires exact canonical rows and split
membership. Without those bytes it is impossible to distinguish missing,
only-after-boundary, stale, unknown, and invalid opportunities or to check
within-fold reuse, outcome overlap, embargo, and event-date versus
execution-window conflicts. No rows were moved or dropped.

The compact analyzer added by this task provides mutually exclusive final
opportunity dispositions plus overlapping diagnostic flags, deterministic ties,
cohort bins (including empty cohorts), and within-fold conflict reporting. It
also states the unavoidable selection limitation: when a cohort has five or
fewer eligible tickers, `min(5, eligible)` selects the entire eligible baseline
and cannot differentiate a selected group.

These unavailable counts cannot establish statistical adequacy or historical
universe completeness. They also cannot support a new registration. Registration
v1 remains `NOT_EVALUABLE — INCOMPATIBLE_GROUP_TIMING; ZERO_FITS`; the exact-time
amendment remains `NOT_APPROVED`; and revised registration/training remains
`NOT_AUTHORIZED / NOT_EXECUTED`.

## Exactly one next action

An authorized reviewer must retrieve Actions artifact `10296724235`, verify the
recorded archive digest and the registered canonical/split-plan file hashes, and
make those exact bytes available for the metadata-only analyzer. If they contain
no snapshot construction/capture availability, stop: saved evidence cannot
prove shared-boundary eligibility and this dataset must not be forced into
singleton cohorts.
