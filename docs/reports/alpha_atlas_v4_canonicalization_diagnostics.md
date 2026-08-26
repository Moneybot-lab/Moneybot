# Alpha Atlas V4 canonicalization diagnostics

Contract: `alpha-atlas-v4-canonical-observation.v2`

These are deterministic synthetic test counts, not production dataset claims.

| Diagnostic | Synthetic evidence |
|---|---:|
| Raw request rows | 3 |
| Canonical observations | 2 |
| Duplicate rows collapsed | 1 |
| Duplicate groups | 1 |
| Multiplicity distribution | one group of 1; one group of 2 |
| Largest duplicate group | 2 requests |
| Conflict count in compatible fixture | 0 |
| Model sample-weight sum | 2.0 |
| Endpoints | Quick Ask 2; Watchlist 1 |
| Symbols | AAPL 1 canonical observation; MSFT 1 |
| Lane | `track_b_research`: 2 |
| Horizon | five sessions: 2 |

Adversarial fixtures separately prove conflicts for features, labels, and execution
prices; each fails closed with a sanitized conflict reason and count. Version-mixing
fixtures fail before observation emission.

## Multiplicity invariance evidence

The focused tests evaluate the same economic observation once with one request and
again with endpoint duplicates. Accuracy, Brier, selected return, and utility are
identical. The deterministic cutoff-date block bootstrap produces the identical 95%
interval with or without duplicate endpoint requests. Top-K accepts unique canonical
IDs, and score fan-out records one scorer invocation while returning the same score to
all request IDs.

## Pipeline locations

1. Raw V4 rows: `scripts/build_massive_decision_training_rows.py`.
2. Canonical stage: `scripts/canonicalize_alpha_atlas_v4_rows.py` and
   `moneybot/services/alpha_atlas_v4_canonical_observations.py`.
3. V4 cleaner: `scripts/clean_training_snapshot.py`, which requires canonical input.
4. Homogeneous cleaned-row guard: `scripts/train_massive_baseline_model.py`.
5. V4-only metrics/bootstrap/ranking/fan-out: canonical observation service module.

## Deferred and safety boundaries

No production data was counted, no V4 model was trained, and no score was routed.
Persistent orchestration of canonical evaluation artifacts, intraday/breadth
canonicalization, cost-bearing targets, and private per-user fan-out storage remain
deferred. V2 production comparison/promotion lineage remains pinned to V2.
