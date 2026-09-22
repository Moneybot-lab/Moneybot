# Alpha Atlas V4 narrow frozen-sample diagnostic v1.1

This revision corrects only split-plan hash semantics. It supersedes specification
SHA-256 `f55e749a6616506b1e227ddebb96dad569b33cd3c56599fe189803dc68173e0f`.

The exact saved split-plan file is pinned by byte SHA-256
`f11257cff0befc1f0b46e4cab9a64678c766b6fe2abdc50b07861a18f7d6933a`.
Its semantic content is independently recomputed with the producer's
`canonical_json_hash` over the plan object after removing `plan_sha256`; that result
must equal both the registered content hash and embedded `plan_sha256`,
`bd60055adc6cb1b3f8b143c6c61031bced01c9151fcb3b67c33f75430106e8e8`.
The embedded field is not trusted by itself.

Dataset, folds, candidate roster, targets, thresholds, metrics, weighting,
uncertainty, and claim limitations are unchanged from v1. The broader net-return
registration remains blocked and unevaluated. Execution remains off by default.
This revision does not approve or perform scoring.
