#!/usr/bin/env python3
"""Extract compact, deterministic V3.1 benchmark evidence from downloaded artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "alpha-atlas-v31-benchmark-recovery.v1"
RUNS = {
    "156": {
        "actions_run_id": 32700956267,
        "artifact_id": 9510663000,
        "artifact_digest": "sha256:a5061e01cae5ee15e0ef55810ca9932d952eae5ab27684b6dfd699ef0299376a",
        "head_sha": "058a7030cce899de3c7e3f0aaa304e7d3ed143ce",
    },
    "157": {
        "actions_run_id": 32820019463,
        "artifact_id": 9553009280,
        "artifact_digest": "sha256:12103332cdbda5a443d762780af94ba81a546093be9cd9e5481deb6b3b443d58",
        "head_sha": "058a7030cce899de3c7e3f0aaa304e7d3ed143ce",
    },
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _classification(path: Path) -> str:
    name = path.name.lower()
    if name == "candidate_alpha_atlas_v31_clean.json":
        return "v31_candidate_model"
    exact_evidence = {
        "alpha_atlas_v31_model_report.json": "model_report",
        "alpha_atlas_v31_feature_coverage_report.json": "feature_coverage",
        "alpha_atlas_v31_backtest_report.json": "evaluation",
        "alpha_atlas_v31_representative_serving_dry_runs.json": "serving_dry_runs",
        "production_servability_certification.json": "servability",
        "candidate_selection_report.json": "candidate_selection",
        "candidate_readiness_report.json": "candidate_selection",
    }
    if name in exact_evidence:
        return exact_evidence[name]
    if "model" in name and path.suffix.lower() in {
        ".joblib",
        ".pkl",
        ".pickle",
        ".bin",
    }:
        return "model_artifact"
    if "metadata" in name:
        return "model_metadata"
    if "feature" in name and ("contract" in name or "manifest" in name):
        return "feature_contract"
    if "backtest" in name or "evaluation" in name:
        return "evaluation"
    if "health" in name or "servab" in name:
        return "servability"
    if "diagnostic" in name or "quality" in name:
        return "training_diagnostics"
    if "promotion" in name or "candidate" in name or "routing" in name:
        return "status"
    return "other"


def extract_run(run_number: str, directory: Path) -> dict[str, Any]:
    if run_number not in RUNS:
        raise ValueError(f"unsupported run number: {run_number}")
    files = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        record: dict[str, Any] = {
            "path": path.relative_to(directory).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
            "classification": _classification(path),
        }
        if path.suffix.lower() == ".json" and path.stat().st_size <= 2_000_000:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, dict):
                record["extracted"] = {
                    key: payload[key]
                    for key in sorted(payload)
                    if key
                    in {
                        "model_version",
                        "feature_contract_version",
                        "schema_version",
                        "candidate_win",
                        "automatic_promotion",
                        "ready_for_live_routing",
                        "production_servable",
                        "status",
                        "metrics",
                    }
                }
        files.append(record)
    models = [item for item in files if item["classification"] == "v31_candidate_model"]
    metadata = [item for item in files if item["classification"] == "model_metadata"]
    return {
        "schema_version": SCHEMA_VERSION,
        "run_number": int(run_number),
        **RUNS[run_number],
        "artifact_name": "track-b-offline-output",
        "download_status": "RECOVERED" if files else "EMPTY_ARTIFACT_DIRECTORY",
        "artifact_size_bytes_extracted": sum(item["size_bytes"] for item in files),
        "file_count": len(files),
        "model_artifact_found": bool(models),
        "metadata_found": bool(metadata),
        "model_artifact_path": models[0]["path"] if len(models) == 1 else None,
        "model_artifact_sha256": models[0]["sha256"] if len(models) == 1 else None,
        "evidence_hashes_by_classification": {
            classification: [
                item["sha256"]
                for item in files
                if item["classification"] == classification
            ]
            for classification in sorted({item["classification"] for item in files})
            if classification != "other"
        },
        "files": files,
        "unverified_fields": [] if files else ["all_extracted_benchmark_evidence"],
    }


def recover(run156: Path, run157: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    manifests = {
        number: extract_run(number, path)
        for number, path in (("156", run156), ("157", run157))
    }
    for number, manifest in manifests.items():
        (output / f"run_{number}_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    hashes = [manifest["model_artifact_sha256"] for manifest in manifests.values()]
    comparison = {
        "schema_version": SCHEMA_VERSION,
        "runs": [156, 157],
        "status": (
            "INDEPENDENT_CANDIDATE_SNAPSHOTS_RECOVERED"
            if all(hashes)
            else "RECOVERED_WITHOUT_UNIQUE_MATCHING_MODEL_EVIDENCE"
        ),
        "model_artifact_sha256_by_run": {
            number: manifest["model_artifact_sha256"]
            for number, manifest in manifests.items()
        },
        "automatic_promotion": False,
        "ready_for_live_routing": False,
        "benchmark_only": True,
    }
    (output / "comparison.json").write_text(
        json.dumps(comparison, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output / "comparison.md").write_text(
        "# V3.1 Track B runs #156/#157 recovery\n\n"
        f"Status: `{comparison['status']}`\n\n"
        f"- Run #156 model SHA-256: `{hashes[0] or 'NOT_FOUND'}`\n"
        f"- Run #157 model SHA-256: `{hashes[1] or 'NOT_FOUND'}`\n"
        "- Benchmark only; automatic promotion and live routing remain disabled.\n",
        encoding="utf-8",
    )
    return comparison


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-156-dir", required=True)
    parser.add_argument("--run-157-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    comparison = recover(
        Path(args.run_156_dir), Path(args.run_157_dir), Path(args.output_dir)
    )
    print(json.dumps(comparison, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
