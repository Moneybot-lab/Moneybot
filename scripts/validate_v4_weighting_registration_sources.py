#!/usr/bin/env python3
"""Fail-closed resolver for weighting-registration workflow source artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from moneybot.services.alpha_atlas_v4_temporal_split import validate_split_plan  # noqa: E402

EXPECTED_INPUT = "506073994052be852a5a00fc38e239b85e72a50494c3559640ea980fb6a52d9e"
EXPECTED_PLAN = "bd60055adc6cb1b3f8b143c6c61031bced01c9151fcb3b67c33f75430106e8e8"
EXPECTED_CAPTURE = "454febde2e14ca8a916222d86a6872db1c5e790a29429f2db6393d828e87e434"
EXPECTED_CAPTURE_CODE = "5d360cdbda802ae8527b35fe59f75920b8c827c8"
EXPECTED_DIAGNOSTIC_RUN = "34769717178-1"
EXPECTED_TRACK_B_RUN = "34689216730"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _one(root: Path, pattern: str) -> Path:
    matches = sorted(path for path in root.rglob(pattern) if path.is_file())
    if len(matches) != 1:
        raise ValueError(f"expected_exactly_one:{pattern}:found={len(matches)}")
    resolved = matches[0].resolve()
    if root.resolve() not in resolved.parents:
        raise ValueError(f"unsafe_resolved_path:{pattern}")
    return resolved


def validate_sources(track_root: Path, diagnostics_root: Path) -> dict[str, object]:
    feature_store = _one(track_root, "flat_feature_store/all.jsonl")
    plan = _one(track_root, "challenger_split_plan.json")
    manifest = _one(track_root, "challenger_suite_manifest.json")
    certification = _one(track_root, "temporal_safety_certification.json")
    capture = _one(diagnostics_root, "development_walk_forward_predictions.json")
    workflow_provenance = _one(diagnostics_root, "workflow_provenance.json")
    capture_provenance = _one(diagnostics_root, "development_oof_capture_provenance.json")
    if _sha(feature_store) != EXPECTED_INPUT or _sha(capture) != EXPECTED_CAPTURE:
        raise ValueError("reviewed_input_or_capture_byte_hash_mismatch")
    plan_payload = json.loads(plan.read_text())
    validate_split_plan(plan_payload, input_path=feature_store)
    if plan_payload.get("plan_sha256") != EXPECTED_PLAN:
        raise ValueError("reviewed_canonical_split_plan_hash_mismatch")
    manifest_hash = _sha(manifest)
    manifest_payload = json.loads(manifest.read_text())
    policy = manifest_payload.get("temporal_validation_policy") or {}
    if policy.get("split_plan_sha256") != EXPECTED_PLAN or policy.get("split_input_sha256") != EXPECTED_INPUT:
        raise ValueError("manifest_frozen_lineage_mismatch")
    certification_payload = json.loads(certification.read_text())
    if certification_payload.get("status") != "VERIFIED_FOR_THIS_ARTIFACT":
        raise ValueError("source_certification_not_verified")
    workflow = json.loads(workflow_provenance.read_text())
    capture_info = json.loads(capture_provenance.read_text())
    if (workflow.get("diagnostic_workflow_run") != EXPECTED_DIAGNOSTIC_RUN
            or str(workflow.get("source_track_b_run_id")) != EXPECTED_TRACK_B_RUN
            or workflow.get("diagnostic_code_sha") != EXPECTED_CAPTURE_CODE
            or workflow.get("capture_sha256") != EXPECTED_CAPTURE):
        raise ValueError("diagnostic_workflow_provenance_mismatch")
    if (capture_info.get("manifest_sha256") != manifest_hash
            or capture_info.get("canonical_input_sha256") != EXPECTED_INPUT
            or capture_info.get("split_plan_sha256") != EXPECTED_PLAN
            or capture_info.get("final_holdout_overlap_count") != 0
            or capture_info.get("final_holdout_evaluated") is not False):
        raise ValueError("capture_provenance_lineage_mismatch")
    return {"feature_store": str(feature_store), "split_plan": str(plan),
            "manifest": str(manifest), "capture": str(capture),
            "certification": str(certification), "workflow_provenance": str(workflow_provenance),
            "capture_provenance": str(capture_provenance), "input_sha256": EXPECTED_INPUT,
            "split_plan_sha256": EXPECTED_PLAN, "capture_sha256": EXPECTED_CAPTURE,
            "manifest_sha256": manifest_hash, "diagnostic_code_sha": EXPECTED_CAPTURE_CODE,
            "diagnostic_workflow_run": EXPECTED_DIAGNOSTIC_RUN, "source_track_b_run": EXPECTED_TRACK_B_RUN}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--track-root", required=True, type=Path)
    parser.add_argument("--diagnostics-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = validate_sources(args.track_root, args.diagnostics_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
