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
        {
            "symbol": "AAPL",
            "ts": 100,
            "payload": {"probability_up": 0.7, "forecast_horizon": "5d"},
        },
        {
            "symbol": "MSFT",
            "ts": 1000,
            "payload": {"probability_up": 0.4, "forecast_horizon": "5d"},
        },
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
            "payload": {"probability_up": None, "forecast_horizon": "5d"},
        },
        {
            "symbol": "MSFT",
            "ts": 100,
            "endpoint": "portfolio",
            "decision_source": "rule",
            "payload": {
                "probability_up": 0.7,
                "provider": "portfolio_quote_only",
                "forecast_horizon": "5d",
            },
        },
        {
            "symbol": "NVDA",
            "ts": 100,
            "endpoint": "quick_ask",
            "decision_source": "model",
            "payload": {
                "probability_up": 0.8,
                "forecast_horizon": "5d",
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
    input_segments = calibration_script.calibration_input_segments(
        events, horizon_days=5
    )

    assert [row["symbol"] for row in rows] == ["NVDA"]
    assert profile["null_probability_rows"] == 1
    assert profile["portfolio_quote_only_rows"] == 1
    assert segments["endpoint"]["quick_ask"]["rows"] == 1
    assert segments["signal_completeness"]["partial_signal"]["rows"] == 1
    assert input_segments["probability_presence"]["null"]["excluded_rows"] == 1
    assert input_segments["provider"]["portfolio_quote_only"]["excluded_rows"] == 1
    assert input_segments["endpoint"]["quick_ask"]["rows_scanned"] == 2


def test_mixed_decision_warning_uses_all_scanned_types():
    events = [
        {
            "endpoint": "quick_ask",
            "decision_source": "deterministic_model",
            "payload": {
                "probability_up": 0.8,
                "model_version": "v1",
                "features": {"rsi": 55},
            },
        },
        {
            "endpoint": "user_watchlist",
            "decision_source": "rule_based",
            "payload": {"probability_up": None, "provider": "portfolio_quote_only"},
        },
    ]
    profile = calibration_script.calibration_input_profile(events)
    input_segments = calibration_script.calibration_input_segments(events)

    warnings = calibration_script._mixed_decision_warnings([], profile, input_segments)

    assert any("mixes endpoint" in warning for warning in warnings)
    assert any("probability_up=null" in warning for warning in warnings)


def test_horizon_isolation_excludes_mismatch_and_unknown(monkeypatch):
    events = [
        {
            "symbol": "FIVE",
            "ts": 100,
            "payload": {"probability_up": 0.7, "forecast_horizon": "5d"},
        },
        {
            "symbol": "ONE",
            "ts": 100,
            "payload": {"probability_up": 0.8, "forecast_horizon": "1d"},
        },
        {"symbol": "UNKNOWN", "ts": 100, "payload": {"probability_up": 0.9}},
    ]
    monkeypatch.setattr(
        calibration_script, "_future_return", lambda symbol, ts, days: 0.01
    )
    diagnostics = {
        key: 0
        for key in (
            "rows_matching_horizon",
            "rows_excluded_horizon_mismatch",
            "rows_excluded_horizon_unknown",
            "rows_excluded_probability_null",
            "rows_excluded_portfolio_quote_only",
            "rows_excluded_immature",
            "rows_excluded_outcome_unavailable",
            "calibration_eligible_rows",
            "mature_fitted_rows",
        )
    }

    five_day = calibration_rows_from_events(
        events,
        horizon_days=5,
        now_ts=100 + 7 * 86400,
        eligibility_diagnostics=diagnostics,
    )
    one_day = calibration_rows_from_events(
        events, horizon_days=1, now_ts=100 + 7 * 86400
    )

    assert [row["symbol"] for row in five_day] == ["FIVE"]
    assert [row["symbol"] for row in one_day] == ["ONE"]
    assert diagnostics["rows_excluded_horizon_mismatch"] == 1
    assert diagnostics["rows_excluded_horizon_unknown"] == 1
    profile = calibration_script.calibration_input_profile(events, horizon_days=5)
    warnings = calibration_script._mixed_decision_warnings(
        five_day,
        profile,
        calibration_script.calibration_input_segments(events, horizon_days=5),
    )
    assert any("mixes forecast_horizon" in warning for warning in warnings)
    assert any("did not match" in warning for warning in warnings)


def test_signal_completeness_requires_finite_meaningful_features():
    classify = calibration_script._signal_completeness
    assert (
        classify(
            {"features": {"rsi": None, "macd": None, "volume_ratio": None}}, {}, "model"
        )
        == "quote_only"
    )
    assert (
        classify({"features": {"rsi": float("nan"), "macd": float("inf")}}, {}, "model")
        == "quote_only"
    )
    assert classify({"features": {"rsi_14": 55.0}}, {}, "model") == "partial_signal"
    assert (
        classify(
            {"features": {"return_1d": 0.01, "rsi_14": 55.0, "macd_hist": 0.2}},
            {},
            "model",
        )
        == "full_signal"
    )
    assert (
        classify(
            {"features": {"return_1d": 0.01, "rsi_14": 55.0, "macd_hist": 0.2}},
            {},
            "portfolio_quote_only",
        )
        == "quote_only"
    )


def test_day13_massive_first_outcome_and_diagnostics(monkeypatch):
    import pandas as pd
    from moneybot.services.outcome_history_provider import (
        MassivePreferredHistoryDownload,
    )
    from moneybot.services.outcome_tracking import OutcomeHistoryCache

    class Massive:
        def get_massive_aggregates(self, *args, **kwargs):
            return {
                "bars": [
                    {
                        "start_timestamp": f"2026-01-0{day}T04:00:00+00:00",
                        "close": price,
                    }
                    for day, price in (
                        (2, 100),
                        (3, 101),
                        (4, 102),
                        (5, 103),
                        (6, 104),
                        (7, 110),
                    )
                ]
            }

    download = MassivePreferredHistoryDownload(service=Massive())
    monkeypatch.setattr(
        "moneybot.services.outcome_history_provider.yf.download",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("fallback not expected")
        ),
    )
    cache = OutcomeHistoryCache(
        download=download, now=pd.Timestamp("2026-02-01", tz="UTC").to_pydatetime()
    )
    event_ts = int(pd.Timestamp("2026-01-02", tz="UTC").timestamp())

    assert cache.future_return("AAPL", event_ts, 5) == 0.1
    assert download.provider_for_symbol("AAPL") == "massive"
    assert download.diagnostics_payload()["massive_history_successes"] == 1
    assert download.diagnostics_payload()["yfinance_history_fallbacks"] == 0


def test_day13_outcome_falls_back_to_yfinance_with_diagnostics(monkeypatch):
    import pandas as pd
    from moneybot.services.outcome_history_provider import (
        MassivePreferredHistoryDownload,
    )
    from moneybot.services.outcome_tracking import OutcomeHistoryCache

    class EmptyMassive:
        def get_massive_aggregates(self, *args, **kwargs):
            return {"bars": []}

    dates = pd.date_range("2026-01-02", periods=6, freq="D")
    monkeypatch.setattr(
        "moneybot.services.outcome_history_provider.yf.download",
        lambda *args, **kwargs: pd.DataFrame(
            {"Close": [100, 101, 102, 103, 104, 105]}, index=dates
        ),
    )
    download = MassivePreferredHistoryDownload(service=EmptyMassive())
    cache = OutcomeHistoryCache(
        download=download, now=pd.Timestamp("2026-02-01", tz="UTC").to_pydatetime()
    )
    event_ts = int(pd.Timestamp("2026-01-02", tz="UTC").timestamp())

    assert cache.future_return("AAPL", event_ts, 5) == 0.05
    assert download.provider_for_symbol("AAPL") == "yfinance"
    diagnostics = download.diagnostics_payload()
    assert diagnostics["massive_history_requests"] == 1
    assert diagnostics["massive_history_successes"] == 0
    assert diagnostics["yfinance_history_fallbacks"] == 1
