#!/usr/bin/env python3
"""Validate and manifest the immutable accepted V4 certification ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

SOURCE_RUN_ID = 34906607045
SOURCE_ATTEMPT = 1
SOURCE_WORKFLOW = "V4 Verify Weighting Model Reload"
SOURCE_ARTIFACT = "v4-weighting-model-reload-34906607045-1"
IMPORTANT = (
    "weighting_model_reload_verification.json",
    "corrected_weighting_model_states.json",
    "weighting_model_fill_policy_verification.json",
    "sources.json",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_freeze(
    run_json: Path,
    artifact_json: Path,
    source_zip: Path,
    manifest_path: Path,
    checksum_path: Path,
    extracted_dir: Path,
) -> dict[str, object]:
    run = json.loads(run_json.read_text())
    artifact = json.loads(artifact_json.read_text())
    if (
        int(run.get("id", 0)) != SOURCE_RUN_ID
        or run.get("name") != SOURCE_WORKFLOW
        or int(run.get("run_attempt", 0)) != SOURCE_ATTEMPT
        or run.get("conclusion") != "success"
    ):
        raise ValueError("FROZEN_SOURCE_RUN_MISMATCH")
    if (
        artifact.get("name") != SOURCE_ARTIFACT
        or artifact.get("expired") is not False
        or int(artifact.get("workflow_run", {}).get("id", 0)) != SOURCE_RUN_ID
    ):
        raise ValueError("FROZEN_SOURCE_ARTIFACT_MISMATCH")
    found: dict[str, tuple[str, bytes]] = {}
    with zipfile.ZipFile(source_zip) as archive:
        for member in archive.infolist():
            path = Path(member.filename)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("UNSAFE_ARCHIVE_MEMBER")
            if path.name in IMPORTANT:
                if path.name in found:
                    raise ValueError(f"AMBIGUOUS_EVIDENCE:{path.name}")
                found[path.name] = (member.filename, archive.read(member))
    required = {
        "weighting_model_reload_verification.json",
        "weighting_model_fill_policy_verification.json",
        "sources.json",
    }
    if not required <= set(found):
        raise ValueError(
            f"MISSING_CERTIFICATION_EVIDENCE:{sorted(required - set(found))}"
        )
    reload_report = json.loads(found["weighting_model_reload_verification.json"][1])
    fill_report = json.loads(found["weighting_model_fill_policy_verification.json"][1])
    if (
        reload_report.get("all_replays_verified") is not True
        or reload_report.get("final_holdout_accessed") is not False
        or reload_report.get("automatic_promotion") is not False
        or fill_report.get("status") != "VERIFIED"
        or fill_report.get("models_refit") is not False
        or fill_report.get("final_holdout_accessed") is not False
        or fill_report.get("automatic_promotion_performed") is not False
    ):
        raise ValueError("CERTIFICATION_STATUS_NOT_VERIFIED")
    extracted_dir.mkdir(parents=True, exist_ok=True)
    evidence = []
    for name, (member, content) in sorted(found.items()):
        output = extracted_dir / name
        output.write_bytes(content)
        evidence.append(
            {
                "name": name,
                "archive_member": member,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        )
    archive_sha = _sha(source_zip)
    manifest = {
        "schema_version": "alpha-atlas-v4-weighting-certification-freeze.v1",
        "status": "VERIFIED",
        "archive_only_no_model_computation": True,
        "source": {
            "workflow": SOURCE_WORKFLOW,
            "run_id": SOURCE_RUN_ID,
            "run_attempt": SOURCE_ATTEMPT,
            "head_sha": run["head_sha"],
            "artifact_id": artifact["id"],
            "artifact_name": SOURCE_ARTIFACT,
            "artifact_size_bytes_api": artifact.get("size_in_bytes"),
        },
        "original_artifact": {
            "filename": source_zip.name,
            "sha256": archive_sha,
            "size_bytes": source_zip.stat().st_size,
            "bytes_modified": False,
        },
        "certification": {
            "reload_all_replays_verified": True,
            "fill_policy_status": "VERIFIED",
            "final_holdout_accessed": False,
            "models_refit": False,
            "automatic_promotion_performed": False,
        },
        "extracted_reviewer_copies": evidence,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    checksum_path.write_text(
        f"{archive_sha}  {source_zip.name}\n{_sha(manifest_path)}  {manifest_path.name}\n"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-json", required=True, type=Path)
    parser.add_argument("--artifact-json", required=True, type=Path)
    parser.add_argument("--source-zip", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--checksums", required=True, type=Path)
    parser.add_argument("--extracted-dir", required=True, type=Path)
    args = parser.parse_args()
    build_freeze(
        args.run_json,
        args.artifact_json,
        args.source_zip,
        args.manifest,
        args.checksums,
        args.extracted_dir,
    )


if __name__ == "__main__":
    main()
