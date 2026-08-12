import pandas as pd

from moneybot.services.deterministic_model import BaselineModelArtifact, save_artifact
from scripts.day11_compare_candidate_vs_production import (
    _corporate_action_lineage_report,
    _feature_leakage_name_value_audit,
    _training_source_phase1_passed,
)


def _canonical_source(**overrides):
    source = {
        "uses_massive_canonical_input": True,
        "manifest_loaded": True,
        "leakage_safe": True,
        "schema_version": "massive-decision-training-rows.v2",
        "join_policy": "last_market_row_on_or_before_decision_date; labels strictly after that row",
        "leakage_guard_values": [
            "features_asof_market_close_on_or_before_decision_date_labels_after_decision_date"
        ],
        "corporate_action_normalization_required": True,
        "corporate_action_normalization_passed": True,
        "split_metadata_available": True,
        "split_metadata_hash": "fixture-split-hash",
        "price_adjustment_policy": "event_time_split_adjusted",
        "volume_adjustment_policy": "inverse_split_factor",
        "feature_split_boundary_errors": 0,
        "label_split_boundary_errors": 0,
    }
    source.update(overrides)
    return source


def test_canonical_v2_requires_complete_corporate_action_evidence():
    assert _training_source_phase1_passed(_canonical_source())
    invalid_overrides = (
        {"corporate_action_normalization_passed": False},
        {"split_metadata_hash": ""},
        {"feature_split_boundary_errors": 1},
        {"label_split_boundary_errors": 1},
        {"schema_version": "massive-decision-training-rows.v1"},
    )
    assert all(
        not _training_source_phase1_passed(_canonical_source(**override))
        for override in invalid_overrides
    )


def _artifact(path, features, split_hash="same-hash"):
    artifact = BaselineModelArtifact(
        version=path.stem,
        feature_columns=features,
        means=[0.0] * len(features),
        stds=[1.0] * len(features),
        weights=[1.0] * len(features),
        bias=0.0,
        decision_threshold=0.55,
    )
    artifact.lineage = {
        "dataset_lineage": {
            "canonical_dataset_schema_version": "massive-decision-training-rows.v2",
            "split_metadata_hash": split_hash,
            "price_adjustment_policy": "event_time_split_adjusted",
            "volume_adjustment_policy": "inverse_split_factor",
        }
    }
    save_artifact(artifact, path)


def test_lineage_matching_binds_artifacts_and_holdout_to_same_split_hash(tmp_path):
    candidate = tmp_path / "candidate.json"
    comparator = tmp_path / "comparator.json"
    _artifact(candidate, ["feature_spy_return_5d"])
    _artifact(comparator, ["feature_spy_return_5d"])
    frame = pd.DataFrame(
        {
            "canonical_dataset_schema_version": ["massive-decision-training-rows.v2"],
            "split_metadata_hash": ["same-hash"],
            "price_adjustment_policy": ["event_time_split_adjusted"],
            "volume_adjustment_policy": ["inverse_split_factor"],
        }
    )
    assert _corporate_action_lineage_report(str(candidate), str(comparator), frame)[
        "passed"
    ]
    frame["split_metadata_hash"] = "different-hash"
    report = _corporate_action_lineage_report(str(candidate), str(comparator), frame)
    assert not report["passed"]
    assert "split_metadata_hash" in "; ".join(report["mismatches"])


def test_asof_return_names_need_manifest_proof_and_future_names_stay_blocked(
    tmp_path,
):
    candidate = tmp_path / "candidate.json"
    comparator = tmp_path / "comparator.json"
    safe_features = [
        "feature_spy_return_1d",
        "feature_spy_return_5d",
        "feature_sector_relative_return_5d",
        "feature_symbol_minus_spy_5d",
    ]
    _artifact(candidate, safe_features)
    _artifact(comparator, ["feature_signal"])
    without_proof = _feature_leakage_name_value_audit(
        str(candidate),
        str(comparator),
        {"passed": True, "violations": []},
        {"comparison_valid": True},
    )
    with_proof = _feature_leakage_name_value_audit(
        str(candidate),
        str(comparator),
        {"passed": True, "violations": []},
        {"comparison_valid": True},
        _canonical_source(),
    )
    assert not without_proof["future_feature_name_audit_passed"]
    assert with_proof["future_feature_leakage_passed"]

    _artifact(candidate, ["future_return_5d", "feature_return_5d"])
    blocked = _feature_leakage_name_value_audit(
        str(candidate),
        str(comparator),
        {"passed": True, "violations": []},
        {"comparison_valid": True},
        _canonical_source(),
    )
    assert set(blocked["candidate_name_violations"]) == {
        "feature_return_5d",
        "future_return_5d",
    }


def test_value_leakage_audit_remains_a_hard_failure(tmp_path):
    candidate = tmp_path / "candidate.json"
    comparator = tmp_path / "comparator.json"
    _artifact(candidate, ["feature_spy_return_5d"])
    _artifact(comparator, ["feature_signal"])
    report = _feature_leakage_name_value_audit(
        str(candidate),
        str(comparator),
        {"passed": False, "violations": [{"reason": "feature_matches_outcome_values"}]},
        {"comparison_valid": True},
        _canonical_source(),
    )
    assert not report["future_feature_value_audit_passed"]
    assert not report["future_feature_leakage_passed"]
