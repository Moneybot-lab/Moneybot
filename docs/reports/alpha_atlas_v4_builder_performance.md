# Alpha Atlas V4 builder performance repair

**Schema:** `alpha-atlas-v4-builder-performance-report.v1`

Track B #168 (`33463597909`) was terminated by the shell's 600-second timeout in
`build_raw`; it never reached reconstruction. The regression came from rebuilding
split-adjusted symbol, SPY, and sector histories and regenerating/hashing every
source-row reference for every request observation. Linear date searches and a
second complete no-split feature build for diagnostics compounded the work.

The optimized builder caches adjustment views, source-row identities, source
windows, and canonical lineage; uses direct date-position maps; computes the
registry hash once; and limits the before/after diagnostic replay to symbols that
actually have split events. Deterministic final sorting preserves ledger outputs.
Source-object hashing and parsing remain once per source file.

## Local 10,000-event scale fixture

The unoptimized baseline failed to finish the same fixture within 120 seconds and left no final artifacts. The optimized fixture contained 10,000 request events, 40 canonical economics, three daily
source files, and 365 selected rows. It completed in 21.191 seconds wall time
(19.634 seconds instrumented). It hashed and parsed each of the three files once,
reused 9,960 canonical lineages, and recorded 9,960 feature-cache hits versus 40
misses. Observation construction took 15.794 seconds, manifest hashing 1.885,
raw serialization 1.089, lineage accumulation 0.432, and evidence serialization
0.047 seconds. These timings are operational diagnostics and do not participate
in artifact hashes.

The workflow timeout is raised from 600 to 1,800 seconds only as headroom after
the computational repair. A hosted rerun is still required to establish real-scale
completion and reach the Phase 0 reconstruction gate.
