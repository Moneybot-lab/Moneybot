from scripts import day13_calibration_report as calibration_script
from scripts.day13_calibration_report import (
    calibration_rows_from_events,
    calibration_summary,
)


def test_calibration_summary_computes_brier_and_bins():
    rows = [
        {"predicted": 0.8, "observed": 1.0},
        {"predicted": 0.7, "observed": 1.0},
        {"predicted": 0.3, "observed": 0.0},
        {"predicted": 0.2, "observed": 0.0},
    ]

    summary = calibration_summary(rows, bins=4)

    assert summary["rows"] == 4
    assert isinstance(summary["brier_score"], float)
    assert summary["brier_score"] < 0.1
    assert len(summary["bins"]) >= 2
    assert "recommended" in summary


def test_calibration_rows_from_events_skips_non_mature_events(monkeypatch):
    events = [
        {"symbol": "AAPL", "ts": 100, "payload": {"probability_up": 0.7}},
        {"symbol": "MSFT", "ts": 1000, "payload": {"probability_up": 0.4}},
    ]

    def fake_future_return(symbol, ts, days):
        return 0.01

    monkeypatch.setattr(calibration_script, "_future_return", fake_future_return)

    rows = calibration_rows_from_events(
        events, horizon_days=5, now_ts=100 + (7 * 86400)
    )
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAPL"


def test_calibration_summary_recommends_slope_adjustment_when_overconfident():
    rows = [
        {"predicted": 0.9, "observed": 0.0},
        {"predicted": 0.85, "observed": 0.0},
        {"predicted": 0.8, "observed": 1.0},
        {"predicted": 0.75, "observed": 0.0},
        {"predicted": 0.2, "observed": 0.0},
        {"predicted": 0.15, "observed": 1.0},
    ]

    summary = calibration_summary(rows, bins=4)

    assert abs(summary["recommended"]["slope_delta"]) > 0.01


def test_calibration_summary_reports_effective_calibrated_brier_for_underconfident_predictions():
    rows = [
        {"predicted": 0.35, "observed": 1.0},
        {"predicted": 0.38, "observed": 1.0},
        {"predicted": 0.42, "observed": 1.0},
        {"predicted": 0.45, "observed": 0.0},
        {"predicted": 0.48, "observed": 1.0},
        {"predicted": 0.52, "observed": 1.0},
    ]

    summary = calibration_summary(rows, bins=4)

    assert summary["brier_score_raw"] == summary["brier_score"]
    assert summary["calibrated_brier_score"] <= summary["brier_score"]
    assert summary["effective_brier_score"] == summary["calibrated_brier_score"]
    assert summary["brier_improvement"] > 0


def test_calibration_rows_exclude_null_probability_and_quote_only(monkeypatch):
    events = [
        {
            "symbol": "AAPL",
            "ts": 100,
            "endpoint": "quick_ask",
            "decision_source": "model",
            "payload": {"probability_up": None},
        },
        {
            "symbol": "MSFT",
            "ts": 100,
            "endpoint": "portfolio",
            "decision_source": "rule",
            "payload": {"probability_up": 0.7, "provider": "portfolio_quote_only"},
        },
        {
            "symbol": "NVDA",
            "ts": 100,
            "endpoint": "quick_ask",
            "decision_source": "model",
            "payload": {
                "probability_up": 0.8,
                "features": {"rsi": 55},
                "model_version": "v1",
            },
        },
    ]

    monkeypatch.setattr(
        calibration_script, "_future_return", lambda symbol, ts, days: 0.01
    )

    rows = calibration_rows_from_events(
        events, horizon_days=5, now_ts=100 + (7 * 86400)
    )
    profile = calibration_script.calibration_input_profile(events, horizon_days=5)
    segments = calibration_script.segmented_calibration_summaries(rows, bins=4)
    input_segments = calibration_script.calibration_input_segments(events, horizon_days=5)

    assert [row["symbol"] for row in rows] == ["NVDA"]
    assert profile["null_probability_rows"] == 1
    assert profile["portfolio_quote_only_rows"] == 1
    assert segments["endpoint"]["quick_ask"]["rows"] == 1
    assert segments["signal_completeness"]["full_signal"]["rows"] == 1
    assert input_segments["probability_presence"]["null"]["excluded_rows"] == 1
    assert input_segments["provider"]["portfolio_quote_only"]["excluded_rows"] == 1
    assert input_segments["endpoint"]["quick_ask"]["rows_scanned"] == 2


def test_mixed_decision_warning_uses_all_scanned_types():
    events = [
        {"endpoint": "quick_ask", "decision_source": "deterministic_model", "payload": {"probability_up": 0.8, "model_version": "v1", "features": {"rsi": 55}}},
        {"endpoint": "user_watchlist", "decision_source": "rule_based", "payload": {"probability_up": None, "provider": "portfolio_quote_only"}},
    ]
    profile = calibration_script.calibration_input_profile(events)
    input_segments = calibration_script.calibration_input_segments(events)

    warnings = calibration_script._mixed_decision_warnings([], profile, input_segments)

    assert any("mixes endpoint" in warning for warning in warnings)
    assert any("probability_up=null" in warning for warning in warnings)
