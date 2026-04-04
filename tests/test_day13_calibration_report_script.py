from scripts import day13_calibration_report as calibration_script
from scripts.day13_calibration_report import calibration_rows_from_events, calibration_summary


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

    rows = calibration_rows_from_events(events, horizon_days=5, now_ts=100 + (7 * 86400))
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAPL"
