import numpy as np
import pandas as pd

from scripts.day10_train_candidate_model import (
    THRESHOLD_SEARCH_VALUES,
    _chronological_training_periods,
    _chronological_training_periods_with_diagnostics,
    _flat_optimum_threshold,
    _future_safe_feature_columns,
    _select_feature_columns,
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

    assert _future_safe_feature_columns(columns) == ["feature_price"]


def test_feature_selection_prefers_massive_lagged_technical_and_relative_features():
    frame = pd.DataFrame({
        "feature_rec_buy": [1.0, 0.0],
        "feature_return_5d_lagged": [0.01, 0.02],
        "feature_spy_return_5d": [0.0, 0.01],
        "feature_volume_ratio_20d": [1.2, 0.9],
        "feature_rsi_14": [55.0, 45.0],
    })

    selected = _select_feature_columns(frame)

    assert selected[:4] == ["feature_return_5d_lagged", "feature_rsi_14", "feature_spy_return_5d", "feature_volume_ratio_20d"]
    assert selected[-1] == "feature_rec_buy"


def test_threshold_search_uses_track_b_profit_grid_and_records_big_loss_guardrail():
    frame = pd.DataFrame(
        {
            "return_5d": [-0.05, 0.04, 0.02, -0.01],
            "return_bin_5d": ["big_loss", "big_gain", "gain", "loss"],
        }
    )
    probs = np.array([0.56, 0.66, 0.61, 0.58])

    selected = _select_profit_threshold(frame, probs, min_positive_predictions=1, min_big_gain_examples=1, min_independent_dates=0, min_unique_symbols=0)

    assert THRESHOLD_SEARCH_VALUES == (0.55, 0.575, 0.60, 0.625, 0.65, 0.675, 0.70)
    assert [row["threshold"] for row in selected["search"]] == list(THRESHOLD_SEARCH_VALUES)
    assert selected["threshold"] == 0.625
    assert selected["big_loss_predictions"] == 0
    assert selected["big_loss_guardrail"] == "zero_big_loss_predictions"
    assert selected["flat_optimum"]["policy"] == "center_of_broadest_near_optimal_plateau"
    assert selected["threshold"] in selected["flat_optimum"]["selected_plateau_thresholds"]
    assert all("big_loss_prediction_rate" in row for row in selected["search"])


def test_threshold_selection_keeps_current_threshold_when_support_is_too_thin():
    frame = pd.DataFrame({
        "event_date": ["2026-07-10"] * 201,
        "symbol": ["CMI"] * 201,
        "return_5d": [0.04] + [-0.01] * 200,
        "return_bin_5d": ["big_gain"] + ["loss"] * 200,
    })
    probs = np.array([0.56] + [0.10] * 200)

    selected = _select_profit_threshold(frame, probs, current_threshold=0.55)

    assert selected["threshold"] == 0.55
    assert selected["threshold_selection_sufficient"] is False
    assert selected["selection_status"] == "insufficient_support_keep_current_threshold"
    assert selected["threshold_selection_support"]["maximum_positive_predictions"] == 1
    assert selected["threshold_selection_support"]["big_gain_examples"] == 1
    assert selected["threshold_selection_support"]["independent_dates"] == 1
    assert selected["threshold_selection_support"]["unique_symbols"] == 1


def test_flat_optimum_prefers_broad_plateau_over_isolated_peak():
    candidates = [
        {"threshold": 0.60, "utility_score": 0.275, "big_loss_prediction_rate": 0.0},
        {"threshold": 0.625, "utility_score": 0.276, "big_loss_prediction_rate": 0.0},
        {"threshold": 0.65, "utility_score": 0.2745, "big_loss_prediction_rate": 0.0},
        {"threshold": 0.70, "utility_score": 0.280, "big_loss_prediction_rate": 0.0},
    ]

    selected, diagnostics = _flat_optimum_threshold(candidates)

    assert selected["threshold"] == 0.625
    assert diagnostics["peak_threshold"] == 0.70
    assert diagnostics["selected_plateau_thresholds"] == [0.60, 0.625, 0.65]
