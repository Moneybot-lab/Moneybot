from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from moneybot.services.market_data_providers import NormalizedQuote, ProviderResult
from moneybot.services.market_stream import (
    InMemoryMarketStreamState,
    MassiveStreamParser,
    MassiveWebSocketWorker,
    StreamEvent,
    StreamParseError,
    SubscriptionManager,
    WorkerConfig,
    worker_config_from_env,
)

FIXTURES = Path(__file__).parent / "fixtures" / "massive" / "websocket"
NOW = datetime(2026, 6, 8, 14, 30, 2, tzinfo=timezone.utc)


def fixture(name):
    return (FIXTURES / name).read_text()


class FakeWebSocket:
    def __init__(self, messages=()):
        self.messages = list(messages)
        self.sent = []

    async def send(self, payload):
        self.sent.append(json.loads(payload))

    async def recv(self):
        if not self.messages:
            await asyncio.sleep(3600)
        return self.messages.pop(0)


class FakeRestClient:
    def __init__(self, price=200.21):
        self.price = price
        self.calls = []

    def get_quote(self, symbol):
        self.calls.append(symbol)
        quote = NormalizedQuote(
            symbol=symbol, bid=200.20, ask=200.22, bid_size=5, ask_size=8, midpoint=200.21,
            last_trade_price=self.price, last_trade_size=10, price=self.price, price_source="last_trade",
            price_reason="test", event_timestamp=NOW, received_timestamp=NOW, age_ms=0,
            market_session="regular", source="massive", source_mode="rest", is_stale=False,
            sequence_number=999,
        )
        return ProviderResult(quote, "massive", "snapshot", "req", NOW, 1.0)


def worker(*, state=None, rest=None, config=None):
    return MassiveWebSocketWorker(
        api_key="secret", state=state or InMemoryMarketStreamState(), rest_client=rest or FakeRestClient(),
        config=config or WorkerConfig(enabled=True, server_symbols=("AAPL",), publish_coalesce_ms=100000),
        connect_factory=lambda *args, **kwargs: None, clock=lambda: NOW, sleep=lambda _delay: asyncio.sleep(0),
    )


def test_parser_normalizes_all_subscribed_event_types():
    events = MassiveStreamParser().parse_message(fixture("events.json"), received_at=NOW)

    assert [event.event_type for event in events] == ["A", "AM", "Q", "T"]
    assert events[0].payload["close"] == 200.2
    assert events[1].payload["window_start"].endswith("+00:00")
    assert events[2].payload["midpoint"] == pytest.approx(200.22)
    assert events[3].provider_event_id == "trade-101"
    assert all(event.source_mode == "websocket" for event in events)


def test_parser_rejects_malformed_unknown_and_wildcard_events():
    parser = MassiveStreamParser()
    with pytest.raises(StreamParseError):
        parser.parse_message("not-json", received_at=NOW)
    with pytest.raises(StreamParseError, match="unsupported"):
        parser.parse_message(json.dumps({"ev": "X", "sym": "AAPL", "t": 1}), received_at=NOW)
    with pytest.raises(StreamParseError, match="concrete"):
        parser.parse_message(json.dumps({"ev": "T", "sym": "*", "t": 1780929000000}), received_at=NOW)


def test_authentication_acknowledgement_is_required():
    async def scenario():
        ok = FakeWebSocket([
            json.dumps([{"ev": "status", "status": "connected", "message": "connected"}]),
            fixture("status_auth_success.json"),
        ])
        await worker().authenticate(ok)
        assert ok.sent == [{"action": "auth", "params": "secret"}]

        failed = FakeWebSocket([fixture("status_auth_failed.json")])
        with pytest.raises(RuntimeError, match="authentication failed"):
            await worker().authenticate(failed)


    asyncio.run(scenario())

def test_subscription_manager_reference_counts_caps_and_forbids_wildcards():
    manager = SubscriptionManager(global_symbol_cap=3, quote_cap=2, trade_cap=1, server_symbols=("SPY",))
    plan = manager.plan({"portfolio:1": {"AAPL", "MSFT"}, "quick:1": {"AAPL", "NVDA", "*"}})

    assert plan.symbols == {"SPY", "AAPL", "MSFT"}
    assert plan.reference_counts["AAPL"] == 2
    assert plan.rejected_symbols == ("NVDA",)
    assert plan.desired_by_event["Q"] == {"SPY", "AAPL"}
    assert plan.desired_by_event["T"] == {"SPY"}
    subscribe, _ = manager.commands({event: set() for event in ("A", "AM", "Q", "T")}, plan)
    assert all("*" not in channel for channel in subscribe)


def test_reconcile_subscribes_unsubscribes_and_restores_actual_after_reconnect():
    async def scenario():
        state = InMemoryMarketStreamState()
        state.register_demand("portfolio:1", ["AAPL", "MSFT"], ttl_seconds=60)
        instance = worker(state=state, config=WorkerConfig(enabled=True, symbol_cap=5, quote_cap=5, trade_cap=0))
        socket = FakeWebSocket()

        first = await instance.reconcile(socket)
        assert first.symbols == {"AAPL", "MSFT"}
        assert any(message["action"] == "subscribe" and "A.AAPL" in message["params"] for message in socket.sent)

        state.register_demand("portfolio:1", ["MSFT"], ttl_seconds=60)
        await instance.reconcile(socket)
        assert any(message["action"] == "unsubscribe" and "A.AAPL" in message["params"] for message in socket.sent)

        instance.actual = {event: set() for event in ("A", "AM", "Q", "T")}
        socket.sent.clear()
        await instance.reconcile(socket)
        assert any(message["action"] == "subscribe" and "AM.MSFT" in message["params"] for message in socket.sent)


    asyncio.run(scenario())

def test_duplicate_out_of_order_gap_coalescing_and_rest_recovery():
    async def scenario():
        state = InMemoryMarketStreamState()
        rest = FakeRestClient()
        instance = worker(state=state, rest=rest)
        base = {"ev": "T", "sym": "AAPL", "p": 200.0, "s": 1, "t": 1780929000000000000, "q": 10, "i": "a"}

        await instance.process_raw_message(json.dumps([base]))
        await instance.process_raw_message(json.dumps([base]))
        await instance.process_raw_message(json.dumps([{**base, "q": 9, "i": "old"}]))
        await instance.process_raw_message(json.dumps([{**base, "q": 12, "i": "gap", "t": 1780929001000000000}]))
        await instance.flush_updates_if_due(force=True)

        assert instance.metrics.duplicates == 1
        assert instance.metrics.out_of_order == 1
        assert instance.metrics.sequence_gaps == 1
        assert instance.metrics.coalesced_events == 1
        assert instance.metrics.rest_recovery_count == 1
        assert rest.calls == ["AAPL"]
        assert len(state.published) == 1
        assert state.get_latest("AAPL", "Q")["quality_flags"] == ["rest_recovery", "sequence_gap"]


    asyncio.run(scenario())

def test_redis_ttl_and_abandoned_browser_demand_expire():
    now = [100.0]
    state = InMemoryMarketStreamState(clock=lambda: now[0])
    event = StreamEvent("T", "AAPL", NOW, NOW, 1, "x", {"price": 200.0})
    state.set_latest(event, ttl_seconds=10)
    state.register_demand("quick:browser", ["AAPL"], ttl_seconds=5)

    assert state.get_latest("AAPL", "T") is not None
    assert state.desired_demand() == {"quick:browser": {"AAPL"}}
    now[0] = 106
    assert state.desired_demand() == {}
    now[0] = 111
    assert state.get_latest("AAPL", "T") is None


def test_shadow_comparison_records_discrepancy_without_changing_rest_consumers():
    async def scenario():
        state = InMemoryMarketStreamState()
        event = StreamEvent("T", "AAPL", NOW, NOW, 1, "x", {"price": 190.0})
        state.set_latest(event, ttl_seconds=60)
        instance = worker(state=state, rest=FakeRestClient(price=200.0), config=WorkerConfig(enabled=True, shadow_mode=True, rest_shadow_tolerance_bps=25))

        await instance.shadow_compare(["AAPL"])

        assert instance.metrics.shadow_comparisons == 1
        assert instance.metrics.shadow_discrepancies == 1
        assert instance.config.shadow_mode is True


    asyncio.run(scenario())

def test_worker_config_defaults_to_disabled_shadow_and_bounded_budget():
    config = worker_config_from_env({})
    assert config.enabled is False
    assert config.shadow_mode is True
    assert config.symbol_cap == 250
    assert config.server_symbols == ("SPY", "QQQ")


def test_load_at_symbol_budget_keeps_state_and_lag_bounded():
    async def scenario():
        state = InMemoryMarketStreamState()
        config = WorkerConfig(enabled=True, symbol_cap=250, quote_cap=0, trade_cap=0, publish_coalesce_ms=0)
        instance = worker(state=state, config=config)
        messages = [
            {"ev": "A", "sym": f"S{index}", "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 100, "s": 1780929000000, "e": 1780929001000}
            for index in range(250)
        ]

        await instance.process_raw_message(json.dumps(messages))
        await instance.flush_updates_if_due(force=True)

        assert instance.metrics.messages_received["A"] == 250
        assert instance.metrics.parse_failures == 0
        assert instance.metrics.dropped_events == 0
        assert len(state.latest) == 250
        assert instance.metrics.snapshot()["event_to_redis_lag_ms"]["p95"] < 2000
    asyncio.run(scenario())



def test_subscription_acknowledgement_is_checked_when_enabled():
    async def scenario():
        state = InMemoryMarketStreamState()
        state.register_demand("portfolio:1", ["AAPL"], ttl_seconds=60)
        instance = worker(state=state, config=WorkerConfig(enabled=True, trade_cap=0))
        ok = FakeWebSocket([json.dumps([{"ev": "status", "status": "success", "message": "subscribed"}])])
        await instance.reconcile(ok, check_ack=True)
        assert instance.actual["A"] == {"AAPL"}

        failed = FakeWebSocket([json.dumps([{"ev": "status", "status": "error", "message": "bad params"}])])
        fresh = worker(state=state, config=WorkerConfig(enabled=True, trade_cap=0))
        with pytest.raises(RuntimeError, match="acknowledgement failed"):
            await fresh.reconcile(failed, check_ack=True)

    asyncio.run(scenario())


def test_redis_repository_uses_versioned_keys_ttl_pubsub_and_demand_expiry(monkeypatch):
    from moneybot.services import market_stream

    class FakeRedis:
        def __init__(self):
            self.values = {}; self.sets = {}; self.published = []
        def set(self, key, value, ex=None): self.values[key] = (value, ex)
        def get(self, key): return self.values.get(key, (None, None))[0]
        def publish(self, channel, value): self.published.append((channel, value)); return 1
        def sadd(self, key, value): self.sets.setdefault(key, set()).add(value)
        def smembers(self, key): return set(self.sets.get(key, set()))
        def srem(self, key, value): self.sets.get(key, set()).discard(value)
        def info(self, section): return {"used_memory": 2048}

    fake = FakeRedis()
    class RedisFactory:
        @staticmethod
        def from_url(*args, **kwargs): return fake
    module = type("FakeRedisModule", (), {"Redis": RedisFactory})
    monkeypatch.setattr(market_stream.importlib, "import_module", lambda name: module)

    state = market_stream.RedisMarketStreamState("redis://example")
    event = StreamEvent("T", "AAPL", NOW, NOW, 1, "x", {"price": 200.0})
    state.set_latest(event, ttl_seconds=120)
    state.register_demand("quick:1", ["AAPL"], ttl_seconds=90)
    state.publish_updates([{"symbol": "AAPL", "event_type": "T"}])

    key = "moneybot:market:v1:latest:T:AAPL"
    assert fake.values[key][1] == 120
    assert state.get_latest("AAPL", "T")["schema_version"] == "market-stream.v1"
    assert fake.values["moneybot:market:v1:demand:quick:1"][1] == 90
    assert state.desired_demand() == {"quick:1": {"AAPL"}}
    assert fake.published[0][0] == "moneybot:market:v1:updates"
    assert state.memory_usage_bytes() == 2048


def test_disconnect_marks_stale_recovers_from_rest_and_enters_backoff():
    async def scenario():
        state = InMemoryMarketStreamState()
        rest = FakeRestClient()
        sleeps = []
        instance = None

        class FailingContext:
            async def __aenter__(self): raise RuntimeError("connection lost")
            async def __aexit__(self, *args): return False

        async def stop_after_backoff(delay):
            sleeps.append(delay)
            instance.stop()

        instance = MassiveWebSocketWorker(
            api_key="secret", state=state, rest_client=rest,
            config=WorkerConfig(enabled=True, server_symbols=("AAPL",), reconnect_min_seconds=1, reconnect_max_seconds=1),
            connect_factory=lambda *args, **kwargs: FailingContext(), clock=lambda: NOW, sleep=stop_after_backoff,
        )
        await instance.run()

        assert rest.calls == ["AAPL"]
        assert instance.metrics.rest_recovery_count == 1
        assert sleeps and 0.8 <= sleeps[0] <= 1.2
        assert instance.health_payload()["connection_state"] == "reconnecting"

    asyncio.run(scenario())
