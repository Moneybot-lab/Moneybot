import numpy as np
import pandas as pd

from scripts.day10_train_candidate_model import THRESHOLD_SEARCH_VALUES, _chronological_training_periods, _select_profit_threshold


def test_chronological_training_periods_are_disjoint_and_leave_final_test_untouched():
    frame = pd.DataFrame({"row": range(100)})

    fit, calibration, threshold, final_test = _chronological_training_periods(frame, 0.8)

    assert [len(part) for part in (fit, calibration, threshold, final_test)] == [48, 16, 16, 20]
    assert fit["row"].tolist() == list(range(48))
    assert calibration["row"].tolist() == list(range(48, 64))
    assert threshold["row"].tolist() == list(range(64, 80))
    assert final_test["row"].tolist() == list(range(80, 100))
    later_indexes = set(calibration.index) | set(threshold.index) | set(final_test.index)
    assert set(fit.index).isdisjoint(later_indexes)


def test_threshold_search_uses_track_b_profit_grid_and_records_big_loss_guardrail():
    frame = pd.DataFrame(
        {
            "return_5d": [-0.05, 0.04, 0.02, -0.01],
            "return_bin_5d": ["big_loss", "big_gain", "gain", "loss"],
        }
    )
    probs = np.array([0.56, 0.66, 0.61, 0.58])

    selected = _select_profit_threshold(frame, probs)

    assert THRESHOLD_SEARCH_VALUES == (0.55, 0.575, 0.60, 0.625, 0.65, 0.675, 0.70)
    assert [row["threshold"] for row in selected["search"]] == list(THRESHOLD_SEARCH_VALUES)
    assert selected["threshold"] == 0.625
    assert selected["big_loss_predictions"] == 0
    assert selected["big_loss_guardrail"] == "zero_big_loss_predictions"
    assert all("big_loss_prediction_rate" in row for row in selected["search"])
