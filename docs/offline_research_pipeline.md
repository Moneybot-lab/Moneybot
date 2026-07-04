# Offline Massive Research Pipeline

This pipeline keeps live routing untouched while making every training row and model reproducible.

## 1. Join raw market files to decision logs

After ingesting Massive flat files into immutable dated folders, build leakage-safe training rows:

```bash
python scripts/build_massive_decision_training_rows.py \
  --raw-root data/raw/massive_flatfiles \
  --decision-log data/decision_events.jsonl \
  --output data/decision_training_snapshot.jsonl \
  --horizon-days 5
```

The join uses the last market row on or before the decision date for features and a strictly later market row for labels. The script writes `data/decision_training_snapshot.jsonl.manifest.json` with the raw root, decision log, join policy, row counts, and `leakage_safe: true`.

## 2. Clean rows and materialize a flat feature-store snapshot

```bash
python scripts/clean_training_snapshot.py \
  --input data/decision_training_snapshot.jsonl \
  --output-dir data/training_quality

python scripts/day15_materialize_flat_feature_store.py \
  --input data/training_quality/cleaned_all.jsonl \
  --output-dir data/flat_feature_store
```

The cleaning guard drops exact duplicates, rows missing `label_up_5d`, rows missing required model features, and rows with stale `market_asof_date`; it also saves cleaned train/test JSONL files, probability-only evaluation rows, and `model_quality_report.json`. The feature-store manifest records the source input hash, chronological split policy, every emitted file, and SHA-256 hashes for outputs so downstream model artifacts can be tied back to one immutable snapshot.

## 3. Train many offline challengers

```bash
python scripts/train_challenger_suite.py \
  --input data/flat_feature_store/train.jsonl \
  --output-dir data/challenger_suite \
  --min-rows 200
```

The challenger suite trains many offline competitors in one run: a logistic-regression grid across thresholds and regularization values, the strongest single-feature decision stumps, and simple majority/always-up/always-down baselines. It writes one model artifact per challenger and `challenger_suite_manifest.json` with model-type counts, metrics, ranking, selected features, fill values, and `live_routing: false`.

## 4. Backtest, gate, and shadow-log before promotion

```bash
python scripts/backtest_challenger_suite.py \
  --suite-manifest data/challenger_suite/challenger_suite_manifest.json \
  --feature-store data/flat_feature_store/test.jsonl \
  --output data/challenger_suite/backtest_report.json \
  --transaction-cost-bps 5 \
  --slippage-bps 5
```

The backtest report chronologically scores every challenger with transaction costs, slippage, max drawdown, probability calibration, drift checks, and buy-and-hold/cash/equal-weight benchmark comparisons. Promotion gates are objective and recorded per model; user-facing routing remains disabled in the report.

Gate-cleared challengers should be logged in shadow mode beside production decisions using `moneybot.services.challenger_shadow.log_challenger_shadow_decisions`. Shadow records use a `*_challenger_shadow` endpoint, set `shadow_only: true`, and keep `routing_allowed: false` until a separate human promotion step approves routing after continued drift monitoring.

## 5. Recommended automation: GitHub Actions + manual Render promotion

The recommended production setup is the scheduled `Track B Offline Challenger` GitHub Actions workflow. Configure these GitHub repository or environment secrets before enabling it:

- `MONEYBOT_BASE_URL`
- `DAILY_OPS_TOKEN`
- `MASSIVE_FLATFILES_ACCESS_KEY_ID`
- `MASSIVE_FLATFILES_SECRET_ACCESS_KEY`
- `MASSIVE_FLATFILES_ENDPOINT`
- `MASSIVE_FLATFILES_BUCKET`

The workflow ingests Massive flat files, exports decision logs, builds leakage-safe rows, materializes the feature store, trains the challenger suite, backtests/gates every challenger, prepares manual Render promotion artifacts, and uploads derived artifacts plus ingest manifests. Raw vendor files are intentionally not uploaded as GitHub artifacts.

Promotion remains manual through the `Promote Track B Candidate` workflow. That workflow downloads the offline artifacts, rejects non-winning or non-gated reports by default, and only calls Render's promotion endpoint after manual dispatch.

## 6. Local smoke test before enabling scheduled runs

Run this test after changing the offline workflow or model-training scripts:

```bash
python -m pytest tests/test_offline_pipeline_smoke.py -q
```

The smoke test creates synthetic raw Massive-style market files and decision logs, then runs the join, cleanup/quality guard, feature-store materialization, challenger training, backtest/gating, and promotion-prep commands end to end without network access or real credentials.
