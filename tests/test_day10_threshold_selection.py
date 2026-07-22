import numpy as np
import pandas as pd

from scripts.day10_train_candidate_model import THRESHOLD_SEARCH_VALUES, _select_profit_threshold


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
