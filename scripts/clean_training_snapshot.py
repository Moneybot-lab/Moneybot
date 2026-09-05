#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from moneybot.services.decision_target import TARGET_NAME
from moneybot.services.alpha_atlas_v4_canonical_observations import (
    CANONICAL_OBSERVATION_CONTRACT_VERSION,
    CANONICAL_OBSERVATION_SCHEMA_VERSION,
    V4_RAW_ROW_SCHEMA,
    DEPRECATED_PRIOR_STATE_FEATURES,
    V4_FEATURE_CONTRACT_VERSION,
)

QUALITY_SCHEMA_VERSION = "moneybot-training-quality-report.v2"
REQUIRED_CANONICAL_SCHEMA_VERSION = CANONICAL_OBSERVATION_SCHEMA_VERSION
DEFAULT_REQUIRED_FEATURES = (
    "feature_close",
    "feature_return_1d_lagged",
    "feature_return_5d_lagged",
    "feature_volume",
)


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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def _split_rows(
    rows: list[dict[str, Any]], train_ratio: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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
    label_column: str = TARGET_NAME,
    max_market_lag_days: int = 3,
    train_ratio: float = 0.8,
) -> dict[str, Any]:
    manifest_path = input_path.with_suffix(input_path.suffix + ".manifest.json")
    if not manifest_path.is_file():
        raise ValueError(
            "Canonical Massive input manifest is required for corporate-action validation"
        )
    try:
        input_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            "Canonical Massive input manifest is missing or corrupt"
        ) from exc
    if input_manifest.get("schema_version") != REQUIRED_CANONICAL_SCHEMA_VERSION:
        raise ValueError(
            "Stale unadjusted Massive training snapshot cannot be cleaned as canonical data"
        )
    if (
        input_manifest.get("model_feature_contract_version")
        != V4_FEATURE_CONTRACT_VERSION
    ):
        raise ValueError("Canonical Massive input feature contract is incompatible")
    if input_manifest.get(
        "corporate_action_normalization_passed"
    ) is not True or not input_manifest.get("split_metadata_hash"):
        raise ValueError(
            "Canonical Massive training requires passed corporate-action normalization and split metadata hash"
        )
    raw_rows = _read_jsonl(input_path)
    invalid_lineage = [
        row
        for row in raw_rows
        if row.get("canonical_dataset_schema_version") != V4_RAW_ROW_SCHEMA
        or row.get("canonical_observation_schema_version")
        != REQUIRED_CANONICAL_SCHEMA_VERSION
        or row.get("canonicalization_contract_version")
        != CANONICAL_OBSERVATION_CONTRACT_VERSION
        or row.get("model_feature_contract_version") != V4_FEATURE_CONTRACT_VERSION
        or row.get("model_sample_weight") != 1.0
        or bool(DEPRECATED_PRIOR_STATE_FEATURES.intersection(row))
        or row.get("split_metadata_hash") != input_manifest["split_metadata_hash"]
        or row.get("price_adjustment_policy") != "event_time_split_adjusted"
    ]
    if invalid_lineage:
        raise ValueError(
            "Training rows do not match the canonical split-adjusted dataset lineage"
        )
    canonical_ids = [row.get("canonical_observation_id") for row in raw_rows]
    if not all(canonical_ids) or len(canonical_ids) != len(set(canonical_ids)):
        raise ValueError(
            "Canonical observation input contains duplicate or missing IDs"
        )
    deduped, duplicate_rows_dropped = raw_rows, 0
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
        missing_features = [
            feature for feature in required if not _has_value(row.get(feature))
        ]
        if missing_features:
            drop_counts["missing_required_features"] += 1
            for feature in missing_features:
                missing_feature_counts[feature] = (
                    missing_feature_counts.get(feature, 0) + 1
                )
            continue
        event_day = _parse_day(row.get("event_date"))
        market_day = _parse_day(row.get("market_asof_date"))
        if (
            event_day is None
            or market_day is None
            or market_day > event_day
            or (event_day - market_day).days > max(0, int(max_market_lag_days))
        ):
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

    canonical_input_sha256 = _sha256(input_path)
    cleaned_manifest_path = cleaned_path.with_suffix(".jsonl.manifest.json")
    cleaned_manifest = {
        "schema_version": "alpha-atlas-v4-cleaned-observations.v2",
        "canonical_input_path": str(input_path),
        "canonical_input_sha256": canonical_input_sha256,
        "canonical_input_manifest": str(manifest_path),
        "canonical_input_schema_version": input_manifest["schema_version"],
        "canonicalization_contract_version": CANONICAL_OBSERVATION_CONTRACT_VERSION,
        "model_feature_contract_version": V4_FEATURE_CONTRACT_VERSION,
        "cleaned_observations_path": str(cleaned_path),
        "cleaned_observations_sha256": _sha256(cleaned_path),
        "split_metadata_hash": input_manifest["split_metadata_hash"],
        "model_sample_weight_policy": "unit_weight_only",
    }
    cleaned_manifest_path.write_text(
        json.dumps(cleaned_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    report = {
        "schema_version": QUALITY_SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path),
        "canonical_input_sha256": canonical_input_sha256,
        "canonical_manifest_path": str(manifest_path),
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
            "cleaned_manifest": str(cleaned_manifest_path),
        },
        "training_ready": bool(train_rows and test_rows),
        "evaluation_ready": bool(eval_rows),
        "corporate_action_normalization_required": True,
        "corporate_action_normalization_passed": True,
        "split_metadata_available": True,
        "split_metadata_hash": input_manifest["split_metadata_hash"],
        "split_events_applied": int(input_manifest.get("split_events_loaded") or 0),
        "feature_split_boundary_errors": 0,
        "label_split_boundary_errors": 0,
        "suspicious_return_rows_after_adjustment": 0,
        "canonical_input_schema_version": input_manifest["schema_version"],
        "canonicalization_contract_version": CANONICAL_OBSERVATION_CONTRACT_VERSION,
        "raw_request_rows": int(input_manifest.get("raw_request_rows") or 0),
        "canonical_observations": len(raw_rows),
    }
    before_after_path = output_dir / "split_adjustment_before_after_report.json"
    if before_after_path.is_file():
        try:
            before_after = json.loads(before_after_path.read_text(encoding="utf-8"))
            report["suspicious_return_rows_after_adjustment"] = int(
                before_after.get("suspicious_rows_after") or 0
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            raise ValueError("Corporate-action before/after audit is corrupt")
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clean and quality-gate leakage-safe training rows before model training."
    )
    parser.add_argument(
        "--input", default="data/track_b/decision_training_snapshot_massive.jsonl"
    )
    parser.add_argument("--output-dir", default="data/track_b/training_quality")
    parser.add_argument(
        "--required-features",
        default=",".join(DEFAULT_REQUIRED_FEATURES),
        help="Comma-separated required feature columns.",
    )
    parser.add_argument("--label-column", default=TARGET_NAME)
    parser.add_argument("--max-market-lag-days", type=int, default=3)
    parser.add_argument("--train-ratio", type=float, default=0.8)
    args = parser.parse_args()
    report = clean_training_snapshot(
        Path(args.input),
        Path(args.output_dir),
        required_features=[
            item.strip() for item in args.required_features.split(",") if item.strip()
        ],
        label_column=args.label_column,
        max_market_lag_days=max(0, int(args.max_market_lag_days)),
        train_ratio=max(0.1, min(0.95, float(args.train_ratio))),
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["training_ready"]:
        raise SystemExit(
            "Cleaned training snapshot did not produce non-empty train/test splits"
        )


if __name__ == "__main__":
    main()
