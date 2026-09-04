# Decision-log export completeness contract

MoneyBot decision events are appended to the configured `DECISION_LOG_PATH`
(normally `/var/data/moneybot/decision_events.jsonl`). The logger does not rotate,
compact, tail, or delete this file. Operational backup and volume retention remain
Render configuration responsibilities.

`GET /api/export-decision-log` is an authenticated, unbounded byte stream of a
validated source-file prefix. It preserves append order and never accepts a row
limit. Before responding, the endpoint validates every source line, counts all
records, and computes the source SHA-256. The companion authenticated endpoint
`GET /api/export-decision-log-manifest` reports the count, byte length, timestamp
range, duplicate diagnostics, ordering fields, hash, and explicit completeness
and truncation states.

Track B validates the stream against response metadata and then persists its
machine-readable manifest. It fails before market ingestion when the response is
empty, malformed, incomplete, truncated, has a count or byte mismatch, or does
not match the advertised hash. Exact duplicate record identities are reported but
not silently removed; repeated requests remain distinct records. The canonical
Track B builder does not pass a local row cap.

The former endpoint default and workflow query `limit=50000` invoked
`read_decision_events(..., limit=50000)`, whose deque intentionally retained the
newest 50,000 lines. The builder also received `--limit 50000`. Both canonical
caps have been removed rather than replaced by a larger fixed number.

This contract does not prove that a hosting volume retained data before the
current file's first line. A reduction in the manifest's earliest timestamp or
source count must be reconciled against retained prior Track B manifests or a
documented storage migration before Phase 1.
## Durable continuity checkpoint

The checkpoint defaults to `decision_export_continuity.json` beside the configured
decision log and can be overridden with `DECISION_EXPORT_CONTINUITY_STATE_PATH`.
Exports fail closed when it is missing or corrupt. For the first deployment only,
`DECISION_EXPORT_CONTINUITY_BOOTSTRAP=verified_seed_v1` enables migration from the
verified run 33907221511-1 values. The current source must contain the seed byte
length and its exact prefix SHA-256 before `BASELINE_CREATED` is reported.
Bootstrap consumption creates a durable adjacent marker before the checkpoint is
written. If an established checkpoint is later missing, that marker prevents the
seed from silently recreating it; operator recovery is required. Disable the
bootstrap setting immediately after the first successful checkpoint commit.

The download does not advance state. After curl and local integrity validation
complete, Track B posts the sanitized manifest to the commit endpoint. That
endpoint rechecks the source and atomically replaces the checkpoint using a
same-filesystem temporary file, file and directory `fsync`, and an exclusive
file lock. Subsequent exports must report `PREFIX_VERIFIED`.

## Duplicate audit

`duplicate_records_detected` counts repeated SHA-256 identities of normalized
JSON objects (sorted keys and compact separators), not repeated symbol/date
observations. Physical records are never removed. The Track B duplicate audit
reports exact normalized groups, byte-identical groups, adjacency, safe date,
endpoint and model buckets, and conflicting decision/event IDs. Exact repeats
are warnings; one immutable ID associated with different normalized content is
a fail-closed integrity error.

Raw diagnostic and classification consumers can therefore see repeated physical
rows. Canonical V4 observations collapse compatible economic duplicates to unit
weight, while Phase 0 verifies every resulting canonical row. Economic backtests,
bootstrap samples, symbol/date weighting and challenger ranking operate on the
canonical/cleaned data. The audit publishes raw and exact-deduplicated row counts
and the maximum exact-repeat multiplier so possible raw classification overweight
is quantified without changing training behavior in this continuity commit.
