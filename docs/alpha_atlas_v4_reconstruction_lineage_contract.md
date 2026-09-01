# Alpha Atlas V4 reconstruction lineage contract

Contract: `alpha-atlas-v4-reconstruction-lineage.v1`

This research-only contract binds each V4 economic observation to the immutable,
run-scoped inputs needed to replay its 43 model features and executable label. It
does not certify a survivorship-safe historical universe and does not authorize a
historical backfill, promotion, or live routing.

## Bundle layout

The production builder writes a normalized evidence bundle:

| File | Purpose |
| --- | --- |
| `source_objects.jsonl` | Massive source-object paths, byte sizes, families, and SHA-256 values. |
| `selected_market_rows.jsonl` | Deduplicated raw-row identities plus the exact split-basis OHLCV values used by replay. |
| `corporate_action_evidence.jsonl` | Relevant actions, or an explicit no-relevant-action disposition, bound to the inspected split cache. |
| `security_identity_evidence.jsonl` | Auditable request-event identity evidence; request-only records are not historical-universe eligible. |
| `observation_lineage.jsonl` | Canonical-ID keyed source windows, context, execution rows, contracts, and calculation-engine identity. |
| `source_evidence_manifest.json` | Schema, policies, per-ledger hashes/counts/sizes, source-root reference, and bundle metrics. |

Every JSONL file is deterministically ordered. Source objects and selected rows
are deduplicated by content-derived identifiers. Observations contain only a
compact lineage reference and the exact manifest SHA-256; evidence is never part
of the 43-feature model vector.

## Availability

An explicit provider availability timestamp is retained when present. Otherwise,
a daily aggregate uses `xnys-completed-daily-bar.v1`: the bar becomes eligible at
the official close of its completed XNYS session. This is semantic availability
derived independently by `ExchangeCalendar`, not an invented Massive publication
timestamp. Corporate-action availability is evaluated separately and never
inferred from session completion.

## Identity and transformation rules

`REQUEST_EVENT_IDENTITY` is accepted only for replay of the current immutable
request event. It always carries
`historical_universe_certification_eligible: false`. Canonicalization may collapse
compatible duplicate requests, but the surviving observation remains joined by
`canonical_observation_id` and retains unit model weight. Cleaning, feature-store
materialization, temporal splitting, and verification must preserve the compact
lineage reference.

## Fail-closed rules

Verification fails on a missing or modified source object, ledger or manifest;
unresolved row/action/identity identifier; unavailable feature input; identity,
calendar, contract, feature, execution, or target mismatch; or incomplete
full-artifact coverage. The builder fails with
`PHASE0_EVIDENCE_BUNDLE_TOO_LARGE` rather than truncating evidence when configured
row or byte limits are exceeded.
