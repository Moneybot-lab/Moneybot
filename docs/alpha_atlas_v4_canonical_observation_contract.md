# Alpha Atlas V4 canonical economic observation contract

**Contract version:** `alpha-atlas-v4-canonical-observation.v2`
**Observation schema:** `alpha-atlas-v4-canonical-observations.v2`
**Feature contract:** `alpha-atlas-v4-features.v2`
**Canonical cleaning report:** `moneybot-training-quality-report.v2`
**Hash policy:** `sha256-canonical-json-v1`
**Scope:** V4 research/shadow only

## Record types and pipeline boundary

1. **Raw request/event records** remain immutable in
   `massive-decision-training-rows.v4`. They retain endpoint/request audit metadata.
2. **Canonical economic observations** contain one model row per economic setup,
   fixed `model_sample_weight = 1.0`, timing/execution/feature provenance, and targets.
3. **Request-to-observation mappings** retain request ID, endpoint, original
   `decision_at`, decision source, and canonical observation ID for audit/fan-out.
4. **Canonical scores** are immutable values keyed by canonical observation ID and
   are produced by one scoring call per observation.
5. **Fan-out results** map that score back to request IDs without another model call.

Canonicalization occurs after the immutable V4 raw-row builder and **before** feature
cleaning, fitting, calibration, threshold selection, holdout evaluation, ranking,
utility, or bootstrap. The cleaner accepts the canonical observation schema, not raw
request rows. Production V2 comparison/promotion paths remain separate.

## Canonical economic key

The canonical key contains exactly:

* `point_in_time_symbol_id`: stable security identity; ticker aliases do not split one
  security.
* `feature_cutoff_at`: determines all available features.
* `entry_at` and `exit_at`: determine the executable outcome path.
* `label_horizon_sessions`: distinguishes S0–S4, S0–S9, and future horizons.
* `lane` and `universe_policy_version`: included because eligibility/universe policy
  can alter selection or economics.
* `timing_contract_version` and `model_feature_contract_version`: prevent semantic
  mixing.
* `execution_cost_policy_version`: included when net-target/cost policy is material;
  explicit null means no materialized cost policy.
* `canonicalization_contract_version`: versions the key itself.

The key excludes ticker display aliases, `decision_at`, endpoint, route, request ID,
user/watchlist/portfolio/alert IDs, and UI surface. These identify the product request,
not the economic setup. Different `decision_at` values collapse only when their cutoff,
features, stable security identity, execution path, horizon, policies, and outcome are
identical. A changed cutoff, feature, execution, cost, or target remains distinct or
fails closed as a conflict.

## Canonical ID and compatibility

Timestamps are parsed as timezone-aware UTC and serialized with an explicit `+00:00`.
The key is JSON-serialized with sorted keys and compact separators, UTF-8 encoded, and
SHA-256 hashed. IDs use `aav4obs_<64 lowercase hex>` and are independent of input order
and Python process hashes. Endpoint/request metadata never enters the hash.

Rows with the same key collapse only when all `feature_*`, `label_*`, `return_*`,
feature-family source/availability timestamps, prices, corporate-action factors,
policies, and schema/contract versions agree exactly. A disagreement raises a
`CanonicalizationError` with a sanitized reason and conflict count; no representative
is emitted. There is no first/last-wins, averaging, or request-multiplicity weight.

Mixed raw schemas, timing contracts, feature contracts, canonicalization contracts,
missing request IDs, duplicate request IDs, naïve/non-UTC timestamps, duplicate
canonical IDs, or non-unit model weights fail closed.

## Model and evaluation semantics

Only canonical observations may proceed to V4 cleaning/model research. Raw multiplicity
is diagnostic through `raw_request_count`; default evidence weight is always one.
Calibration, Brier, accuracy, return, utility, and Top-K operate on unique canonical
IDs. Bootstrap resamples canonical observations grouped by feature-cutoff exchange
date, so endpoint repetition cannot narrow intervals. Fan-out is a pure research
utility and is not connected to production recommendations or routing.

## V2 prior-request-state boundary

Version 2 removes the complete prior-signal/request-history family from canonical model
content. Raw rows carry it as `request_prior_state`; canonicalization removes that object
from observations and preserves it per request as `prior_signal_state` in the request
map. Supplying any deprecated `feature_*` prior-state name under
`alpha-atlas-v4-features.v2` fails closed. Genuine feature, target, execution, timing,
and corporate-action conflicts remain material. V1 artifacts are not reinterpreted.
