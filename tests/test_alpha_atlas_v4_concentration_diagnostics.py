from __future__ import annotations

import copy
import hashlib
import json
import zipfile

import pytest

from moneybot.services.alpha_atlas_v4_concentration_diagnostics import (
    ConcentrationDiagnosticError,
    _selected_sensitivity,
    analyze_artifact,
    reporting_weights,
    weighted_metrics,
)


def _row(identifier, security, date, label, score, return_value=0.01):
    return {"id": identifier, "security": security, "session": date, "label": label,
            "score": score, "return": return_value, "abstained": False,
            "risk_rejected": False, "rule_rejected": False}


def test_reporting_weight_formulas_and_metrics_match_hand_calculation():
    records = [_row("a1", "A", "d1", 1, 0.8), _row("a2", "A", "d1", 0, 0.6),
               _row("b1", "B", "d2", 0, 0.2)]
    assert reporting_weights(records, "observation_weighted") == pytest.approx([1 / 3] * 3)
    assert reporting_weights(records, "symbol_date_balanced") == pytest.approx([0.25, 0.25, 0.5])
    metrics = weighted_metrics(records, "symbol_date_balanced", 0.5, probability_semantics=True)
    assert metrics["target_prevalence"] == pytest.approx(0.25)
    assert metrics["score_mean"] == pytest.approx(0.45)
    assert metrics["signal_rate_full_validation_denominator"] == pytest.approx(0.5)
    assert metrics["precision_selected_weight_renormalized"] == pytest.approx(0.5)
    assert metrics["calibration"]["brier_score"] == pytest.approx(
        0.25 * (0.8 - 1) ** 2 + 0.25 * 0.6**2 + 0.5 * 0.2**2
    )


def test_repeating_one_group_changes_observation_view_but_preserves_balanced_estimand():
    original = [_row("a", "A", "d1", 1, 0.8), _row("b", "B", "d2", 0, 0.2)]
    expanded = original + [_row(f"a{i}", "A", "d1", 1, 0.8) for i in range(3)]
    observation_original = weighted_metrics(original, "observation_weighted", 0.5, probability_semantics=True)
    observation_expanded = weighted_metrics(expanded, "observation_weighted", 0.5, probability_semantics=True)
    balanced_original = weighted_metrics(original, "symbol_date_balanced", 0.5, probability_semantics=True)
    balanced_expanded = weighted_metrics(expanded, "symbol_date_balanced", 0.5, probability_semantics=True)
    assert observation_original["target_prevalence"] != observation_expanded["target_prevalence"]
    assert balanced_original["target_prevalence"] == pytest.approx(balanced_expanded["target_prevalence"])
    assert balanced_original["score_mean"] == pytest.approx(balanced_expanded["score_mean"])


def test_mixed_labels_empty_single_class_and_invalid_scores_are_honest():
    mixed = [_row("a", "A", "d", 1, 0.8), _row("b", "A", "d", 0, 0.8)]
    result = weighted_metrics(mixed, "symbol_date_balanced", 0.9, probability_semantics=True)
    assert result["target_prevalence"] == pytest.approx(0.5)
    assert "empty_selection" in result["warnings"]
    single = weighted_metrics([_row("a", "A", "d", 1, 0.8)], "observation_weighted", 0.5, probability_semantics=True)
    assert "single_class_population" in single["warnings"]
    invalid = weighted_metrics([_row("x", "A", "d", 0, float("nan"))], "observation_weighted", 0.5, probability_semantics=True)
    assert invalid == {"available": False, "warnings": ["invalid_scores"]}


def test_concentration_omission_uses_counts_not_outcomes_and_does_not_mutate():
    records = [_row(f"a{i}", "A", "d1", i % 2, 0.8) for i in range(4)] + [
        _row(f"b{i}", "B", "d2", 1, 0.8) for i in range(3)
    ]
    capture = [{"model_version": "selected", "fold_index": 1, "decision_threshold": 0.6,
                "score_semantics": "probability", "records": records}]
    before = copy.deepcopy(capture)
    first = _selected_sensitivity(capture, "selected")
    assert capture == before
    for row in records:
        row["label"] = 1 - row["label"]
        row["return"] = -row["return"]
    second = _selected_sensitivity(capture, "selected")
    assert first[0]["variants"][0]["removed_groups"] == [["A", "d1"]]
    assert second[0]["variants"][0]["removed_groups"] == [["A", "d1"]]
    assert before[0]["records"][0]["score"] == capture[0]["records"][0]["score"]
    assert before[0]["decision_threshold"] == capture[0]["decision_threshold"]


def test_artifact_only_path_enforces_provenance_and_writes_reports(tmp_path):
    capture = [{"model_version": "selected", "fold_index": 1, "decision_threshold": 0.6,
                "score_semantics": "probability", "training_prevalence": 0.4,
                "records": [_row("dev", "A", "2026-06-30", 1, 0.8)]}]
    capture_bytes = (json.dumps(capture) + "\n").encode()
    capture_hash = hashlib.sha256(capture_bytes).hexdigest()
    artifact = tmp_path / "artifact.zip"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("evidence/development_walk_forward_predictions.json", capture_bytes)
        archive.writestr("evidence/workflow_provenance.json", json.dumps({"capture_sha256": capture_hash,
            "diagnostic_code_sha": "code", "source_track_b_run_id": "run"}))
        archive.writestr("reports/development_signal_coverage_report.json", json.dumps({
            "canonical_input_sha256": "input", "split_plan_sha256": "split",
            "final_holdout_overlap_count": 0, "scope": "development_oof_only",
            "candidate_roster": ["selected"], "diagnostic_id_count": 1,
            "prediction_lineage": [{"candidate": "selected", "fold_index": 1}]}))
    paths = analyze_artifact(artifact, tmp_path / "out", expected_capture_sha256=capture_hash,
        expected_input_sha256="input", expected_split_plan_sha256="split", selected_candidate="selected")
    assert set(paths) == {"development_concentration_report.json",
                          "development_score_stability_report.json",
                          "development_concentration_summary.md"}
    with pytest.raises(ConcentrationDiagnosticError, match="capture_sha256_mismatch"):
        analyze_artifact(artifact, tmp_path / "bad", expected_capture_sha256="0" * 64,
            expected_input_sha256="input", expected_split_plan_sha256="split")
