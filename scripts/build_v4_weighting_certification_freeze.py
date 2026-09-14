#!/usr/bin/env python3
"""Build the immutable composite V4 weighting certification freeze."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

WORKFLOW = "V4 Verify Weighting Model Reload"
TAG = "v4-weighting-model-certification-2026-09-14"
TITLE = "V4 Weighting Model Independent Certification — Frozen Evidence"
EXPECTED_CORRECTED_STATES_SHA256 = (
    "f5d7e92e510a5d154a2e0b47ae1c36f317991624af3b9adda0b73bea78cb3b9a"
)


@dataclass(frozen=True)
class Source:
    key: str
    purpose: str
    run_id: int
    artifact_name: str
    required: tuple[str, ...]


RELOAD = Source(
    "reload_certification",
    "weighting_model_reload_and_corrected_states",
    34876711068,
    "v4-weighting-model-reload-34876711068-1",
    (
        "weighting_model_reload_verification.json",
        "corrected_weighting_model_states.json",
        "sources.json",
    ),
)
FILL = Source(
    "fill_policy_certification",
    "fill_policy_certification",
    34906607045,
    "v4-weighting-model-reload-34906607045-1",
    ("weighting_model_fill_policy_verification.json", "sources.json"),
)


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _load_source(
    source: Source, run_path: Path, artifact_path: Path, archive_path: Path
):
    run = json.loads(run_path.read_text())
    artifact = json.loads(artifact_path.read_text())
    prefix = "RELOAD" if source is RELOAD else "FILL"
    if (
        int(run.get("id", 0)) != source.run_id
        or run.get("name") != WORKFLOW
        or not run.get("head_sha")
    ):
        raise ValueError(f"{prefix}_SOURCE_RUN_MISMATCH")
    if int(run.get("run_attempt", 0)) != 1 or run.get("conclusion") != "success":
        raise ValueError(f"{prefix}_SOURCE_NOT_SUCCESSFUL")
    if (
        artifact.get("name") != source.artifact_name
        or int(artifact.get("workflow_run", {}).get("id", 0)) != source.run_id
    ):
        raise ValueError(f"{prefix}_SOURCE_ARTIFACT_MISSING")
    if artifact.get("expired") is not False:
        raise ValueError(f"{prefix}_SOURCE_ARTIFACT_EXPIRED")

    found: dict[str, tuple[str, bytes]] = {}
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            path = Path(member.filename)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"{prefix}_UNSAFE_ARCHIVE_MEMBER")
            if path.name in source.required:
                if path.name in found:
                    raise ValueError(f"{prefix}_AMBIGUOUS_EVIDENCE:{path.name}")
                found[path.name] = (member.filename, archive.read(member))
    missing = set(source.required) - set(found)
    if missing:
        raise ValueError(f"{prefix}_MISSING_CERTIFICATION_EVIDENCE:{sorted(missing)}")
    return run, artifact, found


def _validate_reload(found: dict[str, tuple[str, bytes]]) -> None:
    report = json.loads(found["weighting_model_reload_verification.json"][1])
    models = report.get("models") or []
    if (
        report.get("all_replays_verified") is not True
        or report.get("prediction_reproducibility_status") != "VERIFIED"
        or report.get("models_fitted") is not False
        or report.get("final_holdout_accessed") is not False
        or report.get("automatic_promotion") is not False
        or len(models) != 6
        or any(
            model.get("replay_status") != "VERIFIED"
            or model.get("decision_mismatch_count") != 0
            or model.get("scores_outside_tolerance") != 0
            for model in models
        )
    ):
        raise ValueError("RELOAD_CERTIFICATION_STATUS_NOT_VERIFIED")
    actual = _sha_bytes(found["corrected_weighting_model_states.json"][1])
    if actual != EXPECTED_CORRECTED_STATES_SHA256:
        raise ValueError("CORRECTED_STATES_SHA256_MISMATCH")


def _validate_fill(found: dict[str, tuple[str, bytes]]) -> None:
    report = json.loads(found["weighting_model_fill_policy_verification.json"][1])
    folds = report.get("folds") or []
    if (
        report.get("status") != "VERIFIED"
        or report.get("evidence_only") is not True
        or report.get("models_refit") is not False
        or report.get("predictions_changed") is not False
        or report.get("scores_or_decisions_changed") is not False
        or report.get("corrected_states_changed") is not False
        or report.get("final_holdout_accessed") is not False
        or report.get("automatic_promotion_performed") is not False
        or len(folds) != 6
        or any(
            fold.get("training_only_fill_statistics") is not True
            or fold.get("validation_rows_used_to_compute_fill_values") != 0
            or fold.get("holdout_rows_used_to_compute_fill_values") != 0
            or fold.get("execution_matrix", {}).get("verified") is not True
            or fold.get("execution_matrix", {}).get("mismatched_cells") != 0
            for fold in folds
        )
    ):
        raise ValueError("FILL_CERTIFICATION_STATUS_NOT_VERIFIED")


def build_freeze(
    *,
    reload_run: Path,
    reload_artifact: Path,
    reload_zip: Path,
    fill_run: Path,
    fill_artifact: Path,
    fill_zip: Path,
    manifest_path: Path,
    checksum_path: Path,
    extracted_dir: Path,
) -> dict[str, Any]:
    reload_meta = _load_source(RELOAD, reload_run, reload_artifact, reload_zip)
    fill_meta = _load_source(FILL, fill_run, fill_artifact, fill_zip)
    _validate_reload(reload_meta[2])
    _validate_fill(fill_meta[2])

    extracted_dir.mkdir(parents=True, exist_ok=True)
    copies = {
        "weighting_model_reload_verification.json": reload_meta[2][
            "weighting_model_reload_verification.json"
        ][1],
        "corrected_weighting_model_states.json": reload_meta[2][
            "corrected_weighting_model_states.json"
        ][1],
        "weighting_model_fill_policy_verification.json": fill_meta[2][
            "weighting_model_fill_policy_verification.json"
        ][1],
        "reload_sources.json": reload_meta[2]["sources.json"][1],
        "fill_policy_sources.json": fill_meta[2]["sources.json"][1],
    }
    for name, content in copies.items():
        (extracted_dir / name).write_bytes(content)

    sources = {}
    for source, archive, (run, artifact, _) in (
        (RELOAD, reload_zip, reload_meta),
        (FILL, fill_zip, fill_meta),
    ):
        sources[source.key] = {
            "purpose": source.purpose,
            "workflow": WORKFLOW,
            "run_id": source.run_id,
            "run_attempt": 1,
            "head_sha": run["head_sha"],
            "artifact_id": artifact["id"],
            "artifact_name": source.artifact_name,
            "archive_filename": archive.name,
            "archive_sha256": _sha(archive),
            "archive_size_bytes": archive.stat().st_size,
            "bytes_modified": False,
        }
    evidence_sources = {
        "weighting_model_reload_verification.json": RELOAD.run_id,
        "corrected_weighting_model_states.json": RELOAD.run_id,
        "weighting_model_fill_policy_verification.json": FILL.run_id,
        "reload_sources.json": RELOAD.run_id,
        "fill_policy_sources.json": FILL.run_id,
    }
    principal = {
        name: {"source_run_id": evidence_sources[name], "sha256": _sha_bytes(content)}
        for name, content in copies.items()
    }
    manifest = {
        "schema_version": 2,
        "certification": "V4 Independent Weighting Model Certification",
        "status": "VERIFIED",
        "freeze_type": "immutable_composite_source_evidence_archive",
        "sources": sources,
        "principal_evidence": principal,
        "certification_guards": {
            "evidence_regenerated": False,
            "models_refit": False,
            "preprocessing_executed": False,
            "predictions_changed": False,
            "scores_or_decisions_changed": False,
            "corrected_states_changed": False,
            "final_holdout_accessed": False,
            "automatic_promotion_performed": False,
        },
        "release": {
            "tag": TAG,
            "tag_target_source_run_id": FILL.run_id,
            "tag_target_head_sha": fill_meta[0]["head_sha"],
            "title": TITLE,
        },
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    checksum_entries = [
        (reload_zip.name, _sha(reload_zip)),
        (fill_zip.name, _sha(fill_zip)),
    ]
    checksum_entries += [
        (name, _sha_bytes(content)) for name, content in copies.items()
    ]
    checksum_entries.append((manifest_path.name, _sha(manifest_path)))
    checksum_path.write_text(
        "".join(f"{digest}  {name}\n" for name, digest in checksum_entries)
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    for prefix in ("reload", "fill"):
        parser.add_argument(f"--{prefix}-run", required=True, type=Path)
        parser.add_argument(f"--{prefix}-artifact", required=True, type=Path)
        parser.add_argument(f"--{prefix}-zip", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--checksums", required=True, type=Path)
    parser.add_argument("--extracted-dir", required=True, type=Path)
    args = parser.parse_args()
    build_freeze(
        reload_run=args.reload_run,
        reload_artifact=args.reload_artifact,
        reload_zip=args.reload_zip,
        fill_run=args.fill_run,
        fill_artifact=args.fill_artifact,
        fill_zip=args.fill_zip,
        manifest_path=args.manifest,
        checksum_path=args.checksums,
        extracted_dir=args.extracted_dir,
    )


if __name__ == "__main__":
    main()
