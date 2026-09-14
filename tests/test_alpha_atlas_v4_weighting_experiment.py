from __future__ import annotations
import copy
import json
from pathlib import Path

import pytest

from moneybot.services.alpha_atlas_v4_temporal_split import file_sha256, plan_v4_temporal_split
from moneybot.services.alpha_atlas_v4_weighting_experiment import (
    WeightingExperimentError, build_registration, execute, validate_registration,
)
from scripts.train_challenger_suite import train_challenger_suite
from tests.test_alpha_atlas_v4_holdout_isolation import _fixture_rows, _write_jsonl


def _artifacts(tmp_path):
    rows = _fixture_rows()
    input_path = tmp_path / "input.jsonl"
    _write_jsonl(input_path, rows)
    plan = plan_v4_temporal_split(rows, input_sha256=file_sha256(input_path), min_observations=40).plan
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    observer = []
    manifest = train_challenger_suite(input_path, tmp_path / "suite", min_rows=40,
        split_plan_path=plan_path, walk_forward_observer=observer.append)
    manifest_path = tmp_path / "suite/challenger_suite_manifest.json"
    capture = [item for item in observer if item["model_version"] == "challenger-big-loss-avoider-v1"]
    capture_path = tmp_path / "capture.json"
    capture_path.write_text(json.dumps(capture))
    registration = build_registration(input_path, plan_path, manifest_path, capture_path,
        source_run="fixture", expected_input_hash=file_sha256(input_path), expected_plan_hash=plan["plan_sha256"],
        expected_manifest_hash=file_sha256(manifest_path), expected_capture_hash=file_sha256(capture_path),
        original_capture_code_sha="fixture-capture")
    return input_path, plan_path, manifest_path, capture_path, registration, manifest


def test_registered_two_arm_execution_reproduces_baseline_and_preserves_artifacts(tmp_path):
    input_path, plan_path, manifest_path, capture_path, registration, manifest = _artifacts(tmp_path)
    artifacts_before = {item["model_version"]: Path(item["model_path"]).read_bytes() for item in manifest["challengers"]}
    outputs = execute(registration, input_path, plan_path, manifest_path, capture_path, tmp_path / "out", code_sha="fixture")
    comparison = json.loads(outputs["weighting_experiment_paired_comparison.json"].read_text())
    weights = json.loads(outputs["weighting_experiment_effective_weights.json"].read_text())["folds"]
    assert len(comparison["folds"]) == 3
    assert comparison["promotion_or_threshold_change_allowed"] is False
    for fold in {item["fold_index"] for item in weights}:
        current = next(item for item in weights if item["fold_index"] == fold and item["arm"] == "current_weights")
        uniform = next(item for item in weights if item["fold_index"] == fold and item["arm"] == "uniform_weights")
        assert uniform["raw_min"] == uniform["raw_max"]
        assert uniform["raw_sum"] == pytest.approx(current["raw_sum"])
    assert artifacts_before == {item["model_version"]: Path(item["model_path"]).read_bytes() for item in manifest["challengers"]}


def test_registration_hash_and_missing_fold_fail_closed(tmp_path):
    input_path, plan_path, manifest_path, capture_path, registration, _ = _artifacts(tmp_path)
    corrupted = copy.deepcopy(registration)
    corrupted["candidate_spec"]["threshold"] = 0.1
    with pytest.raises(WeightingExperimentError, match="registration_hash"):
        validate_registration(corrupted)
    incomplete = json.loads(capture_path.read_text())[:-1]
    capture_path.write_text(json.dumps(incomplete))
    with pytest.raises(WeightingExperimentError, match="artifact_hash"):
        execute(registration, input_path, plan_path, manifest_path, capture_path, tmp_path / "bad", code_sha="fixture")
