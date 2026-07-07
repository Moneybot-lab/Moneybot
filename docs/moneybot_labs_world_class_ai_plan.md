# MoneyBot Labs World-Class AI Plan

This plan turns Massive flat files, decision logs, challenger models, and offline analysis into a repeatable research loop for improving MoneyBot without risking live users.

## North star

Build a stock prediction lab that is reproducible, leakage-resistant, risk-aware, and continuously measured. The goal is not one magic model; it is a disciplined machine-learning factory that can discover, test, reject, and safely promote many ideas.

## Operating loop

1. **Ingest authoritative market history** from Massive flat files into a raw, immutable local/object-storage zone.
2. **Normalize and label decisions** into decision-level rows with event timestamps, symbols, model versions, recommendations, returns, labels, and source metadata.
3. **Materialize flat feature-store snapshots** with chronological splits, feature/label inventories, partitioned symbol/year shards, and manifests.
4. **Train challenger models offline** against the same snapshot manifest used for analysis.
5. **Backtest chronologically** with transaction-cost, slippage, drawdown, and calibration checks before any promotion discussion.
6. **Shadow deploy only** at first: log challenger predictions beside production decisions while keeping live routing unchanged.
7. **Promote cautiously** only when the challenger beats production across accuracy, calibrated probabilities, risk-adjusted return, drawdown, and stability gates.
8. **Monitor post-promotion drift** and automatically demote or disable models that breach risk or data-quality thresholds.

## Immediate commands

Validate Massive flat-file configuration without printing secrets:

```bash
python scripts/check_massive_flatfiles_env.py --prefix us_stocks_sip/day_aggs_v1
```

Run the offline Track B loop and produce feature-store artifacts:

```bash
python scripts/run_track_b_offline.py \
  --input-log data/decision_events.jsonl \
  --output-dir data/track_b \
  --train-ratio 0.8 \
  --min-rows 200
```

Inspect the resulting feature-store manifest before trusting a model:

```bash
python -m json.tool data/track_b/flat_feature_store/manifest.json
```

## What to optimize next

### Data advantage

- Pull daily, minute, splits, dividends, and reference datasets into immutable dated raw folders.
- Build derived features for momentum, volatility, gap behavior, liquidity, spread quality, sector/context, and event timing.
- Keep every derived dataset tied to a manifest hash so a model can always be reproduced.

### Modeling advantage

- Train multiple challenger families: logistic baseline, tree/boosted models, sequence features, regime-specific models, and calibrated ensembles.
- Require chronological validation and walk-forward testing; never random-split market time series for promotion decisions.
- Track probability calibration separately from directional accuracy so recommendations can be sized and risk-managed.

### Backtesting advantage

- Measure return, hit rate, Sharpe-like risk-adjusted return, max drawdown, turnover, transaction costs, slippage, and symbol concentration.
- Compare every challenger to production and to simple benchmarks such as buy-and-hold, equal-weight, and momentum baselines.
- Report failures as first-class outputs; a rejected challenger is useful research if the manifest and metrics are preserved.

### Safety advantage

- Keep flat files offline and non-live.
- Keep credentials in env vars or a secret manager only.
- Gate promotions with minimum sample sizes, out-of-sample performance, drawdown limits, calibration limits, and stale-data checks.
- Shadow log before routing user-facing decisions.

## Promotion checklist

A challenger is promotion-ready only if all are true:

- It was trained from a manifest-tracked dataset snapshot.
- Test data is strictly later than training data.
- It beats production on the chosen primary metric and does not regress risk metrics.
- It survives symbol, sector, market-regime, and return-bin breakdowns.
- Its predictions are calibrated enough for downstream recommendation thresholds.
- It has shadow-mode evidence on recent live decisions.
- Rollback is documented before promotion.

## Practical next milestones

1. Add a Massive flat-file sync job that writes raw files into dated local/object-storage paths.
2. Add a feature builder that joins raw market bars with decision events without look-ahead leakage.
3. Add a backtest report that consumes `manifest.json` plus model artifacts and writes a promotion-gate summary.
4. Add challenger shadow logging to production decision snapshots without changing the user-facing recommendation.
5. Add a weekly research report that lists new data volume, champion/challenger metrics, drift warnings, and promotion recommendations.
