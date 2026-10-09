"""C4/C5 synthetic SSE ordering, delivery and verified-health regressions."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from flask import Flask, g, session

from moneybot import api
from moneybot.services.market_data_providers import ExchangeCalendar
from moneybot.services.market_stream import InMemoryMarketStreamState, STREAM_SCHEMA_VERSION, StreamEvent


NOW = datetime(2026, 10, 9, 15, 0, 0, tzinfo=timezone.utc)
CALENDAR = ExchangeCalendar()


def _quote(*, price=101.0, seconds=1, mode="websocket", event_id="AAPL:T:7", stale=False, **extra):
    return {
        "schema_version": "live-market.v1", "symbol": "AAPL", "price": price,
        "event_timestamp": (NOW - timedelta(seconds=seconds)).isoformat(),
        "received_timestamp": NOW.isoformat(), "age_ms": seconds * 1000,
        "event_id": event_id, "event_type": "T", "source": "massive",
        "source_mode": mode, "price_source": "last_trade", "market_session": "regular",
        "is_stale": stale, "is_degraded": stale, "quality_flags": [],
        "bid": None, "ask": None, "midpoint": None, **extra,
    }


def _apply(delivery, quote, *, now=NOW):
    return delivery.apply(quote, now=now, calendar=CALENDAR)


@pytest.mark.parametrize("older_mode", ["websocket", "rest"])
def test_older_observation_retains_newer_price_and_incoming_diagnostics(older_mode):
    delivery = api._SSEQuoteDelivery()
    _apply(delivery, _quote(price=102, mode="rest", seconds=1))
    outgoing, market_change = _apply(delivery, _quote(
        price=100, mode=older_mode, seconds=3, event_id="older", event_type="AM",
        quality_flags=["stream_disconnected"],
    ))
    assert outgoing["price"] == 102
    assert outgoing["event_timestamp"] == (NOW - timedelta(seconds=1)).isoformat()
    assert outgoing["age_ms"] == 1000 and outgoing["event_type"] == "T"
    assert outgoing["observed_price"] == 100 and outgoing["observed_source_mode"] == older_mode
    assert outgoing["observed_event_type"] == "AM"
    assert outgoing["event_id"] == "older"
    assert outgoing["source_mode"] == "last_known"
    assert outgoing["is_stale"] and outgoing["is_degraded"] and not market_change
    assert {"stream_disconnected", "older_observation_retained", "last_known_price"} <= set(outgoing["quality_flags"])


@pytest.mark.parametrize("mode", ["websocket", "rest"])
def test_newer_valid_observation_delivers_in_either_direction_and_price_direction(mode):
    delivery = api._SSEQuoteDelivery()
    _apply(delivery, _quote(price=102, seconds=2))
    outgoing, market_change = _apply(delivery, _quote(price=51, seconds=1, mode=mode))
    assert outgoing["price"] == 51 and outgoing["source_mode"] == mode
    assert not outgoing["is_stale"] and market_change


def test_equal_timestamp_ambiguous_changed_price_fails_closed_until_newer_event():
    delivery = api._SSEQuoteDelivery()
    _apply(delivery, _quote(price=102))
    outgoing, market_change = _apply(delivery, _quote(price=51, event_id="possible-correction"))
    assert outgoing["price"] == 102 and outgoing["is_stale"] and not market_change
    assert "unresolved_equal_timestamp_correction" in outgoing["quality_flags"]
    # A numerical split-adjusted change with a genuinely newer timestamp is
    # accepted. No invented correction flag bypasses timestamp ordering.
    outgoing, market_change = _apply(delivery, _quote(price=51, seconds=0))
    assert outgoing["price"] == 51 and market_change and not outgoing["is_stale"]


def test_equal_timestamp_same_price_source_and_quality_changes_are_deliverable():
    delivery = api._SSEQuoteDelivery()
    first, _ = _apply(delivery, _quote(mode="rest"))
    assert delivery.changed(first)
    changed, market_change = _apply(delivery, _quote(mode="websocket", quality_flags=["recovered"]))
    assert delivery.changed(changed) and not market_change
    assert changed["source_mode"] == "websocket" and not changed["is_stale"]


@pytest.mark.parametrize("timestamp", [None, "2026-10-09T14:59:59", "invalid", (NOW + timedelta(seconds=1)).isoformat()])
def test_untrusted_event_time_cannot_establish_newer_observation(timestamp):
    delivery = api._SSEQuoteDelivery()
    _apply(delivery, _quote(price=102))
    outgoing, market_change = _apply(delivery, _quote(price=105, event_timestamp=timestamp))
    assert outgoing["price"] == 102 and outgoing["source_mode"] == "last_known"
    assert "untrusted_price_timestamp" in outgoing["quality_flags"] and not market_change


@pytest.mark.parametrize("price", [None, 0, -1, float("nan"), float("inf"), True])
def test_unavailable_or_invalid_price_retains_numeric_value_with_unavailability_diagnostic(price):
    delivery = api._SSEQuoteDelivery()
    _apply(delivery, _quote(price=102))
    outgoing, market_change = _apply(delivery, _quote(price=price, seconds=0, quality_flags=["snapshot_unusable"]))
    assert outgoing["price"] == 102 and outgoing["is_stale"] and outgoing["is_degraded"]
    assert "price_unavailable" in outgoing["quality_flags"] and not market_change


def test_initial_unavailable_quote_is_delivered_without_creating_valid_ordering_memory():
    delivery = api._SSEQuoteDelivery()
    outgoing, market_change = _apply(delivery, _quote(price=None, event_timestamp=None))
    assert outgoing["price"] is None and outgoing["is_stale"] and delivery.changed(outgoing)
    assert delivery.previous == {} and not market_change
    outgoing, market_change = _apply(delivery, _quote())
    assert outgoing["price"] == 101 and not outgoing["is_stale"] and market_change


def test_initial_untrusted_timestamp_is_not_an_ordering_watermark():
    delivery = api._SSEQuoteDelivery()
    first, market_change = _apply(delivery, _quote(price=105, event_timestamp=(NOW + timedelta(hours=1)).isoformat()))
    assert first["event_timestamp"] is None and first["age_ms"] is None and not market_change
    recovered, market_change = _apply(delivery, _quote(price=101))
    assert recovered["price"] == 101 and not recovered["is_stale"] and market_change


def test_session_boundary_preserves_last_known_until_fresh_context_then_resets():
    delivery = api._SSEQuoteDelivery()
    before = datetime(2026, 10, 9, 13, 29, 59, tzinfo=timezone.utc)
    after = before + timedelta(seconds=2)
    _apply(delivery, _quote(price=102, event_timestamp=before.isoformat(), market_session="pre"), now=before)
    unavailable, _ = _apply(delivery, _quote(price=None, event_timestamp=None), now=after)
    assert unavailable["price"] == 102 and unavailable["is_stale"]
    stale, _ = _apply(delivery, _quote(price=51, event_timestamp=before.isoformat(), stale=True), now=after)
    assert stale["price"] == 102 and "session_ordering_reset" in stale["quality_flags"]
    recovered, market_change = _apply(delivery, _quote(price=51, event_timestamp=after.isoformat()), now=after)
    assert recovered["price"] == 51 and not recovered["is_stale"] and market_change
    assert recovered["market_session_context"] == "2026-10-09:regular"


def test_delivery_fingerprint_ignores_clock_age_ids_and_receipt_but_tracks_meaningful_fields():
    delivery = api._SSEQuoteDelivery()
    quote = _quote()
    assert delivery.changed(quote)
    assert not delivery.changed({**quote, "age_ms": 9999, "received_timestamp": NOW.isoformat(), "event_id": "new-id"})
    assert delivery.changed({**quote, "event_timestamp": NOW.isoformat()})  # A genuine new observation, not a clock tick.
    assert delivery.changed({**quote, "is_stale": True})
    assert delivery.changed({**quote, "is_stale": True, "quality_flags": ["stream_stale_rest_fallback"]})
    assert delivery.changed({**quote, "price": None})
    assert len(delivery.fingerprints) == 1
    assert len(delivery.fingerprints["AAPL"]) == 64


@pytest.mark.parametrize("state", ["connected", "reconnecting", "disconnected"])
def test_provider_health_requires_current_timestamped_worker_schema(state):
    app = Flask(__name__)
    with app.app_context():
        result = api._verified_provider_stream_health(now=NOW, worker={
            "schema_version": STREAM_SCHEMA_VERSION, "updated_at": (NOW - timedelta(seconds=2)).isoformat(),
            "connection_state": state,
        })
    assert result["verified"] and result["connection_state"] == state
    assert result["age_ms"] == 2000 and result["ttl_seconds"] == 30


@pytest.mark.parametrize("worker", [
    {}, [], {"connection_state": "connected"},
    {"schema_version": "unknown", "updated_at": NOW.isoformat(), "connection_state": "connected"},
    {"schema_version": STREAM_SCHEMA_VERSION, "updated_at": (NOW - timedelta(seconds=31)).isoformat(), "connection_state": "connected"},
    {"schema_version": STREAM_SCHEMA_VERSION, "updated_at": (NOW - timedelta(seconds=30)).isoformat(), "connection_state": "connected"},
    {"schema_version": STREAM_SCHEMA_VERSION, "updated_at": (NOW + timedelta(seconds=1)).isoformat(), "connection_state": "connected"},
    {"schema_version": STREAM_SCHEMA_VERSION, "updated_at": "2026-10-09T14:59:59", "connection_state": "connected"},
])
def test_unverified_or_stale_provider_health_is_unknown(worker):
    app = Flask(__name__)
    with app.app_context():
        result = api._verified_provider_stream_health(now=NOW, worker=worker)
    assert result["connection_state"] == "unknown" and not result["verified"]


class _State(InMemoryMarketStreamState):
    health_reads = 0

    def get_health(self):
        self.health_reads += 1
        return super().get_health()


@contextmanager
def _stream(monkeypatch, inputs, *, state=None, trigger=None, interval=3, clock=lambda: NOW):
    state = state or _State(clock=lambda: 0)
    quotes = iter(inputs)
    calls = []
    def resolve(symbol):
        calls.append(symbol)
        quote = next(quotes)
        if callable(quote):
            quote = quote()
        return SimpleNamespace(payload=lambda: quote)

    resolver = SimpleNamespace(clock=clock, calendar=CALENDAR, resolve=resolve)
    elapsed = [100.0]
    monkeypatch.setattr(api, "time", SimpleNamespace(time=lambda: elapsed[0], sleep=lambda _: elapsed.__setitem__(0, elapsed[0] + interval)))
    monkeypatch.setattr(api, "_symbols_for_live_user", lambda _: {"AAPL"})
    monkeypatch.setattr(api, "_decision_context_for_user", lambda _: SimpleNamespace(after_hours_alerts=False, profile_version=1))
    monkeypatch.setattr(api, "_live_quote_resolver", lambda: resolver)
    app = Flask(__name__)
    app.secret_key = "synthetic-sse"
    app.config.update(TESTING=True, LIVE_SSE_INTERVAL_SECONDS=0.25, LIVE_SSE_HEARTBEAT_SECONDS=2)
    app.extensions.update(market_stream_state=state, live_trigger_engine=trigger)
    with app.test_request_context("/api/live-market-stream?symbols=AAPL", headers={"Last-Event-ID": "previous-id"}):
        session["user_id"] = 1
        g.request_id = "synthetic-request"
        response = api.live_market_stream()
        stream = iter(response.response)
        try:
            yield stream, state, calls, response
        finally:
            stream.close()
        assert state.desired_demand() == {}


def _data(chunk):
    return json.loads(next(line[6:] for line in chunk.splitlines() if line.startswith("data: ")))


def test_actual_sse_same_event_id_status_only_null_updates_cleanup_and_no_advice(monkeypatch):
    evaluated = []
    trigger = SimpleNamespace(evaluate=lambda **kwargs: (evaluated.append(kwargs) or {"fire": False}))
    with _stream(monkeypatch, [_quote(), _quote(stale=True), _quote(price=None, event_timestamp=None)], trigger=trigger) as (stream, state, calls, response):
        ready, first, heartbeat = [next(stream) for _ in range(3)]
        assert '"resume_from":"previous-id"' in ready
        assert _data(first)["quotes"][0]["price"] == 101
        stale, second_heartbeat, unavailable = [next(stream) for _ in range(3)]
        assert "event: quotes" in stale and _data(stale)["quotes"][0]["is_stale"]
        assert "id: AAPL:T:7" in first and "id: AAPL:T:7" in stale
        retained = _data(unavailable)["quotes"][0]
        assert retained["price"] == 101 and retained["observed_price"] is None
        assert retained["source_mode"] == "last_known" and retained["is_stale"]
        assert len(evaluated) == 1 and calls == ["AAPL"] * 3
        assert response.mimetype == "text/event-stream" and response.headers["Cache-Control"] == "no-store"
        assert _data(heartbeat)["provider_health"]["connection_state"] == "unknown"


def test_actual_sse_preserves_controlled_minute_bar_trigger_and_event_name(monkeypatch):
    evaluated = []
    state = _State(clock=lambda: 0)
    state.set_latest(StreamEvent(
        event_type="AM", symbol="AAPL", event_timestamp=NOW, received_timestamp=NOW,
        sequence_number=8, provider_event_id="bar-8", payload={"close": 101}, quality_flags=(),
    ), ttl_seconds=120)
    trigger = SimpleNamespace(evaluate=lambda **kwargs: (evaluated.append(kwargs) or {"fire": True, "reason": "minute_bar_closed"}))
    with _stream(monkeypatch, [_quote(), _quote(stale=True)], state=state, trigger=trigger) as (stream, _, _, _):
        ready, recommendation, quote, heartbeat, status = [next(stream) for _ in range(5)]
        assert "event: recommendation_refresh" in recommendation
        assert _data(recommendation)["reason"] == "minute_bar_closed"
        assert len(evaluated) == 1 and evaluated[0]["event_type"] == "AM"
        assert "event: quotes" in status and _data(status)["quotes"][0]["is_stale"]


def test_actual_sse_health_read_budget_one_per_heartbeat_and_never_browser_inferred(monkeypatch):
    state = _State(clock=lambda: 0)
    state.set_health({"schema_version": STREAM_SCHEMA_VERSION, "updated_at": NOW.isoformat(), "connection_state": "reconnecting"}, ttl_seconds=30)
    with _stream(monkeypatch, [_quote(), _quote(price=102, seconds=0)], state=state, interval=0.5) as (stream, state, calls, _):
        ready, first, heartbeat, second = [next(stream) for _ in range(4)]
        assert state.health_reads == 1 and calls == ["AAPL"] * 2
        for chunk in (first, heartbeat, second):
            health = _data(chunk)["provider_health"]
            assert health["verified"] and health["connection_state"] == "reconnecting"


def test_actual_sse_uses_post_resolution_clock_for_new_market_observations(monkeypatch):
    now = [NOW]

    def delayed_resolution():
        # No real wait or provider request. The quote legitimately occurred
        # after resolution started, so its timestamp is not in the future.
        now[0] += timedelta(seconds=2)
        return _quote(event_timestamp=(NOW + timedelta(seconds=1)).isoformat())

    with _stream(monkeypatch, [delayed_resolution], clock=lambda: now[0]) as (stream, _, _, _):
        ready, quotes, heartbeat = [next(stream) for _ in range(3)]
        quote = _data(quotes)["quotes"][0]
        assert quote["price"] == 101 and not quote["is_stale"]
        assert quote["event_timestamp"] == (NOW + timedelta(seconds=1)).isoformat()


def test_api_quote_is_stateless_and_does_not_inherit_sse_history(monkeypatch):
    app = Flask(__name__)
    monkeypatch.setattr(api, "_live_quote_payload", lambda _: _quote(price=99, seconds=5))
    delivery = api._SSEQuoteDelivery()
    _apply(delivery, _quote(price=102, seconds=1))
    with app.test_request_context("/api/quote?symbol=AAPL"):
        g.request_id = "stateless"
        data = api.api_quote().get_json()["data"]
    assert data["price"] == 99 and data["source_mode"] == "websocket"


def test_initial_enrichment_metadata_uses_existing_quote_and_fails_closed_without_time(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(api, "datetime", Clock)
    quote = _quote(mode="rest")
    before = dict(quote)
    metadata = api._initial_live_quote_metadata("AAPL", quote)
    assert metadata["source_mode"] == "rest" and not metadata["is_stale"]
    assert metadata["price"] == 101 and metadata["age_ms"] == 1000 and quote == before
    missing = api._initial_live_quote_metadata("AAPL", {"price": 101, "quote_source": "massive", "live_data_available": True})
    assert missing["is_stale"] and missing["event_timestamp"] is None and missing["age_ms"] is None
