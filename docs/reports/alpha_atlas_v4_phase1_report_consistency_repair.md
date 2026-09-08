# Alpha Atlas V4 Phase 1 report consistency repair

**Status:** repaired and regression-tested  
**Scope:** private personal-use research development

## Root cause

The audit generated live `preflight.json` and an in-memory summary, but constructed
the source inventory and backfill plan from their static zero-argument factories.
The Markdown files were independently maintained repository documents rather than
renderings of the live result. The preflight workflow then copied those static
Markdown files over the run output. Consequently, a successful live probe could
coexist with `REQUIRES_OPTIONAL_PREFLIGHT`, `BLOCKED_PENDING_TECHNICAL_PROBE`, and
“not executed” prose.

## Repair

`normalized_phase1_result` now binds sanitized preflight evidence, provenance,
summary, source inventory, readiness verdict, and backfill plan. All generated JSON
and Markdown consume that object. `validate_phase1_consistency` rejects contradictory
mode/completion/delisted/backfill claims and secret-bearing evidence. The workflow no
longer overwrites generated Markdown with committed static copies.

The confirmed live evidence is propagated as `LIVE_BOUNDED`: 10/10 representative
probes, 10 requests, 2,946 bytes, about 2.5 seconds, zero writes, and no backfill.
TWTR is explicitly `REPRESENTATIVE_ACCESS_DEMONSTRATED`; full inactive/delisted
universe completeness remains unverified. The final verdict remains
`BLOCKED_FULL_UNIVERSE_BACKFILL`.

The separate coverage-discovery workflow defaults to `STATIC_ONLY` and cannot train
or backfill. It enumerates only bounded reference metadata when live mode is
explicitly selected, and emits partial evidence cleanly when a safety limit stops it.
