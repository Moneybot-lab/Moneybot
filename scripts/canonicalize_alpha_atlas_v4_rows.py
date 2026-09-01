#!/usr/bin/env python3
"""Materialize research-only Alpha Atlas V4 canonical observations and request map."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from moneybot.services.alpha_atlas_v4_canonical_observations import (
    CANONICAL_HASH_POLICY,
    CANONICAL_OBSERVATION_CONTRACT_VERSION,
    CANONICAL_OBSERVATION_SCHEMA_VERSION,
    CanonicalizationError,
    canonicalize_v4_rows,
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"input row {number} is not an object")
        rows.append(value)
    return rows


def _write_jsonl(path: Path, rows) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":"), default=str) + "\n"
        for row in rows
    )
    path.write_text(content, encoding="utf-8")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def materialize_canonical_observations(
    input_path: Path, output_dir: Path
) -> dict[str, Any]:
    raw_manifest_path = input_path.with_suffix(input_path.suffix + ".manifest.json")
    if not raw_manifest_path.is_file():
        raise ValueError("raw V4 dataset manifest is required")
    raw_manifest = json.loads(raw_manifest_path.read_text(encoding="utf-8"))
    if raw_manifest.get("schema_version") != "massive-decision-training-rows.v4":
        raise ValueError("canonicalization requires raw V4 rows")
    output_dir.mkdir(parents=True, exist_ok=True)
    diagnostics_path = output_dir / "canonicalization_diagnostics.json"
    try:
        result = canonicalize_v4_rows(_read_jsonl(input_path))
    except CanonicalizationError as exc:
        diagnostics_path.write_text(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error": exc.reason,
                    **exc.diagnostics,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        raise
    observations_path = output_dir / "canonical_observations.jsonl"
    request_map_path = output_dir / "request_to_observation_map.jsonl"
    observation_hash = _write_jsonl(observations_path, result.observations)
    request_map_hash = _write_jsonl(request_map_path, result.request_map)
    diagnostics_path.write_text(
        json.dumps(result.diagnostics, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": CANONICAL_OBSERVATION_SCHEMA_VERSION,
        "canonicalization_contract_version": CANONICAL_OBSERVATION_CONTRACT_VERSION,
        "canonical_hash_policy": CANONICAL_HASH_POLICY,
        "model_feature_contract_version": raw_manifest.get(
            "model_feature_contract_version"
        ),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "raw_input_path": str(input_path),
        "raw_input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "canonical_observations_path": str(observations_path),
        "request_map_path": str(request_map_path),
        "canonical_observations_sha256": observation_hash,
        "request_map_sha256": request_map_hash,
        "raw_dataset_manifest": str(raw_manifest_path),
        "raw_dataset_manifest_hash": raw_manifest.get("dataset_manifest_hash"),
        "corporate_action_normalization_passed": raw_manifest.get(
            "corporate_action_normalization_passed"
        ),
        "split_metadata_hash": raw_manifest.get("split_metadata_hash"),
        "split_events_loaded": raw_manifest.get("split_events_loaded", 0),
        **result.diagnostics,
    }
    observations_path.with_suffix(".jsonl.manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Canonicalize Alpha Atlas V4 raw requests before cleaning/model use."
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            materialize_canonical_observations(Path(args.input), Path(args.output_dir)),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
