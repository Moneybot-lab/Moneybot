#!/usr/bin/env python3
"""Validate and compose the immutable hosted-portfolio verification archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

TRACK_B_RUN_ID = 35002276286
TRACK_B_WORKFLOW = "Track B Offline Challenger"
TRACK_B_HEAD_SHA = "7da77897e10f228d2b60bedfbeb3c983836de5c6"
TRACK_B_ARTIFACT = "track-b-offline-output"
TRACK_B_ARCHIVE_SHA256 = "e7039b26a535e9399540b4a9e1033c85811091afc4a889efe77232c5287fc7f3"
VERIFY_RUN_ID = 35040195995
VERIFY_WORKFLOW = "V4 Verify Hosted Portfolio Evidence"
VERIFY_ARTIFACT = "v4-hosted-portfolio-verification-35040195995-1"
VERIFY_ARCHIVE_SHA256 = "0132182c58fb48082897f27e7c517a796fc51e0af330527662930050047e3abd"
EVIDENCE_HASHES = {
    "v4_hosted_portfolio_verification.json": "bdb57d0f692c7f32fd3a14b5de288a8c113a2294087a5cd35dcfb446ec80c693",
    "v4_hosted_portfolio_source_manifest.json": "c8fbe9b0760e4caf515d203bc05f7433879fe895c4fcc9de3fa91d8d5e682279",
    "source_artifact_SHA256SUMS.txt": "e4e2cf402c287ae9799a4fd5c14649986b0670f1a1d9962d284cd963a4f19ac6",
}
REVIEW_FILES = (
    "execution_policy.json", "execution_ledger.json", "daily_portfolio_equity.json",
    "valuation_evidence_manifest.json", "portfolio_metrics.json",
    "selected_portfolio_valuation_certification.json", "reconstructability_report.json",
    "temporal_safety_certification.json", "track_b_v4_research_summary.json",
    "backtest_report.json",
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("FREEZE_INPUT_NOT_OBJECT")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_run(run: dict[str, Any], *, run_id: int, workflow: str, success: bool) -> None:
    if run.get("id") != run_id or run.get("name") != workflow or run.get("run_attempt") != 1 or not run.get("head_sha"):
        raise ValueError("SOURCE_RUN_MISMATCH")
    if success and run.get("conclusion") != "success":
        raise ValueError("VERIFIER_SOURCE_NOT_SUCCESSFUL")
    if not success and run.get("head_sha") != TRACK_B_HEAD_SHA:
        raise ValueError("TRACK_B_SOURCE_RUN_MISMATCH")


def _validate_artifact(artifact: dict[str, Any], *, name: str, run_id: int) -> None:
    if artifact.get("name") != name or artifact.get("expired") is not False or (artifact.get("workflow_run") or {}).get("id") != run_id:
        raise ValueError("SOURCE_ARTIFACT_MISMATCH")


def _unique_member(archive: zipfile.ZipFile, name: str) -> zipfile.ZipInfo:
    matches = [item for item in archive.infolist() if not item.is_dir() and Path(item.filename).name == name]
    if len(matches) != 1:
        raise ValueError(f"REQUIRED_EVIDENCE_COUNT_MISMATCH:{name}")
    return matches[0]


def build_freeze(*, track_b_run: Path, track_b_artifact: Path, track_b_zip: Path,
                 verifier_run: Path, verifier_artifact: Path, verifier_zip: Path,
                 manifest_path: Path, checksum_path: Path, extracted_dir: Path) -> dict[str, Any]:
    track_run, verify_run = _load(track_b_run), _load(verifier_run)
    _validate_run(track_run, run_id=TRACK_B_RUN_ID, workflow=TRACK_B_WORKFLOW, success=False)
    if track_run.get("conclusion") != "failure":
        raise ValueError("TRACK_B_HISTORICAL_CONCLUSION_MISMATCH")
    _validate_run(verify_run, run_id=VERIFY_RUN_ID, workflow=VERIFY_WORKFLOW, success=True)
    _validate_artifact(_load(track_b_artifact), name=TRACK_B_ARTIFACT, run_id=TRACK_B_RUN_ID)
    _validate_artifact(_load(verifier_artifact), name=VERIFY_ARTIFACT, run_id=VERIFY_RUN_ID)
    if _sha(track_b_zip) != TRACK_B_ARCHIVE_SHA256:
        raise ValueError("TRACK_B_ARCHIVE_SHA256_MISMATCH")
    if _sha(verifier_zip) != VERIFY_ARCHIVE_SHA256:
        raise ValueError("VERIFIER_ARCHIVE_SHA256_MISMATCH")

    extracted_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(verifier_zip) as archive:
        for name, expected in EVIDENCE_HASHES.items():
            data = archive.read(_unique_member(archive, name))
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError(f"PRINCIPAL_EVIDENCE_SHA256_MISMATCH:{name}")
            (extracted_dir / name).write_bytes(data)
    verification = _load(extracted_dir / "v4_hosted_portfolio_verification.json")
    expected_guards = {
        "status": "VERIFIED", "failures": [], "evidence_only": True,
        "model_fitting_performed": False, "holdout_access_performed": False,
        "automatic_promotion": False, "automatic_promotion_performed": False,
        "ready_for_live_routing": False, "live_routing_performed": False,
        "economic_gates_preserved": True,
    }
    if any(verification.get(key) != value for key, value in expected_guards.items()):
        raise ValueError("VERIFICATION_RESULT_NOT_ACCEPTED")
    source = verification.get("hosted_source") or {}
    if source != {"environment": "github_actions", "workflow": TRACK_B_WORKFLOW, "run_id": TRACK_B_RUN_ID, "run_attempt": 1, "head_sha": TRACK_B_HEAD_SHA}:
        raise ValueError("VERIFIED_HOSTED_SOURCE_MISMATCH")
    phase0, scope = verification.get("phase0") or {}, verification.get("valuation_scope_verification") or {}
    equity, ledger = verification.get("equity_reconciliation") or {}, verification.get("ledger_summary") or {}
    required = (
        phase0.get("core_failure_count") == 0 and phase0.get("reconstructable_rows") == 34446
        and phase0.get("rows_checked") == 34446
        and scope.get("selected_portfolio_required_observations") == 25
        and scope.get("selected_portfolio_verified_observations") == 25
        and scope.get("selected_portfolio_incomplete_observations") == 0
        and scope.get("selected_portfolio_independent_adjustments_verified") is True
        and scope.get("diagnostic_failure_selected_portfolio_intersection") == 0
        and equity.get("mismatched_sessions") == 0 and equity.get("maximum_absolute_difference") == 0.0
        and ledger.get("duplicate_violations") == 0
    )
    if not required:
        raise ValueError("FROZEN_CERTIFICATION_EXPECTATION_MISMATCH")

    with zipfile.ZipFile(track_b_zip) as archive:
        for name in REVIEW_FILES:
            (extracted_dir / name).write_bytes(archive.read(_unique_member(archive, name)))
    manifest = {
        "schema_version": 1,
        "certification": "V4 Hosted Portfolio Verification",
        "status": "VERIFIED",
        "freeze_type": "immutable_composite_source_evidence_archive",
        "sources": {
            "hosted_track_b": {"workflow": TRACK_B_WORKFLOW, "run_id": TRACK_B_RUN_ID, "run_attempt": 1, "head_sha": TRACK_B_HEAD_SHA, "artifact_name": TRACK_B_ARTIFACT, "archive_sha256": TRACK_B_ARCHIVE_SHA256, "historical_workflow_conclusion": "failure", "conclusion_context": "initial hosted verifier scope defect"},
            "independent_verification": {"workflow": verify_run["name"], "run_id": VERIFY_RUN_ID, "run_attempt": 1, "head_sha": verify_run["head_sha"], "artifact_name": VERIFY_ARTIFACT, "archive_sha256": VERIFY_ARCHIVE_SHA256},
        },
        "principal_evidence": {name: {"sha256": digest} for name, digest in EVIDENCE_HASHES.items()},
        "release": {"tag": "v4-hosted-portfolio-verification-2026-09-15", "title": "V4 Hosted Portfolio Verification — Frozen Evidence", "tag_target_head_sha": verify_run["head_sha"]},
        "guards": {"evidence_regenerated": False, "model_fitting_performed": False, "holdout_access_performed": False, "automatic_promotion_performed": False, "live_routing_performed": False},
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    files = [track_b_zip, verifier_zip, manifest_path, *(extracted_dir / name for name in (*EVIDENCE_HASHES, *REVIEW_FILES))]
    checksum_path.write_text("".join(f"{_sha(path)}  {path.name}\n" for path in files), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("track-b-run", "track-b-artifact", "track-b-zip", "verifier-run", "verifier-artifact", "verifier-zip", "manifest", "checksums", "extracted-dir"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    build_freeze(track_b_run=args.track_b_run, track_b_artifact=args.track_b_artifact, track_b_zip=args.track_b_zip, verifier_run=args.verifier_run, verifier_artifact=args.verifier_artifact, verifier_zip=args.verifier_zip, manifest_path=args.manifest, checksum_path=args.checksums, extracted_dir=args.extracted_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
