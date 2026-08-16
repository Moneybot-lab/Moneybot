# Flat Feature Store for Offline AI Research

Yes: large flat files are beneficial for MoneyBot's training, backtesting, challenger models, and offline analysis when they are treated as an offline feature store rather than a live-serving database.

## Why this helps

Flat files give the project a reproducible, append-friendly corpus of decision rows and labels. They make it easier to:

- train challenger models without touching production routing;
- backtest on chronological train/test splits;
- inspect feature coverage and label quality in notebooks or external ML tools;
- preserve exactly which rows produced a model or comparison report;
- scale later to Parquet/object storage without changing the live app path.

## Guardrails

- Flat files are for offline workflows only; they must not become a real-time quote source.
- Every export writes a manifest with schema version, input hash, split sizes, feature columns, label columns, and file inventory.
- Splits are chronological to reduce look-ahead leakage.
- Partitioned files use `symbol` and event year so offline jobs can scan slices without loading the entire corpus.
- Raw provider redistribution restrictions still apply; keep Massive-sourced or other licensed data internal unless the license explicitly permits broader use.

## Workflow

The Track B offline pipeline now materializes a flat feature store after building the labeled decision dataset and before candidate training:

```bash
python scripts/run_track_b_offline.py --input-log data/decision_events.jsonl --output-dir data/track_b
```

The feature-store-only export can also be run directly:

```bash
python scripts/day15_materialize_flat_feature_store.py \
  --input data/track_b/decision_training_snapshot_track_b.jsonl \
  --output-dir data/track_b/flat_feature_store
```

Expected outputs:

- `manifest.json` for reproducibility and audit;
- `train.jsonl` / `test.jsonl` / `all.jsonl` for modeling pipelines;
- optional CSV mirrors for spreadsheet and quick notebook analysis;
- `partitions/symbol=<SYMBOL>/year=<YYYY>/data.jsonl` for targeted offline scans.

## Next upgrades

Once the JSONL/CSV workflow is stable and row counts are high, add a Parquet writer and a rolling validation report. Parquet should be an additive format, not a replacement for the manifest contract.
