from __future__ import annotations

from moneybot.services.market_data import MarketDataService


def test_get_quote_uses_runtime_finnhub_key(monkeypatch):
    svc = MarketDataService()

    monkeypatch.setenv("FINNHUB_API_KEY", "runtime-key")

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"c": 123.45, "dp": 1.23, "pc": 122.0}

    captured = {}

    def fake_get(url, params, headers, timeout):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        captured["timeout"] = timeout
        return DummyResponse()

    monkeypatch.setattr("moneybot.services.market_data.requests.get", fake_get)

    quote = svc.get_quote("AAPL")

    assert quote["quote_source"] == "finnhub"
    assert quote["price"] == 123.45
    assert captured["params"]["token"] == "runtime-key"
    assert captured["headers"]["X-Finnhub-Token"] == "runtime-key"


def test_get_quote_uses_finnhub_token_alias(monkeypatch):
    svc = MarketDataService()

    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.setenv("FINNHUB_TOKEN", "alias-key")

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"c": 99.0, "dp": 0.5, "pc": 98.5}

    captured = {}

    def fake_get(url, params, headers, timeout):
        captured["params"] = params
        captured["headers"] = headers
        return DummyResponse()

    monkeypatch.setattr("moneybot.services.market_data.requests.get", fake_get)

    quote = svc.get_quote("TSLA")

    assert quote["quote_source"] == "finnhub"
    assert captured["params"]["token"] == "alias-key"
    assert captured["headers"]["X-Finnhub-Token"] == "alias-key"


def test_get_quote_falls_back_to_yfinance_without_finnhub_key(monkeypatch):
    svc = MarketDataService()

    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("FINNHUB_TOKEN", raising=False)
    monkeypatch.delenv("X_FINNHUB_TOKEN", raising=False)

    class DummyTicker:
        info = {
            "regularMarketPrice": 250.0,
            "regularMarketPreviousClose": 245.0,
            "regularMarketChangePercent": 2.04,
        }

    monkeypatch.setattr("moneybot.services.market_data.yf.Ticker", lambda _symbol: DummyTicker())

    quote = svc.get_quote("MSFT")

    assert quote["quote_source"] == "yfinance"
    assert quote["live_data_available"] is True
    assert quote["price"] == 250.0
