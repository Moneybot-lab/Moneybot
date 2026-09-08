#!/usr/bin/env python3
"""Validate run-scoped V4 workflow artifacts without weakening pipeline contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from moneybot.services.alpha_atlas_v4_canonical_observations import (
    CANONICAL_OBSERVATION_CONTRACT_VERSION,
    CANONICAL_OBSERVATION_SCHEMA_VERSION,
    V4_RAW_ROW_SCHEMA,
)

TIMING_CONTRACT_VERSION = "alpha-atlas-v4-prediction-execution-contract.v1"
FEATURE_CONTRACT_VERSION = "alpha-atlas-v4-features.v2"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"required artifact is missing: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"artifact is not a JSON object: {path.name}")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"required artifact is missing: {path.name}")
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"row {number} is not an object: {path.name}")
        rows.append(value)
    if not rows:
        raise ValueError(f"artifact contains no rows: {path.name}")
    return rows


def validate_raw(rows_path: Path, manifest_path: Path) -> dict[str, Any]:
    manifest = _json(manifest_path)
    rows = _jsonl(rows_path)
    if manifest.get("schema_version") != V4_RAW_ROW_SCHEMA:
        raise ValueError("raw manifest schema is not massive-decision-training-rows.v4")
    if manifest.get("timing_contract_version") != TIMING_CONTRACT_VERSION:
        raise ValueError("raw manifest timing contract is incompatible")
    if manifest.get("model_feature_contract_version") != FEATURE_CONTRACT_VERSION:
        raise ValueError("raw manifest feature contract is incompatible")
    for row in rows:
        if row.get("canonical_dataset_schema_version") != V4_RAW_ROW_SCHEMA:
            raise ValueError("raw row schema is incompatible")
        if row.get("timing_contract_version") != TIMING_CONTRACT_VERSION:
            raise ValueError("raw row timing contract is incompatible")
        if row.get("model_feature_contract_version") != FEATURE_CONTRACT_VERSION:
            raise ValueError("raw row feature contract is incompatible")
    return {
        "schema": V4_RAW_ROW_SCHEMA,
        "rows": len(rows),
        "sha256": _sha256(rows_path),
    }


def validate_canonical(rows_path: Path, manifest_path: Path) -> dict[str, Any]:
    manifest = _json(manifest_path)
    rows = _jsonl(rows_path)
    if manifest.get("schema_version") != CANONICAL_OBSERVATION_SCHEMA_VERSION:
        raise ValueError("canonical manifest schema is incompatible")
    if (
        manifest.get("canonicalization_contract_version")
        != CANONICAL_OBSERVATION_CONTRACT_VERSION
    ):
        raise ValueError("canonicalization contract is incompatible")
    if manifest.get("model_feature_contract_version") != FEATURE_CONTRACT_VERSION:
        raise ValueError("canonical manifest feature contract is incompatible")
    if manifest.get("canonical_observations_sha256") != _sha256(rows_path):
        raise ValueError("canonical observation hash does not match manifest")
    identifiers = [row.get("canonical_observation_id") for row in rows]
    if not all(identifiers) or len(identifiers) != len(set(identifiers)):
        raise ValueError("canonical IDs are missing or duplicated")
    if any(row.get("model_sample_weight") != 1.0 for row in rows):
        raise ValueError("canonical model sample weights must equal one")
    if any(
        row.get("canonicalization_contract_version")
        != CANONICAL_OBSERVATION_CONTRACT_VERSION
        for row in rows
    ):
        raise ValueError("canonical rows mix contract versions")
    if any(
        row.get("model_feature_contract_version") != FEATURE_CONTRACT_VERSION
        for row in rows
    ):
        raise ValueError("canonical rows mix feature contract versions")
    return {
        "schema": CANONICAL_OBSERVATION_SCHEMA_VERSION,
        "rows": len(rows),
        "sha256": _sha256(rows_path),
    }


def write_failure_summary(
    path: Path,
    *,
    failed_stage: str,
    input_label: str,
    manifest_path: Path | None,
) -> dict[str, Any]:
    manifest = _json(manifest_path) if manifest_path and manifest_path.is_file() else {}
    detected = manifest.get("schema_version")
    kind = (
        "canonical"
        if detected == CANONICAL_OBSERVATION_SCHEMA_VERSION
        else "raw" if detected == V4_RAW_ROW_SCHEMA else "unknown"
    )
    summary = {
        "schema_version": "alpha-atlas-v4-track-b-failure-summary.v1",
        "failed_stage": failed_stage,
        "input_path_label": input_label,
        "input_kind": kind,
        "detected_schema": detected,
        "expected_schema": (
            CANONICAL_OBSERVATION_SCHEMA_VERSION
            if failed_stage in {"clean", "feature_store", "training", "evaluation"}
            else V4_RAW_ROW_SCHEMA
        ),
        "timing_contract_version": manifest.get("timing_contract_version"),
        "canonicalization_contract_version": manifest.get(
            "canonicalization_contract_version"
        ),
        "manifest_hash": manifest.get("dataset_manifest_hash")
        or manifest.get("canonical_observations_sha256"),
        "recommended_corrective_stage": (
            "canonicalize_raw_v4_rows"
            if kind != "canonical"
            else "inspect_failed_stage_diagnostics"
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage", choices=("raw", "canonical", "failure-summary"), required=True
    )
    parser.add_argument("--rows")
    parser.add_argument("--manifest")
    parser.add_argument("--output")
    parser.add_argument("--failed-stage", default="unknown")
    parser.add_argument("--input-label", default="unknown")
    args = parser.parse_args()
    if args.stage == "failure-summary":
        result = write_failure_summary(
            Path(args.output),
            failed_stage=args.failed_stage,
            input_label=args.input_label,
            manifest_path=Path(args.manifest) if args.manifest else None,
        )
    else:
        if not args.rows or not args.manifest:
            parser.error("--rows and --manifest are required")
        result = (validate_raw if args.stage == "raw" else validate_canonical)(
            Path(args.rows), Path(args.manifest)
        )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
