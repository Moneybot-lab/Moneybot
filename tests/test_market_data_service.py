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
    assert quote["diagnostics"]["finnhub_key_source"] == "FINNHUB_API_KEY"


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
    assert quote["diagnostics"]["finnhub_key_source"] == "FINNHUB_TOKEN"


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
    assert quote["diagnostics"]["finnhub_attempted"] is False
    assert quote["diagnostics"]["finnhub_error"] == "missing_api_key"


def test_get_market_indices_prefers_finnhub_quote_data(monkeypatch):
    svc = MarketDataService()

    class DummyHistory:
        empty = False

        def __getitem__(self, _key):
            class Tailable:
                def tail(self, _days):
                    return [100.0, 101.0, 102.0]

            return Tailable()

    class DummyTicker:
        def history(self, period, interval):
            return DummyHistory()

    monkeypatch.setattr("moneybot.services.market_data.yf.Ticker", lambda _symbol: DummyTicker())
    monkeypatch.setattr(
        svc,
        "get_quote",
        lambda _symbol: {"price": 321.0, "change_percent": 1.5, "quote_source": "finnhub"},
    )

    data = svc.get_market_indices()

    assert len(data) == 5
    assert all(item["price"] == 321.0 for item in data)
    assert all(item["quote_source"] == "finnhub" for item in data)


def test_get_quote_stops_yfinance_retries_on_rate_limit(monkeypatch):
    svc = MarketDataService(retries=3)

    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("FINNHUB_TOKEN", raising=False)
    monkeypatch.delenv("X_FINNHUB_TOKEN", raising=False)

    calls = {"count": 0}

    class DummyTicker:
        @property
        def info(self):
            calls["count"] += 1
            raise Exception("Too Many Requests. Rate limited. Try after a while.")

    monkeypatch.setattr("moneybot.services.market_data.yf.Ticker", lambda _symbol: DummyTicker())

    quote = svc.get_quote("AAPL")

    assert quote["quote_source"] == "yfinance"
    assert quote["live_data_available"] is False
    assert calls["count"] == 1

def test_get_signal_skips_analysis_when_quote_missing(monkeypatch):
    svc = MarketDataService()

    monkeypatch.setattr(
        svc,
        "get_quote",
        lambda _symbol: {
            "symbol": "LCDI",
            "price": "DATA_MISSING",
            "change_percent": "DATA_MISSING",
            "live_data_available": False,
            "quote_source": "yfinance",
            "diagnostics": {"provider": "yfinance", "error": "not_found"},
        },
    )

    def explode(_symbol):
        raise AssertionError("analyze_ticker should not be called when quote is unavailable")

    monkeypatch.setattr("moneybot.services.market_data.analyze_ticker", explode)

    signal = svc.get_signal("LCDI")

    assert signal["action"] == "HOLD"
    assert signal["quote_data_available"] is False
    assert signal["diagnostics"]["error"] == "quote_unavailable"
    assert "Signal skipped because quote data was unavailable." in signal["reasons"]

def test_get_market_indices_uses_quote_when_history_rate_limited(monkeypatch):
    svc = MarketDataService()

    class DummyTicker:
        def history(self, period, interval):
            raise Exception("Too Many Requests. Rate limited. Try after a while.")

    monkeypatch.setattr("moneybot.services.market_data.yf.Ticker", lambda _symbol: DummyTicker())
    monkeypatch.setattr(
        svc,
        "get_quote",
        lambda _symbol: {"price": 410.5, "change_percent": 0.8, "quote_source": "finnhub"},
    )

    data = svc.get_market_indices()

    assert len(data) == 5
    assert all(item["price"] == 410.5 for item in data)
    assert all(item["change_percent"] == 0.8 for item in data)
    assert all(item["quote_source"] == "finnhub" for item in data)
    assert all(len(item["series"]) == 15 for item in data)



def test_get_quote_uses_massive_primary_when_configured(monkeypatch):
    svc = MarketDataService()

    monkeypatch.setenv("MASSIVE_API_KEY", "massive-key")
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("FINNHUB_TOKEN", raising=False)
    monkeypatch.delenv("X_FINNHUB_TOKEN", raising=False)

    captured = {}

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ticker": {"day": {"c": 456.0}, "prevDay": {"c": 450.0}}}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params or {}
        return DummyResponse()

    monkeypatch.setattr("moneybot.services.market_data.requests.get", fake_get)

    quote = svc.get_quote("MSFT")

    assert quote["quote_source"] == "massive"
    assert quote["price"] == 456.0
    assert round(quote["change_percent"], 2) == 1.33
    assert captured["url"].startswith("https://api.massive.com/v2/snapshot")
    assert captured["params"]["apiKey"] == "massive-key"


def test_get_quote_falls_back_to_finnhub_when_massive_unavailable(monkeypatch):
    svc = MarketDataService()

    monkeypatch.setenv("MASSIVE_API_KEY", "massive-key")
    monkeypatch.setenv("FINNHUB_API_KEY", "finnhub-key")

    class MassiveFailResponse:
        def raise_for_status(self):
            raise Exception("403 Forbidden")

        def json(self):
            return {}

    class FinnhubOkResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"c": 150.0, "dp": 1.0, "pc": 148.5}

    def fake_get(url, params=None, headers=None, timeout=None):
        if "api.massive.com" in url:
            return MassiveFailResponse()
        return FinnhubOkResponse()

    monkeypatch.setattr("moneybot.services.market_data.requests.get", fake_get)

    quote = svc.get_quote("AAPL")


def test_get_stable_watchlist_enriches_with_quote_data(monkeypatch):
    svc = MarketDataService()

    def fake_quote(symbol):
        return {
            "symbol": symbol,
            "price": 501.25,
            "change_percent": 0.75,
            "live_data_available": True,
            "quote_source": "test",
        }

    monkeypatch.setattr(svc, "get_quote", fake_quote)

    stable = svc.get_stable_watchlist()

    assert stable[0]["price"] == 501.25
    assert stable[0]["quote_source"] == "test"
    assert stable[0]["live_data_available"] is True


def test_get_wells_picks_enriches_stock_quotes(monkeypatch):
    svc = MarketDataService()

    def fake_quote(symbol):
        return {
            "symbol": symbol,
            "price": 123.0,
            "change_percent": 0.25,
            "live_data_available": True,
            "quote_source": "test",
        }

    monkeypatch.setattr(svc, "get_quote", fake_quote)

    wells = svc.get_wells_picks()

    first_stock = wells[0]["stocks"][0]
    assert first_stock["price"] == 123.0
    assert first_stock["quote_source"] == "test"
    assert first_stock["live_data_available"] is True


def test_get_company_snapshot_skips_placeholder_news(monkeypatch):
    svc = MarketDataService()

    class DummyTicker:
        info = {"longName": "Lucid Group, Inc.", "longBusinessSummary": "EV maker."}
        news = [
            {"title": "Untitled", "publisher": "Unknown source", "link": "https://example.com/a"},
            {"title": "Valid headline", "publisher": "Reuters", "link": "https://example.com/b"},
            {"title": "", "publisher": "Bloomberg", "link": "https://example.com/c"},
        ]

    monkeypatch.setattr("moneybot.services.market_data.yf.Ticker", lambda _symbol: DummyTicker())

    snapshot = svc.get_company_snapshot("LCID")

    assert snapshot["company_name"] == "Lucid Group, Inc."
    assert snapshot["latest_news"] == [
        {
            "title": "Valid headline",
            "publisher": "Reuters",
            "link": "https://example.com/b",
        }
    ]
