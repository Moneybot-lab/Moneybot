"""C3 synthetic price provenance, session and cache regressions; no transport I/O."""
from datetime import datetime, timedelta, timezone
import socket

import pandas as pd
import pytest

from moneybot.services.market_data import MarketDataService
from moneybot.services.market_data_providers import (
    MassiveRestClient, ProviderResponseError, normalized_fallback_quote, quote_freshness,
)

NOW = datetime(2026, 6, 8, 14, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise AssertionError("Only injected synthetic transports are authorized")
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    for name in ("MASSIVE_API_KEY", "POLYGON_API_KEY", "FINNHUB_API_KEY", "FINNHUB_TOKEN",
                 "X_FINNHUB_TOKEN", "TWELVE_DATA_API_KEY", "TWELVEDATA_API_KEY"):
        monkeypatch.delenv(name, raising=False)


class Response:
    status_code = 200
    headers = {}

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def snapshot(price=100, *, at=NOW, **extra):
    trade = {"p": price}
    if at is not None:
        trade["t"] = int(at.timestamp() * 1000)
    return {"ticker": {"lastTrade": trade, "updated": int(NOW.timestamp() * 1000), **extra}}


def provider(transport, **kwargs):
    return MassiveRestClient(api_key="synthetic", http_get=transport, retries=0,
                             sleep=lambda _: None, **kwargs)


@pytest.mark.parametrize("value", [100, 0.01])
def test_strictly_positive_price_keeps_trade_provenance(value):
    quote = provider(lambda *_a, **_k: Response(snapshot(value)), clock=lambda: NOW).get_quote("ACTIVE").data
    assert quote.price == value and quote.price_source == "last_trade"
    assert quote.event_timestamp == NOW and quote.is_stale is False


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), float("-inf"), True, 10**400])
def test_invalid_stock_prices_cannot_be_normalized_as_live(value):
    client = provider(lambda *_a, **_k: Response(snapshot(value)), clock=lambda: NOW)
    with pytest.raises(ProviderResponseError, match="usable positive price"):
        client.get_quote("MHVYF")
    fallback = normalized_fallback_quote(symbol="MHVYF", price=value, change_percent=1,
                                         source="synthetic", event_timestamp=NOW, received_timestamp=NOW)
    assert fallback["price"] is None and fallback["is_stale"] is True


@pytest.mark.parametrize("kind", ["trade", "quote", "minute"])
def test_selected_price_uses_only_its_own_timestamp(kind):
    old = NOW - timedelta(minutes=5)
    ticker = {"updated": int(NOW.timestamp() * 1000)}
    timestamp = int(old.timestamp() * 1000)
    ticker.update({
        "trade": {"lastTrade": {"p": 100, "t": timestamp}},
        "quote": {"lastQuote": {"p": 99, "P": 101, "t": timestamp}},
        "minute": {"min": {"c": 100, "t": timestamp}},
    }[kind])
    quote = provider(lambda *_a, **_k: Response({"ticker": ticker}), clock=lambda: NOW).get_quote("THIN").data
    assert quote.event_timestamp == old and quote.age_ms == 300_000
    assert quote.is_stale and "stale_price_fallback" in quote.quality_flags


def test_undated_trade_preserves_precedence_but_never_borrows_quote_time():
    payload = snapshot(at=None, lastQuote={"p": 101, "P": 103, "t": int(NOW.timestamp() * 1000)})
    quote = provider(lambda *_a, **_k: Response(payload), clock=lambda: NOW).get_quote("THIN").data
    assert quote.price == 100 and quote.price_source == "last_trade"
    assert quote.event_timestamp is None and quote.age_ms is None and quote.is_stale
    assert "freshness_unknown" in quote.quality_flags


def test_fresh_nbbo_retains_its_price_time_when_old_trade_is_ineligible():
    payload = snapshot(at=NOW - timedelta(seconds=16),
                       lastQuote={"p": 101, "P": 103, "t": int((NOW - timedelta(seconds=1)).timestamp() * 1000)})
    quote = provider(lambda *_a, **_k: Response(payload), clock=lambda: NOW).get_quote("ACTIVE").data
    assert quote.price == 102 and quote.price_source == "nbbo_midpoint"
    assert quote.event_timestamp == NOW - timedelta(seconds=1) and quote.age_ms == 1_000
    assert not quote.is_stale


@pytest.mark.parametrize("bid,ask", [(0, 100), (-1, 100), (float("nan"), 100), (100, float("inf")), (101, 100)])
def test_invalid_or_crossed_nbbo_cannot_supply_live_stock_price(bid, ask):
    payload = {"ticker": {"lastQuote": {"p": bid, "P": ask, "t": int(NOW.timestamp() * 1000)}}}
    client = provider(lambda *_a, **_k: Response(payload), clock=lambda: NOW)
    with pytest.raises(ProviderResponseError):
        client.get_quote("THIN")


def test_large_finite_nbbo_cannot_overflow_into_infinite_midpoint():
    payload = {"ticker": {"lastQuote": {"p": 1.7e308, "P": 1.7e308, "t": int(NOW.timestamp() * 1000)}}}
    quote = provider(lambda *_a, **_k: Response(payload), clock=lambda: NOW).get_quote("ACTIVE").data
    assert quote.price == 1.7e308 and not quote.is_stale


@pytest.mark.parametrize("now,session,threshold", [
    (datetime(2026, 6, 8, 12, tzinfo=timezone.utc), "pre", 60),
    (NOW, "regular", 15),
    (datetime(2026, 6, 8, 21, tzinfo=timezone.utc), "after", 60),
    (datetime(2026, 6, 8, 7, tzinfo=timezone.utc), "closed", 120),
])
def test_price_freshness_in_each_market_session(now, session, threshold):
    # Closed threshold remains 24h, but same-context/date is required.
    _, actual_session, stale, _ = quote_freshness(now - timedelta(seconds=threshold), now)
    assert actual_session == session and not stale
    _, _, stale, _ = quote_freshness(now - timedelta(seconds=threshold + 1), now)
    assert stale is (session != "closed")


@pytest.mark.parametrize("now,event", [
    (datetime(2026, 6, 8, 13, 30, tzinfo=timezone.utc), datetime(2026, 6, 8, 13, 29, 59, tzinfo=timezone.utc)),
    (datetime(2026, 6, 8, 20, tzinfo=timezone.utc), datetime(2026, 6, 8, 19, 59, 59, tzinfo=timezone.utc)),
    (datetime(2026, 6, 9, 2, tzinfo=timezone.utc), datetime(2026, 6, 8, 2, tzinfo=timezone.utc)),
])
def test_new_context_cannot_make_old_session_price_current(now, event):
    _, _, stale, flags = quote_freshness(event, now)
    assert stale and "market_session_mismatch" in flags


@pytest.mark.parametrize("event", [None, NOW.replace(tzinfo=None), NOW + timedelta(seconds=1)])
def test_unknown_or_future_event_timestamp_cannot_establish_freshness(event):
    age_ms, _, stale, flags = quote_freshness(event, NOW)
    assert stale and "stale" in flags
    assert (age_ms is None) is (event is None or event.tzinfo is None)


@pytest.mark.parametrize("now", [NOW, datetime(2026, 6, 8, 7, tzinfo=timezone.utc)])
def test_daily_close_is_last_known_even_with_recent_ticker_update(now):
    payload = {"ticker": {"day": {"c": 100}, "updated": int(now.timestamp() * 1000)}}
    quote = provider(lambda *_a, **_k: Response(payload), clock=lambda: now).get_quote("THIN").data
    assert quote.price == 100 and quote.price_source == "day_close"
    assert quote.event_timestamp is None and quote.is_stale
    assert "daily_close_not_realtime" in quote.quality_flags


def test_provider_raw_cache_reages_without_refetch_or_rewriting_receipt(monkeypatch):
    elapsed, calls = [0.0], []
    monkeypatch.setattr("moneybot.services.market_data_providers.time.monotonic", lambda: elapsed[0])
    client = provider(lambda *_a, **_k: calls.append(elapsed[0]) or Response(snapshot()),
                      clock=lambda: NOW + timedelta(seconds=elapsed[0]), quote_cache_seconds=20)
    first = client.get_quote("ACTIVE").data
    elapsed[0] = 16
    second = client.get_quote("ACTIVE")
    assert calls == [0] and second.cache_status == "hit"
    assert first.age_ms == 0 and not first.is_stale
    assert second.data.age_ms == 16_000 and second.data.is_stale
    assert second.data.received_timestamp == first.received_timestamp == NOW


def test_outer_cache_is_independent_and_immutable_with_normal_expiry(monkeypatch):
    elapsed, calls = [0.0], []
    monkeypatch.setattr("moneybot.services.market_data.time.time", lambda: elapsed[0])
    service = MarketDataService(clock=lambda: NOW + timedelta(seconds=elapsed[0]))
    client = provider(lambda *_a, **_k: calls.append(elapsed[0]) or Response(snapshot(at=NOW + timedelta(seconds=elapsed[0]))),
                      clock=service.clock, quote_cache_seconds=0)
    monkeypatch.setattr(service, "_massive_client", lambda: client)
    first = service.get_quote("ACTIVE")
    first["quality_flags"].append("client_mutation")
    first["diagnostics"]["provider"] = "wrong"
    first["price"] = -100
    elapsed[0] = 15.001
    second = service.get_quote("ACTIVE")
    assert calls == [0] and second["age_ms"] == 15_001 and second["is_stale"]
    assert second["price"] == 100 and second["diagnostics"]["provider"] == "massive"
    assert "client_mutation" not in second["quality_flags"]
    assert service.quote_cache._store["ACTIVE"].value["age_ms"] == 0
    elapsed[0] = 20.001
    assert service.get_quote("ACTIVE")["is_stale"] is False
    assert calls == [0, 20.001]


def test_unusable_snapshot_negative_cache_expires_and_valid_price_recovers(monkeypatch):
    elapsed, calls = [0.0], []
    monkeypatch.setattr("moneybot.services.market_data_providers.time.monotonic", lambda: elapsed[0])
    def transport(*_args, **_kwargs):
        calls.append(elapsed[0])
        return Response({"ticker": {}} if elapsed[0] < 5 else snapshot(at=NOW + timedelta(seconds=elapsed[0])))
    client = provider(transport, clock=lambda: NOW + timedelta(seconds=elapsed[0]), quote_cache_seconds=100)
    for moment in (0, 1, 2, 4.999):
        elapsed[0] = moment
        with pytest.raises(ProviderResponseError):
            client.get_quote("MHVYF")
    assert calls == [0]
    elapsed[0] = 5
    quote = client.get_quote("MHVYF").data
    assert calls == [0, 5] and quote.price == 100 and not quote.is_stale


def test_yahoo_daily_history_cannot_borrow_recent_market_time(monkeypatch):
    class SyntheticTicker:
        info = {"regularMarketTime": NOW.timestamp(), "regularMarketPreviousClose": 99}
        def history(self, **_kwargs):
            return pd.DataFrame({"Close": [99, 100]})
    service = MarketDataService(clock=lambda: NOW)
    monkeypatch.setattr("moneybot.services.market_data.yf.Ticker", lambda _: SyntheticTicker())
    quote = service.get_quote("THIN")
    assert quote["price"] == 100 and quote["price_source"] == "day_close"
    assert quote["event_timestamp"] is None and quote["is_stale"]
    assert not quote["live_data_available"] and "daily_close_not_realtime" in quote["quality_flags"]
