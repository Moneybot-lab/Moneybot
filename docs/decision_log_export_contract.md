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
The workflow currently enforces the observed April 8, 2026 06:18:20 UTC
historical-beginning continuity floor; moving the earliest record later fails
before ingestion.
