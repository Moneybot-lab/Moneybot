#!/usr/bin/env python3
"""Resolve and validate the exact reviewed evidence for V4 weight execution."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from moneybot.services.alpha_atlas_v4_weighting_experiment import (  # noqa: E402
    ARMS,
    EXPERIMENT_ID,
    SELECTED,
    validate_registration,
)
from scripts.validate_v4_weighting_registration_sources import (  # noqa: E402
    _one,
    validate_sources,
)

EXPECTED_REGISTRATION = "4112f445d8ef079550f02185e4592c05aff9c0321ee4bfa615b632360af4c5c3"
EXPECTED_REGISTRATION_CODE = "4fcbf11a523dd440d3eaf01e2a6c5be6500629bf"
EXPECTED_REGISTRATION_RUN = "34796621288-1"
EXPECTED_MANIFEST = "ea4e55f9faa848219945d7e03c92c7a541645cd4d6df8aa3cfbd0d1334872f15"


def validate_execution_sources(
    track_root: Path, diagnostics_root: Path, registration_root: Path
) -> dict[str, object]:
    sources = validate_sources(track_root, diagnostics_root)
    if sources["manifest_sha256"] != EXPECTED_MANIFEST:
        raise ValueError("reviewed_challenger_manifest_hash_mismatch")
    registration_path = _one(
        registration_root, "weighting_experiment_registration.json"
    )
    workflow_path = _one(registration_root, "workflow_execution_provenance.json")
    registration = json.loads(registration_path.read_text(encoding="utf-8"))
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    validate_registration(registration)
    if registration.get("registration_sha256") != EXPECTED_REGISTRATION:
        raise ValueError("reviewed_canonical_registration_hash_mismatch")
    if (
        workflow.get("status") != "REGISTERED"
        or workflow.get("workflow_run") != EXPECTED_REGISTRATION_RUN
        or workflow.get("registration_code_sha") != EXPECTED_REGISTRATION_CODE
        or workflow.get("registration_sha256") != EXPECTED_REGISTRATION
        or workflow.get("experiment_executed") is not False
    ):
        raise ValueError("registration_workflow_provenance_mismatch")
    expected_registration = {
        "experiment_id": EXPERIMENT_ID,
        "selected_candidate": SELECTED,
        "source_track_b_run": "34689216730",
        "input_sha256": sources["input_sha256"],
        "split_plan_sha256": sources["split_plan_sha256"],
        "manifest_sha256": EXPECTED_MANIFEST,
        "capture_sha256": sources["capture_sha256"],
        "original_capture_code_sha": sources["diagnostic_code_sha"],
        "automatic_promotion": False,
        "ready_for_live_routing": False,
    }
    for key, value in expected_registration.items():
        if registration.get(key) != value:
            raise ValueError(f"registration_source_binding_mismatch:{key}")
    if set(registration.get("arms") or {}) != set(ARMS):
        raise ValueError("registration_arm_mismatch")
    if len(registration.get("folds") or []) != 3:
        raise ValueError("registration_fold_count_mismatch")
    if float((registration.get("candidate_spec") or {}).get("threshold", -1)) != 0.60:
        raise ValueError("registration_threshold_mismatch")
    return {
        **sources,
        "registration": str(registration_path),
        "registration_workflow_provenance": str(workflow_path),
        "registration_sha256": EXPECTED_REGISTRATION,
        "registration_code_sha": EXPECTED_REGISTRATION_CODE,
        "registration_workflow_run": EXPECTED_REGISTRATION_RUN,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--track-root", required=True, type=Path)
    parser.add_argument("--diagnostics-root", required=True, type=Path)
    parser.add_argument("--registration-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = validate_execution_sources(
        args.track_root, args.diagnostics_root, args.registration_root
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
