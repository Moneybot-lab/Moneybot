import numpy as np
import pandas as pd

from scripts.day10_train_candidate_model import (
    THRESHOLD_SEARCH_VALUES,
    _chronological_training_periods,
    _chronological_training_periods_with_diagnostics,
    _future_safe_feature_columns,
    _select_profit_threshold,
)


def test_chronological_training_periods_are_disjoint_and_leave_final_test_untouched():
    frame = pd.DataFrame({"row": range(100)})

    fit, calibration, threshold, final_test = _chronological_training_periods(frame, 0.8)

    assert [len(part) for part in (fit, calibration, threshold, final_test)] == [43, 10, 10, 19]
    assert fit["row"].tolist() == list(range(43))
    assert calibration["row"].tolist() == list(range(49, 59))
    assert threshold["row"].tolist() == list(range(65, 75))
    assert final_test["row"].tolist() == list(range(81, 100))
    later_indexes = set(calibration.index) | set(threshold.index) | set(final_test.index)
    assert set(fit.index).isdisjoint(later_indexes)


def test_chronological_training_periods_allocate_whole_dense_event_dates():
    dates = pd.date_range("2026-04-01", periods=80, freq="D", tz="UTC")
    rows = []
    for offset, event_date in enumerate(dates):
        # Reproduce production-like density where recent dates contain most rows.
        count = 50 if offset >= 60 else 1
        rows.extend({"event_date": event_date.isoformat(), "row": len(rows) + item} for item in range(count))
    frame = pd.DataFrame(rows)

    periods, boundaries = _chronological_training_periods_with_diagnostics(frame, 0.8)

    assert all(not period.empty for period in periods)
    assert len(boundaries) == 3
    assert all(boundary["method"] == "event_time" for boundary in boundaries)
    assert all(boundary["label_horizon_gap_passed"] for boundary in boundaries)
    period_dates = [set(pd.to_datetime(period["event_date"], utc=True).dt.date) for period in periods]
    assert all(period_dates[left].isdisjoint(period_dates[right]) for left in range(4) for right in range(left + 1, 4))


def test_future_labels_outcomes_and_realized_returns_are_never_features():
    columns = ["feature_price", "feature_return_5d", "feature_forward_return_5d", "feature_future_return", "feature_realized_return", "feature_outcome_5d", "feature_label_gain_5d"]

    assert _future_safe_feature_columns(columns) == ["feature_price", "feature_return_5d"]


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
