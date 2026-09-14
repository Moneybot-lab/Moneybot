from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from moneybot.services import alpha_atlas_v4_development_diagnostics as diagnostics
from moneybot.services.alpha_atlas_v4_temporal_split import file_sha256, plan_v4_temporal_split
from scripts.train_challenger_suite import (
    capture_v4_development_walk_forward_predictions,
    train_challenger_suite,
)
from tests.test_alpha_atlas_v4_holdout_isolation import _fixture_rows, _write_jsonl


def test_calibration_fixture_matches_independent_calculation():
    probabilities = [0.1, 0.2, 0.8, 0.9]
    labels = [0, 1, 1, 1]
    result = diagnostics.calibration_metrics(probabilities, labels)
    assert result["brier_score"] == pytest.approx(sum((p - y) ** 2 for p, y in zip(probabilities, labels)) / 4)
    clipped = [min(1 - 1e-15, max(1e-15, p)) for p in probabilities]
    expected = -sum(y * math.log(p) + (1 - y) * math.log(1 - p) for p, y in zip(clipped, labels)) / 4
    assert result["log_loss"] == pytest.approx(expected)
    assert sum(item["count"] for item in result["reliability_bins"]) == 4
    assert result["ece_definition"] == "10 fixed equal-width bins [0,.1),...,[.9,1]"


def test_invalid_empty_and_single_class_are_reported():
    assert diagnostics.calibration_metrics([], [])["warnings"] == ["empty_validation"]
    invalid = diagnostics.calibration_metrics([float("nan")], [1])
    assert invalid["available"] is False
    assert "invalid_probability_values" in invalid["warnings"]
    single = diagnostics.calibration_metrics([0.4, 0.6], [1, 1])
    assert "single_class_validation" in single["warnings"]
    assert any(warning.startswith("sparse_bin_") for warning in single["warnings"])


def test_fixed_bounded_threshold_grid_and_empty_selection():
    assert diagnostics.THRESHOLD_GRID == (0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8)
    assert diagnostics._threshold_result([], 0.5)["warnings"] == ["empty_selection"]


def test_generation_enforces_development_membership_provenance_and_reconciliation(tmp_path, monkeypatch):
    canonical = tmp_path / "canonical.jsonl"
    canonical.write_text("certified-input\n")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps({"plan_sha256": "frozen-plan"}))
    fold = {"fold_index": 1, "usable": True, "train_ids": ["train"], "validation_ids": ["dev"],
            "latest_train_label_completion_at": "2026-01-01T00:00:00+00:00",
            "earliest_validation_decision_at": "2026-01-02T00:00:00+00:00", "embargo_sessions": 1}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"challengers": [{"model_version": "c1", "candidate_lane": "decision",
        "spec": {"sample_weight_policy": "balanced"}}], "walk_forward_windows": [fold]}))
    base = {"model_version": "c1", "fold_index": 1, "train_ids": ["train"], "validation_ids": ["dev"],
            "decision_threshold": 0.6, "score_semantics": "probability", "training_prevalence": 0.25,
            "target_definition": {"name": "label_up_5d"}, "calibration_audit": {"validation_labels_used_to_fit_calibrator": False},
            "records": [{"id": "dev", "security": "AAPL", "session": "2026-01-02", "label": 1,
                         "score": 0.7, "abstained": False, "risk_rejected": False, "rule_rejected": False}]}
    predictions = tmp_path / "oof.json"
    predictions.write_text(json.dumps([base]))
    monkeypatch.setattr(diagnostics, "validate_split_plan", lambda plan, input_path: ({"train", "dev"}, {"holdout"}))
    outputs = diagnostics.generate_development_diagnostics(canonical_input=canonical, split_plan_path=plan_path,
        manifest_path=manifest_path, predictions_path=predictions, output_dir=tmp_path / "out", baseline_sha="abc")
    coverage = json.loads(outputs["development_signal_coverage_report.json"].read_text())
    item = coverage["coverage"][0]
    assert coverage["final_holdout_overlap_count"] == 0
    assert item["funnel_reconciles"] is True
    assert item["training_prevalence_baseline"] == 0.25
    assert "weighted_classifier_score_not_established_as_unweighted_event_probability" in item["warnings"]
    contaminated = json.loads(predictions.read_text())
    contaminated[0]["validation_ids"] = ["holdout"]
    contaminated[0]["records"][0]["id"] = "holdout"
    predictions.write_text(json.dumps(contaminated))
    with pytest.raises(diagnostics.DevelopmentDiagnosticError):
        diagnostics.generate_development_diagnostics(canonical_input=canonical, split_plan_path=plan_path,
            manifest_path=manifest_path, predictions_path=predictions, output_dir=tmp_path / "bad", baseline_sha="abc")


def test_real_capture_runner_covers_frozen_candidates_and_folds_without_holdout(tmp_path):
    rows = _fixture_rows()
    input_path = tmp_path / "canonical.jsonl"
    _write_jsonl(input_path, rows)
    split = plan_v4_temporal_split(
        rows, input_sha256=file_sha256(input_path), min_observations=40
    ).plan
    split_path = tmp_path / "split.json"
    split_path.write_text(json.dumps(split))
    original_observer: list[dict] = []
    manifest = train_challenger_suite(
        input_path,
        tmp_path / "suite",
        min_rows=40,
        split_plan_path=split_path,
        walk_forward_observer=original_observer.append,
    )
    manifest_path = tmp_path / "suite/challenger_suite_manifest.json"
    learned_artifacts_before = {
        item["model_version"]: json.loads(item_path.read_text())
        for item in manifest["challengers"]
        if (item_path := Path(item["model_path"]))
    }
    capture, provenance = capture_v4_development_walk_forward_predictions(
        input_path, split_path, manifest_path
    )
    expected_pairs = {
        (item["model_version"], int(fold["fold_index"]))
        for item in manifest["challengers"]
        for fold in manifest["walk_forward_windows"]
        if fold["usable"]
    }
    assert {(item["model_version"], item["fold_index"]) for item in capture} == expected_pairs
    holdout_ids = set(split["test_canonical_observation_ids"])
    assert not holdout_ids & {
        record["id"] for item in capture for record in item["records"]
    }
    assert provenance["candidate_fold_capture_count"] == len(expected_pairs)
    assert provenance["final_holdout_overlap_count"] == 0
    assert provenance["final_holdout_evaluated"] is False
    assert capture == original_observer
    assert learned_artifacts_before == {
        item["model_version"]: json.loads(Path(item["model_path"]).read_text())
        for item in manifest["challengers"]
    }

    predictions_path = tmp_path / "development_walk_forward_predictions.json"
    predictions_path.write_text(json.dumps(capture))
    outputs = diagnostics.generate_development_diagnostics(
        canonical_input=input_path,
        split_plan_path=split_path,
        manifest_path=manifest_path,
        predictions_path=predictions_path,
        output_dir=tmp_path / "reports",
        baseline_sha="fixture",
    )
    assert set(outputs) == {
        "development_signal_coverage_report.json",
        "development_calibration_report.json",
        "development_threshold_sensitivity_report.json",
        "development_diagnostics_summary.md",
    }
