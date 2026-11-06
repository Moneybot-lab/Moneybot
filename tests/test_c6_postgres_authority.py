"""Production Option A methods, actual local SQL transactions and production Lua.

SQLite supplies atomic local transactions, NOT PostgreSQL WAL durability proof.
Every test must run under the kernel network-isolated test launcher.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from moneybot import api
from moneybot.services import market_stream as stream
from moneybot.services.live_market import LiveQuoteResolver
from moneybot.services.stream_integrity import IntegrityRejection, timestamp_us
from moneybot.services.stream_postgres_authority import (
    PostgresAuthority, control, fields, metadata, encode, endpoint_identity)
from moneybot.services.stream_postgres_state import PostgresMarketStreamState, STAGE_LUA
from tests.offline_redis_integrity import RedisLuaAdapter
from tests.test_c6_durable_integrity import NOW, Backend, raw, worker_for

OWNER = 'ba8ce4d8-8976-4e85-88b6-44d3d7203c15'


@pytest.fixture
def rig(tmp_path):
    clock = SimpleNamespace(now=NOW)
    engine = create_engine('sqlite:///' + str(tmp_path / 'authority.sqlite'), connect_args={'timeout': 5})
    metadata.create_all(engine)  # offline fixture ONLY; production does not call this
    selection = dict(schema=1, contract='OPTION_A_DEVELOPMENT', namespace='offline:c6', generation=1,
                     capacity=4, instance_id='offline-db', endpoint=endpoint_identity(engine.url),
                     expires_at=(NOW+timedelta(days=30)).isoformat())
    a = PostgresAuthority(engine, selection, clock=lambda: clock.now, local_test=True)
    plan = dict(selection=selection, previous_generation=0, floor=0, history_sha256='1'*64,
                writer_exclusion_sha256='2'*64, selection_approval_sha256='3'*64)
    a.initialize(plan); a.activate(plan)
    redis = RedisLuaAdapter(Backend(clock), namespace=selection['namespace'], initialize=False)
    state = PostgresMarketStreamState(redis, a, clock=lambda: clock.now)
    r = SimpleNamespace(clock=clock, engine=engine, a=a, plan=plan, state=state, cache=redis)
    yield r
    engine.dispose()


def event(r, channel='Q', *, seconds=0, symbol='AAA', revision=1, owner=OWNER, price=100, recovery=False):
    when = NOW + timedelta(seconds=seconds)
    r.clock.now = max(r.clock.now, when)
    message = raw(channel, when=when, symbol=symbol, q=100+revision, price=price)
    if channel in {'A', 'AM'}:
        message.update(s=int(when.timestamp()*1000)-1000, e=int(when.timestamp()*1000), c=price, o=price, h=price, l=price, v=1)
    parsed = stream.MassiveStreamParser().parse_event(message, received_at=when)
    return replace(parsed, stream_commit=dict(worker_instance_id=owner, revision=revision), source_mode='rest' if recovery else 'websocket')


def put(r, channel='Q', **options):
    e = event(r, channel, **options)
    r.state.set_latest(e, ttl_seconds=120)
    return e


def reserve(r, channel='Q', **options):
    e = event(r, channel, **options)
    p = e.serialized(); p['is_stale'] = False
    return r.a.reserve(f'{channel}:{e.symbol}', timestamp_us(e.event_timestamp), p, *stream.stream_commit_version(e.stream_commit),
                       now=timestamp_us(r.clock.now), recovery=e.source_mode == 'rest')


def quality(r, channel='Q', symbol='AAA'):
    return r.state.event_quality(r.state.get_latest(symbol, channel))


def healthy(r, owner=OWNER, state='connected'):
    r.state.set_health(dict(schema_version=stream.STREAM_SCHEMA_VERSION, worker_instance_id=owner,
        updated_at=r.clock.now.isoformat(), connection_state=state,
        freshness=dict(ttl_seconds=30, clock_trusted=True, held_channels=[], overflow=False, state='no_known_hold')), ttl_seconds=30)


def resolver(r):
    return LiveQuoteResolver(state=r.state, rest_quote=lambda _: pytest.fail('No provider fallback may bypass authority'), clock=lambda:r.clock.now)


@pytest.mark.parametrize('channel', ['Q','T','A','AM'])
def test_normal_confirmation_and_restart(rig, channel):
    r=rig; put(r, channel); healthy(r)
    root,row=r.a.read(channel+':AAA')
    assert row['pending'] is None and not row['uncertain'] and root['count']==1
    assert quality(r, channel)=='LIVE'
    r.a=PostgresAuthority(r.engine,r.a.selection,clock=lambda:r.clock.now,local_test=True)
    r.state.authority=r.a
    assert quality(r, channel)=='LIVE'
    assert resolver(r).resolve('AAA').admission_status=='LIVE'


@pytest.mark.parametrize('phase', ['reserve','confirm'])
@pytest.mark.parametrize('committed', [False, True])
def test_transaction_failure_or_lost_ack_retains_safe_bound(rig, monkeypatch, phase, committed):
    r=rig; put(r); original_connect=r.engine.connect; calls=[]
    class Connection:
        def __init__(self, c): self.c=c
        def __enter__(self): self.c.__enter__(); return self
        def __exit__(self,*a): return self.c.__exit__(*a)
        def __getattr__(self,n): return getattr(self.c,n)
        def commit(self):
            calls.append(1)
            if len(calls)==(1 if phase=='reserve' else 2):
                if committed:self.c.commit()
                raise OSError('injected lost COMMIT reply' if committed else 'injected precommit failure')
            return self.c.commit()
    monkeypatch.setattr(r.engine,'connect',lambda:Connection(original_connect()))
    with pytest.raises(IntegrityRejection,match='outcome_unknown'):
        put(r, seconds=1, revision=2, price=101)
    monkeypatch.setattr(r.engine,'connect',original_connect)
    _,row=r.a.read('Q:AAA')
    if phase=='reserve' and not committed:
        assert row['bound']==timestamp_us(NOW) and quality(r)=='LIVE'
    elif phase=='confirm' and committed:
        assert row['confirmed']['stamp']==timestamp_us(NOW+timedelta(seconds=1)) and quality(r)=='LIVE'
    else:
        assert row['bound']==timestamp_us(NOW+timedelta(seconds=1)) and row['uncertain']
        assert quality(r)=='UNKNOWN'
        with pytest.raises(IntegrityRejection):put(r, seconds=.5, revision=3)
        assert r.state.reconcile_price('AAA','Q')=='reconciled'
        assert quality(r)=='LIVE'
    put(r, seconds=2, revision=4, price=102)
    assert quality(r)=='LIVE'


@pytest.mark.parametrize('committed', [False,True])
def test_redis_failure_or_unknown_ack_is_not_confirmation(rig,monkeypatch,committed):
    r=rig;put(r);original=r.cache.eval
    def failed(*args):
        if committed: original(*args)
        raise OSError('Redis reply unavailable')
    monkeypatch.setattr(r.cache,'eval',failed)
    with pytest.raises(OSError):put(r,seconds=1,revision=2)
    assert quality(r)=='UNKNOWN'
    _,row=r.a.read('Q:AAA'); assert row['confirmed']['stamp']==timestamp_us(NOW) and row['pending']
    monkeypatch.setattr(r.cache,'eval',original)
    r.state.reconcile_price('AAA','Q')
    assert quality(r)=='LIVE'


def test_pending_cannot_permanently_pin_valid_newer_updates(rig):
    r=rig; put(r)
    abandoned=reserve(r,seconds=1,revision=2)
    put(r,seconds=2,revision=3)
    with pytest.raises(IntegrityRejection,match='cas_changed'):r.a.confirm(abandoned)
    assert quality(r)=='LIVE' and r.a.read('Q:AAA')[1]['bound']==timestamp_us(NOW+timedelta(seconds=2))


@pytest.mark.parametrize('loss', ['expiry','rollback','all_cache','integrity_indexes'])
def test_cache_loss_or_rollback_never_overrides_current_authority(rig,loss):
    r=rig;put(r);old=deepcopy(r.cache.backend.values)
    put(r,seconds=1,revision=2,price=101)
    if loss=='expiry':r.clock.now+=timedelta(seconds=121)
    elif loss=='rollback':r.cache.backend.values=old
    elif loss=='all_cache':r.cache.backend.values.clear()
    else:r.cache.hashes.clear();r.cache.zsets.clear()
    if loss=='integrity_indexes':assert quality(r)=='LIVE'  # PG uses no Redis integrity index
    else:assert quality(r)=='UNKNOWN'
    r.state.reconcile_price('AAA','Q')
    assert quality(r)=='LIVE'  # source-age classification is separately tested
    assert r.a.read('Q:AAA')[1]['confirmed']['payload']['payload']['midpoint']==101
    with pytest.raises(IntegrityRejection):put(r,seconds=.5,revision=3)


def test_delayed_stage_cannot_replace_reused_slot(rig):
    r=rig;put(r);first=r.a.read('Q:AAA')[1]['confirmed']
    put(r,seconds=1,revision=2);put(r,seconds=2,revision=3)
    with pytest.raises(IntegrityRejection,match='stage_conflict'):r.state._stage(first,120)
    # Complete cache loss can erase a cache-side fence, but never PG authority.
    r.cache.backend.values.clear();r.state._stage(first,120)
    assert quality(r)=='UNKNOWN'
    r.state.reconcile_price('AAA','Q');assert quality(r)=='LIVE'


def test_exact_payload_not_just_timestamp_or_commit_token(rig):
    r=rig;put(r);e=r.state.get_latest('AAA','Q');e['payload']=dict(e['payload'],midpoint=999)
    assert r.state.event_quality(e)=='UNKNOWN'
    row=r.a.read('Q:AAA')[1];slot=r.state._slot(row['confirmed']['slot'])
    rawvalue,ttl=r.cache.backend.values[slot]
    op=json.loads(rawvalue.split('|',1)[1]);op['payload']['payload']['midpoint']=999
    r.cache.backend.values[slot]=(rawvalue[:20]+encode(op),ttl)
    assert quality(r)=='UNKNOWN'


def test_concurrent_reservations_and_old_confirmation_are_ordered(rig):
    r=rig;put(r);r.clock.now+=timedelta(seconds=2)
    def attempt(i):
        try:return reserve(r,seconds=i,revision=i+1)
        except IntegrityRejection:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,[1,2]))
    current=r.a.read('Q:AAA')[1]['pending'];assert current['stamp']==timestamp_us(NOW+timedelta(seconds=2))
    for op in results:
        if op and op!=current:
            with pytest.raises(IntegrityRejection):r.a.confirm(op)
    r.state.reconcile_price('AAA','Q');assert quality(r)=='LIVE'


@pytest.mark.parametrize('uncertain',[False,True])
@pytest.mark.parametrize('retired_channel',['Q','T'])
def test_cross_channel_retired_bound_survives_and_recovery_cannot_regress(rig,uncertain,retired_channel):
    r=rig;other='T' if retired_channel=='Q' else 'Q'
    put(r,other,seconds=0)
    if uncertain:reserve(r,retired_channel,seconds=10)
    else:put(r,retired_channel,seconds=10)
    # Keep the older opposite-channel row hot, retire the higher row with churn.
    for i in range(6):
        put(r,other,seconds=i+1,revision=i+2)
        put(r,symbol='S'+str(i),seconds=20+i)
        # Stop setup once its target retirement occurs: later lower writes now
        # correctly hit the propagated safety bound. All safety assertions below
        # retain the original older sibling and higher retired-bound scenario.
        if r.a.read(retired_channel+':AAA')[1] is None:
            break
    assert r.a.read(retired_channel+':AAA')[1] is None
    assert r.a.read(other+':AAA')[1] is not None
    with pytest.raises(IntegrityRejection):put(r,other,seconds=9,revision=20,recovery=True)
    with pytest.raises(IntegrityRejection):put(r,retired_channel,seconds=9,revision=20)
    put(r,retired_channel,seconds=40,revision=21)


@pytest.mark.parametrize('boundary',['before_stage','after_stage'])
def test_hold_race_is_not_cleared_by_earlier_reservation(rig,boundary):
    r=rig;put(r);op=reserve(r,seconds=1,revision=2)
    if boundary=='after_stage':r.state._stage(op,120)
    r.a.hold('Q:AAA','synthetic_admission_hold')
    if boundary=='before_stage':r.state._stage(op,120)
    r.a.confirm(op)
    assert quality(r)=='DEGRADED'
    put(r,seconds=2,revision=3);assert quality(r)=='LIVE'


def test_stale_marking_cas_cannot_mark_racing_new_price(rig,monkeypatch):
    r=rig;put(r);original=r.a.hold;fired=[]
    def racing(*args,**kwargs):
        if not fired:fired.append(1);put(r,seconds=1,revision=2)
        return original(*args,**kwargs)
    monkeypatch.setattr(r.a,'hold',racing)
    r.state.mark_symbols_stale(['AAA'],reason='disconnect',ttl_seconds=120)
    assert quality(r)=='LIVE'
    monkeypatch.setattr(r.a,'hold',original)
    r.state.mark_symbols_stale(['AAA'],reason='disconnect',ttl_seconds=120)
    assert quality(r)=='STALE'


def test_rest_cannot_overwrite_racing_newer_trade(rig):
    r=rig;put(r);op=reserve(r,seconds=1,revision=2,recovery=True)
    put(r,'T',seconds=2,revision=3)
    # Recheck all channels at confirmation, not merely reservation time.
    r.state._stage(op,120)
    with pytest.raises(IntegrityRejection,match='recovery'):
        r.a.confirm(op)


def next_authority(r):
    selection=dict(r.a.selection,generation=2)
    a=PostgresAuthority(r.engine,selection,clock=lambda:r.clock.now,local_test=True)
    plan=dict(r.plan,selection=selection,previous_generation=1)
    return a,plan


def test_interrupted_initialization_resumes_without_replay_or_old_generation(rig):
    r=rig;put(r);a,plan=next_authority(r)
    assert a.initialize(plan)=='PREPARING'
    with pytest.raises(IntegrityRejection):r.a.read('Q:AAA')
    with pytest.raises(IntegrityRejection):a.read('Q:AAA')
    assert a.initialize(plan)=='PREPARING'
    a.activate(plan);assert a.initialize(plan)=='READY'
    assert a.read()[0]['retired']==timestamp_us(NOW)
    with pytest.raises(IntegrityRejection):r.a.initialize(r.plan)
    r.state.authority=a
    assert quality(r)=='UNKNOWN'
    with pytest.raises(IntegrityRejection):put(r,revision=2)
    put(r,seconds=1,revision=3);assert quality(r)=='LIVE'


@pytest.mark.parametrize('bad',['endpoint','generation','instance_id','expires_at','schema','contract'])
def test_selection_and_pitr_are_explicitly_fenced(rig,bad):
    r=rig;put(r);s=deepcopy(r.a.selection)
    s[bad]={'endpoint':'unexpected/clone','generation':2,'instance_id':'other-instance','expires_at':NOW.isoformat(),'schema':0,'contract':'unapproved'}[bad]
    with pytest.raises(IntegrityRejection):
        a=PostgresAuthority(r.engine,s,clock=lambda:r.clock.now,local_test=True);a.read('Q:AAA')


@pytest.mark.parametrize('missing',['root','row','malformed_row','schema'])
def test_missing_or_inconsistent_metadata_is_unknown(rig,missing):
    r=rig;put(r)
    with r.engine.begin() as c:
        if missing=='root':c.execute(delete(control))
        elif missing=='row':c.execute(delete(fields))
        elif missing=='schema':
            root=json.loads(c.execute(select(control.c.body)).scalar_one());root['schema']=99
            c.execute(update(control).values(body=encode(root)))
        else:c.execute(update(fields).values(body='{}'))
    assert quality(r)=='UNKNOWN'
    with pytest.raises(IntegrityRejection):put(r,seconds=1,revision=2)


def test_plain_legacy_cache_writer_cannot_fabricate_live(rig):
    r=rig;put(r)
    with pytest.raises(IntegrityRejection,match='legacy_writer'):
        r.state.set_latest(replace(event(r),stream_commit=None),ttl_seconds=120)
    r.cache.set(r.state._latest_key('AAA','Q'),encode(event(r,price=900).serialized()),ex=120)
    assert r.state.get_latest('AAA','Q')['payload']['midpoint']==100
    assert quality(r)=='LIVE'


def test_source_age_survives_cache_rebuild(rig):
    r=rig;put(r);r.clock.now+=timedelta(minutes=30);r.state.reconcile_price('AAA','Q');healthy(r)
    q=resolver(r).resolve('AAA');assert q.is_stale and q.admission_status=='STALE'


def test_missing_health_and_authority_loss_are_visible(rig,monkeypatch):
    r=rig;put(r);assert resolver(r).resolve('AAA').admission_status=='UNKNOWN'
    healthy(r);assert resolver(r).resolve('AAA').admission_status=='LIVE'
    monkeypatch.setattr(r.a,'read',lambda *a:(_ for _ in ()).throw(OSError('connection interrupted')))
    q=resolver(r).resolve('AAA');assert q.admission_status=='UNKNOWN' and q.is_degraded


def test_confirmation_is_not_starved_between_health_publications(rig):
    r=rig;put(r);healthy(r)
    for i in range(1,20):
        put(r,seconds=i/10,revision=i+1)
        assert resolver(r).resolve('AAA').admission_status=='LIVE'
    r.a.hold('Q:AAA','known_hold')
    assert resolver(r).resolve('AAA').admission_status=='DEGRADED'


def test_postgres_ddl_is_logged_and_has_unique_bounded_slot_constraint():
    ddl='\n'.join(str(CreateTable(t).compile(dialect=postgresql.dialect())) for t in metadata.sorted_tables)
    assert 'UNLOGGED' not in ddl and 'TEMPORARY' not in ddl
    assert 'market_authority_position_unique' in ddl
    assert 'FOR SHARE' in str(select(control).with_for_update(read=True).compile(dialect=postgresql.dialect()))
    assert 'FOR UPDATE' in str(select(control).with_for_update().compile(dialect=postgresql.dialect()))


def test_worker_preserves_contract_and_delivers_equal_time_revisions(rig):
    r=rig
    async def run():
        w=worker_for(r.clock,r.state)
        try:
            await w.process_raw_message(json.dumps(raw('Q',q=1)))
            await w._publish_health(force=True)
            first=resolver(r).resolve('AAA').payload()
            await w.process_raw_message(json.dumps(raw('Q',q=5,price=101)))
            second=resolver(r).resolve('AAA').payload()
            assert second['price']==101 and second['admission_status']=='LIVE'
            assert second['stream_commit']['revision']>first['stream_commit']['revision']
            assert w.metrics.rest_recovery_queued==0
            d=api._SSEQuoteDelivery();d.apply(first,now=r.clock.now,calendar=resolver(r).calendar)
            out,change=d.apply(second,now=r.clock.now,calendar=resolver(r).calendar)
            assert change and out['price']==101 and not out['is_degraded']
        finally:await w.aclose()
    asyncio.run(run())


def test_generation_transition_retains_aggregate_ordering_without_live_replay(rig):
    r=rig;put(r,'A');a,plan=next_authority(r);a.initialize(plan);a.activate(plan);r.state.authority=a
    assert quality(r,'A')=='UNKNOWN'
    assert r.state.aggregate_context('AAA','A')[0]==NOW
    async def run():
        w=worker_for(r.clock,r.state)
        try:
            r.clock.now+=timedelta(seconds=1)
            e=event(r,'A',seconds=1,revision=2)
            await w.process_raw_message(json.dumps(dict(ev='A',sym='AAA',s=int(NOW.timestamp()*1000),e=int(r.clock.now.timestamp()*1000),q=102,c=101)))
            await w._publish_health(force=True)
            assert quality(r,'A')=='LIVE'
        finally:await w.aclose()
    asyncio.run(run())


def test_seeded_migration_context_is_not_a_confirmation_but_allows_future_aggregate(rig):
    r=rig
    # A separately created empty namespace with an explicit reviewed history floor.
    selection=dict(r.a.selection,namespace='offline:migration')
    a=PostgresAuthority(r.engine,selection,clock=lambda:r.clock.now,local_test=True)
    plan=dict(r.plan,selection=selection,floor=timestamp_us(NOW),aggregate_contexts={
        'A:AAA':dict(stamp=timestamp_us(NOW),sequence=10,event_id='prior-window')})
    a.initialize(plan);a.activate(plan);r.state=PostgresMarketStreamState(r.cache,a,clock=lambda:r.clock.now);r.a=a
    assert quality(r,'A')=='UNKNOWN'
    assert r.state.aggregate_context('AAA','A')==(NOW,10,'prior-window',None)
    put(r,'A',seconds=1);assert quality(r,'A')=='LIVE'


@pytest.mark.parametrize('phase',['before_stage','after_stage','after_confirm'])
def test_restart_reconciles_incomplete_operation_without_replaying_new_request(rig,phase):
    r=rig;put(r);op=reserve(r,seconds=1,revision=2)
    if phase!='before_stage':r.state._stage(op,120)
    if phase=='after_confirm':r.a.confirm(op)
    r.state=PostgresMarketStreamState(r.cache,PostgresAuthority(r.engine,r.a.selection,clock=lambda:r.clock.now,local_test=True),clock=lambda:r.clock.now)
    assert r.state.reconcile_price('AAA','Q')=='reconciled'
    assert quality(r)=='LIVE' and r.a.read('Q:AAA')[1]['confirmed']['ticket']==op['ticket']


def test_partial_sql_mutations_roll_back_together(rig,monkeypatch):
    r=rig;put(r);before=r.a.read('Q:AAA');original=r.a._root_save
    def failed(*args):raise OSError('after row mutation, before root mutation')
    monkeypatch.setattr(r.a,'_root_save',failed)
    with pytest.raises(IntegrityRejection):put(r,seconds=1,revision=2)
    monkeypatch.setattr(r.a,'_root_save',original)
    assert r.a.read('Q:AAA')==before and quality(r)=='LIVE'


def test_selection_expiry_fences_existing_connection(rig):
    r=rig;put(r);r.clock.now+=timedelta(days=31)
    assert quality(r)=='UNKNOWN'
    with pytest.raises(IntegrityRejection,match='selection'):put(r,seconds=1,revision=2)


def test_no_schema_or_initialization_on_constructor(rig):
    r=rig
    with r.engine.begin() as c:c.execute(delete(fields));c.execute(delete(control))
    state=PostgresMarketStreamState(r.cache,PostgresAuthority(r.engine,r.a.selection,clock=lambda:r.clock.now,local_test=True),clock=lambda:r.clock.now)
    with pytest.raises(IntegrityRejection,match='metadata'):state.integrity_preflight()
    with r.engine.connect() as c:assert c.execute(select(control)).all()==[]


@pytest.mark.parametrize('mode',['expired','unknown'])
def test_api_and_existing_and_new_sse_expose_authority_quality(rig,monkeypatch,mode):
    from flask import Flask,g,session
    r=rig;put(r);healthy(r);live=resolver(r)
    app=Flask(__name__);app.secret_key='synthetic';app.config['TESTING']=True
    app.extensions.update(market_stream_state=r.state)
    monkeypatch.setattr(api,'_live_quote_resolver',lambda:live)
    monkeypatch.setattr(api,'_symbols_for_live_user',lambda _: {'AAA'})
    monkeypatch.setattr(api,'_decision_context_for_user',lambda _:SimpleNamespace(after_hours_alerts=False,profile_version=1))
    monkeypatch.setattr(api,'_register_stream_demand',lambda *a,**k:True)
    monkeypatch.setattr(api,'_clear_stream_demand',lambda *a,**k:None)
    delivery=api._SSEQuoteDelivery();delivery.apply(live.resolve('AAA').payload(),now=r.clock.now,calendar=live.calendar)
    if mode=='expired':r.cache.backend.values.clear()
    else:r.a.quarantine()
    with app.test_request_context('/api/quote?symbol=AAA'):
        g.request_id='offline';q=api.api_quote().get_json()['data']
        assert q['admission_status']=='UNKNOWN' and q['is_degraded']
    out,_=delivery.apply(q,now=r.clock.now,calendar=live.calendar)
    assert out['admission_status']=='UNKNOWN' and out['is_degraded']
    with app.test_request_context('/api/live-market-stream?symbols=AAA&once=1'):
        g.request_id='offline';session['user_id']=1
        response=api.live_market_stream();frames=list(response.response)
        data=[json.loads(line[6:]) for frame in frames if 'event: quotes' in frame for line in frame.splitlines() if line.startswith('data: ')]
        assert data and data[0]['quotes'][0]['admission_status']=='UNKNOWN'
        assert data[0]['quotes'][0]['is_degraded']


def test_bounded_storage_with_retired_uncertainty_and_returning_symbols(rig):
    r=rig
    for i in range(180):
        if i%3==0:reserve(r,symbol='S'+str(i),seconds=i+1)
        else:put(r,symbol='S'+str(i),seconds=i+1)
        root,_=r.a.read();assert root['count']<=4
    assert len([k for k in r.cache.backend.values if ':pgcache:' in k])<=8
    with pytest.raises(IntegrityRejection):put(r,symbol='S0',seconds=1,revision=2)
    put(r,symbol='S0',seconds=181,revision=3)
    assert quality(r,symbol='S0')=='LIVE'


def test_unknown_factory_selection_cannot_fall_back_to_legacy(monkeypatch):
    monkeypatch.setenv('MASSIVE_STREAM_AUTHORITY','postgres_typo')
    with pytest.raises(IntegrityRejection,match='unsupported'):stream.create_stream_state(None)
    monkeypatch.setenv('MASSIVE_STREAM_AUTHORITY','postgres_option_a')
    with pytest.raises(IntegrityRejection,match='requires_cache'):stream.create_stream_state(None)


def test_postgres_readonly_secondary_and_unsafe_session_settings_rejected(rig):
    # PG-specific connection contract is exercised without a socket. The actual
    # SQL strings execute against a strict recorder; row logic is covered by SQLite.
    from contextlib import contextmanager
    class Rows:
        def __init__(self,value):self.value=value
        def one(self):return self.value
        def all(self):return self.value
    class Conn:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execution_options(self,**kw):assert kw=={'isolation_level':'READ COMMITTED'};return self
        def begin(self):pass
        def rollback(self):pass
        def exec_driver_sql(self,sql):
            seen.append(sql)
            if sql.startswith('SET LOCAL'):return None
            if 'current_setting' in sql:return Rows(settings)
            if 'pg_class' in sql:return Rows(storage)
            pytest.fail('Unrecognized PG statement')
        def execute(self,*args):raise IntegrityRejection('reached_selected_root')
    # Real dialect/URL plus explicit synthetic connection identity; no transport.
    engine=create_engine('postgresql+psycopg://synthetic@approved.example:5432/approved_db')
    Conn.connection=SimpleNamespace(driver_connection=SimpleNamespace(info=SimpleNamespace(
        host='approved.example',port=5432,dbname='approved_db')))
    engine.connect=lambda:Conn()
    selection=dict(rig.a.selection,endpoint=endpoint_identity(engine.url))
    for settings,storage,expected in [
        (('off','on',False),[],'durability_settings'),
        (('on','off',False),[],'durability_settings'),
        (('on','on',True),[],'durability_settings'),
        (('on','on',False),[('market_authority_control','u'),('market_authority_fields','p')],'logged_tables'),
        (('on','on',False),[('market_authority_control','p'),('market_authority_fields','p')],'reached_selected_root')]:
        seen=[];a=PostgresAuthority(engine,selection,clock=lambda:rig.clock.now)
        with pytest.raises(IntegrityRejection,match=expected):a.read()
        assert "SET LOCAL synchronous_commit = 'on'" in seen


def test_sustained_synthetic_worker_throughput_and_bounded_memory(rig):
    import os,time,statistics
    from pathlib import Path
    from sqlalchemy import event as sa_event
    r=rig;counters=dict(commits=0,statements=0,body_bytes_sent=0);latencies=[]
    def committed(conn):counters['commits']+=1
    def statement(conn,cursor,sql,params,context,many):
        counters['statements']+=1
        if sql.startswith(('INSERT','UPDATE')):
            counters['body_bytes_sent']+=sum(len(p.encode()) for p in params if isinstance(p,str) and p.startswith('{'))
    sa_event.listen(r.engine,'commit',committed);sa_event.listen(r.engine,'after_cursor_execute',statement)
    count=1000
    async def run():
        w=worker_for(r.clock,r.state,symbol_cap=4)
        try:
            start=time.perf_counter()
            for i in range(count):
                r.clock.now=NOW+timedelta(microseconds=round(i*1_000_000/166))
                before=time.perf_counter()
                await w.process_raw_message(json.dumps(raw('Q',symbol='AAA',when=r.clock.now,q=1+i*4,price=100+(i%10)/100)))
                latencies.append((time.perf_counter()-before)*1000)
            elapsed=time.perf_counter()-start
            assert r.a.read('Q:AAA')[1]['confirmed']['revision']==count
            assert len(w._last_event)<=16 and len(w._stored_timestamps)<=16 and len(w._rest_futures)<=2
            assert w.metrics.rest_recovery_queued==0
            return elapsed
        finally:await w.aclose()
    elapsed=asyncio.run(run())
    sa_event.remove(r.engine,'commit',committed);sa_event.remove(r.engine,'after_cursor_execute',statement)
    root,row=r.a.read('Q:AAA')
    metrics=dict(events=count,wall_seconds=elapsed,synthetic_events_per_second=count/elapsed,
        p50_ms=statistics.median(latencies),p95_ms=sorted(latencies)[int(count*.95)-1],max_ms=max(latencies),
        counters=counters,authority_json_bytes=len(encode(root).encode())+len(encode(row).encode()),
        redis_cache_bytes=sum(len(v[0].encode()) for k,v in r.cache.backend.values.items() if ':pgcache:' in k),
        hosted_capacity_proven=False,network_blocked=True)
    if os.environ.get('C6_OPTION_A_OUTPUT'):
        target=Path(os.environ['C6_OPTION_A_OUTPUT'])/'synthetic_performance.json'
        with target.open('x') as f:json.dump(metrics,f,indent=2);f.write('\n')
    assert count==len(latencies) and root['count']<=4


def test_initial_portfolio_rest_metadata_does_not_bypass_authority(rig):
    from flask import Flask
    from tests.test_portfolio_ui_offline_investigation import _ui, _item
    r=rig;app=Flask(__name__);app.extensions['market_stream_state']=r.state
    with app.app_context():
        result=api._initial_live_quote_metadata('AAA',dict(price=100,event_timestamp=r.clock.now.isoformat(),
            received_timestamp=r.clock.now.isoformat(),source='massive',source_mode='rest',is_stale=False),displayed_price=100)
    assert result['admission_status']=='UNKNOWN' and result['is_degraded']
    browser=_ui([dict(_item(),symbol='AAA',current_price=100,live_market=result)],'capture();')
    assert 'Status unknown' in browser['cells'][0]
    assert 'REST · Fresh' not in browser['cells'][0] and 'WebSocket · Live' not in browser['cells'][0]


def test_repair_cannot_restage_old_generation_confirmation(rig):
    r=rig;put(r);a,plan=next_authority(r);a.initialize(plan);a.activate(plan);r.state.authority=a
    before=deepcopy(r.cache.backend.values)
    with pytest.raises(IntegrityRejection,match='no_current_authoritative'):
        r.state.reconcile_price('AAA','Q')
    assert r.cache.backend.values==before


def test_initialization_lost_ack_can_resume_same_plan(rig,monkeypatch):
    r=rig;put(r);a,plan=next_authority(r);original=r.engine.connect
    class Connection:
        def __init__(self,c):self.c=c
        def __enter__(self):self.c.__enter__();return self
        def __exit__(self,*args):return self.c.__exit__(*args)
        def __getattr__(self,n):return getattr(self.c,n)
        def commit(self):self.c.commit();raise OSError('initialization committed; acknowledgment lost')
    monkeypatch.setattr(r.engine,'connect',lambda:Connection(original()))
    with pytest.raises(IntegrityRejection,match='outcome_unknown'):a.initialize(plan)
    monkeypatch.setattr(r.engine,'connect',original)
    with pytest.raises(IntegrityRejection):r.a.read()
    assert a.initialize(plan)=='PREPARING';a.activate(plan)
    assert a.read('Q:AAA')[1]['bound']==timestamp_us(NOW)


def test_incompatible_capacity_change_rejects_before_partial_initialization(rig):
    r=rig;put(r);before=r.a.read('Q:AAA')
    selection=dict(r.a.selection,generation=2,capacity=2)
    a=PostgresAuthority(r.engine,selection,clock=lambda:r.clock.now,local_test=True)
    plan=dict(r.plan,selection=selection,previous_generation=1)
    with pytest.raises(IntegrityRejection,match='capacity_change'):a.initialize(plan)
    assert r.a.read('Q:AAA')==before


def test_browser_retained_value_preserves_unknown_authority_label(rig):
    from tests.test_portfolio_ui_offline_investigation import _ui, _item
    r=rig;put(r);healthy(r);live=resolver(r);d=api._SSEQuoteDelivery()
    d.apply(live.resolve('AAA').payload(),now=r.clock.now,calendar=live.calendar)
    r.a.quarantine()
    outgoing,_=d.apply(live.resolve('AAA').payload(),now=r.clock.now,calendar=live.calendar)
    assert outgoing['admission_status']=='UNKNOWN' and outgoing['source_mode']=='last_known'
    browser=_ui([dict(_item(),symbol='AAA',current_price=100,live_market=outgoing)],'capture();')
    assert 'Status unknown' in browser['cells'][0]
    assert 'WebSocket · Live' not in browser['cells'][0]


def test_partial_old_control_with_current_rows_is_inconsistent_not_live(rig):
    r=rig;put(r)
    with r.engine.connect() as c:old=c.execute(select(control.c.body)).scalar_one()
    put(r,seconds=1,revision=2)
    with r.engine.begin() as c:c.execute(update(control).values(body=old))
    assert quality(r)=='UNKNOWN'
    with pytest.raises(IntegrityRejection):put(r,seconds=2,revision=3)


def test_separate_pitr_copy_cannot_be_selected_by_only_changing_url(rig,tmp_path):
    import shutil
    r=rig;put(r)
    target=tmp_path/'unapproved_restore.sqlite'
    shutil.copyfile(r.engine.url.database,target)
    engine=create_engine('sqlite:///'+str(target))
    try:
        with pytest.raises(IntegrityRejection,match='selection'):
            PostgresAuthority(engine,r.a.selection,clock=lambda:r.clock.now,local_test=True)
        # Changing the file's endpoint alone cannot authorize the copied root.
        selection=dict(r.a.selection,endpoint=endpoint_identity(engine.url))
        copied=PostgresAuthority(engine,selection,clock=lambda:r.clock.now,local_test=True)
        with pytest.raises(IntegrityRejection,match='endpoint_fenced'):copied.read()
    finally:engine.dispose()
