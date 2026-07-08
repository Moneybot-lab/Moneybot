#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

QUALITY_SCHEMA_VERSION = "moneybot-training-quality-report.v1"
DEFAULT_REQUIRED_FEATURES = ("feature_close", "feature_return_1d_lagged", "feature_return_5d_lagged", "feature_volume")


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


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def _parse_day(value: Any) -> date | None:
    if value in {None, ""}:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def _dedupe_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    dropped = 0
    for row in rows:
        key = json.dumps(row, sort_keys=True, default=str)
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        out.append(row)
    return out, dropped


def _row_sort_key(row: dict[str, Any]) -> tuple[int, str]:
    try:
        ts = int(float(row.get("ts") or 0))
    except (TypeError, ValueError):
        ts = 0
    return ts, str(row.get("symbol") or "")


def _split_rows(rows: list[dict[str, Any]], train_ratio: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ordered = sorted(rows, key=_row_sort_key)
    if not ordered:
        return [], []
    pivot = int(len(ordered) * train_ratio)
    if len(ordered) > 1:
        pivot = min(max(1, pivot), len(ordered) - 1)
    return ordered[:pivot], ordered[pivot:]


def clean_training_snapshot(
    input_path: Path,
    output_dir: Path,
    *,
    required_features: Iterable[str] = DEFAULT_REQUIRED_FEATURES,
    label_column: str = "label_up_5d",
    max_market_lag_days: int = 3,
    train_ratio: float = 0.8,
) -> dict[str, Any]:
    raw_rows = _read_jsonl(input_path)
    deduped, duplicate_rows_dropped = _dedupe_rows(raw_rows)
    required = [str(feature) for feature in required_features if str(feature).strip()]

    kept: list[dict[str, Any]] = []
    eval_rows: list[dict[str, Any]] = []
    drop_counts = {
        "duplicates": duplicate_rows_dropped,
        "missing_label": 0,
        "missing_required_features": 0,
        "stale_market_asof_date": 0,
    }
    missing_feature_counts = {feature: 0 for feature in required}

    for row in deduped:
        if not _has_value(row.get(label_column)):
            drop_counts["missing_label"] += 1
            continue
        missing_features = [feature for feature in required if not _has_value(row.get(feature))]
        if missing_features:
            drop_counts["missing_required_features"] += 1
            for feature in missing_features:
                missing_feature_counts[feature] = missing_feature_counts.get(feature, 0) + 1
            continue
        event_day = _parse_day(row.get("event_date"))
        market_day = _parse_day(row.get("market_asof_date"))
        if event_day is None or market_day is None or market_day > event_day or (event_day - market_day).days > max(0, int(max_market_lag_days)):
            drop_counts["stale_market_asof_date"] += 1
            continue
        kept.append(row)
        if _has_value(row.get("probability_up")):
            eval_rows.append(row)

    train_rows, test_rows = _split_rows(kept, train_ratio)
    output_dir.mkdir(parents=True, exist_ok=True)
    cleaned_path = output_dir / "cleaned_all.jsonl"
    train_path = output_dir / "cleaned_train.jsonl"
    test_path = output_dir / "cleaned_test.jsonl"
    eval_path = output_dir / "evaluation_probability_rows.jsonl"
    report_path = output_dir / "model_quality_report.json"
    _write_jsonl(cleaned_path, kept)
    _write_jsonl(train_path, train_rows)
    _write_jsonl(test_path, test_rows)
    _write_jsonl(eval_path, eval_rows)

    report = {
        "schema_version": QUALITY_SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path),
        "output_dir": str(output_dir),
        "label_column": label_column,
        "required_features": required,
        "max_market_lag_days": max_market_lag_days,
        "train_ratio": train_ratio,
        "raw_rows": len(raw_rows),
        "rows_after_deduplication": len(deduped),
        "cleaned_rows": len(kept),
        "train_rows": len(train_rows),
        "test_rows": len(test_rows),
        "evaluation_rows_with_probability_up": len(eval_rows),
        "drop_counts": drop_counts,
        "missing_feature_counts": missing_feature_counts,
        "outputs": {
            "cleaned_all": str(cleaned_path),
            "cleaned_train": str(train_path),
            "cleaned_test": str(test_path),
            "evaluation_probability_rows": str(eval_path),
        },
        "training_ready": bool(train_rows and test_rows),
        "evaluation_ready": bool(eval_rows),
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean and quality-gate leakage-safe training rows before model training.")
    parser.add_argument("--input", default="data/track_b/decision_training_snapshot_massive.jsonl")
    parser.add_argument("--output-dir", default="data/track_b/training_quality")
    parser.add_argument("--required-features", default=",".join(DEFAULT_REQUIRED_FEATURES), help="Comma-separated required feature columns.")
    parser.add_argument("--label-column", default="label_up_5d")
    parser.add_argument("--max-market-lag-days", type=int, default=3)
    parser.add_argument("--train-ratio", type=float, default=0.8)
    args = parser.parse_args()
    report = clean_training_snapshot(
        Path(args.input),
        Path(args.output_dir),
        required_features=[item.strip() for item in args.required_features.split(",") if item.strip()],
        label_column=args.label_column,
        max_market_lag_days=max(0, int(args.max_market_lag_days)),
        train_ratio=max(0.1, min(0.95, float(args.train_ratio))),
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["training_ready"]:
        raise SystemExit("Cleaned training snapshot did not produce non-empty train/test splits")


if __name__ == "__main__":
    main()
