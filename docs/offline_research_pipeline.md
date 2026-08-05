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

The challenger suite trains many offline competitors in one run: a logistic-regression grid across thresholds and regularization values, Phase 2 calibrated linear variants with distinct feature subsets, full/recent-half/recent-quarter training windows and weighting policies, two-stage decision-plus-big-loss-risk filters, bounded second-generation hard-example models, dedicated ranking-lane models, depth-2/depth-3 decision trees, Phase 3 abstention-margin models, mistake-mined specialized challengers (`big_loss_avoider`, `big_gain_hunter`, `recent_window_model`, and `ranking_top5_model`), the strongest single-feature decision stumps, and simple majority/always-up/always-down baselines. Hard-example mining uses actual seed-artifact predictions, caps selected mistakes at 20% of training rows, caps sample weights, records parent lineage, and reports whether repeated tail mistakes decline in the next generation. Calibrated linear candidates use a separate purged calibration period and retain Platt scaling only when it improves Brier score; two-stage candidates independently calibrate the return and big-loss models and abstain whenever estimated big-loss risk exceeds their persisted risk threshold; abstention-margin candidates route probabilities near the decision cutoff to cash/no-signal using persisted 0.025 or 0.05 margins; shallow trees have a hard maximum depth of three. All training recipes are replayed exactly in every walk-forward fold, including feature subset, calibration policy, family-specific sample weights, hard-example bounds, training window, target, decision/risk thresholds, and abstention config. Decision and ranking candidates have separate manifest rankings, and ranking-lane candidates are explicitly forbidden from replacing the main decision model. Holdout and walk-forward boundaries purge training rows whose label horizon overlaps evaluation and embargo the first evaluation day. Backtests publish deterministic 95% date-block bootstrap confidence intervals and add a conservative promotion blocker when the lower confidence bound for average net return is negative. Phase 3 retention keeps every non-dominated candidate on separate decision and ranking Pareto frontiers instead of collapsing research to one overall winner; decision objectives cover bootstrap lower bound, average return, Brier, drawdown, and big-loss rate, while ranking objectives cover bootstrap lower bound, ranking utility, top-k return, drawdown, and big-loss rate. Only gate-cleared, supported decision-lane artifacts on the retained frontier can reach promotion packaging. After ranking, the strongest logistic artifact scores the untouched, embargoed holdout rows with its persisted features and threshold; those actual predictions—not recommendation or `feature_probability_up` proxies—produce daily missed-big-gain and bad-buy/big-loss mistake slices. Each mined row records the scoring artifact, probability, prediction, threshold, and mistake type. Every challenger and artifact also carries immutable recipe lineage: a stable recipe hash, lineage ID, generation, parents, and deployable model-family, thresholds, calibration, feature-subset, time-window, sample-weight, risk-filter, lane, and abstention configuration. The suite writes one model artifact per challenger and `challenger_suite_manifest.json` with model-type counts, Phase 2/3 family limits, prediction-cluster diversity, lane-specific rankings, metrics, ranking, selected features, fill values, temporal-validation policy, lineage, mistake-scoring provenance, and `live_routing: false`.

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
