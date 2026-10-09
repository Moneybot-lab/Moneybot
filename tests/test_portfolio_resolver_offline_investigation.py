"""Offline reproductions retained as acceptance tests for approved C3/C4 repairs.

The frozen investigation report records the original failing behavior. These
assertions now verify the repaired boundaries or intentionally stateless policy.
All provider transports are synthetic, and actual socket access is forbidden.
"""
from datetime import datetime, timedelta, timezone
import socket

import pytest

from moneybot.services.live_market import LiveQuoteResolver
from moneybot.services.market_data import MarketDataService
from moneybot.services.market_data_providers import (
    MassiveRestClient,
    ProviderForbiddenError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from moneybot.services.market_stream import InMemoryMarketStreamState, StreamEvent


NOW = datetime(2026, 6, 8, 14, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def forbid_actual_network(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise AssertionError("Actual network access is forbidden in offline investigation")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    for name in (
        "MASSIVE_API_KEY", "POLYGON_API_KEY", "FINNHUB_API_KEY", "FINNHUB_TOKEN",
        "X_FINNHUB_TOKEN", "TWELVE_DATA_API_KEY", "TWELVEDATA_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def event(kind="T", *, symbol="THIN", at=NOW, received=NOW, seq=1, price=100.0, source_mode="websocket", **payload):
    values = {"price": price} if kind == "T" else (
        {"close": price} if kind in {"A", "AM"} else {"midpoint": price}
    )
    values.update(payload)
    return StreamEvent(
        event_type=kind, symbol=symbol, event_timestamp=at,
        received_timestamp=received, sequence_number=seq,
        provider_event_id=f"synthetic-{kind}-{seq}", payload=values, source_mode=source_mode,
    )


def rest(*, price=99.0, at=NOW, stale=False):
    return {
        "price": price, "event_timestamp": at.isoformat(),
        "received_timestamp": NOW.isoformat(), "age_ms": 0,
        "market_session": "regular", "source": "massive",
        "source_mode": "rest", "is_stale": stale,
    }


def state():
    return InMemoryMarketStreamState(clock=lambda: NOW.timestamp())


def test_recent_receipt_does_not_refresh_old_event():
    saved = state()
    saved.set_latest(event(at=NOW - timedelta(seconds=16), received=NOW), ttl_seconds=120)
    calls = []
    resolver = LiveQuoteResolver(
        state=saved, rest_quote=lambda symbol: calls.append(symbol) or rest(), clock=lambda: NOW,
    )
    stream = resolver._freshest_stream("THIN")
    assert stream.received_timestamp == NOW.isoformat()
    assert stream.age_ms == 16_000 and stream.is_stale is True
    assert resolver.resolve("THIN").source_mode == "rest"
    assert calls == ["THIN"]


@pytest.mark.parametrize("age_ms,uses_rest", [(15_000, False), (15_001, True)])
def test_regular_session_cutover_uses_event_age_at_15_seconds(age_ms, uses_rest):
    saved = state()
    saved.set_latest(event(at=NOW - timedelta(milliseconds=age_ms)), ttl_seconds=120)
    calls = []
    resolver = LiveQuoteResolver(
        state=saved, rest_quote=lambda symbol: calls.append(symbol) or rest(), clock=lambda: NOW,
    )
    quote = resolver.resolve("THIN")
    assert bool(calls) is uses_rest
    assert (quote.event_type is None) is uses_rest


def test_event_time_wins_over_newer_receipt_and_price_channel_priority():
    saved = state()
    saved.set_latest(event("T", at=NOW - timedelta(seconds=1), received=NOW - timedelta(seconds=1)), ttl_seconds=120)
    saved.set_latest(event("Q", at=NOW - timedelta(seconds=2), received=NOW, price=101), ttl_seconds=120)
    resolver = LiveQuoteResolver(state=saved, rest_quote=lambda _: pytest.fail("No REST expected"), clock=lambda: NOW)
    assert resolver.resolve("THIN").event_type == "T"
    saved.set_latest(event("Q", at=NOW, received=NOW, seq=2, price=102), ttl_seconds=120)
    assert resolver.resolve("THIN").event_type == "Q"
    assert resolver.resolve("THIN").price == 102


def test_newest_marked_stale_candidate_masks_older_healthy_candidate():
    saved = state()
    saved.set_latest(event("T", at=NOW, price=100), ttl_seconds=120, stale=True)
    saved.set_latest(event("Q", at=NOW - timedelta(seconds=1), price=101), ttl_seconds=120)
    resolver = LiveQuoteResolver(state=saved, rest_quote=lambda _: rest(price=98), clock=lambda: NOW)
    quote = resolver.resolve("THIN")
    assert quote.price == 101 and quote.source_mode == "websocket"
    assert quote.is_degraded is False
    assert "stream_stale_rest_fallback" not in quote.quality_flags


def test_equal_timestamp_recovery_quote_can_be_masked_by_stale_trade():
    saved = state()
    saved.set_latest(event("T", at=NOW), ttl_seconds=120)
    saved.mark_symbols_stale(["THIN"], reason="sequence_gap", ttl_seconds=300)
    saved.set_latest(event("Q", at=NOW, price=101, seq=2), ttl_seconds=120)
    resolver = LiveQuoteResolver(state=saved, rest_quote=lambda _: rest(price=98), clock=lambda: NOW)
    assert saved.get_latest("THIN", "Q")["is_stale"] is False
    assert resolver._freshest_stream("THIN").event_type == "Q"
    assert resolver.resolve("THIN").source_mode == "websocket"


def test_trade_only_rest_recovery_price_is_not_used_as_quote_midpoint():
    saved = state()
    saved.set_latest(event("Q", price=None, bid=None, ask=None, source_mode="rest", recovery_price=123,
                           recovery_price_source="last_trade", recovery_price_timestamp=NOW.isoformat()), ttl_seconds=120)
    resolver = LiveQuoteResolver(state=saved, rest_quote=lambda _: pytest.fail("Valid recovery must not refetch"), clock=lambda: NOW)
    assert resolver._freshest_stream("THIN").price == 123
    quote = resolver.resolve("THIN")
    assert quote.price == 123 and quote.price_source == "last_trade"
    assert quote.midpoint is None and quote.bid is None and quote.ask is None
    assert "stream_stale_rest_fallback" not in quote.quality_flags


def test_rest_freshness_flags_are_trusted_without_recomputing_event_age():
    resolver = LiveQuoteResolver(
        state=state(), rest_quote=lambda _: rest(at=NOW - timedelta(hours=1), stale=False), clock=lambda: NOW,
    )
    quote = resolver.resolve("THIN")
    assert quote.event_timestamp == (NOW - timedelta(hours=1)).isoformat()
    assert quote.age_ms == 3_600_000 and quote.is_stale is True


def test_fallback_to_delayed_healthy_stream_can_regress_price_and_event_timestamp():
    current = [NOW]
    saved = InMemoryMarketStreamState(clock=lambda: current[0].timestamp())
    saved.set_latest(event(at=NOW - timedelta(seconds=30), price=90), ttl_seconds=120)
    resolver = LiveQuoteResolver(state=saved, rest_quote=lambda _: rest(price=110), clock=lambda: current[0])
    fallback = resolver.resolve("THIN")
    assert fallback.price == 110 and fallback.event_timestamp == NOW.isoformat()
    current[0] += timedelta(seconds=1)
    saved.set_latest(event("Q", at=NOW - timedelta(seconds=1), received=current[0], price=95), ttl_seconds=120)
    recovered = resolver.resolve("THIN")
    assert recovered.is_stale is False and recovered.source_mode == "websocket"
    assert recovered.price == 95
    assert datetime.fromisoformat(recovered.event_timestamp) < datetime.fromisoformat(fallback.event_timestamp)


@pytest.mark.parametrize("current,session,threshold_ms", [
    (datetime(2026, 6, 8, 12, tzinfo=timezone.utc), "pre", 60_000),
    (datetime(2026, 6, 8, 21, tzinfo=timezone.utc), "after", 60_000),
    (datetime(2026, 6, 9, 2, tzinfo=timezone.utc), "closed", 86_400_000),
])
def test_pre_after_and_closed_thresholds_use_event_session(current, session, threshold_ms):
    saved = InMemoryMarketStreamState(clock=lambda: current.timestamp())
    calls = []
    resolver = LiveQuoteResolver(state=saved, rest_quote=lambda symbol: calls.append(symbol) or rest(), clock=lambda: current)
    # Closed-session values from a previous exchange date are no longer eligible
    # merely because their nominal 24h TTL has not elapsed.
    fresh_age = threshold_ms if session != "closed" else 1000
    for age_ms, expected_stale in ((fresh_age, False), (threshold_ms + 1, True)):
        saved.set_latest(event(at=current - timedelta(milliseconds=age_ms)), ttl_seconds=120)
        stream = resolver._freshest_stream("THIN")
        assert stream.market_session == session
        assert stream.age_ms == age_ms and stream.is_stale is expected_stale
        assert (resolver.resolve("THIN").event_type is None) is expected_stale
    assert calls == ["THIN"]


@pytest.mark.parametrize("current,event_at,current_session,event_session,expected_stale", [
    (
        datetime(2026, 6, 8, 13, 30, tzinfo=timezone.utc),
        datetime(2026, 6, 8, 13, 29, 40, tzinfo=timezone.utc),
        "regular", "pre", True,
    ),
    (
        datetime(2026, 6, 8, 20, tzinfo=timezone.utc),
        datetime(2026, 6, 8, 19, 59, 40, tzinfo=timezone.utc),
        "after", "regular", True,
    ),
])
def test_session_boundary_uses_event_session_not_current_session(current, event_at, current_session, event_session, expected_stale):
    saved = InMemoryMarketStreamState(clock=lambda: current.timestamp())
    saved.set_latest(event(at=event_at, received=current), ttl_seconds=120)
    resolver = LiveQuoteResolver(state=saved, rest_quote=lambda _: rest(), clock=lambda: current)
    assert resolver.calendar.session_at(current) == current_session
    stream = resolver._freshest_stream("THIN")
    assert stream.market_session == current_session
    assert stream.age_ms == 20_000 and stream.is_stale is expected_stale
    assert "market_session_mismatch" in stream.quality_flags


@pytest.mark.parametrize("price", [0, -1])
def test_stream_accepts_finite_nonpositive_price_as_live(price):
    saved = state()
    saved.set_latest(event(price=price), ttl_seconds=120)
    resolver = LiveQuoteResolver(state=saved, rest_quote=lambda _: rest(price=99), clock=lambda: NOW)
    quote = resolver.resolve("THIN")
    assert quote.price == 99 and quote.source_mode == "rest" and quote.is_stale is False


class Response:
    def __init__(self, payload=None, *, status=200):
        self.payload = payload or {}
        self.status_code = status
        self.headers = {}

    def json(self):
        return self.payload

    def raise_for_status(self):
        return None


def snapshot(*, trade_at=NOW, quote_at=NOW, updated_at=NOW, trade_price=100):
    return {"ticker": {
        "lastTrade": {"p": trade_price, "t": int(trade_at.timestamp() * 1000)},
        "lastQuote": {"p": 101, "P": 103, "t": int(quote_at.timestamp() * 1000)},
        "updated": int(updated_at.timestamp() * 1000), "prevDay": {"c": 99},
    }}


def client(transport, *, clock=lambda: NOW, **kwargs):
    return MassiveRestClient(
        api_key="synthetic-not-a-secret", http_get=transport, clock=clock,
        sleep=lambda _: None, **kwargs,
    )


def test_snapshot_trade_precedence_differs_from_stream_freshest_event_selection():
    payload = snapshot(trade_at=NOW - timedelta(seconds=10), quote_at=NOW)
    quote = client(lambda *_a, **_k: Response(payload)).get_quote("THIN").data
    assert quote.price == 100 and quote.price_source == "last_trade"
    assert quote.midpoint == 102
    assert quote.event_timestamp == NOW - timedelta(seconds=10) and quote.age_ms == 10_000


def test_recent_ticker_updated_cannot_mark_stale_selected_price_as_fresh():
    old = NOW - timedelta(minutes=5)
    payload = snapshot(trade_at=old, quote_at=old, updated_at=NOW)
    quote = client(lambda *_a, **_k: Response(payload)).get_quote("THIN").data
    assert quote.price == 100 and quote.price_source == "last_trade"
    assert "stale_price_fallback" in quote.quality_flags
    assert quote.event_timestamp == old and quote.is_stale is True
    compatible = MarketDataService._compatible_quote_payload(quote)
    assert compatible["live_data_available"] is False


def test_selected_trade_with_missing_timestamp_has_unknown_freshness():
    payload = snapshot()
    payload["ticker"]["lastTrade"].pop("t")
    quote = client(lambda *_a, **_k: Response(payload)).get_quote("THIN").data
    assert quote.price_source == "last_trade" and quote.price == 100
    assert quote.event_timestamp is None and quote.age_ms is None and quote.is_stale is True
    assert "freshness_unknown" in quote.quality_flags


def test_outer_service_cache_recomputes_freshness_after_threshold(monkeypatch):
    elapsed = [0.0]
    monkeypatch.setattr("moneybot.services.market_data.time.time", lambda: 1_000 + elapsed[0])
    calls = []
    provider = client(
        lambda *_a, **_k: calls.append(elapsed[0]) or Response(snapshot()),
        clock=lambda: NOW + timedelta(seconds=elapsed[0]), quote_cache_seconds=0,
    )
    svc = MarketDataService(clock=lambda: NOW + timedelta(seconds=elapsed[0]))
    monkeypatch.setattr(svc, "_massive_client", lambda: provider)
    first = svc.get_quote("THIN")
    elapsed[0] = 19
    second = svc.get_quote("THIN")
    resolver = LiveQuoteResolver(state=state(), rest_quote=svc.get_quote, clock=lambda: NOW + timedelta(seconds=19))
    assert second is not first and calls == [0]
    assert second["age_ms"] == 19_000 and second["is_stale"] is True
    assert first["age_ms"] == 0 and first["is_stale"] is False
    assert resolver.resolve("THIN").is_stale is True
    elapsed[0] = 20.001
    assert svc.get_quote("THIN")["is_stale"] is True
    assert calls == [0, 20.001]


def test_transport_retries_are_bounded_and_errors_negative_cached(monkeypatch):
    elapsed = [0.0]
    monkeypatch.setattr("moneybot.services.market_data_providers.time.monotonic", lambda: elapsed[0])
    calls = []

    def transport(*_args, **_kwargs):
        calls.append(elapsed[0])
        raise RuntimeError("synthetic transport outage")

    provider = client(transport, retries=2, negative_cache_seconds=30)
    with pytest.raises(ProviderUnavailableError):
        provider.get_quote("THIN")
    elapsed[0] = 29
    with pytest.raises(ProviderUnavailableError):
        provider.get_quote("THIN")
    assert calls == [0, 0, 0]
    elapsed[0] = 30
    with pytest.raises(ProviderUnavailableError):
        provider.get_quote("THIN")
    assert calls == [0, 0, 0, 30, 30, 30]


def test_forbidden_error_is_not_retried_and_is_negative_cached(monkeypatch):
    monkeypatch.setattr("moneybot.services.market_data_providers.time.monotonic", lambda: 0)
    calls = []
    provider = client(lambda *_a, **_k: calls.append(1) or Response(status=403), retries=2)
    for _ in range(5):
        with pytest.raises(ProviderForbiddenError):
            provider.get_quote("THIN")
    assert calls == [1]


def test_unusable_success_snapshot_has_short_bounded_negative_cache(monkeypatch):
    elapsed = [0.0]
    monkeypatch.setattr("moneybot.services.market_data_providers.time.monotonic", lambda: elapsed[0])
    calls = []
    provider = client(lambda *_a, **_k: calls.append(elapsed[0]) or Response({"ticker": {}}), retries=2)
    for moment in (0, 1, 2):
        elapsed[0] = moment
        with pytest.raises(ProviderResponseError):
            provider.get_quote("THIN")
    assert calls == [0]
    elapsed[0] = 5
    with pytest.raises(ProviderResponseError):
        provider.get_quote("THIN")
    assert calls == [0, 5]


def test_repeated_thin_resolution_shares_outer_cache_while_active_avoids_rest(monkeypatch):
    elapsed = [0]
    monkeypatch.setattr("moneybot.services.market_data.time.time", lambda: 1_000 + elapsed[0])
    calls = []
    provider = client(
        lambda *_a, **_k: calls.append(elapsed[0]) or Response(snapshot(trade_at=NOW - timedelta(minutes=5), quote_at=NOW - timedelta(minutes=5), updated_at=NOW - timedelta(minutes=5))),
        clock=lambda: NOW + timedelta(seconds=elapsed[0]), quote_cache_seconds=0,
    )
    svc = MarketDataService(clock=lambda: NOW + timedelta(seconds=elapsed[0]))
    monkeypatch.setattr(svc, "_massive_client", lambda: provider)
    saved = InMemoryMarketStreamState(clock=lambda: NOW.timestamp() + elapsed[0])
    rest_calls = []
    resolver = LiveQuoteResolver(
        state=saved, rest_quote=lambda symbol: rest_calls.append(symbol) or svc.get_quote(symbol),
        clock=lambda: NOW + timedelta(seconds=elapsed[0]),
    )
    for moment in range(61):
        elapsed[0] = moment
        saved.set_latest(event(symbol="ACTIVE", at=NOW + timedelta(seconds=moment), seq=moment + 1), ttl_seconds=120)
        assert resolver.resolve("ACTIVE").source_mode == "websocket"
        assert resolver.resolve("THIN").source_mode == "rest"
    assert rest_calls == ["THIN"] * 61
    assert calls == [0, 21, 42]


def test_independent_service_instances_do_not_share_cache(monkeypatch):
    calls = []
    for _ in range(3):
        svc = MarketDataService(clock=lambda: NOW)
        provider = client(lambda *_a, **_k: calls.append(1) or Response(snapshot()), quote_cache_seconds=0)
        monkeypatch.setattr(svc, "_massive_client", lambda provider=provider: provider)
        svc.get_quote("THIN")
        svc.get_quote("THIN")
    assert calls == [1, 1, 1]


def test_full_fallback_cascade_has_bounded_application_attempts_and_outer_cache(monkeypatch):
    attempts = []

    def massive_transport(*_args, **_kwargs):
        attempts.append("massive")
        raise RuntimeError("synthetic transport outage")

    provider = client(massive_transport, retries=2)
    svc = MarketDataService(retries=2)
    monkeypatch.setattr(svc, "_massive_client", lambda: provider)
    monkeypatch.setattr(svc, "_get_massive_key", lambda: ("synthetic", "synthetic"))
    monkeypatch.setattr(svc, "_get_finnhub_key", lambda: ("synthetic", "synthetic"))
    monkeypatch.setattr(svc, "_get_twelve_data_key", lambda: ("synthetic", "synthetic"))
    monkeypatch.setattr("moneybot.services.market_data.time.sleep", lambda _: None)

    def fallback_transport(url, **_kwargs):
        attempts.append("finnhub" if "finnhub" in url else "twelve_data")
        return Response({})

    class MissingYahoo:
        @property
        def info(self):
            attempts.append("yfinance")
            raise RuntimeError("synthetic missing ticker")

    monkeypatch.setattr("moneybot.services.market_data.requests.get", fallback_transport)
    monkeypatch.setattr("moneybot.services.market_data.yf.Ticker", lambda _: MissingYahoo())
    quote = svc.get_quote("THIN")
    assert quote["price"] == "DATA_MISSING" and quote["is_stale"] is True
    assert attempts == ["massive"] * 3 + ["finnhub", "twelve_data"] + ["yfinance"] * 3
    repeated = svc.get_quote("THIN")
    assert repeated is not quote and repeated == quote
    assert len(attempts) == 8
