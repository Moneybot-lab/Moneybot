"""Focused C6 admission/commit/SSE regressions. Run with offline_investigation_runner."""
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from moneybot import api
from moneybot.services import market_stream as stream
from moneybot.services.live_market import LiveQuoteResolver

NOW = datetime(2026, 10, 13, 15, tzinfo=timezone.utc)


def raw(channel='Q', *, q=100, when=NOW, price=100, symbol='AAA'):
    result = {'ev': channel, 'sym': symbol, 'q': q, 't': int(when.timestamp()*1000)}
    if channel == 'Q':
        result.update(bp=price-.5, ap=price+.5, bs=1, **{'as':1}, i=[604])
    else:
        result.update(p=price, s=1, i=f'trade-{q}-{price}', x=4, trfi=0)
    return result


@asynccontextmanager
async def rig(**config):
    clock = SimpleNamespace(now=NOW)
    state = stream.InMemoryMarketStreamState(clock=lambda: clock.now.timestamp())
    calls = []
    def forbidden(symbol):
        calls.append(symbol)
        raise AssertionError('No provider work permitted')
    worker = stream.MassiveWebSocketWorker(api_key='synthetic', state=state,
        rest_client=SimpleNamespace(get_quote=forbidden), connect_factory=forbidden,
        config=stream.WorkerConfig(enabled=True, shadow_mode=False, publish_coalesce_ms=100000, **config),
        clock=lambda: clock.now)
    try:
        yield clock, state, worker
    finally:
        await worker.aclose()
        assert calls == []


async def send(worker, value):
    await worker.process_raw_message(json.dumps(value))


def marks(worker):
    return dict(worker._last_event), dict(worker._stored_timestamps), dict(worker._symbol_generation)


@pytest.mark.parametrize('channel',['Q','T'])
def test_old_and_future_inputs_do_not_commit_any_watermark(channel):
    async def scenario():
        async with rig() as (clock,state,worker):
            await send(worker,raw(channel))
            saved=marks(worker)
            await send(worker,raw(channel,q=10000,when=NOW-timedelta(seconds=1),price=99))
            assert marks(worker)==saved
            await send(worker,raw(channel,q=1,when=NOW+timedelta(days=1),price=999))
            assert marks(worker)==saved
            assert worker.metrics.stale_state_writes_rejected==worker.metrics.timestamp_rejections==1
            assert worker.metrics.ambiguous_sequence_regressions==0
            assert worker.health_payload()['freshness']['held_channels'][0]['reason']=='invalid_or_future_timestamp'
            clock.now += timedelta(seconds=1)
            await send(worker,raw(channel,q=101,when=clock.now,price=101))
            assert state.get_latest('AAA',channel)['sequence_number']==101
            assert worker._symbol_generation['AAA']==2
            assert worker.health_payload()['freshness']['state']=='no_known_hold'
    asyncio.run(scenario())


@pytest.mark.parametrize('channel',['Q','T'])
def test_restart_repeated_fresh_updates_and_expired_redis_state(channel):
    async def scenario():
        async with rig() as (clock,state,worker):
            await send(worker,raw(channel,q=10000))
            for q,seconds in ((1,121),(2,122),(2,123),(8,124)):
                clock.now=NOW+timedelta(seconds=seconds)
                await send(worker,raw(channel,q=q,when=clock.now,price=100+seconds))
                assert state.get_latest('AAA',channel)['event_timestamp']==clock.now.isoformat()
            assert worker.metrics.ambiguous_sequence_regressions==1
            assert worker.metrics.sequence_gaps==1
            assert worker.metrics.rest_recovery_queued==worker.metrics.duplicates==0
            assert worker._symbol_generation['AAA']==5
    asyncio.run(scenario())


@pytest.mark.parametrize('channel',['Q','T'])
@pytest.mark.parametrize('sequence',[None,99,100])
def test_equal_time_ambiguous_change_holds_explicitly_then_newer_time_recovers(channel,sequence):
    async def scenario():
        async with rig() as (clock,state,worker):
            first=raw(channel)
            await send(worker,first)
            saved=marks(worker)
            await send(worker,raw(channel,q=sequence,price=101))
            assert marks(worker)==saved
            assert worker.metrics.freshness_holds==1 and worker.metrics.duplicates==0
            assert worker.health_payload()['freshness']['state']=='degraded'
            await send(worker,first)  # A genuine duplicate does not clear degradation.
            assert worker.metrics.duplicates==1 and worker.health_payload()['freshness']['state']=='degraded'
            clock.now+=timedelta(seconds=1)
            await send(worker,raw(channel,q=sequence,when=clock.now,price=101))
            assert state.get_latest('AAA',channel)['payload'].get('price',101)==101
            assert worker.health_payload()['freshness']['state']=='no_known_hold'
    asyncio.run(scenario())


@pytest.mark.parametrize('bad',[None,'invalid',1e100,float('nan')])
def test_invalid_provider_timestamp_never_changes_history(bad):
    async def scenario():
        async with rig() as (clock,state,worker):
            await send(worker,raw())
            saved=marks(worker);message=raw(q=101);message['t']=bad
            await send(worker,message)
            assert marks(worker)==saved and worker.metrics.parse_failures==1
    asyncio.run(scenario())


@pytest.mark.parametrize('bad',[None,'clock',datetime(2026,10,13,15),'raises'])
def test_clock_failure_rejects_frame_without_inventing_time(bad):
    async def scenario():
        async with rig() as (clock,state,worker):
            await send(worker,raw())
            saved=marks(worker)
            def broken():
                if bad=='raises':raise RuntimeError('synthetic clock unavailable')
                return bad
            worker.clock=broken
            await send(worker,raw(q=101))
            assert marks(worker)==saved and worker.metrics.invalid_clock_frames==1
            health=worker.health_payload()
            assert health['updated_at'] is None and not health['freshness']['clock_trusted']
            worker.clock=lambda: clock.now
            await send(worker,raw(q=101))
            assert worker._symbol_generation['AAA']==2
    asyncio.run(scenario())


@pytest.mark.parametrize('when', ['2026-03-09T03:59:00-04:00','2026-11-02T03:59:00-05:00','2026-10-13T21:30:00+05:30'])
def test_trusted_aware_clock_normalizes_to_utc_and_dst(when):
    async def scenario():
        async with rig() as (clock,state,worker):
            clock.now=datetime.fromisoformat(when)
            await send(worker,raw(when=clock.now))
            event=state.get_latest('AAA','Q')
            assert datetime.fromisoformat(event['event_timestamp'])==clock.now.astimezone(timezone.utc)
            assert event['received_timestamp']==clock.now.astimezone(timezone.utc).isoformat()
            assert worker._last_event[('Q','AAA')][3]==stream._SIP_CALENDAR.local_date(clock.now)
    asyncio.run(scenario())


@pytest.mark.parametrize('after_write',[False,True])
def test_failed_store_never_commits_history_or_reuses_revision(after_write):
    async def scenario():
        async with rig() as (clock,state,worker):
            await send(worker,raw())
            before=marks(worker);original=state.set_latest
            def failure(event,**kw):
                if after_write:original(event,**kw)
                raise RuntimeError('unknown SET outcome')
            state.set_latest=failure;clock.now+=timedelta(seconds=2)
            with pytest.raises(RuntimeError):await send(worker,raw(q=101,when=clock.now,price=102))
            assert worker._last_event==before[0] and dict(worker._symbol_generation)==before[2]
            assert ('Q','AAA') not in worker._stored_timestamps  # Force reload after uncertain SET.
            state.set_latest=original
            await send(worker,raw(q=102,when=clock.now-timedelta(seconds=1),price=101))
            assert state.get_latest('AAA','Q')['payload']['midpoint']==(102 if after_write else 101)
            clock.now+=timedelta(seconds=1)
            await send(worker,raw(q=103,when=clock.now,price=103))
            current=state.get_latest('AAA','Q')
            assert current['payload']['midpoint']==103 and current['stream_commit']['revision']>=3
    asyncio.run(scenario())


def test_tentative_admission_is_not_a_commit():
    async def scenario():
        async with rig() as (clock,state,worker):
            event=worker.parser.parse_event(raw(),received_at=NOW)
            assert worker._accept_event(event)==(True,False)
            assert marks(worker)==({},{},{})
            await send(worker,raw())
            assert worker._last_event[('Q','AAA')][1]==100
    asyncio.run(scenario())


def test_diagnostics_have_explicit_bounded_overflow():
    async def scenario():
        async with rig(symbol_cap=1) as (clock,state,worker):
            for i in range(6):await send(worker,raw(symbol=f'SYM{i}',when=NOW+timedelta(days=1)))
            h=worker.health_payload()['freshness']
            assert len(h['held_channels'])==4 and h['overflow'] and h['state']=='degraded'
            assert marks(worker)==({},{},{})
    asyncio.run(scenario())


@pytest.mark.parametrize('channel',['Q','T'])
def test_store_revision_reaches_resolver_sse_and_coalesced_publication(channel):
    async def scenario():
        async with rig() as (clock,state,worker):
            resolver=LiveQuoteResolver(state=state,rest_quote=lambda _: {},clock=lambda:clock.now)
            delivery=api._SSEQuoteDelivery()
            await send(worker,raw(channel))
            first,_=delivery.apply(resolver.resolve('AAA').payload(),now=clock.now,calendar=resolver.calendar)
            assert delivery.changed(first)
            await send(worker,[raw(channel,q=101,price=101),raw(channel,q=102,price=102)])
            await worker.flush_updates_if_due(force=True)
            latest=state.get_latest('AAA',channel)
            assert latest['stream_commit']['revision']==3
            assert len(state.published)==1 and len(state.published[0])==1
            assert state.published[0][0]['stream_commit']==latest['stream_commit']
            quote,changed=delivery.apply(resolver.resolve('AAA').payload(),now=clock.now,calendar=resolver.calendar)
            assert quote['price']==102 and not quote['is_stale'] and changed and delivery.changed(quote)
            repeat,changed=delivery.apply(resolver.resolve('AAA').payload(),now=clock.now,calendar=resolver.calendar)
            assert not changed and not delivery.changed(repeat)
            # A new downstream connection has no provider replay assumption.
            assert api._SSEQuoteDelivery().apply(quote,now=clock.now,calendar=resolver.calendar)[0]['price']==102
    asyncio.run(scenario())


@pytest.mark.parametrize('kind',['older_time','older_revision','same_revision','other_worker','missing','malformed'])
def test_sse_revision_never_overrides_stale_or_unproven_ordering(kind):
    epoch=str(uuid4());calendar=stream._SIP_CALENDAR;delivery=api._SSEQuoteDelivery()
    first={'symbol':'AAA','price':100,'event_timestamp':NOW.isoformat(),'source_mode':'websocket',
           'is_stale':False,'stream_commit':{'worker_instance_id':epoch,'revision':2}}
    delivery.apply(first,now=NOW,calendar=calendar)
    next_quote={**first,'price':101,'stream_commit':{'worker_instance_id':epoch,'revision':3}}
    if kind=='older_time':next_quote['event_timestamp']=(NOW-timedelta(seconds=1)).isoformat()
    if kind=='older_revision':next_quote['stream_commit']['revision']=1
    if kind=='same_revision':next_quote['stream_commit']['revision']=2
    if kind=='other_worker':next_quote['stream_commit']['worker_instance_id']=str(uuid4())
    if kind=='missing':next_quote.pop('stream_commit')
    if kind=='malformed':next_quote['stream_commit']['revision']=True
    quote,changed=delivery.apply(next_quote,now=NOW,calendar=calendar)
    assert quote['price']==100 and quote['is_stale'] and not changed
    assert len(delivery.previous)==1 and quote['stream_commit']==first['stream_commit']


def test_equal_time_cross_channel_selection_keeps_existing_T_priority():
    async def scenario():
        async with rig() as (clock,state,worker):
            resolver=LiveQuoteResolver(state=state,rest_quote=lambda _: {},clock=lambda: clock.now)
            delivery=api._SSEQuoteDelivery()
            await send(worker,raw('Q'))
            delivery.apply(resolver.resolve('AAA').payload(),now=NOW,calendar=resolver.calendar)
            await send(worker,raw('T',price=101))
            second,changed=delivery.apply(resolver.resolve('AAA').payload(),now=NOW,calendar=resolver.calendar)
            assert second['price']==101 and second['event_type']=='T' and changed
            await send(worker,raw('Q',q=101,price=102))
            assert resolver.resolve('AAA').event_type=='T' and resolver.resolve('AAA').price==101
    asyncio.run(scenario())


def test_mock_redis_serialization_publication_and_sse_ordering():
    async def scenario():
        async with rig() as (clock,_,worker):
            class RedisFake:
                def __init__(self):self.values={};self.notifications=[]
                def set(self,key,value,ex):self.values[key]=value
                def get(self,key):return self.values.get(key)
                def publish(self,channel,value):self.notifications.append((channel,json.loads(value)))
            state=stream.RedisMarketStreamState.__new__(stream.RedisMarketStreamState)
            from tests.offline_redis_integrity import RedisLuaAdapter
            state.namespace='synthetic:market:v1';state.client=RedisLuaAdapter(RedisFake(), namespace=state.namespace);worker.state=state
            state._expected_integrity_control=state.client.get(state.namespace+':integrity-control')
            resolver=LiveQuoteResolver(state=state,rest_quote=lambda _: {},clock=lambda:clock.now)
            delivery=api._SSEQuoteDelivery()
            await send(worker,raw())
            initial=resolver.resolve('AAA').payload()
            delivery.apply(initial,now=NOW,calendar=resolver.calendar)
            await send(worker,[raw(q=101,price=101),raw(q=102,price=102)])
            await worker.flush_updates_if_due(force=True)
            notification=state.client.notifications[0][1]
            assert len(state.client.notifications)==1 and len(notification['updates'])==1
            latest=state.get_latest('AAA','Q')
            assert notification['updates'][0]['stream_commit']==latest['stream_commit']
            current=resolver.resolve('AAA').payload()
            delivered,changed=delivery.apply(current,now=NOW,calendar=resolver.calendar)
            assert delivered['price']==102 and changed and not delivered['is_stale']
            old,changed=delivery.apply(initial,now=NOW,calendar=resolver.calendar)
            assert old['price']==102 and not changed and old['is_stale']
            # Re-reading Redis restores quality without replacing the later price.
            restored,_=delivery.apply(current,now=NOW,calendar=resolver.calendar)
            assert restored['price']==102 and not restored['is_stale']
    asyncio.run(scenario())


@pytest.mark.parametrize('channel',['Q','T'])
def test_equal_time_after_unbound_rest_or_worker_history_is_held(channel):
    async def scenario():
        async with rig() as (clock,state,worker):
            event=worker.parser.parse_event(raw(channel),received_at=NOW)
            state.set_latest(event,ttl_seconds=120)
            await send(worker,raw(channel,q=101,price=101))
            assert marks(worker)==({},{},{}) and worker.metrics.freshness_holds==1
            assert state.get_latest('AAA',channel)['payload']==event.payload
            clock.now+=timedelta(seconds=1)
            await send(worker,raw(channel,q=1,when=clock.now,price=102))
            assert worker._symbol_generation['AAA']==1
    asyncio.run(scenario())
