import pandas as pd

from moneybot.services.deterministic_model import BaselineModelArtifact, save_artifact
from scripts.day11_compare_candidate_vs_production import (
    _comparison_scope_report,
    _candidate_feature_coverage_segmented_report,
    _feature_leakage_name_value_audit,
    _feature_risk_audit,
    _production_comparison_status,
    _prediction_error_examples,
    _paired_date_bootstrap_utility_delta,
    _promotion_decision,
    _select_threshold_from_search,
    _threshold_guardrails_pass,
    _threshold_optimizer_report,
    _threshold_stability_summary,
)


def _artifact(path, *, threshold):
    save_artifact(
        BaselineModelArtifact(
            version=path.stem,
            feature_columns=["feature_signal"],
            means=[0.0],
            stds=[1.0],
            weights=[10.0],
            bias=0.0,
            decision_threshold=threshold,
        ),
        path,
    )


def test_prediction_error_examples_include_threshold_overlap_and_symbol_date_rows(tmp_path):
    candidate_path = tmp_path / "candidate.json"
    production_path = tmp_path / "production.json"
    _artifact(candidate_path, threshold=0.55)
    _artifact(production_path, threshold=0.95)
    frame = pd.DataFrame(
        [
            {"symbol": "CMI", "event_date": "2026-07-10", "feature_signal": 0.1, "return_5d": -0.05, "return_bin_5d": "big_loss"},
            {"symbol": "GAIN", "event_date": "2026-07-21", "feature_signal": -1.0, "return_5d": 0.05, "return_bin_5d": "big_gain"},
        ]
    )

    examples = _prediction_error_examples(str(candidate_path), str(production_path), frame)

    assert examples["chosen_threshold"] == 0.55
    assert examples["prediction_overlap"]["rows"] == 2
    assert examples["prediction_overlap"]["shared_positive_predictions"] == 0
    assert examples["big_loss_false_positive_count"] == 1
    assert examples["big_loss_false_positives"][0]["symbol"] == "CMI"
    assert examples["big_loss_false_positives"][0]["event_date"] == "2026-07-10"
    assert examples["cmi_false_positive_diagnostic"]["symbol"] == "CMI"
    assert examples["cmi_false_positive_diagnostic"]["top_candidate_positive_features"]
    stored = examples["cmi_false_positive_diagnostic"]["stored_regression_example"]
    assert stored["regression_example_id"] == "track-b-cmi-2026-07-10-big-loss-false-positive"
    assert stored["observed_comparison"]["candidate_probability"] == 0.557726
    assert stored["expected_guardrails"]["must_trigger_big_loss_false_positive_penalty"] is True
    assert stored["path"] == "regression_examples/track_b_cmi_2026-07-10.json"
    assert examples["missed_big_gain_count"] == 1
    assert examples["missed_big_gain_rows"][0]["symbol"] == "GAIN"
    assert examples["missed_big_gain_rows"][0]["event_date"] == "2026-07-21"


def test_comparison_scope_report_marks_narrow_or_incompatible_comparison_invalid():
    frame = pd.DataFrame({
        "event_date": ["2026-07-10"] * 517,
        "symbol": ["CMI"] * 517,
        "endpoint": ["user_watchlist"] * 517,
    })
    candidate_availability = {"features": {"feature_signal": {"availability_rate": 1.0}}}
    production_availability = {"features": {"legacy_feature": {"availability_rate": 0.0}}}

    report = _comparison_scope_report("data/track_b/decision_training_snapshot_massive.jsonl", frame, candidate_availability, production_availability)

    assert report["scope"] == "narrow_diagnostic"
    assert report["promotion_eligible_evidence"] is False
    assert report["production_feature_coverage_passed"] is False
    assert report["candidate_feature_coverage_passed"] is True
    assert report["feature_schema_compatible"] is False
    assert report["comparison_valid"] is False
    assert "narrow" in report["comparison_invalid_reason"]
    assert report["allowed_uses"] == ["diagnostic_only"]
    assert report["production_scoring_mode"] == "fill_or_default_values"
    assert report["narrow_comparison_can_override_full_backtest"] is False


def test_invalid_default_filled_production_status_is_not_promotion_eligible():
    comparison = {"comparison_valid": False, "production_feature_mode": "fill_or_default_values"}
    metrics = {"positive_predictions": 0, "utility_score": None}

    status = _production_comparison_status(comparison, metrics, pd.Series([0.3458, 0.3458]).to_numpy())

    assert status["production_comparison_status"] == "invalid_default_filled"
    assert status["production_positive_predictions"] == 0
    assert status["production_probability_constant"] is True
    assert status["production_metric_validity"]["brier"] == "not_promotion_eligible"


def test_segmented_feature_coverage_does_not_fail_market_features_for_sparse_echo():
    report = _candidate_feature_coverage_segmented_report({
        "features": {
            "feature_spy_return_5d_lagged": {"availability_rate": 0.99},
            "feature_relative_volume": {"availability_rate": 0.98},
            "feature_probability_up_delta_from_last_signal": {"availability_rate": 0.10},
        }
    })

    assert report["candidate_market_feature_coverage_passed"] is True
    assert report["candidate_model_echo_feature_coverage_passed"] is False
    assert report["low_coverage_features"] == ["feature_probability_up_delta_from_last_signal"]


def test_feature_name_value_audit_allows_lagged_returns_but_reports_production_future_like_schema(tmp_path):
    candidate_path = tmp_path / "candidate.json"
    production_path = tmp_path / "production.json"
    save_artifact(
        BaselineModelArtifact(version="candidate", feature_columns=["feature_return_5d_lagged"], means=[0.0], stds=[1.0], weights=[1.0], bias=0.0, decision_threshold=0.55),
        candidate_path,
    )
    save_artifact(
        BaselineModelArtifact(version="production", feature_columns=["feature_return_5d"], means=[0.0], stds=[1.0], weights=[1.0], bias=0.0, decision_threshold=0.55),
        production_path,
    )

    report = _feature_leakage_name_value_audit(str(candidate_path), str(production_path), {"passed": True, "violations": []}, {"comparison_valid": False})

    assert report["candidate_future_feature_leakage_passed"] is True
    assert report["production_legacy_schema_contains_future_like_feature_name"] is True
    assert report["production_schema_comparison_invalid"] is True


def test_feature_name_value_audit_allows_asof_market_returns_only_with_manifest_proof(tmp_path):
    candidate_path = tmp_path / "candidate.json"
    production_path = tmp_path / "production.json"
    save_artifact(
        BaselineModelArtifact(
            version="candidate",
            feature_columns=["feature_sector_relative_return_5d", "feature_spy_return_5d"],
            means=[0.0, 0.0],
            stds=[1.0, 1.0],
            weights=[1.0, 1.0],
            bias=0.0,
            decision_threshold=0.55,
        ),
        candidate_path,
    )
    training_source = {
        "uses_massive_canonical_input": True,
        "manifest_loaded": True,
        "leakage_safe": True,
        "schema_version": "massive-decision-training-rows.v2",
        "join_policy": "features on or before decision; labels strictly after decision",
        "leakage_guard_values": ["features_asof_market_close_labels_after_decision"],
        "corporate_action_normalization_required": True,
        "corporate_action_normalization_passed": True,
        "split_metadata_available": True,
        "split_metadata_hash": "fixture-hash",
        "price_adjustment_policy": "event_time_split_adjusted",
        "volume_adjustment_policy": "inverse_split_factor",
        "feature_split_boundary_errors": 0,
        "label_split_boundary_errors": 0,
    }

    without_proof = _feature_leakage_name_value_audit(
        str(candidate_path), str(production_path), {"passed": True, "violations": []}, {"comparison_valid": False}
    )
    with_proof = _feature_leakage_name_value_audit(
        str(candidate_path), str(production_path), {"passed": True, "violations": []}, {"comparison_valid": False}, training_source
    )

    assert without_proof["future_feature_name_audit_passed"] is False
    assert with_proof["future_feature_name_audit_passed"] is True
    assert with_proof["asof_feature_timing_proven_by_manifest"] is True
    assert with_proof["manifest_proven_asof_return_features"] == [
        "feature_sector_relative_return_5d",
        "feature_spy_return_1d",
        "feature_spy_return_5d",
        "feature_symbol_minus_spy_5d",
    ]


def test_promotion_decision_labels_promote_hold_watch_and_no_op_clone():
    assert _promotion_decision(True, False, True, True, True) == "PROMOTE"
    assert _promotion_decision(False, True, True, True, True) == "NO_OP_CLONE"
    assert _promotion_decision(False, False, True, True, False) == "WATCH"
    assert _promotion_decision(False, False, False, True, True) == "HOLD"


def test_threshold_optimizer_selects_best_guardrailed_utility_threshold():
    metrics = {
        "threshold_search": [
            {"threshold": 0.55, "utility_score": 0.20, "positive_predictions": 100, "big_loss_predictions": 0, "big_loss_prediction_rate": 0.0, "big_gain_capture_rate": 0.5},
            {"threshold": 0.70, "utility_score": 0.28, "positive_predictions": 50, "big_loss_predictions": 0, "big_loss_prediction_rate": 0.0, "big_gain_capture_rate": 0.3},
        ]
    }

    selected = _select_threshold_from_search(metrics, 0.55)

    assert selected["recommended_threshold"] == 0.70
    assert selected["reason"] == "flat-optimum threshold passed guardrails"
    assert selected["flat_optimum"]["selected_plateau_thresholds"] == [0.7]


def test_threshold_optimizer_keeps_current_threshold_when_support_is_too_thin(tmp_path):
    artifact_path = tmp_path / "candidate.json"
    _artifact(artifact_path, threshold=0.55)
    metrics = {
        "positive_predictions": 1,
        "big_gain_predictions": 1,
        "big_gain_capture_rate": 1.0,
        "big_loss_predictions": 0,
        "utility_score": 0.20,
        "selected_trade_unique_symbols": 1,
        "selected_trade_unique_dates": 1,
        "selected_trade_unique_symbol_dates": 1,
        "threshold_search": [
            {"threshold": 0.55, "utility_score": 0.20, "positive_predictions": 1, "big_loss_predictions": 0, "big_loss_prediction_rate": 0.0, "big_gain_capture_rate": 1.0},
            {"threshold": 0.70, "utility_score": None, "positive_predictions": 0, "big_loss_predictions": 0, "big_loss_prediction_rate": 0.0, "big_gain_capture_rate": 0.0},
        ]
    }
    frame = pd.DataFrame({
        "event_date": ["2026-07-10"] * 201,
        "symbol": ["CMI"] * 201,
        "feature_signal": [1.0] + [-1.0] * 200,
        "return_5d": [0.04] + [-0.01] * 200,
        "return_bin_5d": ["big_gain"] + ["loss"] * 200,
    })

    report = _threshold_optimizer_report(str(artifact_path), metrics, frame, min_rows=200)

    assert report["recommended_threshold"] == 0.55
    assert report["threshold_change_recommended"] is False
    assert report["threshold_selection_sufficient"] is False
    assert report["threshold_selection_support"]["rows"] == 201
    assert report["threshold_selection_support"]["maximum_positive_predictions"] == 1
    assert report["threshold_selection_support"]["positive_predictions"] == 1
    assert report["threshold_selection_support"]["big_gain_rows"] == 1
    assert "support insufficient" in report["threshold_change_reason"]


def test_threshold_support_never_substitutes_total_big_gain_rows_for_selected_trades(tmp_path):
    artifact_path = tmp_path / "candidate.json"
    _artifact(artifact_path, threshold=0.55)
    frame = pd.DataFrame({
        "event_date": [f"2026-07-{(index % 20) + 1:02d}" for index in range(2000)],
        "symbol": [f"SYM{index % 20}" for index in range(2000)],
        "feature_signal": [0.0] * 2000,
        "return_5d": [0.04] * 1706 + [-0.04] * 294,
    })
    metrics = {
        "positive_predictions": 151,
        "big_gain_rows": 1706,
        "big_gain_predictions": 0,
        "big_gain_capture_rate": 0.0,
        "big_loss_predictions": 148,
        "utility_score": -0.2414,
        "symbol_selection_concentration": 0.9669,
        "threshold_search": [
            {
                "threshold": 0.55,
                "positive_predictions": 151,
                "big_gain_rows": 1706,
                "big_gain_predictions": 0,
                "big_gain_capture_rate": 0.0,
                "big_loss_predictions": 148,
                "utility_score": -0.2414,
            }
        ],
    }

    report = _threshold_optimizer_report(str(artifact_path), metrics, frame, min_rows=200)
    support = report["threshold_selection_support"]

    assert support["total_big_gain_rows"] == 1706
    assert support["selected_trade_big_gain_count"] == 0
    assert support["big_gain_predictions"] == 0
    assert support["big_gain_capture_rate"] == 0.0
    assert support["checks"]["selected_big_gain_capture_passed"] is False
    assert report["threshold_selection_sufficient"] is False


def test_flat_optimum_keeps_current_threshold_inside_near_optimal_plateau():
    metrics = {
        "threshold_search": [
            {"threshold": 0.60, "utility_score": 0.270, "positive_predictions": 70, "big_loss_predictions": 0, "big_loss_prediction_rate": 0.0, "big_gain_capture_rate": 0.4},
            {"threshold": 0.625, "utility_score": 0.274, "positive_predictions": 65, "big_loss_predictions": 0, "big_loss_prediction_rate": 0.0, "big_gain_capture_rate": 0.38},
            {"threshold": 0.65, "utility_score": 0.273, "positive_predictions": 60, "big_loss_predictions": 0, "big_loss_prediction_rate": 0.0, "big_gain_capture_rate": 0.35},
            {"threshold": 0.70, "utility_score": 0.24, "positive_predictions": 50, "big_loss_predictions": 0, "big_loss_prediction_rate": 0.0, "big_gain_capture_rate": 0.3},
        ]
    }

    selected = _select_threshold_from_search(metrics, 0.60)

    assert selected["recommended_threshold"] == 0.60
    assert selected["reason"] == "current threshold is inside the flat-optimum utility plateau"
    assert selected["flat_optimum"]["selected_plateau_thresholds"] == [0.6, 0.625, 0.65]


def test_paired_date_bootstrap_requires_positive_candidate_utility_delta(tmp_path):
    candidate_path = tmp_path / "candidate.json"
    production_path = tmp_path / "production.json"
    _artifact(candidate_path, threshold=0.55)
    save_artifact(
        BaselineModelArtifact(
            version="production",
            feature_columns=["feature_signal"],
            means=[0.0],
            stds=[1.0],
            weights=[-10.0],
            bias=0.0,
            decision_threshold=0.55,
        ),
        production_path,
    )
    frame = pd.DataFrame(
        {
            "event_date": pd.date_range("2026-01-01", periods=12, freq="D").astype(str),
            "feature_signal": [1.0] * 12,
            "return_5d": [0.02] * 12,
        }
    )

    result = _paired_date_bootstrap_utility_delta(str(candidate_path), str(production_path), frame)

    assert result["passed"] is True
    assert result["independent_date_blocks"] == 12
    assert result["utility_delta_lower"] > 0.0
    assert result["probability_positive"] == 1.0


def test_threshold_guardrails_reject_too_few_positive_predictions():
    proposed = {"positive_predictions": 2, "big_loss_predictions": 0, "big_loss_prediction_rate": 0.0, "big_gain_capture_rate": 0.3}
    current = {"positive_predictions": 100, "big_loss_predictions": 0, "big_loss_prediction_rate": 0.0, "big_gain_capture_rate": 0.5}

    passed, reasons = _threshold_guardrails_pass(proposed, current)

    assert passed is False
    assert any("positive_predictions below minimum" in reason for reason in reasons)


def test_threshold_guardrails_reject_symbol_and_date_concentration():
    proposed = {"positive_predictions": 50, "big_loss_predictions": 0, "big_loss_prediction_rate": 0.0, "big_gain_capture_rate": 0.3, "symbol_utility_concentration": 0.7, "date_utility_concentration": 0.8}
    current = {"positive_predictions": 100, "big_loss_predictions": 0, "big_loss_prediction_rate": 0.0, "big_gain_capture_rate": 0.5}

    passed, reasons = _threshold_guardrails_pass(proposed, current)

    assert passed is False
    assert any("symbol utility concentration exceeds maximum" in reason for reason in reasons)
    assert any("date utility concentration exceeds maximum" in reason for reason in reasons)


def test_threshold_stability_rejects_one_lucky_threshold_window():
    unstable = _threshold_stability_summary([0.55, 0.70, 0.55], 0.70)
    stable = _threshold_stability_summary([0.675, 0.70, 0.70], 0.70)

    assert unstable["stable"] is False
    assert unstable["observed_threshold_spread"] == 0.15
    assert stable["stable"] is True


def test_feature_risk_audit_flags_raw_price_dominating_positive_predictions(tmp_path):
    artifact_path = tmp_path / "price-heavy.json"
    save_artifact(
        BaselineModelArtifact(
            version="price-heavy-v1",
            feature_columns=["feature_price", "feature_signal"],
            means=[10.0, 0.0],
            stds=[1.0, 1.0],
            weights=[4.0, 0.1],
            bias=0.0,
            decision_threshold=0.55,
        ),
        artifact_path,
    )
    frame = pd.DataFrame({"symbol": ["CMI", "ABC"], "event_date": ["2026-07-10", "2026-07-11"], "feature_price": [12.0, 13.0], "feature_signal": [0.1, 0.1]})

    audit = _feature_risk_audit(str(artifact_path), frame)

    assert audit["raw_feature_price_present"] is True
    assert audit["raw_price_top_positive_contributor_rate"] == 1.0
    assert audit["requires_review"] is True
    assert audit["examples"][0]["symbol"] == "CMI"
