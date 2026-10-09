"""Synthetic resolver acceptance checks; provider transports are never opened."""
from datetime import datetime, timedelta, timezone

import pytest

from moneybot.services.live_market import LiveQuoteResolver
from moneybot.services.market_stream import InMemoryMarketStreamState, StreamEvent


NOW = datetime(2026, 6, 8, 14, 30, tzinfo=timezone.utc)


def save(state, kind="T", *, at=NOW, price=100, stale=False, source_mode="websocket", **fields):
    payload = {"price": price} if kind == "T" else (
        {"close": price} if kind in {"A", "AM"} else {"midpoint": price}
    )
    payload.update(fields)
    state.set_latest(StreamEvent(
        event_type=kind, symbol="THIN", event_timestamp=at, received_timestamp=NOW,
        sequence_number=42, provider_event_id=f"synthetic-{kind}", payload=payload,
        source_mode=source_mode,
    ), ttl_seconds=120, stale=stale)


def rest(*, at=NOW, price=99, **fields):
    quote = {"price": price, "event_timestamp": at.isoformat() if isinstance(at, datetime) else at,
             "received_timestamp": NOW.isoformat(), "source": "massive", "source_mode": "rest",
             "is_stale": False, "age_ms": 0, "market_session": "regular"}
    quote.update(fields)
    return quote


def fixture_resolver(*, rest_quote=None, clock=lambda: NOW):
    state = InMemoryMarketStreamState(clock=lambda: clock().timestamp())
    calls = []

    def fetch(symbol):
        calls.append(symbol)
        return rest_quote(symbol) if rest_quote is not None else rest()

    return state, LiveQuoteResolver(state=state, rest_quote=fetch, clock=clock), calls


@pytest.mark.parametrize("kind", ["Q", "A", "AM"])
def test_unusable_newer_trade_cannot_hide_healthy_other_channel(kind):
    state, resolver, calls = fixture_resolver()
    save(state, "T", stale=True, at=NOW)
    save(state, kind, at=NOW - timedelta(seconds=1), price=101)
    quote = resolver.resolve("THIN")
    assert quote.price == 101 and quote.event_type == kind and not quote.is_stale
    assert calls == []


def test_stale_tie_is_filtered_before_legacy_channel_priority():
    state, resolver, calls = fixture_resolver()
    save(state, "T", stale=True)
    save(state, "Q", price=101)
    assert resolver.resolve("THIN").event_type == "Q"
    save(state, "T", price=102)
    assert resolver.resolve("THIN").event_type == "T"
    assert calls == []


def test_newest_eligible_price_event_wins_independently_of_receipt():
    state, resolver, calls = fixture_resolver()
    save(state, "T", at=NOW - timedelta(seconds=1), price=100)
    save(state, "AM", at=NOW, price=102)
    quote = resolver.resolve("THIN")
    assert quote.event_type == "AM" and quote.price == 102
    assert quote.price_source == "minute_close"
    assert calls == []


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf"), float("-inf"), True, None])
@pytest.mark.parametrize("kind", ["T", "Q", "A", "AM"])
def test_invalid_stream_prices_use_rest_without_becoming_live(kind, price):
    state, resolver, calls = fixture_resolver()
    save(state, kind, price=price)
    quote = resolver.resolve("THIN")
    assert quote.price == 99 and quote.source_mode == "rest" and calls == ["THIN"]


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf"), float("-inf"), True, None])
def test_invalid_rest_prices_remain_unavailable_even_if_provider_claims_fresh(price):
    _, resolver, _ = fixture_resolver(rest_quote=lambda _: rest(price=price))
    quote = resolver.resolve("THIN")
    assert quote.price is None and quote.is_stale and quote.is_degraded
    assert "data_missing" in quote.quality_flags


@pytest.mark.parametrize("at,flag", [
    (None, "missing_event_timestamp"),
    ("not-an-event-time", "missing_event_timestamp"),
    ("2026-06-08T14:30:00", "missing_event_timestamp"),
    (NOW + timedelta(seconds=1), "future_event_timestamp"),
])
def test_untrusted_rest_timestamps_never_establish_freshness(at, flag):
    _, resolver, _ = fixture_resolver(rest_quote=lambda _: rest(at=at))
    quote = resolver.resolve("THIN")
    assert quote.price == 99 and quote.is_stale and quote.is_degraded
    assert flag in quote.quality_flags


def test_rest_age_is_recomputed_on_each_resolution_without_mutating_payload():
    now = [NOW]
    cached = rest()
    _, resolver, calls = fixture_resolver(rest_quote=lambda _: cached, clock=lambda: now[0])
    fresh = resolver.resolve("THIN")
    now[0] += timedelta(seconds=15, milliseconds=1)
    stale = resolver.resolve("THIN")
    assert fresh.age_ms == 0 and not fresh.is_stale
    assert stale.age_ms == 15_001 and stale.is_stale
    assert stale.event_id == fresh.event_id
    assert calls == ["THIN", "THIN"]  # A resolver invocation, not a provider request.
    assert cached["age_ms"] == 0 and cached["is_stale"] is False


@pytest.mark.parametrize("now,at,session", [
    (datetime(2026, 6, 8, 12, tzinfo=timezone.utc), datetime(2026, 6, 8, 11, 59, 59, tzinfo=timezone.utc), "pre"),
    (NOW, NOW - timedelta(seconds=1), "regular"),
    (datetime(2026, 6, 8, 21, tzinfo=timezone.utc), datetime(2026, 6, 8, 20, 59, 59, tzinfo=timezone.utc), "after"),
    (datetime(2026, 6, 9, 2, tzinfo=timezone.utc), datetime(2026, 6, 9, 1, 59, 59, tzinfo=timezone.utc), "closed"),
])
def test_price_time_and_current_market_context_remain_explicit_in_each_session(now, at, session):
    state, resolver, calls = fixture_resolver(clock=lambda: now)
    save(state, at=at)
    quote = resolver.resolve("THIN")
    assert quote.market_session == session and quote.age_ms == 1000 and not quote.is_stale
    assert quote.market_session_context == f"{resolver.calendar.local_date(now).isoformat()}:{session}"
    assert calls == []


@pytest.mark.parametrize("now,at", [
    (datetime(2026, 6, 8, 13, 30, tzinfo=timezone.utc), datetime(2026, 6, 8, 13, 29, 59, tzinfo=timezone.utc)),
    (datetime(2026, 6, 8, 20, tzinfo=timezone.utc), datetime(2026, 6, 8, 19, 59, 59, tzinfo=timezone.utc)),
    (datetime(2026, 6, 9, 4, tzinfo=timezone.utc), datetime(2026, 6, 9, 3, 59, 59, tzinfo=timezone.utc)),
])
def test_session_or_exchange_date_transition_invalidates_previous_context(now, at):
    _, resolver, _ = fixture_resolver(rest_quote=lambda _: rest(at=at), clock=lambda: now)
    quote = resolver.resolve("THIN")
    assert quote.age_ms == 1000 and quote.is_stale
    assert "market_session_mismatch" in quote.quality_flags


def test_daily_close_cannot_become_live_from_recent_ticker_timestamp():
    _, resolver, _ = fixture_resolver(rest_quote=lambda _: rest(price_source="day_close"))
    quote = resolver.resolve("THIN")
    assert quote.price == 99 and quote.is_stale
    assert "daily_close_not_realtime" in quote.quality_flags


def test_trade_only_recovery_keeps_actual_price_provenance_and_no_invented_nbbo():
    state, resolver, calls = fixture_resolver()
    save(state, "Q", price=None, bid=None, ask=None, source_mode="rest",
         recovery_price=123, recovery_price_source="last_trade", recovery_price_timestamp=NOW.isoformat())
    quote = resolver.resolve("THIN")
    assert quote.price == 123 and quote.price_source == "last_trade" and quote.source_mode == "rest"
    assert quote.bid is None and quote.ask is None and quote.midpoint is None
    assert quote.event_timestamp == NOW.isoformat() and not quote.is_stale and calls == []


def test_recovery_uses_selected_trade_time_and_not_quote_container_time():
    state, resolver, calls = fixture_resolver()
    save(state, "Q", price=102, bid=101, ask=103, source_mode="rest",
         recovery_price=123, recovery_price_source="last_trade",
         recovery_price_timestamp=(NOW - timedelta(seconds=16)).isoformat())
    candidate = resolver._freshest_stream("THIN")
    assert candidate.price == 123 and candidate.midpoint == 102
    assert candidate.age_ms == 16_000 and candidate.is_stale
    assert resolver.resolve("THIN").price == 99 and calls == ["THIN"]


def test_recovery_missing_actual_price_timestamp_remains_stale():
    state, resolver, calls = fixture_resolver()
    save(state, "Q", price=None, source_mode="rest", recovery_price=123, recovery_price_source="last_trade")
    candidate = resolver._freshest_stream("THIN")
    assert candidate.price == 123 and candidate.event_timestamp is None and candidate.is_stale
    assert "missing_event_timestamp" in candidate.quality_flags
    assert resolver.resolve("THIN").price == 99 and calls == ["THIN"]


def test_crossed_quote_does_not_hide_healthy_trade():
    state, resolver, calls = fixture_resolver()
    save(state, "T", at=NOW - timedelta(seconds=1))
    save(state, "Q", price=102, bid=103, ask=101)
    quote = resolver.resolve("THIN")
    assert quote.price == 100 and quote.event_type == "T" and calls == []


@pytest.mark.parametrize("bid", [0, -1, float("nan"), float("inf")])
def test_invalid_nbbo_component_does_not_confer_eligibility_on_positive_midpoint(bid):
    state, resolver, calls = fixture_resolver()
    save(state, "T", at=NOW - timedelta(seconds=1))
    save(state, "Q", price=102, bid=bid, ask=103)
    quote = resolver.resolve("THIN")
    assert quote.event_type == "T" and quote.price == 100 and calls == []


@pytest.mark.parametrize("at", [None, "2026-06-08T14:30:00", "0001-01-01T00:00:00+14:00"])
def test_receipt_time_cannot_make_unknown_stream_price_time_fresh(monkeypatch, at):
    state, resolver, calls = fixture_resolver()
    record = {"payload": {"price": 100}, "event_timestamp": at,
              "received_timestamp": NOW.isoformat(), "sequence_number": 1,
              "source_mode": "websocket", "is_stale": False}
    monkeypatch.setattr(state, "get_latest", lambda symbol, kind: record if kind == "T" else None)
    candidate = resolver._freshest_stream("THIN")
    assert candidate.price == 100 and candidate.is_stale and candidate.event_timestamp is None
    assert "missing_event_timestamp" in candidate.quality_flags
    assert resolver.resolve("THIN").price == 99 and calls == ["THIN"]


def test_future_stream_price_timestamp_is_stale_and_cannot_mask_current_aggregate():
    state, resolver, calls = fixture_resolver()
    save(state, "T", at=NOW + timedelta(seconds=1))
    save(state, "AM", price=101)
    quote = resolver.resolve("THIN")
    assert quote.price == 101 and quote.event_type == "AM" and calls == []


def test_invalid_fresh_stream_data_can_transition_to_null_without_inventing_price():
    state, resolver, calls = fixture_resolver(rest_quote=lambda _: rest(price=None))
    save(state, "T", price=None)
    quote = resolver.resolve("THIN")
    assert quote.price is None and quote.is_stale and quote.source_mode == "rest"
    assert calls == ["THIN"]


def test_stateless_resolver_does_not_create_global_monotonic_price_memory():
    state, resolver, calls = fixture_resolver()
    first = resolver.resolve("THIN")
    save(state, "T", at=NOW - timedelta(seconds=1), price=98)
    later = resolver.resolve("THIN")
    assert first.price == 99 and later.price == 98 and later.event_timestamp < first.event_timestamp
    assert calls == ["THIN"]  # Ordering belongs to the existing SSE connection.
