"""Retained investigation cases with expectations updated for approved repairs.

All prices, queues, transports, and clocks are synthetic; no incident cause is asserted.
"""
from __future__ import annotations

import asyncio
import json
import socket
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from websockets.asyncio import connection as websocket_connection
from websockets.asyncio.connection import Connection
from websockets.asyncio.messages import Assembler
from websockets.frames import Frame, Opcode

from moneybot.services import market_stream as stream
from moneybot.services.market_data_providers import MassiveRestClient


NOW = datetime(2026, 10, 9, 14, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Network forbidden in offline stream investigation")

    for name in ("connect", "connect_ex", "sendto", "sendmsg"):
        if hasattr(socket.socket, name):
            monkeypatch.setattr(socket.socket, name, blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


def quote_result(symbol):
    return SimpleNamespace(
        data=SimpleNamespace(
            symbol=symbol, price=100.0, bid=99.9, ask=100.1, bid_size=1,
            ask_size=1, midpoint=100.0, event_timestamp=NOW,
            sequence_number=999, provider_event_id=None, is_stale=False,
        ),
        received_timestamp=NOW,
    )


class RestStub:
    def __init__(self):
        self.calls = []

    def get_quote(self, symbol):
        self.calls.append(symbol)
        return quote_result(symbol)


def make_worker(*, state=None, rest=None, config=None, connect_factory=None):
    return stream.MassiveWebSocketWorker(
        api_key="synthetic-only",
        state=state if state is not None else stream.InMemoryMarketStreamState(),
        rest_client=rest if rest is not None else RestStub(),
        config=config or stream.WorkerConfig(
            enabled=True, server_symbols=("AAA",), publish_coalesce_ms=100000,
        ),
        connect_factory=connect_factory or (lambda *args, **kwargs: None),
        clock=lambda: NOW,
    )


def trade(symbol="AAA", sequence=1):
    return stream.StreamEvent(
        event_type="T", symbol=symbol, event_timestamp=NOW,
        received_timestamp=NOW, sequence_number=sequence,
        provider_event_id=None, payload={"price": 100.0},
    )


def test_frame_processing_and_health_redis_commands_run_off_loop_with_yield(monkeypatch):
    trace = []
    loop_thread = threading.get_ident()
    synthetic = {"seconds": 0.0}
    monkeypatch.setattr(stream, "time", SimpleNamespace(
        monotonic=lambda: synthetic["seconds"], perf_counter=lambda: synthetic["seconds"],
    ))

    def record_command(name):
        trace.append((name, threading.get_ident()))
        synthetic["seconds"] += 2.0  # Model delay without blocking wall-clock time.

    class RedisStub:
        def get(self, *args, **kwargs):
            return None

        def set(self, *args, **kwargs):
            record_command("SET")

        def publish(self, *args, **kwargs):
            record_command("PUBLISH")

        def info(self, *args, **kwargs):
            record_command("INFO")
            return {"used_memory": 1}

    # Invoke the real Redis repository methods with a non-network client.
    state = stream.RedisMarketStreamState.__new__(stream.RedisMarketStreamState)
    state.client = RedisStub()
    state.namespace = "synthetic:market:v1"

    async def scenario():
        instance = make_worker(
            state=state,
            config=stream.WorkerConfig(enabled=True, publish_coalesce_ms=0),
        )
        asyncio.get_running_loop().call_soon(trace.append, ("loop_callback", loop_thread))
        await instance.process_raw_message(json.dumps([
            {"ev": "T", "sym": symbol, "p": 100, "s": 1,
             "t": int(NOW.timestamp() * 1000), "q": 1}
            for symbol in ("AAA", "BBB", "CCC")
        ]))
        await instance._publish_health(force=True)
        assert [name for name, _thread in trace if name != "loop_callback"] == [
            "SET", "SET", "SET", "PUBLISH", "INFO", "SET",
        ]
        assert all(thread != loop_thread for name, thread in trace if name != "loop_callback")
        assert synthetic["seconds"] == 12.0
        await asyncio.sleep(0)
        assert trace[0][0] == "loop_callback"
        await instance.aclose()

    asyncio.run(scenario())


def test_shadow_comparison_is_serial_and_adds_per_symbol_synthetic_latency(monkeypatch):
    clock = {"seconds": 0.0}
    active = 0
    maximum_active = 0
    loop_progress = []
    rest = RestStub()
    state = stream.InMemoryMarketStreamState()
    for symbol in ("AAA", "BBB", "CCC"):
        state.set_latest(trade(symbol), ttl_seconds=120)

    async def simulated_to_thread(function, symbol):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0)
        clock["seconds"] += 6.0  # Simulated single-request delay, no wall wait.
        result = function(symbol)
        active -= 1
        return result

    async def scenario():
        instance = make_worker(state=state, rest=rest, config=stream.WorkerConfig(enabled=True, shadow_batch_timeout_seconds=30))
        monkeypatch.setattr(instance, "_rest_call", lambda symbol: simulated_to_thread(rest.get_quote, symbol))
        asyncio.get_running_loop().call_soon(loop_progress.append, "running")
        await instance.shadow_compare(("AAA", "BBB", "CCC"))
        assert loop_progress == ["running"]
        assert rest.calls == ["AAA", "BBB", "CCC"]
        assert maximum_active == 1
        assert clock["seconds"] == 18.0
        assert instance.metrics.shadow_comparisons == 3
        await instance.aclose()

    asyncio.run(scenario())


def test_run_connection_drains_recv_during_background_shadow_cycle(monkeypatch):
    synthetic = {"seconds": 0.0}
    monkeypatch.setattr(stream, "time", SimpleNamespace(
        monotonic=lambda: synthetic["seconds"], perf_counter=lambda: synthetic["seconds"],
    ))
    trace = []
    state = stream.InMemoryMarketStreamState()
    for symbol in ("AAA", "BBB", "CCC"):
        state.set_latest(trade(symbol), ttl_seconds=120)
    config = stream.WorkerConfig(enabled=True, server_symbols=("AAA", "BBB", "CCC"),
                                 reconcile_seconds=1000, shadow_compare_seconds=30)

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        instance = make_worker(state=state, config=config)

        async def pending_rest(symbol):
            trace.append("shadow:start:" + symbol)
            started.set()
            await release.wait()
            trace.append("shadow:done:" + symbol)
            return quote_result(symbol)

        monkeypatch.setattr(instance, "_rest_call", pending_rest)

        class WebSocketStub:
            reads = 0
            async def send(self, payload):
                trace.append("send:" + json.loads(payload)["action"])
            async def recv(self):
                self.reads += 1
                trace.append(f"recv:{self.reads}")
                if self.reads == 1:
                    return json.dumps({"ev": "status", "status": "auth_success"})
                if self.reads == 2:
                    return json.dumps({"ev": "status", "status": "success"})
                if self.reads == 3:
                    synthetic["seconds"] = 31.0
                elif self.reads == 4:
                    await started.wait()
                    assert not any(item.startswith("shadow:done:") for item in trace)
                    release.set()
                    await instance._shadow_task
                    instance.stop()
                return json.dumps({"ev": "status", "status": "connected"})

        await instance.run_connection(WebSocketStub())
        assert trace.index("recv:4") < trace.index("shadow:done:AAA")
        assert instance.metrics.shadow_comparisons == 3
        await instance.aclose()

    asyncio.run(scenario())


def test_installed_websockets_full_frame_queue_pauses_transport_until_consumer_drains():
    calls = []
    pong_acknowledgements = []

    async def scenario():
        assembler = Assembler(
            high=1024, pause=lambda: calls.append("pause_reading"),
            resume=lambda: calls.append("resume_reading"),
        )
        connection = SimpleNamespace(
            recv_messages=assembler, acknowledge_pings=pong_acknowledgements.append,
        )
        for _ in range(1024):
            Connection.process_event(connection, Frame(Opcode.TEXT, b"{}"))
        assert calls == []
        Connection.process_event(connection, Frame(Opcode.TEXT, b"{}"))
        assert assembler.paused and calls == ["pause_reading"]
        # Already-parsed PONGs are handled, even while the data queue is full.
        Connection.process_event(connection, Frame(Opcode.PONG, b"parsed-pong"))
        assert pong_acknowledgements == [b"parsed-pong"]
        loop_progress = []
        asyncio.get_running_loop().call_soon(loop_progress.append, "running")
        await asyncio.sleep(0)
        assert loop_progress == ["running"] and assembler.paused
        for _ in range(768):
            assert await assembler.get() == "{}"
        assert assembler.paused  # 257 frames: still above low water (256).
        assert await assembler.get() == "{}"
        assert not assembler.paused and calls == ["pause_reading", "resume_reading"]

    asyncio.run(scenario())


def test_installed_websockets_keepalive_timeout_branch_with_synthetic_unread_pong(monkeypatch):
    trace = []
    original_sleep = asyncio.sleep

    async def synthetic_sleep(delay):
        trace.append(("ping_interval", delay))
        await original_sleep(0)

    class SyntheticTimeout:
        def __init__(self, seconds):
            trace.append(("pong_timeout", seconds))

        async def __aenter__(self):
            self.task = asyncio.current_task()
            # Make the synthetic deadline expire at the next scheduler turn.
            asyncio.get_running_loop().call_soon(self.task.cancel)

        async def __aexit__(self, exception_type, exception, traceback):
            if exception_type is asyncio.CancelledError:
                self.task.uncancel()
                raise asyncio.TimeoutError
            return False

    class SyntheticClose:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *args):
            # Actual connection_lost cancels the keepalive task after close.
            raise asyncio.CancelledError

    monkeypatch.setattr(websocket_connection.asyncio, "sleep", synthetic_sleep)
    monkeypatch.setattr(websocket_connection, "asyncio_timeout", SyntheticTimeout)

    async def scenario():
        pong = asyncio.get_running_loop().create_future()

        async def ping():
            trace.append(("PING", None))
            return pong  # No PONG delivery; no real transport is created.

        connection = SimpleNamespace(
            ping_interval=20, ping_timeout=20, debug=False, ping=ping,
            send_context=SyntheticClose,
            protocol=SimpleNamespace(fail=lambda code, reason: trace.append((code, reason))),
            logger=SimpleNamespace(error=lambda *args: trace.append(("unexpected_error", args))),
        )
        with pytest.raises(asyncio.CancelledError):
            await Connection.keepalive(connection)
        assert pong.cancelled()

    asyncio.run(scenario())
    assert trace == [
        ("ping_interval", 20.0), ("PING", None), ("pong_timeout", 20),
        (1011, "keepalive ping timeout"),
    ]


def test_sequence_gap_counter_is_per_event_type_and_not_missing_event_count():
    instance = make_worker()
    assert instance._accept_event(trade(sequence=10)) == (True, False)
    quote = stream.StreamEvent(
        event_type="Q", symbol="AAA", event_timestamp=NOW,
        received_timestamp=NOW, sequence_number=11, provider_event_id=None,
        payload={"bid": 99.9, "ask": 100.1},
    )
    assert instance._accept_event(quote) == (True, False)
    # A combined q=10,11,12 stream is contiguous, but T alone isn't.
    assert instance._accept_event(trade(sequence=12)) == (True, True)
    assert instance._accept_event(trade(sequence=100)) == (True, True)
    assert instance.metrics.sequence_gaps == 2  # Two transitions, not 87 missing IDs.
    assert instance.metrics.rest_recovery_count == 0


def test_queued_recovery_cooldown_also_limits_reconnect_bulk_recovery(monkeypatch):
    rest = RestStub()

    async def inline_stub_to_thread(function, *args):
        return function(*args)

    monkeypatch.setattr(stream.asyncio, "to_thread", inline_stub_to_thread)

    async def scenario():
        instance = make_worker(rest=rest)
        instance._queue_recovery("AAA", reason="sequence_gap")
        instance._queue_recovery("AAA", reason="sequence_gap")
        instance._queue_recovery("AAA", reason="sequence_gap")
        await instance.drain_recoveries()
        assert rest.calls == ["AAA"]
        assert instance.metrics.rest_recovery_deduped == 2
        await instance._recover_symbols(("AAA", "BBB"), reason="stream_reconnect")
        await instance._recover_symbols(("AAA", "BBB"), reason="stream_reconnect")
        assert rest.calls.count("AAA") == 1
        assert rest.calls.count("BBB") == 1
        assert instance.metrics.rest_recovery_count == 2
        await instance.aclose()

    asyncio.run(scenario())


def test_shadow_and_queued_recovery_share_rest_concurrency_budget(monkeypatch):
    async def scenario():
        loop = asyncio.get_running_loop()
        entered = asyncio.Event()
        release = threading.Event()
        active = 0
        maximum_active = 0
        lock = threading.Lock()
        rest = RestStub()
        original = rest.get_quote

        def gated_quote(symbol):
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
                if active == 2:
                    loop.call_soon_threadsafe(entered.set)
            release.wait()
            result = original(symbol)
            with lock:
                active -= 1
            return result

        rest.get_quote = gated_quote
        state = stream.InMemoryMarketStreamState()
        state.set_latest(trade("AAA"), ttl_seconds=120)
        instance = make_worker(state=state, rest=rest,
                               config=stream.WorkerConfig(enabled=True, recovery_concurrency=2))
        instance._queue_recovery("BBB", reason="sequence_gap")
        instance._queue_recovery("CCC", reason="sequence_gap")
        shadow_task = asyncio.create_task(instance.shadow_compare(("AAA",)))
        await entered.wait()
        await asyncio.sleep(0)
        assert maximum_active == 2
        assert instance._rest_executor._work_queue.qsize() == 0
        release.set()
        await shadow_task
        await instance.drain_recoveries()
        assert sorted(rest.calls) == ["AAA", "BBB", "CCC"]
        await instance.aclose()

    asyncio.run(scenario())


def test_reconnect_enqueues_recovery_before_backoff_and_preserves_ping_settings(monkeypatch):
    trace = []
    options = []

    class ContextStub:
        async def __aenter__(self):
            trace.append("connected")
            return object()

        async def __aexit__(self, *args):
            trace.append("connection_exit")

    def connect_stub(url, **kwargs):
        options.append(kwargs)
        return ContextStub()

    instance = make_worker(
        config=stream.WorkerConfig(enabled=True, server_symbols=("AAA", "BBB")),
        connect_factory=connect_stub,
    )

    async def connection_stub(_websocket):
        trace.append("synthetic_failure")
        raise RuntimeError("synthetic transport failure")

    async def recovery_stub(symbol, *, reason):
        assert reason == "stream_reconnect"
        trace.append("recovery_start:" + symbol)
        await asyncio.Event().wait()
        trace.append("recovery_done:" + symbol)

    async def sleep_stub(delay):
        trace.append("backoff")
        if trace.count("backoff") == 2:
            instance.stop()

    monkeypatch.setattr(instance, "run_connection", connection_stub)
    monkeypatch.setattr(instance, "_recover_symbol", recovery_stub)
    instance.sleep = sleep_stub
    asyncio.run(instance.run())
    assert len(options) == 2
    assert instance.metrics.reconnect_count == 1
    assert instance.config.heartbeat_timeout_seconds == 0
    for value in options:
        assert value["ping_interval"] == value["ping_timeout"] == 20
        assert value["max_queue"] == 1024
    cycles = []
    previous = 0
    for index, item in enumerate(trace):
        if item == "backoff":
            cycles.append(trace[previous:index])
            previous = index + 1
    assert len(cycles) == 2
    assert not any(item.startswith("recovery_done:") for item in cycles[0])
    assert instance.metrics.rest_recovery_queued == 2
    assert instance.metrics.rest_recovery_deduped == 2


def test_default_rest_retry_cost_in_serial_shadow_uses_only_synthetic_time(monkeypatch):
    seconds = {"value": 0.0}
    requests = []
    delays = []

    def failed_http(url, *, params, timeout):
        requests.append((url, timeout))
        seconds["value"] += timeout
        raise TimeoutError("synthetic timeout")

    def synthetic_sleep(delay):
        delays.append(delay)
        seconds["value"] += delay

    rest = MassiveRestClient(
        api_key="synthetic-only", http_get=failed_http, sleep=synthetic_sleep,
        clock=lambda: NOW, negative_cache_seconds=0,
    )
    state = stream.InMemoryMarketStreamState()
    for symbol in ("AAA", "BBB", "CCC"):
        state.set_latest(trade(symbol), ttl_seconds=120)

    async def inline_stub_to_thread(function, *args):
        return function(*args)

    monkeypatch.setattr(stream.asyncio, "to_thread", inline_stub_to_thread)
    instance = make_worker(state=state, rest=rest)
    async def scenario():
        await instance.shadow_compare(("AAA", "BBB", "CCC"))
        await instance.aclose()
    asyncio.run(scenario())
    assert len(requests) == 9
    assert all(timeout == 6.0 for _url, timeout in requests)
    assert delays == [0.2, 0.4] * 3
    assert seconds["value"] == pytest.approx(55.8)
    assert instance.metrics.shadow_comparisons == 0
