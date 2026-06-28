from datetime import datetime, timedelta, timezone

from moneybot.services.live_market import ControlledTriggerEngine, LiveQuoteResolver, sse_encode
from moneybot.services.market_stream import InMemoryMarketStreamState, StreamEvent

NOW = datetime(2026, 6, 8, 14, 30, tzinfo=timezone.utc)


def _trade(*, at=NOW, price=201.25):
    return StreamEvent(
        event_type="T", symbol="AAPL", event_timestamp=at, received_timestamp=at,
        sequence_number=42, provider_event_id="trade-42", payload={"price": price}, quality_flags=(),
    )


def _rest(price=199.5):
    return {
        "symbol": "AAPL", "price": price, "event_timestamp": NOW.isoformat(),
        "received_timestamp": NOW.isoformat(), "age_ms": 0, "market_session": "regular",
        "source": "massive", "source_mode": "rest", "is_stale": False,
    }


def test_live_quote_resolver_prefers_fresh_stream_state():
    state = InMemoryMarketStreamState(clock=lambda: NOW.timestamp())
    state.set_latest(_trade(), ttl_seconds=120)
    resolver = LiveQuoteResolver(state=state, rest_quote=lambda _symbol: _rest(), clock=lambda: NOW)

    quote = resolver.resolve("aapl")

    assert quote.price == 201.25
    assert quote.source_mode == "websocket"
    assert quote.is_stale is False
    assert quote.event_id == "AAPL:T:42"


def test_live_quote_resolver_uses_rest_when_stream_is_stale():
    state = InMemoryMarketStreamState(clock=lambda: NOW.timestamp())
    state.set_latest(_trade(at=NOW - timedelta(minutes=5)), ttl_seconds=120, stale=True)
    resolver = LiveQuoteResolver(state=state, rest_quote=lambda _symbol: _rest(), clock=lambda: NOW)

    quote = resolver.resolve("AAPL")

    assert quote.price == 199.5
    assert quote.source_mode == "rest"
    assert quote.is_degraded is True
    assert "stream_stale_rest_fallback" in quote.quality_flags


def test_controlled_trigger_debounces_then_deduplicates_recommendation_state():
    now = [1000.0]
    engine = ControlledTriggerEngine(debounce_seconds=5, cooldown_seconds=10, clock=lambda: now[0])
    args = dict(user_id=1, symbol="AAPL", event_type="AM", price=200, market_session="regular", after_hours_allowed=False, recommendation_state="BUY")

    assert engine.evaluate(**args)["reason"] == "debouncing"
    now[0] += 5
    assert engine.evaluate(**args)["fire"] is True
    now[0] += 11
    assert engine.evaluate(**args)["reason"] == "debouncing"
    now[0] += 5
    assert engine.evaluate(**args)["reason"] == "duplicate"
    assert engine.snapshot()["fired"] == 1


def test_controlled_trigger_hysteresis_after_hours_cooldown_and_emergency_switch():
    now = [1000.0]
    engine = ControlledTriggerEngine(debounce_seconds=0, cooldown_seconds=60, hysteresis_percent=1, clock=lambda: now[0])
    base = dict(user_id=2, symbol="MSFT", event_type="T", market_session="regular", after_hours_allowed=True, recommendation_state="BUY", price_threshold=100)
    assert engine.evaluate(price=98, **base)["fire"] is False
    assert engine.evaluate(price=101.1, **base)["reason"] == "debouncing"
    assert engine.evaluate(price=101.1, **base)["fire"] is True
    assert engine.evaluate(price=98, **base)["fire"] is False
    assert engine.evaluate(price=101.1, **base)["reason"] == "debouncing"
    assert engine.evaluate(price=101.1, **base)["reason"] == "cooldown"
    assert engine.evaluate(user_id=2, symbol="MSFT", event_type="AM", price=101, market_session="after", after_hours_allowed=False)["reason"] == "after_hours_disabled"

    disabled = ControlledTriggerEngine(enabled=False)
    assert disabled.evaluate(user_id=1, symbol="AAPL", event_type="AM", price=1, market_session="regular", after_hours_allowed=True)["reason"] == "emergency_disabled"


def test_sse_encoding_sets_event_id_retry_and_compact_json():
    payload = sse_encode(event="quotes", event_id="AAPL:T:42", retry_ms=3000, data={"symbol": "AAPL", "price": 201.25})
    assert payload.startswith("id: AAPL:T:42\nretry: 3000\nevent: quotes\n")
    assert 'data: {"price":201.25,"symbol":"AAPL"}' in payload
    assert payload.endswith("\n\n")
