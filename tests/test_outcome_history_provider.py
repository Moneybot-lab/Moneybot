import pandas as pd
import pytest

from moneybot.services.outcome_history_provider import MassivePreferredHistoryDownload


class _MassiveService:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error

    def get_massive_aggregates(self, *args, **kwargs):
        if self.error is not None:
            raise self.error
        return self.payload


def _fallback_frame():
    return pd.DataFrame(
        {"Close": [100.0, 101.0]},
        index=pd.to_datetime(["2026-07-01", "2026-07-02"]),
    )


def test_normalized_start_timestamp_uses_massive_without_fallback(monkeypatch):
    service = _MassiveService(
        {
            "bars": [
                {"start_timestamp": "2026-07-01T04:00:00+00:00", "close": 745.76},
                {"start_timestamp": "2026-07-02T04:00:00+00:00", "close": 744.78},
            ]
        }
    )
    download = MassivePreferredHistoryDownload(service=service)
    monkeypatch.setattr(
        "moneybot.services.outcome_history_provider.yf.download",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("fallback not expected")
        ),
    )

    frame = download("SPY", start="2026-07-01", end="2026-07-03")
    diagnostics = download.diagnostics_payload()

    assert frame["Close"].tolist() == [745.76, 744.78]
    assert diagnostics["massive_history_requests"] == 1
    assert diagnostics["massive_history_successes"] == 1
    assert diagnostics["yfinance_history_fallbacks"] == 0
    assert diagnostics["massive_last_error"] is None
    assert download.provider_for_symbol("SPY") == "massive"


@pytest.mark.parametrize("timestamp_field", ["date", "timestamp_utc", "t"])
def test_legacy_timestamp_aliases_still_use_massive(monkeypatch, timestamp_field):
    timestamp = (
        1782864000000000000 if timestamp_field == "t" else "2026-07-01T04:00:00+00:00"
    )
    download = MassivePreferredHistoryDownload(
        service=_MassiveService({"bars": [{timestamp_field: timestamp, "c": 42.0}]})
    )
    monkeypatch.setattr(
        "moneybot.services.outcome_history_provider.yf.download",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("fallback not expected")
        ),
    )

    frame = download("LEGACY", start="2026-07-01", end="2026-07-02")

    assert frame["Close"].tolist() == [42.0]
    assert download.provider_for_symbol("LEGACY") == "massive"


def test_empty_massive_response_falls_back_with_diagnostics(monkeypatch):
    download = MassivePreferredHistoryDownload(service=_MassiveService({"bars": []}))
    monkeypatch.setattr(
        "moneybot.services.outcome_history_provider.yf.download",
        lambda *args, **kwargs: _fallback_frame(),
    )

    frame = download("EMPTY", start="2026-07-01", end="2026-07-03")
    diagnostics = download.diagnostics_payload()

    assert len(frame) == 2
    assert diagnostics["massive_empty_responses"] == 1
    assert diagnostics["yfinance_history_fallbacks"] == 1
    assert download.provider_for_symbol("EMPTY") == "yfinance"


def test_malformed_massive_bars_fall_back_with_parse_diagnostics(monkeypatch):
    download = MassivePreferredHistoryDownload(
        service=_MassiveService(
            {"bars": [{"close": 12.0}, {"start_timestamp": "bad", "close": 13.0}]}
        )
    )
    monkeypatch.setattr(
        "moneybot.services.outcome_history_provider.yf.download",
        lambda *args, **kwargs: _fallback_frame(),
    )

    frame = download("BAD", start="2026-07-01", end="2026-07-03")
    diagnostics = download.diagnostics_payload()

    assert len(frame) == 2
    assert diagnostics["massive_parse_failures"] == 1
    assert diagnostics["yfinance_history_fallbacks"] == 1
    assert download.provider_for_symbol("BAD") == "yfinance"


def test_massive_exception_is_visible_and_nonfatal(monkeypatch):
    download = MassivePreferredHistoryDownload(
        service=_MassiveService(error=RuntimeError("Massive temporarily unavailable"))
    )
    monkeypatch.setattr(
        "moneybot.services.outcome_history_provider.yf.download",
        lambda *args, **kwargs: _fallback_frame(),
    )

    frame = download("ERROR", start="2026-07-01", end="2026-07-03")
    diagnostics = download.diagnostics_payload()

    assert len(frame) == 2
    assert "temporarily unavailable" in diagnostics["massive_last_error"]
    assert diagnostics["yfinance_history_fallbacks"] == 1
    assert download.provider_for_symbol("ERROR") == "yfinance"
