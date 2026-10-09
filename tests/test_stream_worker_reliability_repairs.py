"""Deterministic C1/C2 acceptance checks; all I/O is synthetic and network denied."""
from __future__ import annotations

import asyncio
import json
import threading
from datetime import timedelta
from types import SimpleNamespace

import pytest

from moneybot.services import market_stream as stream
from test_stream_worker_offline_investigation import NOW, RestStub, make_worker, quote_result, trade


def frame(*, symbol="AAA", sequence=1, price=100, timestamp=NOW):
    return json.dumps({"ev": "T", "sym": symbol, "p": price, "s": 1,
                       "t": int(timestamp.timestamp() * 1000), "q": sequence})


def test_delayed_state_operation_yields_loop_and_ordered_writes_preserve_ttl():
    async def scenario():
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        thread_ids, writes = [], []

        class DelayedState(stream.InMemoryMarketStreamState):
            def set_latest(self, event, *, ttl_seconds, stale=False):
                thread_ids.append(threading.get_ident())
                if event.sequence_number == 1:
                    loop.call_soon_threadsafe(entered.set)
                    release.wait()
                writes.append((event.sequence_number, ttl_seconds))
                return super().set_latest(event, ttl_seconds=ttl_seconds, stale=stale)

        state = DelayedState()
        worker = make_worker(state=state)
        first = asyncio.create_task(worker.process_raw_message(frame(sequence=1)))
        await entered.wait()
        second = asyncio.create_task(worker.process_raw_message(frame(sequence=2, price=102)))
        progress = []
        loop.call_soon(progress.append, "loop alive")
        await asyncio.sleep(0)
        assert progress == ["loop alive"]
        assert not first.done() and not second.done()
        assert worker._state_executor._work_queue.qsize() == 0
        release.set()
        await asyncio.gather(first, second)
        assert writes == [(1, 120), (2, 120)]
        assert state.get_latest("AAA", "T")["payload"]["price"] == 102
        assert set(thread_ids) != {threading.get_ident()}
        await worker.aclose()

    asyncio.run(scenario())


def test_cancellation_keeps_state_admission_until_running_write_finishes():
    async def scenario():
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        writes = []
        worker = make_worker()

        def delayed_write(value):
            if value == 1:
                loop.call_soon_threadsafe(entered.set)
                release.wait()
            writes.append(value)

        first = asyncio.create_task(worker._state_call(delayed_write, 1))
        await entered.wait()
        first.cancel()
        second = asyncio.create_task(worker._state_call(delayed_write, 2))
        await asyncio.sleep(0)
        assert worker._state_lock.locked() and writes == []
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await second
        assert writes == [1, 2]
        await worker.aclose()

    asyncio.run(scenario())


def test_health_work_is_offloaded_and_cadence_does_not_repeat_info(monkeypatch):
    seconds = {"value": 0.0}
    monkeypatch.setattr(stream, "time", SimpleNamespace(monotonic=lambda: seconds["value"], perf_counter=lambda: seconds["value"]))

    async def scenario():
        info_threads, health_ttls = [], []

        class State(stream.InMemoryMarketStreamState):
            def memory_usage_bytes(self):
                info_threads.append(threading.get_ident())
                return 12
            def set_health(self, payload, *, ttl_seconds):
                health_ttls.append(ttl_seconds)
                super().set_health(payload, ttl_seconds=ttl_seconds)

        worker = make_worker(state=State())
        await worker._publish_health(force=True)
        for _ in range(100):
            await worker._publish_health()
        assert len(info_threads) == 1 and health_ttls == [30]
        seconds["value"] = 5.0
        await worker._publish_health()
        assert len(info_threads) == 2 and health_ttls == [30, 30]
        assert threading.get_ident() not in info_threads
        await worker.aclose()

    asyncio.run(scenario())


def test_large_frame_parser_and_every_write_leave_loop_schedulable():
    async def scenario():
        state = stream.InMemoryMarketStreamState()
        worker = make_worker(state=state)
        loop_thread = threading.get_ident()
        parse_threads = []
        original = worker.parser.parse_message

        def parse(*args, **kwargs):
            parse_threads.append(threading.get_ident())
            return original(*args, **kwargs)

        worker.parser.parse_message = parse
        total = 200
        batch = json.dumps([json.loads(frame(symbol=f"S{index}")) for index in range(total)])
        ticks = []
        done = False

        async def ticker():
            while not done:
                ticks.append(worker.metrics.messages_received["T"])
                await asyncio.sleep(0)

        tick = asyncio.create_task(ticker())
        await worker.process_raw_message(batch)
        done = True
        await tick
        assert len(state.latest) == total and worker.metrics.dropped_events == 0
        assert loop_thread not in parse_threads
        assert any(0 < value < total for value in ticks)
        assert worker._state_executor._work_queue.qsize() == 0
        await worker.aclose()

    asyncio.run(scenario())


def test_redis_demand_reads_writes_and_publish_all_use_single_state_thread():
    async def scenario():
        calls = []

        class State(stream.InMemoryMarketStreamState):
            def register_demand(self, *args, **kwargs):
                calls.append(("register", threading.get_ident()))
                return super().register_demand(*args, **kwargs)
            def desired_demand(self):
                calls.append(("read", threading.get_ident()))
                return super().desired_demand()
            def publish_updates(self, *args, **kwargs):
                calls.append(("publish", threading.get_ident()))
                return super().publish_updates(*args, **kwargs)

        state = State()
        worker = make_worker(state=state, config=stream.WorkerConfig(enabled=True, publish_coalesce_ms=0))
        worker.demand_loader = lambda: {"portfolio:1": {"AAA"}}

        class Socket:
            async def send(self, payload):
                pass

        plan = await worker.reconcile(Socket())
        await worker.process_raw_message(frame())
        assert plan.symbols == {"AAA"}
        assert [name for name, _ in calls] == ["register", "read", "publish"]
        assert len({thread for _, thread in calls}) == 1
        assert calls[0][1] != threading.get_ident()
        await worker.aclose()

    asyncio.run(scenario())


def test_single_shadow_batch_keeps_receiving_with_slow_rest_and_stops_cleanly():
    async def scenario():
        loop = asyncio.get_running_loop()
        entered, release = asyncio.Event(), threading.Event()
        state = stream.InMemoryMarketStreamState()
        state.set_latest(trade(), ttl_seconds=120)
        rest = RestStub()
        original = rest.get_quote

        def slow_quote(symbol):
            loop.call_soon_threadsafe(entered.set)
            release.wait()
            return original(symbol)

        rest.get_quote = slow_quote
        worker = make_worker(state=state, rest=rest)
        worker._start_shadow_compare(("AAA",))
        first = worker._shadow_task
        await entered.wait()
        for _ in range(100):
            worker._start_shadow_compare(("AAA",))
        assert worker._shadow_task is first
        await worker.process_raw_message(frame(symbol="BBB"))
        assert state.get_latest("BBB", "T") is not None
        assert not first.done() and len(worker._rest_futures) == 1
        close = asyncio.create_task(worker.aclose())
        await asyncio.sleep(0)
        assert not close.done()  # Uncancelable REST work retains its owned slot.
        release.set()
        await close
        assert first.done() and not worker._rest_futures
        assert all(task.done() for task in worker._recovery_tasks)

    asyncio.run(scenario())


def test_recovery_cannot_replace_newer_stream_event_and_retains_actual_source():
    async def scenario():
        loop = asyncio.get_running_loop()
        entered, release = asyncio.Event(), threading.Event()
        state = stream.InMemoryMarketStreamState()
        rest = RestStub()
        original = rest.get_quote

        def delayed_quote(symbol):
            loop.call_soon_threadsafe(entered.set)
            release.wait()
            result = original(symbol)
            result.data.price_source = "last_trade"
            result.data.midpoint = None
            result.data.bid = result.data.ask = None
            return result

        rest.get_quote = delayed_quote
        worker = make_worker(state=state, rest=rest)
        worker._queue_recovery("AAA", reason="stream_reconnect")
        await entered.wait()
        await worker.process_raw_message(frame(sequence=1, price=105, timestamp=NOW + timedelta(seconds=1)))
        release.set()
        await worker.drain_recoveries()
        assert state.get_latest("AAA", "Q") is None
        assert state.get_latest("AAA", "T")["payload"]["price"] == 105
        assert worker.metrics.rest_recovery_rejected == 1
        # An unrelated symbol with no newer observation can retain a real trade
        # recovery price without fabricating a midpoint or its source timestamp.
        worker._queue_recovery("BBB", reason="stream_reconnect")
        await worker.drain_recoveries()
        recovered = state.get_latest("BBB", "Q")
        assert recovered["source_mode"] == "rest"
        assert recovered["payload"]["midpoint"] is None
        assert recovered["payload"]["recovery_price"] == 100
        assert recovered["payload"]["recovery_price_source"] == "last_trade"
        assert recovered["payload"]["recovery_price_timestamp"] == NOW.isoformat()
        await worker.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("price", [None, 0, -1, float("nan"), float("inf"), 10**400])
def test_invalid_recovery_prices_never_replace_state(price):
    async def scenario():
        rest = RestStub()
        def invalid(symbol):
            result = quote_result(symbol)
            result.data.price = price
            return result
        rest.get_quote = invalid
        state = stream.InMemoryMarketStreamState()
        state.set_latest(trade(), ttl_seconds=120)
        worker = make_worker(state=state, rest=rest)
        worker._queue_recovery("AAA", reason="stream_reconnect")
        await worker.drain_recoveries()
        assert state.get_latest("AAA", "Q") is None
        assert state.get_latest("AAA", "T")["is_stale"]
        assert worker.metrics.rest_recovery_failures == 1
        await worker.aclose()

    asyncio.run(scenario())


def test_missing_recovery_time_and_older_recovery_are_rejected():
    async def scenario():
        rest = RestStub()
        def undated(symbol):
            result = quote_result(symbol)
            result.data.event_timestamp = None
            return result
        rest.get_quote = undated
        worker = make_worker(rest=rest)
        worker._queue_recovery("AAA", reason="stream_reconnect")
        await worker.drain_recoveries()
        assert worker.state.get_latest("AAA", "Q") is None
        rest.get_quote = lambda symbol: quote_result(symbol)
        newer = stream.StreamEvent("T", "BBB", NOW + timedelta(seconds=1), NOW, 1, None, {"price": 110})
        worker.state.set_latest(newer, ttl_seconds=120)
        worker._queue_recovery("BBB", reason="stream_reconnect")
        await worker.drain_recoveries()
        assert worker.state.get_latest("BBB", "Q") is None
        assert worker.metrics.rest_recovery_rejected == 2
        await worker.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("event_time", [NOW + timedelta(days=1), NOW.replace(tzinfo=None)])
def test_untrustworthy_recovery_time_cannot_poison_later_valid_quote(event_time):
    async def scenario():
        rest = RestStub()
        def untrustworthy(symbol):
            result = quote_result(symbol)
            result.data.event_timestamp = event_time
            result.data.is_stale = True
            return result
        rest.get_quote = untrustworthy
        worker = make_worker(rest=rest)
        worker._queue_recovery("AAA", reason="stream_reconnect")
        await worker.drain_recoveries()
        assert worker.state.get_latest("AAA", "Q") is None
        assert ("Q", "AAA") not in worker._stored_timestamps
        assert worker.metrics.rest_recovery_rejected == 1
        await worker.process_raw_message(json.dumps({
            "ev": "Q", "sym": "AAA", "bp": 104, "ap": 106,
            "t": int(NOW.timestamp() * 1000), "q": 1,
        }))
        stored = worker.state.get_latest("AAA", "Q")
        assert stored["source_mode"] == "websocket"
        assert stored["payload"]["midpoint"] == 105
        assert stored["event_timestamp"] == NOW.isoformat()
        assert not stored["is_stale"]
        assert worker.metrics.stale_state_writes_rejected == 0
        await worker.aclose()

    asyncio.run(scenario())


def test_shadow_work_budget_and_fresh_candidate_skip(monkeypatch):
    async def scenario():
        state = stream.InMemoryMarketStreamState()
        rest = RestStub()
        worker = make_worker(state=state, rest=rest,
                             config=stream.WorkerConfig(enabled=True, shadow_batch_symbol_limit=2))
        state.set_latest(trade("AAA"), ttl_seconds=120, stale=True)
        state.set_latest(trade("BBB"), ttl_seconds=120)
        state.set_latest(trade("CCC"), ttl_seconds=120)
        await worker.shadow_compare(("AAA", "BBB", "CCC"))
        assert rest.calls == ["BBB"]
        assert worker.metrics.shadow_skipped == 1
        assert worker.metrics.shadow_budget_exhausted == 1
        await worker.aclose()

    asyncio.run(scenario())


def test_shadow_deadline_cancels_wait_but_keeps_provider_slot_until_completion(monkeypatch):
    async def scenario():
        loop = asyncio.get_running_loop()
        entered, release = asyncio.Event(), threading.Event()
        state = stream.InMemoryMarketStreamState()
        state.set_latest(trade(), ttl_seconds=120)
        rest = RestStub()
        original = rest.get_quote

        def slow_quote(symbol):
            loop.call_soon_threadsafe(entered.set)
            release.wait()
            return original(symbol)

        rest.get_quote = slow_quote
        worker = make_worker(state=state, rest=rest,
                             config=stream.WorkerConfig(enabled=True, recovery_concurrency=1))
        # Deterministic timeout injection at the exact active-call point. It does
        # not use a short, scheduler-dependent wall-clock timeout.
        original_wait_for = asyncio.wait_for
        async def deadline(awaitable, timeout):
            if awaitable.cr_code.co_name != "_rest_call":
                return await original_wait_for(awaitable, timeout)
            pending = asyncio.create_task(awaitable)
            await entered.wait()
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            raise asyncio.TimeoutError

        monkeypatch.setattr(stream.asyncio, "wait_for", deadline)
        await worker.shadow_compare(("AAA", "BBB"))
        assert worker.metrics.shadow_budget_exhausted == 1
        assert len(worker._rest_futures) == 1 and worker._rest_slots.locked()
        assert "AAA" in worker._shadow_inflight
        assert "AAA" not in worker._recovery_inflight
        worker._queue_recovery("AAA", reason="stream_reconnect")
        assert worker.metrics.rest_recovery_queued == 1
        assert worker.metrics.rest_recovery_deduped == 0
        close = asyncio.create_task(worker.aclose())
        await asyncio.sleep(0)
        release.set()
        await close
        assert rest.calls == ["AAA"] and not worker._rest_futures
        assert not worker._shadow_inflight and not worker._recovery_inflight
        assert not worker._recovery_generation
        await worker.drain_recoveries()

    asyncio.run(scenario())


def test_higher_sequence_with_older_time_keeps_c6_classification_and_recovery_trigger():
    async def scenario():
        worker = make_worker()
        await worker.process_raw_message(frame(sequence=1, price=105))
        calls = []
        worker._queue_recovery = lambda symbol, reason: calls.append((symbol, reason))
        await worker.process_raw_message(frame(sequence=3, price=99, timestamp=NOW - timedelta(seconds=1)))
        assert worker.metrics.sequence_gaps == 1 and worker.metrics.out_of_order == 0
        assert calls == [("AAA", "sequence_gap")]
        assert worker._last_event[("T", "AAA")][1] == 3
        assert worker.state.get_latest("AAA", "T")["payload"]["price"] == 105
        assert worker.metrics.stale_state_writes_rejected == 1
        await worker.aclose()

    asyncio.run(scenario())


def test_queued_recovery_cannot_stale_mark_new_stream_data_before_dequeue():
    async def scenario():
        loop = asyncio.get_running_loop()
        entered, release = asyncio.Event(), threading.Event()
        rest = RestStub()
        original = rest.get_quote
        def delayed(symbol):
            if symbol == "AAA":
                loop.call_soon_threadsafe(entered.set)
                release.wait()
            result = original(symbol)
            if symbol == "BBB":
                result.data.price = None
            return result
        rest.get_quote = delayed
        worker = make_worker(rest=rest, config=stream.WorkerConfig(enabled=True, recovery_concurrency=1))
        worker._queue_recovery("AAA", reason="stream_reconnect")
        await entered.wait()
        worker._queue_recovery("BBB", reason="stream_reconnect")
        await worker.process_raw_message(frame(symbol="BBB", price=105))
        assert not worker.state.get_latest("BBB", "T")["is_stale"]
        release.set()
        await worker.drain_recoveries()
        assert not worker.state.get_latest("BBB", "T")["is_stale"]
        assert rest.calls == ["AAA"]
        assert worker.metrics.rest_recovery_rejected == 1
        await worker.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["stale", "undated", "future", "daily_close"])
def test_shadow_rejects_stale_or_undated_rest_comparison(change):
    async def scenario():
        rest = RestStub()
        def unusable(symbol):
            result = quote_result(symbol)
            result.data.price = 50
            if change == "stale":
                result.data.is_stale = True
            elif change == "undated":
                result.data.event_timestamp = None
            elif change == "future":
                result.data.event_timestamp = NOW + timedelta(seconds=1)
            else:
                result.data.price_source = "daily_close"
            return result
        rest.get_quote = unusable
        state = stream.InMemoryMarketStreamState()
        state.set_latest(trade(), ttl_seconds=120)
        worker = make_worker(state=state, rest=rest)
        await worker.shadow_compare(("AAA",))
        assert worker.metrics.shadow_comparisons == worker.metrics.shadow_discrepancies == 0
        assert worker.metrics.shadow_skipped == 1
        await worker.aclose()

    asyncio.run(scenario())


def test_equal_timestamp_recovery_does_not_invent_quote_correction():
    async def scenario():
        state = stream.InMemoryMarketStreamState()
        state.set_latest(stream.StreamEvent("Q", "AAA", NOW, NOW, 1, None,
                                           {"midpoint": 105, "bid": 104, "ask": 106}), ttl_seconds=120)
        worker = make_worker(state=state)
        worker._queue_recovery("AAA", reason="stream_reconnect")
        await worker.drain_recoveries()
        assert state.get_latest("AAA", "Q")["payload"]["midpoint"] == 105
        assert worker.metrics.rest_recovery_rejected == 1
        await worker.aclose()

    asyncio.run(scenario())


def test_shutdown_clears_unstarted_queue_jobs_and_drain_remains_safe():
    async def scenario():
        loop = asyncio.get_running_loop()
        entered, release = asyncio.Event(), threading.Event()
        rest = RestStub()
        original = rest.get_quote
        def delayed(symbol):
            loop.call_soon_threadsafe(entered.set)
            release.wait()
            return original(symbol)
        rest.get_quote = delayed
        worker = make_worker(rest=rest, config=stream.WorkerConfig(enabled=True, recovery_concurrency=1))
        worker._queue_recovery("AAA", reason="stream_reconnect")
        await entered.wait()
        worker._queue_recovery("BBB", reason="stream_reconnect")
        worker._queue_recovery("CCC", reason="stream_reconnect")
        close = asyncio.create_task(worker.aclose())
        await asyncio.sleep(0)
        release.set()
        await close
        await worker.drain_recoveries()
        assert worker._recovery_queue.empty()
        assert not worker._recovery_inflight and not worker._recovery_generation
        assert not worker._rest_futures
        assert rest.calls == ["AAA"]

    asyncio.run(scenario())


def test_queue_overflow_stale_marker_cannot_invalidate_newer_frame():
    async def scenario():
        loop = asyncio.get_running_loop()
        entered, release = asyncio.Event(), threading.Event()
        rest = RestStub()
        original = rest.get_quote
        def delayed(symbol):
            if symbol == "AAA":
                loop.call_soon_threadsafe(entered.set)
                release.wait()
            return original(symbol)
        rest.get_quote = delayed
        worker = make_worker(rest=rest, config=stream.WorkerConfig(enabled=True, recovery_concurrency=1, recovery_queue_max=1))
        worker._queue_recovery("AAA", reason="stream_reconnect")
        await entered.wait()
        worker._queue_recovery("BBB", reason="stream_reconnect")
        worker._queue_recovery("CCC", reason="sequence_gap")
        assert worker.metrics.rest_recovery_queue_drops == 1
        await worker.process_raw_message(frame(symbol="CCC", price=105))
        assert not worker.state.get_latest("CCC", "T")["is_stale"]
        release.set()
        await worker.drain_recoveries()
        assert rest.calls == ["AAA", "BBB"]
        await worker.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", ["sequence_gap", "stream_reconnect"])
def test_failed_shadow_does_not_consume_actual_recovery_cooldown(reason):
    async def scenario():
        calls = []
        rest = RestStub()
        def provider(symbol):
            calls.append(symbol)
            if len(calls) == 1:
                raise stream.ProviderError("synthetic failed shadow")
            result = quote_result(symbol)
            result.data.price = 110
            return result
        rest.get_quote = provider
        state = stream.InMemoryMarketStreamState()
        state.set_latest(trade(), ttl_seconds=120)
        worker = make_worker(state=state, rest=rest)
        try:
            await worker.shadow_compare(("AAA",))
            assert "AAA" not in worker._last_recovery_monotonic
            worker._queue_recovery("AAA", reason=reason)
            await worker.drain_recoveries()
            assert calls == ["AAA", "AAA"]
            assert state.get_latest("AAA", "Q")["payload"]["recovery_price"] == 110
            cooldown = worker._last_recovery_monotonic["AAA"]
            worker._queue_recovery("AAA", reason=reason)
            await worker.shadow_compare(("AAA",))
            assert calls == ["AAA", "AAA"]
            assert worker._last_recovery_monotonic["AAA"] == cooldown
            assert worker.metrics.rest_recovery_queued == worker.metrics.rest_recovery_deduped == 1
        finally:
            await worker.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("shadow_fails,waiting_for_slot", [(False, False), (True, False), (True, True)])
def test_inflight_shadow_retains_one_queued_recovery_without_future_overwrite(shadow_fails, waiting_for_slot):
    async def scenario():
        loop = asyncio.get_running_loop()
        shadow_entered, recovery_entered, stale_marked = asyncio.Event(), asyncio.Event(), asyncio.Event()
        shadow_release, recovery_release = threading.Event(), threading.Event()
        calls = []
        rest = RestStub()
        def provider(symbol):
            calls.append(symbol)
            if len(calls) == 1:
                loop.call_soon_threadsafe(shadow_entered.set)
                shadow_release.wait()
                if shadow_fails:
                    raise stream.ProviderError("synthetic failed active shadow")
            else:
                loop.call_soon_threadsafe(recovery_entered.set)
                recovery_release.wait()
            result = quote_result(symbol)
            result.data.price = 110 if len(calls) > 1 else 100
            return result
        rest.get_quote = provider
        class State(stream.InMemoryMarketStreamState):
            def mark_symbols_stale(self, *args, **kwargs):
                super().mark_symbols_stale(*args, **kwargs)
                loop.call_soon_threadsafe(stale_marked.set)
        state = State()
        state.set_latest(trade(), ttl_seconds=120)
        worker = make_worker(state=state, rest=rest)
        held_slots = 0
        try:
            waiting = asyncio.Event()
            if waiting_for_slot:
                # Synthetic competing work occupies the unchanged two slots.
                await worker._rest_slots.acquire()
                await worker._rest_slots.acquire()
                held_slots = 2
                acquire = worker._rest_slots.acquire
                async def admission():
                    waiting.set()
                    return await acquire()
                worker._rest_slots.acquire = admission
            worker._start_shadow_compare(("AAA",))
            await (waiting.wait() if waiting_for_slot else shadow_entered.wait())
            shadow_future = worker._rest_futures.get("AAA")
            worker._queue_recovery("AAA", reason="sequence_gap")
            assert worker.metrics.rest_recovery_queued == 1
            worker._queue_recovery("AAA", reason="stream_reconnect")
            await stale_marked.wait()
            assert state.get_latest("AAA", "T")["is_stale"]
            assert worker._rest_futures.get("AAA") is shadow_future
            if waiting_for_slot:
                assert calls == [] and "AAA" in worker._shadow_inflight
                for _ in range(held_slots):
                    worker._rest_slots.release()
                held_slots = 0
                await shadow_entered.wait()
                shadow_future = worker._rest_futures["AAA"]
            assert calls == ["AAA"] and not recovery_entered.is_set()
            assert worker.config.recovery_concurrency == 2 and worker._rest_slots._value == 1
            shadow_release.set()
            await recovery_entered.wait()
            assert worker._rest_futures["AAA"] is not shadow_future
            worker._queue_recovery("AAA", reason="sequence_gap")
            assert worker.metrics.rest_recovery_deduped == 2
            recovery_release.set()
            await worker.drain_recoveries()
            await worker._shadow_task
            assert calls == ["AAA", "AAA"]
            assert state.get_latest("AAA", "Q")["payload"]["recovery_price"] == 110
            assert not worker._rest_futures and not worker._shadow_inflight
            assert not worker._recovery_inflight and not worker._recovery_generation
        finally:
            for _ in range(held_slots):
                worker._rest_slots.release()
            shadow_release.set()
            recovery_release.set()
            await worker.aclose()

    asyncio.run(scenario())
