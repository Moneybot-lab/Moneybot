# Alpha Atlas fail-closed serving repair

Base: `4f2d4d6624d792f27fd320e5b75c2df0ae8d8202` (remote main checked before branching).

## Failure and repair

Before repair, an isolated ten-feature V3.1-style JSON artifact loaded successfully, entered `legacy_day1_contract`, imputed all ten inputs, reported `servable=true`, and reached an intercepted scorer once. The regression assertion that the scorer must not execute failed. After repair the same reproduction returns `UNSUPPORTED_SCHEMA`, no builder, and zero scorer calls; unsupported features are rejected before imputation/history retrieval.

A small runtime resolver explicitly classifies supported Day-1, blocked V2 leakage, supported V3, unsupported schemas and invalid learned state. It rejects duplicate/empty names, inconsistent dimensions, nonfinite parameters, nonpositive scales, invalid thresholds and incomplete/inverted clipping. Vector construction preserves serialized order, with dimension/finite checks before scoring. No coefficient, probability, calibration, target or outcome logic changed.

Supported Day-1 versions are `day1-logreg-v1`, `alpha-atlas-v1`, and `alpha-atlas-v1-fallback`, with either the exact original five technical fields or the exact ten-column `FEATURE_COLUMNS` extension. Other labels cannot qualify by matching feature count. Existing Day-1 partial-input imputation remains: absent fields use serialized centers; the legacy builder currently does not populate the five news fields and retains its existing proxy five-day-return behavior. This repair does not certify those historical choices. When every usable input is absent, no inference occurs.

Full eleven-feature V3 remains supported for `candidate-alpha-atlas-v3-clean-v1` and `alpha-atlas-v3`, preserving its current market builder, ordering and at-most-two-missing-input gate. V2's exact unsafe schema remains blocked regardless of claimed version; its existing pure feature-construction tests remain usable, but runtime scoring is forbidden. The recovered ten-feature V3.1 schemas remain unsupported; neither padding nor new V3.1 serving capability is introduced.

Warnings contain only stable reasons and occur once per reason per artifact load. Diagnostics distinguish loaded from inference-servable. `reload_artifact()` retains its existing loaded-state return meaning; no health API scope changed. Contract checks run on each feature construction, so reload or in-memory state changes cannot retain an obsolete eligibility decision.

## Entry points and evidence

Quick Ask, portfolio, watchlist advice, momentum/scanner integration and shadow routes use the shared deterministic advisor. Returning `None` preserves existing rule-based fallbacks and source attribution. Unsupported models never invoke `predict_proba` or `make_forecast`, so they cannot produce model probabilities or prediction identities. The watchlist API integration regression verifies unchanged rule advice, `decision_source=rule_based`, null forecast/probability and exclusion by PR #665 projection.

Other direct scorer callers are offline model utilities and V4 research/reload code. The generic mathematical scorer remains unchanged; it is not made a serving-authorization API. Model loaders/certificates remain distinct from runtime eligibility.

## Verification

Final focused suite: **220 passed, 0 failed, 0 skipped, 7.37 s**.

```
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/workspace/work/alpha-atlas-audit:/workspace/Moneybot .venv/bin/pytest -q \
  tests/test_serving_feature_contract.py tests/test_deterministic_advisor.py \
  tests/test_alpha_atlas_feature_contract.py tests/test_alpha_atlas_v3_features.py \
  tests/test_production_servability.py tests/test_historical_evidence.py \
  tests/test_forecast_horizon.py tests/test_decision_log.py \
  tests/test_portfolio_explanation_integrity.py tests/test_portfolio_advice_explanations.py \
  tests/test_live_market_api.py tests/test_run_daily_ops_script.py \
  tests/test_run_weekly_model_refresh_script.py
```

The optional audit PYTHONPATH adds a socket-denying `sitecustomize.py`; tests can run from the repository without that audit-specific path. Synthetic fixtures and local subprocess tests do not execute production workflows. Existing tests expecting all-missing inference or unsupported version labels were updated to the new intentional rejection behavior; evidence-serialization and quote-only exclusion tests retain actual partial input so they still exercise their original concerns. No authorization-window fixture or policy was changed.

Both recovered V3.1 files were also loaded privately and tested through Quick Ask, portfolio and shadow with an exception-on-call scorer. Each produced zero scorer calls and `UNSUPPORTED_SCHEMA`; original byte hashes remain:

- #156: `f702a4267895c1b65a6bf432cd675f761197e61e29d51780401a5a34aae69531`
- #157: `9da4fb4135fdf6de546172465ee8f32b0813122a671bf489e823aa8fbd9c05b4`

No original model/data files are added to the PR. Local evidence is retained under `/workspace/work/fail-closed-serving/` (before/after reproduction, JUnit/stdout and candidate/resource checks).

A 10,000-call local resolver microbenchmark took .06222 seconds (~6.22 microseconds/call), with 59.98 MiB peak RSS for the Python/numpy process. This is not incremental memory or a production concurrency measurement. Validation uses small arrays proportional to model feature count and adds no network, persistent-disk or history-fetch cost. Unsupported schemas are rejected before market-history retrieval.

## Compatibility, risks and rollback

Known Day-1/full-V3 paths and rollout controls are retained. Unknown/custom version labels, partial/extra schemas, invalid state and all-unavailable Day-1 inputs now intentionally fall back. A future model/version requires explicit contract review before extending the allowlist. Full-V3 mean/median parity, timestamp/session alignment and historical calibration/data provenance limitations remain unresolved; this PR does not make any audited candidate production-ready or strengthen those unrelated implementations. Existing V2 protection, PR #664 execution reliability, PR #665 evidence gates and promotion rules remain intact.

Rollback is a reviewable revert of this commit followed by the normal separately authorized release process. Reverting restores the fail-open defect; do not activate unsupported candidates as a rollback workaround. No merge, deployment, model promotion/training, calibration refit, production workflow, outcome-contract change, historical evidence fabrication or governance approval change was performed. Temporal-lineage investigation is a separate task and was not started.
