# Alpha Atlas V4 Stage A implementation record v1

**Stage A: `AUTHORIZED / IMPLEMENTED / SYNTHETICALLY TESTED`.** The implementation
binds and verifies acquisition amendment v3/v2, the snapshot contract, and timing
clarification; provides deterministic request planning, injected fake transport,
append-only crash-conservative accounting, immutable source receipts, local
split-basis reconstruction, versioned primary/backup/restore, and validated
cache-only handoff.

Synthetic entry point:

```bash
python -m scripts.run_alpha_atlas_v4_synthetic_acquisition \
  --synthetic --output-dir <test-evidence-dir>
```

Manual workflow: **Alpha Atlas V4 Stage A Synthetic Acquisition Only**.

The CLI has no live mode or provider client, reports synthetic attempts separately
from zero live requests, uses temporary directories explicitly labeled test-only,
and emits JSON, Markdown, logs, and artifact-root-relative checksums.

**Stage B and the pilot remain `NOT_AUTHORIZED / NOT_EXECUTED`.** Fake storage,
backup, and restore prove offline behavior only—not worker access, durability,
backup lifecycle, retention, entitlement, timing, or capacity.
