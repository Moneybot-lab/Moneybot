#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

FEATURE_STORE_SCHEMA = "flat-feature-store.v1"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            raw = line.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


def _safe_partition(value: object, fallback: str = "unknown") -> str:
    text = str(value or "").strip().upper()
    if not text:
        return fallback
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in text)[:32] or fallback


def _event_year(row: dict[str, Any]) -> str:
    try:
        ts = int(float(row.get("ts")))
    except (TypeError, ValueError):
        return "unknown_year"
    return str(datetime.fromtimestamp(ts, tz=timezone.utc).year)


def _row_key(row: dict[str, Any]) -> tuple[int, str]:
    try:
        ts = int(float(row.get("ts")))
    except (TypeError, ValueError):
        ts = 0
    return ts, str(row.get("symbol") or "")


def _dataset_sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _split_rows(rows: list[dict[str, Any]], train_ratio: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ordered = sorted(rows, key=_row_key)
    if not ordered:
        return [], []
    pivot = int(len(ordered) * train_ratio)
    if len(ordered) > 1:
        pivot = min(max(1, pivot), len(ordered) - 1)
    return ordered[:pivot], ordered[pivot:]


def _all_columns(rows: Iterable[dict[str, Any]]) -> list[str]:
    preferred = [
        "ts",
        "symbol",
        "endpoint",
        "decision_source",
        "recommendation",
        "probability_up",
        "model_version",
        "return_1d",
        "return_5d",
        "label_up_5d",
        "return_bin_5d",
        "label_profit_5d",
        "label_drawdown_5d",
        "experiment_id",
        "cohort_id",
    ]
    found: set[str] = set()
    for row in rows:
        found.update(str(key) for key in row.keys())
    return [col for col in preferred if col in found] + sorted(found.difference(preferred))


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def materialize_flat_feature_store(input_path: Path, output_dir: Path, *, train_ratio: float = 0.8, write_csv: bool = True) -> dict[str, Any]:
    rows = _read_jsonl(input_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    columns = _all_columns(rows)
    train_rows, test_rows = _split_rows(rows, train_ratio)

    dataset_files: list[dict[str, Any]] = []
    splits = {"train": train_rows, "test": test_rows, "all": sorted(rows, key=_row_key)}
    for split_name, split_rows in splits.items():
        jsonl_path = output_dir / f"{split_name}.jsonl"
        _write_jsonl(jsonl_path, split_rows)
        entry: dict[str, Any] = {"split": split_name, "format": "jsonl", "path": str(jsonl_path), "rows": len(split_rows)}
        dataset_files.append(entry)
        if write_csv:
            csv_path = output_dir / f"{split_name}.csv"
            _write_csv(csv_path, split_rows, columns)
            dataset_files.append({"split": split_name, "format": "csv", "path": str(csv_path), "rows": len(split_rows)})

    partition_count = 0
    partition_rows = 0
    by_partition: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        by_partition.setdefault((_safe_partition(row.get("symbol")), _event_year(row)), []).append(row)
    for (symbol, year), partition in sorted(by_partition.items()):
        partition_path = output_dir / "partitions" / f"symbol={symbol}" / f"year={year}" / "data.jsonl"
        _write_jsonl(partition_path, sorted(partition, key=_row_key))
        dataset_files.append({"split": "all", "format": "jsonl", "path": str(partition_path), "rows": len(partition), "partition": {"symbol": symbol, "year": year}})
        partition_count += 1
        partition_rows += len(partition)

    feature_columns = sorted(col for col in columns if col.startswith("feature_"))
    manifest = {
        "schema_version": FEATURE_STORE_SCHEMA,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path),
        "input_sha256": _dataset_sha256(input_path),
        "output_dir": str(output_dir),
        "rows": len(rows),
        "train_rows": len(train_rows),
        "test_rows": len(test_rows),
        "train_ratio": train_ratio,
        "columns": columns,
        "feature_columns": feature_columns,
        "label_columns": sorted(col for col in columns if col.startswith("label_") or col.startswith("return_")),
        "partitioning": {"keys": ["symbol", "event_year"], "partitions": partition_count, "partition_rows": partition_rows},
        "files": dataset_files,
        "intended_uses": ["training", "backtesting", "challenger_models", "offline_analysis"],
        "live_routing": False,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Materialize decision training data into flat files for offline ML and backtesting.")
    parser.add_argument("--input", default="data/decision_training_snapshot.jsonl")
    parser.add_argument("--output-dir", default="data/flat_feature_store")
    parser.add_argument("--train-ratio", type=float, default=0.8)
    parser.add_argument("--no-csv", action="store_true", help="Write JSONL only.")
    args = parser.parse_args()
    manifest = materialize_flat_feature_store(
        Path(args.input),
        Path(args.output_dir),
        train_ratio=max(0.1, min(0.95, float(args.train_ratio))),
        write_csv=not args.no_csv,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
