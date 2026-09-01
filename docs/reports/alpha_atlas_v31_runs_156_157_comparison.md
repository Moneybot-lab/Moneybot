# V3.1 Track B runs #156/#157 comparison

Status: `RECOVERY_WORKFLOW_REQUIRED`.

The current environment has no authenticated GitHub CLI session, so the original
artifacts were not downloaded. The manual **Recover V3.1 Benchmark Evidence**
workflow uses the exact run IDs `32700956267` and `32820019463`, extracts compact
hash-bound evidence, and does not retrain or promote V3.1.

The known GitHub artifact digests are preserved in the machine-readable report,
but they do not substitute for extracted model and metadata hashes. V3.1 remains
benchmark-only, non-promoting, and unavailable for automatic live routing.
