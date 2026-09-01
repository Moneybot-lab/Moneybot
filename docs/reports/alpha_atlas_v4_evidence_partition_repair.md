# Alpha Atlas V4 evidence partition repair

**Status:** implemented locally; hosted verification required

The post-performance hosted build processed 50,000 requests, emitted 41,268 raw
observations, and selected 84,723 unique evidence rows. Observation and lineage
construction completed. The builder then compared the sum of every uncompressed
ledger plus the primary manifest against the former 1,073,741,824-byte
`--phase0-max-evidence-bytes` guard and raised
`PHASE0_EVIDENCE_BUNDLE_TOO_LARGE:byte_limit`. Because that version did not emit
pre-failure size diagnostics, the exact hosted serialized size is unavailable; it
is verified only to have exceeded 1,073,741,824 bytes. The primary manifest itself
was not the high-cardinality component.

The repair redefines that option as a 1,048,576-byte primary-manifest limit with
metadata headroom. High-cardinality source, selected-row, identity, action, and
observation-lineage sections are canonically sorted and written to deterministic
gzip partitions targeting at most 16,777,216 uncompressed bytes. A separate
4,294,967,296-byte compressed-total guard remains fail closed. No evidence is
sampled or truncated.

The primary manifest indexes every partition by section, path, SHA-256,
compression, compressed/uncompressed bytes, record count, and contiguous record
offset. `evidence_bundle_diagnostics.json` records configured limits, total size,
selected-row count, per-section sizes, and largest sections before either limit can
raise. The verifier follows all partitions and fails on a missing, altered,
malformed, out-of-order, or count-mismatched partition.

In the production-path fixture, forced 32,768-byte partitions produced 36
partitions, a 15,037-byte primary manifest, and 217,339 compressed evidence bytes
(814,370 uncompressed), while preserving 229 selected rows, 36 canonical lineages,
and full 34/34 exact-artifact certification. A hosted rerun is required to record
the real 84,723-row partition count and compressed sizes.
