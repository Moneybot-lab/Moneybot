import numpy as np

from scripts.day11_compare_candidate_vs_production import _no_op_clone_summary, _walk_forward_consistency


def test_no_op_clone_summary_marks_nearly_identical_predictions_as_clone():
    candidate_preds = np.array([1, 0, 1, 1, 0] * 20)
    production_preds = candidate_preds.copy()
    candidate_probs = np.array([0.80, 0.40, 0.70, 0.90, 0.30] * 20)
    production_probs = candidate_probs + 0.005

    summary = _no_op_clone_summary(candidate_preds, production_preds, candidate_probs, production_probs)

    assert summary["no_op_clone"] is True
    assert summary["prediction_agreement"] == 1.0
    assert summary["probability_mae"] <= summary["probability_mae_threshold"]


def test_no_op_clone_summary_allows_materially_different_predictions():
    candidate_preds = np.array([1, 0, 1, 1, 0] * 20)
    production_preds = np.array([0, 1, 0, 1, 0] * 20)
    candidate_probs = np.array([0.80, 0.40, 0.70, 0.90, 0.30] * 20)
    production_probs = np.array([0.30, 0.80, 0.40, 0.88, 0.32] * 20)

    summary = _no_op_clone_summary(candidate_preds, production_preds, candidate_probs, production_probs)

    assert summary["no_op_clone"] is False
    assert summary["prediction_agreement"] < summary["prediction_agreement_threshold"]


def test_walk_forward_consistency_requires_multiple_passing_windows():
    result = _walk_forward_consistency(
        [
            {"window": 1, "evaluated": True, "candidate_win": True},
            {"window": 2, "evaluated": True, "candidate_win": False},
            {"window": 3, "evaluated": True, "candidate_win": True},
        ]
    )

    assert result["consistent"] is False
    assert result["windows_evaluated"] == 3


def test_walk_forward_consistency_passes_when_all_evaluated_windows_pass():
    result = _walk_forward_consistency(
        [
            {"window": 1, "evaluated": True, "candidate_win": True},
            {"window": 2, "evaluated": True, "candidate_win": True},
            {"window": 3, "evaluated": True, "candidate_win": True},
        ]
    )

    assert result["consistent"] is True
    assert result["windows_evaluated"] == 3
