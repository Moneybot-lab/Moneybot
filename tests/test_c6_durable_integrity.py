"""Durable C6 safety boundaries. Run only through offline_investigation_runner.

Lua tests execute the unchanged production script in local liblua, with no
Redis process or socket. They do not certify Redis durability/failover policy.
"""
import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from moneybot import api
from moneybot.services import market_stream as stream
from moneybot.services import market_data_providers as providers
from moneybot.services.live_market import LiveQuoteResolver
from moneybot.services.stream_integrity import IntegrityRejection, Watermark, timestamp_us
from tests.offline_redis_integrity import RedisLuaAdapter
from tests.test_c6_freshness_admission import raw

NOW = datetime(2026, 10, 13, 15, tzinfo=timezone.utc)


async def send(worker, value):
    await worker.process_raw_message(json.dumps(value))
    # Exercise the production loop's subsequent health publication explicitly.
    # Controlled wall time advances independently of real monotonic test time.
    await worker._publish_health(force=True)


class Backend:
    def __init__(self, clock):
        self.clock, self.values = clock, {}
        self.failure = None
        self.failure_mode = None
    def get(self, key):
        value, expiry = self.values.get(key, (None, 0))
        if expiry <= self.clock.now.timestamp():
            self.values.pop(key, None)
            return None
        return value
    def set(self, key, value, ex):
        failing = ':latest:' in key and self.failure
        if failing and self.failure_mode and json.loads(value).get('source_mode') != self.failure_mode:
            failing = False
        if failing == 'before_set':
            raise RuntimeError('verified local SET failure before mutation')
        self.values[key] = value, self.clock.now.timestamp() + ex
        if failing == 'after_set':
            raise RuntimeError('SET result unavailable after mutation')
        return True
    def publish(self, *args): return 0
    def info(self, *args): return {'used_memory': 0}


def repository(clock, backend=None, initialize=True):
    state = object.__new__(stream.RedisMarketStreamState)
    state.namespace = 'moneybot:market:v1'
    state.client = RedisLuaAdapter(backend or Backend(clock), initialize=initialize)
    state._expected_integrity_control = state.client.get(state.namespace+':integrity-control') or ''
    return state


def worker_for(clock, state, **options):
    def forbidden(*args, **kwargs):
        pytest.fail('Unexpected provider/transport operation')
    worker = stream.MassiveWebSocketWorker(api_key='offline', state=state,
        rest_client=SimpleNamespace(get_quote=forbidden), connect_factory=forbidden,
        config=stream.WorkerConfig(enabled=True, shadow_mode=False, publish_coalesce_ms=100000, **options),
        clock=lambda: clock.now)
    worker._connection_state = 'connected'
    return worker


@asynccontextmanager
async def rig(**options):
    clock = SimpleNamespace(now=NOW)
    state = repository(clock)
    worker = worker_for(clock, state, **options)
    try: yield clock, state, worker
    finally: await worker.aclose()


def record(state, channel='Q', symbol='AAA'):
    return Watermark.decode(state.client.hget(state.namespace + ':integrity', channel + ':' + symbol))


def response(clock, when, price=100):
    return SimpleNamespace(data=SimpleNamespace(price=price, event_timestamp=when,
        sequence_number=None, provider_event_id=None, bid=price-.5, ask=price+.5,
        bid_size=1, ask_size=1, midpoint=price, is_stale=False, price_source='last_trade'),
        received_timestamp=clock.now)


@pytest.mark.parametrize('channel', ['Q', 'T'])
@pytest.mark.parametrize('failure', ['before_set', 'after_set', 'lost_script_ack'])
def test_uncertain_commit_reconciles_without_lowering_floor(channel, failure):
    async def scenario():
        async with rig() as (clock, state, worker):
            await send(worker, raw(channel))
            committed = record(state, channel)
            clock.now += timedelta(seconds=1)
            original = state.client.eval
            if failure == 'lost_script_ack':
                def lost(*args):
                    original(*args)
                    raise RuntimeError('lost complete script reply')
                state.client.eval = lost
            else: state.client.backend.failure = failure
            with pytest.raises(RuntimeError):
                await send(worker, raw(channel, when=clock.now, q=101, price=101))
            # Ordering only follows acknowledged writes; reservation is distinct.
            assert worker._last_event[(channel, 'AAA')][0] == NOW
            bound = record(state, channel)
            assert bound.reserved_us == timestamp_us(clock.now)
            assert bound.committed_us == (bound.reserved_us if failure == 'lost_script_ack' else committed.committed_us)
            assert bound.status == ('c' if failure == 'lost_script_ack' else 'u')
            assert worker.health_payload()['freshness']['held_channels'][0]['reason'] == 'state_write_outcome_unknown'
            state.client.eval = original
            state.client.backend.failure = None
            # A new repository/process reads durable state, not the expired value.
            await worker.aclose()
            clock.now += timedelta(seconds=121)
            restarted_state = repository(clock, state.client.backend, initialize=False)
            restarted = worker_for(clock, restarted_state)
            try:
                assert restarted_state.get_latest('AAA', channel) is None
                assert restarted_state.integrity_floor('AAA', channel) == (NOW + timedelta(seconds=1), failure != 'lost_script_ack')
                await send(restarted, raw(channel, q=9999, when=NOW, price=99))
                assert restarted_state.get_latest('AAA', channel) is None
                await send(restarted, raw(channel, q=10000, when=NOW+timedelta(seconds=1), price=105))
                assert restarted_state.get_latest('AAA', channel) is None  # Unbound equal time.
                await send(restarted, raw(channel, q=1, when=clock.now, price=102))
                assert record(state, channel).status == 'c'
                assert record(state, channel).committed_us == timestamp_us(clock.now)
                assert restarted.health_payload()['freshness']['state'] == 'no_known_hold'
            finally: await restarted.aclose()
    asyncio.run(scenario())


def test_missing_or_corrupt_durable_state_never_bootstraps_history():
    async def scenario():
        async with rig() as (clock, state, worker):
            await send(worker, raw())
            clock.now += timedelta(seconds=121)
            state.client.hashes.clear()
            await send(worker, raw(q=9999, when=NOW-timedelta(seconds=1)))
            assert state.get_latest('AAA', 'Q') is None
            assert not state.client.hashes
            assert worker.health_payload()['freshness']['held_channels'][0]['reason'] == 'integrity_state_missing'
            state.client.hashes[state.namespace+':integrity'] = {'__schema__':'wrong'}
            await send(worker, raw(q=10000, when=clock.now))
            assert state.get_latest('AAA', 'Q') is None
            assert worker.health_payload()['freshness']['held_channels'][0]['reason'] == 'invalid_integrity_schema'
    asyncio.run(scenario())


@pytest.mark.parametrize('newer_first', [True, False])
def test_atomic_script_prevents_concurrent_timestamp_regression(newer_first):
    clock = SimpleNamespace(now=NOW+timedelta(seconds=3))
    state = repository(clock)
    other = repository(clock, state.client.backend, initialize=False)
    owner = str(uuid4())
    def event(seconds):
        parsed = stream.MassiveStreamParser().parse_event(raw(when=NOW+timedelta(seconds=seconds)), received_at=clock.now)
        return replace(parsed, stream_commit={'worker_instance_id': owner, 'revision':seconds})
    first, second = (2, 1) if newer_first else (1, 2)
    entered, release = threading.Event(), threading.Event()
    original = state.client.backend.set
    def paused(key, value, ex):
        entered.set()
        assert release.wait(2)
        return original(key, value, ex)
    state.client.backend.set = paused
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(state.set_latest, event(first), ttl_seconds=120)
        assert entered.wait(2)
        b = pool.submit(other.set_latest, event(second), ttl_seconds=120)
        release.set()
        a.result(timeout=2)
        if newer_first:
            with pytest.raises(IntegrityRejection): b.result(timeout=2)
        else: b.result(timeout=2)
    assert record(state).committed_us == timestamp_us(NOW+timedelta(seconds=2))
    assert state.get_latest('AAA', 'Q')['event_timestamp'] == (NOW+timedelta(seconds=2)).isoformat()


@pytest.mark.parametrize('channel', ['Q', 'T'])
def test_script_equal_time_rejects_different_worker_owner(channel):
    clock = SimpleNamespace(now=NOW)
    state = repository(clock)
    parsed = stream.MassiveStreamParser().parse_event(raw(channel), received_at=NOW)
    state.set_latest(replace(parsed, stream_commit={'worker_instance_id':str(uuid4()),'revision':1}), ttl_seconds=120)
    with pytest.raises(IntegrityRejection, match='equal_time_unbound'):
        state.set_latest(replace(parsed, stream_commit={'worker_instance_id':str(uuid4()),'revision':2}), ttl_seconds=120)


@pytest.mark.parametrize('failure', ['before_set', 'after_set', 'lost_script_ack'])
def test_recovery_unknown_commit_survives_expiry_and_restart(failure):
    async def scenario():
        async with rig(recovery_cooldown_seconds=0) as (clock, state, worker):
            await send(worker, raw())
            clock.now += timedelta(seconds=1)
            worker.rest_client = SimpleNamespace(get_quote=lambda _: response(clock, clock.now, 101))
            state.client.backend.failure_mode = 'rest'
            original = state.client.eval
            if failure == 'lost_script_ack':
                def lost(*args):
                    result = original(*args)
                    if args[0] == stream.COMMIT_LUA:
                        raise RuntimeError('lost commit reply')
                    return result
                state.client.eval = lost
            else: state.client.backend.failure = failure
            await worker._recover_symbols(['AAA'], reason='offline')
            assert worker.metrics.rest_recovery_failures == 1
            assert record(state).reserved_us == timestamp_us(clock.now)
            state.client.eval = original; state.client.backend.failure = None
            clock.now += timedelta(seconds=max(worker.config.state_ttl_seconds, worker.config.stale_ttl_seconds)+1)
            restarted = worker_for(clock, repository(clock, state.client.backend, initialize=False), recovery_cooldown_seconds=0)
            try:
                restarted.rest_client = SimpleNamespace(get_quote=lambda _: response(clock, NOW))
                await restarted._recover_symbols(['AAA'], reason='offline')
                assert restarted.state.get_latest('AAA', 'Q') is None
                assert restarted.metrics.rest_recovery_rejected == 1
                assert record(state).reserved_us == timestamp_us(NOW+timedelta(seconds=1))
            finally: await restarted.aclose()
    asyncio.run(scenario())


def test_retirement_pins_inflight_generation_and_rejects_late_recovery():
    async def scenario():
        async with rig(symbol_cap=1, recovery_cooldown_seconds=0) as (clock, state, worker):
            await send(worker, raw())
            await worker.flush_updates_if_due(force=True)
            entered, release = threading.Event(), threading.Event()
            def get(_):
                entered.set()
                assert release.wait(3)
                return response(clock, NOW+timedelta(seconds=1), 90)
            worker.rest_client = SimpleNamespace(get_quote=get)
            clock.now += timedelta(seconds=3)
            worker._queue_recovery('AAA', reason='offline')
            assert await asyncio.to_thread(entered.wait, 2)
            try:
                await worker._state_call(worker._retire_inactive_history)
                assert worker._symbol_generation['AAA'] == 1
                await send(worker, raw(q=1, when=clock.now, price=103))
                assert worker._symbol_generation['AAA'] == 2
            finally: release.set()
            await worker.drain_recoveries()
            assert state.get_latest('AAA', 'Q')['payload']['midpoint'] == 103
            assert record(state).committed_us == timestamp_us(clock.now)
            assert worker.metrics.rest_recovery_rejected == 1
            await worker.flush_updates_if_due(force=True)
            await worker._state_call(worker._retire_inactive_history)
            assert not worker._last_event and not worker._symbol_generation
            clock.now += timedelta(seconds=121)
            await send(worker, raw(q=9999, when=NOW))
            assert state.get_latest('AAA', 'Q') is None
            await send(worker, raw(q=1, when=clock.now, price=104))
            assert worker._symbol_generation['AAA'] == 1
            assert state.get_latest('AAA','Q')['payload']['midpoint'] == 104
    asyncio.run(scenario())


def test_bounded_durable_eviction_preserves_retired_floor(monkeypatch):
    monkeypatch.setattr(stream, 'MAX_WATERMARKS', 4)
    clock = SimpleNamespace(now=NOW)
    state = repository(clock)
    for index in range(40):
        clock.now += timedelta(seconds=1)
        event = stream.MassiveStreamParser().parse_event(raw(symbol=f'S{index}', when=clock.now), received_at=clock.now)
        state.set_latest(replace(event, stream_commit={'worker_instance_id':str(uuid4()), 'revision':1}), ttl_seconds=1)
        assert len(state.client.hashes[state.namespace+':integrity']) <= 7
        assert len(state.client.zsets[state.namespace+':integrity-lru']) <= 4
    returning = stream.MassiveStreamParser().parse_event(raw(symbol='S0', when=NOW+timedelta(seconds=1)), received_at=clock.now)
    with pytest.raises(IntegrityRejection):
        state.set_latest(replace(returning, stream_commit={'worker_instance_id':str(uuid4()), 'revision':1}), ttl_seconds=1)
    stamp, uncertain = repository(clock, state.client.backend, initialize=False).integrity_floor('S0', 'Q')
    assert uncertain and stamp > returning.event_timestamp
    clock.now += timedelta(seconds=1)
    state.set_latest(replace(returning, event_timestamp=clock.now, stream_commit={'worker_instance_id':str(uuid4()), 'revision':1}), ttl_seconds=1)
    assert record(state, symbol='S0').committed_us == timestamp_us(clock.now)


def test_thousands_of_symbols_bound_worker_histories_cooldowns_and_rest_cache():
    async def scenario():
        clock = SimpleNamespace(now=NOW)
        state = stream.InMemoryMarketStreamState(clock=lambda:clock.now.timestamp())
        worker = worker_for(clock, state, symbol_cap=1, recovery_cooldown_seconds=0)
        worker.rest_client = SimpleNamespace(get_quote=lambda _: None)
        class Socket:
            async def send(self, _): pass
        socket = Socket()
        try:
            for index in range(2100):
                symbol = f'S{index:04}'
                state.register_demand('ticks:churn', [symbol], ttl_seconds=90)
                await worker.reconcile(socket)
                await send(worker, raw('T', symbol=symbol, when=clock.now))
                # Exercise real request admission with deterministic empty local results.
                await worker.shadow_compare([symbol])
                await worker._recover_symbols([symbol], reason='offline')
                await worker.flush_updates_if_due(force=True)
                state.clear_demand('ticks:churn'); await worker.reconcile(socket)
                worker.actual = {channel:set() for channel in stream.ALLOWED_EVENT_TYPES}
                clock.now += timedelta(seconds=301)
                assert state.get_latest(symbol, 'T') is None
                assert len(worker._last_event) <= 4 * worker.config.symbol_cap
                assert len(worker._stored_timestamps) <= 4 and len(worker._history_keys) <= 4
                assert len(worker._symbol_generation) <= 1
                assert len(worker._last_recovery_monotonic) <= 1024
                assert len(worker._last_shadow_monotonic) <= 1024
                assert len(state.integrity.records) <= 1024
                assert not worker._recovery_generation and not worker._rest_futures
            await send(worker, raw('T', symbol='S0000', when=NOW, q=999999))
            assert state.get_latest('S0000','T') is None
            await send(worker, raw('T', symbol='S0000', when=clock.now, q=1, price=102))
            assert state.get_latest('S0000','T')['payload']['price'] == 102
        finally: await worker.aclose()
    asyncio.run(scenario())
    client = providers.MassiveRestClient(api_key='offline', http_get=lambda *a, **k:pytest.fail('No HTTP'), cache_max_entries=32)
    for index in range(4000):
        client._cache_set(str(index), providers.ProviderError('synthetic'), ttl=86400)
        assert len(client._cache) <= 32
    assert client._cache_get('0') is None
    with pytest.raises(providers.ProviderError): client._cache_get('3999')


def test_evicted_cooldown_does_not_restore_budget_or_cross_shadow_boundary():
    recovery = stream._BoundedCooldown(capacity=2)
    shadow = stream._BoundedCooldown(capacity=2)
    for index in range(20): shadow[str(index)] = float(index)
    assert recovery.get('0') is None and len(shadow) == 2
    for index in range(20): recovery[str(index)] = float(index)
    assert recovery.get('0') == 17 and recovery.get('unseen') == 17
    assert recovery.get('19') == 19 and len(recovery) == 2


@pytest.mark.parametrize('channel', ['Q','T'])
def test_quality_only_sse_change_and_fresh_commit_restore(channel):
    async def scenario():
        async with rig() as (clock, state, worker):
            await send(worker, raw(channel))
            resolver = LiveQuoteResolver(state=state, rest_quote=lambda _:pytest.fail('No REST'), clock=lambda:clock.now)
            delivery = api._SSEQuoteDelivery()
            first, changed = delivery.apply(resolver.resolve('AAA').payload(), now=clock.now, calendar=resolver.calendar)
            assert first['admission_status'] == 'LIVE' and changed
            assert delivery.changed(first)
            await send(worker, raw(channel, q=1, price=101))
            await worker._publish_health(force=True)
            held, changed = delivery.apply(resolver.resolve('AAA').payload(), now=clock.now, calendar=resolver.calendar)
            assert held['admission_status'] == 'DEGRADED' and held['is_degraded']
            assert not changed and delivery.changed(held)  # Quality delivery, no advice trigger.
            assert held['price'] == first['price'] and held['stream_commit'] == first['stream_commit']
            worker._connection_state = 'reconnecting'; await worker._publish_health(force=True)
            assert resolver.resolve('AAA').is_degraded
            worker._connection_state = 'connected'; await worker._publish_health(force=True)
            assert resolver.resolve('AAA').admission_status == 'DEGRADED'
            clock.now += timedelta(seconds=1)
            await send(worker, raw(channel, q=1, when=clock.now, price=102))
            await worker._publish_health(force=True)
            fresh, changed = delivery.apply(resolver.resolve('AAA').payload(), now=clock.now, calendar=resolver.calendar)
            assert fresh['price'] == 102 and fresh['admission_status'] == 'LIVE' and changed and not fresh['is_degraded']
    asyncio.run(scenario())


def test_quality_isolation_health_expiry_and_fallback_provenance():
    async def scenario():
        async with rig() as (clock, state, worker):
            await send(worker, [raw(), raw('T', symbol='BBB')])
            await send(worker, raw(q=1, price=101))
            await worker._publish_health(force=True)
            resolver = LiveQuoteResolver(state=state, rest_quote=lambda _: {}, clock=lambda:clock.now)
            assert resolver.resolve('AAA').admission_status == 'DEGRADED'
            assert resolver.resolve('BBB').admission_status == 'LIVE'
            await send(worker, raw('T', price=102))
            await worker._publish_health(force=True)
            assert resolver.resolve('AAA').event_type == 'T' and resolver.resolve('AAA').admission_status == 'LIVE'
            state.client.backend.values.pop(state.namespace+':health')
            assert resolver.resolve('BBB').admission_status == 'UNKNOWN'
            assert resolver.resolve('BBB').is_degraded
            clock.now += timedelta(seconds=121)
            resolver.rest_quote = lambda _:dict(price=88, event_timestamp=NOW.isoformat(), source='offline_rest', source_mode='rest')
            value = resolver.resolve('BBB')
            assert value.price == 88 and value.event_timestamp == NOW.isoformat() and value.is_stale
            assert value.source_mode == 'rest'
    asyncio.run(scenario())


def test_atomic_stale_mark_cannot_restore_snapshot_over_concurrent_commit():
    clock = SimpleNamespace(now=NOW)
    state = repository(clock)
    parser = stream.MassiveStreamParser()
    owner = str(uuid4())
    first = replace(parser.parse_event(raw(), received_at=NOW), stream_commit={'worker_instance_id':owner,'revision':1})
    state.set_latest(first, ttl_seconds=120)
    newer = replace(first, event_timestamp=NOW+timedelta(seconds=1), stream_commit={'worker_instance_id':owner,'revision':2}, payload={'midpoint':101})
    original = state.client.get
    armed = [True]
    def interleaved(key):
        old = original(key)
        if ':latest:Q:' in key and armed[0]:
            armed[0] = False
            state.set_latest(newer, ttl_seconds=120)
        return old
    state.client.get = interleaved
    state.mark_symbols_stale(['AAA'], reason='offline_disconnect', ttl_seconds=300)
    latest = state.get_latest('AAA','Q')
    assert latest['payload']['midpoint'] == 101 and not latest['is_stale']
    assert record(state).committed_us == timestamp_us(newer.event_timestamp)


@pytest.mark.parametrize('error', [providers.ProviderError('offline failure'), TimeoutError('offline timeout')])
def test_recovery_failure_timeout_preserves_bounds_and_releases_owned_work(error):
    async def scenario():
        async with rig() as (clock, state, worker):
            await send(worker, raw())
            before = record(state)
            def fail(_): raise error
            worker.rest_client = SimpleNamespace(get_quote=fail)
            await worker._recover_symbols(['AAA'], reason='offline')
            assert record(state) == before
            assert worker.metrics.rest_recovery_failures == 1
            assert state.get_latest('AAA','Q')['is_stale']
            assert not worker._recovery_inflight and not worker._recovery_generation and not worker._rest_futures
            worker._queue_recovery('AAA', reason='offline_duplicate')
            assert worker.metrics.rest_recovery_deduped == 1
            assert worker.metrics.rest_recovery_queued == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['before_set','after_set','lost_script_ack'])
def test_visible_price_does_not_prove_confirmed_live_storage(failure):
    async def scenario():
        async with rig() as (clock, state, worker):
            await send(worker, raw())
            original = state.client.eval
            clock.now += timedelta(seconds=1)
            if failure == 'lost_script_ack':
                def lost(*args):
                    original(*args)
                    raise RuntimeError('lost reply')
                state.client.eval = lost
            else: state.client.backend.failure = failure
            with pytest.raises(RuntimeError):
                await send(worker, raw(when=clock.now, q=101, price=101))
            resolver = LiveQuoteResolver(state=state, rest_quote=lambda _: pytest.fail('No REST'), clock=lambda:clock.now)
            # Old health exists and the price key is visible, but no current
            # confirmed admission may be inferred from those facts alone.
            current = resolver.resolve('AAA')
            assert current.price in {100,101} and current.is_degraded
            assert current.admission_status == 'UNKNOWN'
            state.client.eval = original; state.client.backend.failure = None
            await worker._publish_health(force=True)
            assert resolver.resolve('AAA').admission_status == ('DEGRADED' if failure == 'lost_script_ack' else 'UNKNOWN')
            clock.now += timedelta(seconds=1)
            await send(worker, raw(when=clock.now, q=1, price=102))
            assert resolver.resolve('AAA').price == 102 and resolver.resolve('AAA').admission_status == 'LIVE'
    asyncio.run(scenario())


def test_legacy_value_has_unknown_admission_even_with_healthy_connection():
    clock = SimpleNamespace(now=NOW)
    state = repository(clock)
    event = stream.MassiveStreamParser().parse_event(raw(), received_at=NOW)
    # A pre-migration value already exists; new repository writes are fenced.
    state.client.backend.set(state._latest_key('AAA','Q'), json.dumps(event.serialized()), ex=120)
    state.set_health({'connection_state':'connected','updated_at':NOW.isoformat()}, ttl_seconds=30)
    resolver = LiveQuoteResolver(state=state,rest_quote=lambda _:pytest.fail('No REST'),clock=lambda:clock.now)
    value = resolver.resolve('AAA')
    assert value.price == 100 and value.admission_status == 'UNKNOWN' and value.is_degraded


def test_actual_api_sse_browser_quality_transition_without_new_price(monkeypatch):
    from flask import Flask, g, session
    from tests import test_portfolio_ui_offline_investigation as ui
    async def setup():
        async with rig() as (clock,state,worker):
            await send(worker, raw())
            healthy = state.get_health()
            healthy_quality = dict(state.client.hashes[state.namespace+':integrity-quality'])
            await send(worker, raw(q=1,price=101))
            degraded = state.get_health()
            degraded_quality = dict(state.client.hashes[state.namespace+':integrity-quality'])
            state.set_health(healthy,ttl_seconds=30)
            state.client.hashes[state.namespace+':integrity-quality'] = healthy_quality
            return clock,state,degraded,degraded_quality
    clock,state,degraded,degraded_quality = asyncio.run(setup())
    resolver = LiveQuoteResolver(state=state,rest_quote=lambda _:pytest.fail('No REST'),clock=lambda:clock.now)
    monkeypatch.setattr(api, '_live_quote_resolver', lambda:resolver)
    monkeypatch.setattr(api, '_symbols_for_live_user', lambda _: {'AAA'})
    monkeypatch.setattr(api, '_decision_context_for_user', lambda _: SimpleNamespace(after_hours_alerts=False,profile_version=1))
    elapsed=[100.]
    def advance(_):
        elapsed[0] += 3
        state.client.hashes[state.namespace+':integrity-quality'] = dict(degraded_quality)
        state.set_health(degraded,ttl_seconds=30)
    monkeypatch.setattr(api,'time',SimpleNamespace(time=lambda:elapsed[0],sleep=advance))
    app=Flask(__name__);app.secret_key='offline'
    app.config.update(TESTING=True,LIVE_SSE_INTERVAL_SECONDS=.25,LIVE_SSE_HEARTBEAT_SECONDS=2)
    evaluated=[]
    app.extensions.update(market_stream_state=state,live_trigger_engine=SimpleNamespace(evaluate=lambda **kwargs: (evaluated.append(kwargs) or {'fire':False})))
    with app.test_request_context('/api/live-market-stream?symbols=AAA'):
        session['user_id']=1;g.request_id='offline'
        response=api.live_market_stream();iterator=iter(response.response)
        try: chunks=[next(iterator) for _ in range(4)]
        finally: iterator.close()
    quotes=[json.loads(next(line[6:] for line in part.splitlines() if line.startswith('data: '))) for part in chunks if 'event: quotes' in part]
    assert len(quotes)==2 and len(evaluated)==1
    first,held=[value['quotes'][0] for value in quotes]
    assert first['admission_status']=='LIVE' and held['admission_status']=='DEGRADED'
    assert first['price']==held['price']==100 and first['stream_commit']==held['stream_commit']
    with app.test_request_context('/quote?symbol=AAA'):
        g.request_id='offline'
        value=api.api_quote().get_json()['data']
        assert value['admission_status']=='DEGRADED' and value['is_degraded']
    monkeypatch.setattr(ui,'NOW',clock.now)
    rendered=ui._ui([dict(symbol='AAA',current_price=100,entry_price=90,shares=1)],
        'startPortfolioLive(); EventSource.instances[0].emit("quotes", '+json.dumps(quotes[-1])+'); capture();')
    assert 'Admission degraded' in rendered['cells'][0] and 'WebSocket · Live' not in rendered['cells'][0]


def test_partial_metadata_index_loss_fails_closed_without_latest_mutation():
    clock=SimpleNamespace(now=NOW)
    state=repository(clock)
    event=stream.MassiveStreamParser().parse_event(raw(),received_at=NOW)
    owner=str(uuid4())
    state.set_latest(replace(event,stream_commit={'worker_instance_id':owner,'revision':1}),ttl_seconds=120)
    previous=state.get_latest('AAA','Q')
    state.client.zsets.clear()
    with pytest.raises(IntegrityRejection,match='invalid_integrity_index'):
        state.set_latest(replace(event,event_timestamp=NOW+timedelta(seconds=1),stream_commit={'worker_instance_id':owner,'revision':2}),ttl_seconds=120)
    assert state.get_latest('AAA','Q')==previous


@pytest.mark.parametrize('channel',['A','AM'])
def test_retired_aggregate_sequence_context_is_not_silently_reset(channel):
    async def scenario():
        async with rig() as (clock,state,worker):
            def aggregate(q, price):
                return dict(ev=channel,sym='AAA',q=q,e=int(clock.now.timestamp()*1000),c=price,o=price,h=price,l=price,v=1)
            await send(worker,aggregate(100,100))
            clock.now+=timedelta(seconds=1)
            await send(worker,aggregate(101,101))
            assert state.get_latest('AAA',channel)['sequence_number']==101
            await worker.flush_updates_if_due(force=True)
            await worker._state_call(worker._retire_inactive_history)
            clock.now+=timedelta(seconds=121)
            await send(worker,aggregate(1,102))
            assert state.get_latest('AAA',channel) is None
            assert worker.health_payload()['freshness']['held_channels'][0]['reason']=='aggregate_ordering_context_unavailable'
            assert not worker._recovery_inflight and worker.metrics.rest_recovery_queued==0
    asyncio.run(scenario())


def test_recovery_after_history_retirement_requires_strictly_newer_time():
    async def scenario():
        async with rig(recovery_cooldown_seconds=0) as (clock,state,worker):
            await send(worker,raw())
            await worker.flush_updates_if_due(force=True)
            await worker._state_call(worker._retire_inactive_history)
            assert not worker._last_event and not worker._stored_timestamps
            clock.now += timedelta(seconds=301)
            assert state.get_latest('AAA','Q') is None
            provider_time=[NOW-timedelta(seconds=1)]
            worker.rest_client=SimpleNamespace(get_quote=lambda _:response(clock,provider_time[0],101))
            await worker._recover_symbols(['AAA'],reason='offline')
            assert state.get_latest('AAA','Q') is None and record(state).committed_us==timestamp_us(NOW)
            provider_time[0]=clock.now
            await worker._recover_symbols(['AAA'],reason='offline')
            assert state.get_latest('AAA','Q')['payload']['recovery_price']==101
            assert record(state).committed_us==timestamp_us(clock.now)
            assert not worker._last_event  # REST does not invent SIP ordering.
    asyncio.run(scenario())
