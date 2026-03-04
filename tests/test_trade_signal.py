from __future__ import annotations

import trade_signal


def test_fetch_fundamentals_skips_scrape_when_info_rate_limited(monkeypatch):
    class DummyTicker:
        @property
        def info(self):
            raise Exception("Too Many Requests. Rate limited. Try after a while.")

    monkeypatch.setattr(trade_signal, "get_ticker", lambda _ticker: DummyTicker())

    called = {"count": 0}

    def fail_if_called(*_args, **_kwargs):
        called["count"] += 1
        raise AssertionError("requests.get should not be called after yfinance rate limit")

    monkeypatch.setattr(trade_signal.requests, "get", fail_if_called)

    data = trade_signal.fetch_fundamentals("AAPL")

    assert data["revenue_growth_yoy"] is None
    assert data["active_users_qoq"] is None
    assert data["subs_yoy"] is None
    assert called["count"] == 0
