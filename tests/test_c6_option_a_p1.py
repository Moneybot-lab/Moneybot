"""Focused P1 acceptance tests; all transport is replaced under kernel isolation."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.engine import make_url

from moneybot.services.stream_integrity import IntegrityRejection, timestamp_us
from moneybot.services.stream_postgres_authority import PostgresAuthority, control, fields, encode, endpoint_identity
from moneybot.services.stream_postgres_state import PostgresMarketStreamState
from tests.test_c6_postgres_authority import rig, put, reserve, quality, healthy, resolver, next_authority, NOW, OWNER

URL = 'postgresql+psycopg://synthetic:synthetic@approved.example:5432/approved_db'


def pg_selection(r):
    return dict(r.a.selection, endpoint=endpoint_identity(make_url(URL)))


@pytest.mark.parametrize('query', ['host=x', 'port=6432', 'dbname=x', 'hostaddr=192.0.2.1',
    'service=alternate', 'servicefile=/tmp/alternate', 'options=-csearch_path=other',
    'host=approved.example&host=alternate', 'load_balance_hosts=random', 'target_session_attrs=any'])
def test_uri_route_options_rejected(rig, query):
    engine=create_engine(URL+'?'+query)
    try:
        with pytest.raises(IntegrityRejection):PostgresAuthority(engine, pg_selection(rig), clock=lambda:rig.clock.now)
    finally:engine.dispose()


@pytest.mark.parametrize('option,value', [('host','alternate'),('port',6432),('dbname','alternate'),
    ('hostaddr','192.0.2.1'),('service','alternate'),('conninfo','host=alternate'),('dsn','host=alternate')])
def test_explicit_driver_overrides_rejected_before_transport(rig,monkeypatch,option,value):
    engine=create_engine(URL,connect_args={option:value})
    try:
        authority=PostgresAuthority(engine,pg_selection(rig),clock=lambda:rig.clock.now)
        monkeypatch.setattr(engine.dialect.loaded_dbapi,'connect',lambda *a,**k:pytest.fail('Unauthorized driver invocation'))
        with pytest.raises(IntegrityRejection):authority.read()
    finally:engine.dispose()


@pytest.mark.parametrize('name', ['PGHOST','PGHOSTADDR','PGPORT','PGDATABASE','PGSERVICE','PGSERVICEFILE',
    'PGSYSCONFDIR','PGOPTIONS','PGLOADBALANCEHOSTS','PGTARGETSESSIONATTRS'])
def test_routing_environment_rejected_on_existing_authority(rig,monkeypatch,name):
    engine=create_engine(URL)
    try:
        authority=PostgresAuthority(engine,pg_selection(rig),clock=lambda:rig.clock.now)
        monkeypatch.setenv(name,'alternate')
        monkeypatch.setattr(engine.dialect.loaded_dbapi,'connect',lambda *a,**k:pytest.fail('Unauthorized driver invocation'))
        with pytest.raises(IntegrityRejection):authority.read()
    finally:engine.dispose()


@pytest.mark.parametrize('channel',['Q','T','A','AM'])
@pytest.mark.parametrize('floor_offset',[-1,0,10])
def test_recovery_floor_preserved_for_every_retained_channel(rig,channel,floor_offset):
    r=rig;put(r,channel);a,plan=next_authority(r)
    floor=timestamp_us(NOW+timedelta(seconds=floor_offset));plan['floor']=floor
    a.initialize(plan);a.activate(plan);r.a=a;r.state.authority=a
    assert quality(r,channel)=='UNKNOWN'
    assert r.state.integrity_floor('AAA',channel)[0]>=NOW+timedelta(seconds=floor_offset)
    with pytest.raises(IntegrityRejection):put(r,channel,seconds=max(0,floor_offset),revision=2)
    put(r,channel,seconds=max(0,floor_offset)+1,revision=3)
    assert quality(r,channel)=='LIVE'
    r.state.authority=PostgresAuthority(r.engine,a.selection,clock=lambda:r.clock.now,local_test=True)
    assert quality(r,channel)=='LIVE'


@pytest.mark.parametrize('bad',[None,True,-1,'0',1.5])
def test_missing_or_invalid_floor_never_initializes(rig,bad):
    r=rig;put(r);a,plan=next_authority(r);plan['floor']=bad
    with pytest.raises(IntegrityRejection):a.initialize(plan)
    assert quality(r)=='LIVE'


def test_missing_floor_and_unsupported_channel_floor_fail_closed(rig):
    r=rig;put(r);a,plan=next_authority(r)
    del plan['floor']
    with pytest.raises(IntegrityRejection):a.initialize(plan)
    plan['floor']=timestamp_us(NOW);plan['channel_floors']={'Q:AAA':timestamp_us(NOW+timedelta(seconds=10))}
    with pytest.raises(IntegrityRejection):a.initialize(plan)
    assert quality(r)=='LIVE'


def test_floor_cannot_decrease_on_next_generation_or_obsolete_receipt(rig):
    r=rig;put(r);a,plan=next_authority(r);plan['floor']=timestamp_us(NOW+timedelta(seconds=10))
    a.initialize(plan);a.activate(plan);r.a=a;r.state.authority=a
    selection=dict(a.selection,generation=3)
    b=PostgresAuthority(r.engine,selection,clock=lambda:r.clock.now,local_test=True)
    next_plan=dict(plan,selection=selection,previous_generation=2,floor=0)
    b.initialize(next_plan);b.activate(next_plan);r.a=b;r.state.authority=b
    assert r.state.integrity_floor('AAA','Q')[0]>=NOW+timedelta(seconds=10)
    with pytest.raises(IntegrityRejection):a.activate(plan)
    with pytest.raises(IntegrityRejection):put(r,seconds=5,revision=2)


def test_activation_checks_every_recovered_row(rig):
    r=rig;put(r);a,plan=next_authority(r);plan['floor']=timestamp_us(NOW+timedelta(seconds=10));a.initialize(plan)
    with r.engine.begin() as conn:
        import json
        body=json.loads(conn.execute(select(fields.c.body).where(fields.c.field=='Q:AAA')).scalar_one())
        body['bound']=timestamp_us(NOW)
        conn.execute(update(fields).where(fields.c.field=='Q:AAA').values(body=encode(body)))
    with pytest.raises(IntegrityRejection):a.activate(plan)


def test_initialization_transaction_interruption_cannot_publish_partial_floor(rig,monkeypatch):
    r=rig;put(r);put(r,'T',seconds=1);before=r.a.read('Q:AAA');a,plan=next_authority(r)
    plan['floor']=timestamp_us(NOW+timedelta(seconds=10));original=a._save;calls=[]
    def fail_after_save(*args,**kw):
        original(*args,**kw);calls.append(1)
        if len(calls)==1:raise OSError('synthetic interruption')
    monkeypatch.setattr(a,'_save',fail_after_save)
    with pytest.raises(IntegrityRejection):a.initialize(plan)
    assert r.a.read('Q:AAA')==before
    monkeypatch.setattr(a,'_save',original);assert a.initialize(plan)=='PREPARING'
    r.state.authority=a
    with pytest.raises(IntegrityRejection):put(r,seconds=11,revision=2)
    assert a.initialize(plan)=='PREPARING';a.activate(plan);assert a.initialize(plan)=='READY'
    put(r,seconds=11,revision=3);assert quality(r)=='LIVE'


def test_concurrent_initializers_preserve_exact_receipt_and_floor(rig):
    r=rig;put(r);a,plan=next_authority(r);plan['floor']=timestamp_us(NOW+timedelta(seconds=10))
    with ThreadPoolExecutor(max_workers=2) as pool:assert list(pool.map(lambda _:a.initialize(plan),range(2)))==['PREPARING']*2
    a.activate(plan);r.a=a;r.state.authority=a
    with pytest.raises(IntegrityRejection):put(r,seconds=5,revision=2)
    put(r,seconds=11,revision=3);assert quality(r)=='LIVE'


@pytest.mark.parametrize('channel',['Q','T','A','AM'])
def test_rest_cannot_bypass_recovered_floor(rig,channel):
    r=rig;put(r,channel);a,plan=next_authority(r);plan['floor']=timestamp_us(NOW+timedelta(seconds=10));a.initialize(plan);a.activate(plan)
    r.a=a;r.state.authority=a
    with pytest.raises(IntegrityRejection):put(r,channel,seconds=5,revision=2,recovery=True)
    put(r,channel,seconds=11,revision=3,recovery=True);assert quality(r,channel)=='LIVE'


def retire(r,*,victim='T',survivor='Q',uncertain=False):
    (reserve if uncertain else put)(r,victim,seconds=4,price=104)
    put(r,survivor,seconds=2,price=102)
    for i in range(3):put(r,symbol='S'+str(i),seconds=5+i)
    healthy(r)
    assert r.a.read(victim+':AAA')[1] is None


@pytest.mark.parametrize('victim,survivor',[('T','Q'),('Q','T'),('A','Q'),('AM','T'),('T','A'),('Q','AM')])
def test_retirement_holds_survivor_then_allows_strictly_newer_reentry(rig,victim,survivor):
    r=rig;retire(r,victim=victim,survivor=survivor)
    assert resolver(r).resolve('AAA').admission_status!='LIVE'
    with pytest.raises(IntegrityRejection):put(r,survivor,seconds=3,revision=2)
    put(r,survivor,seconds=8,revision=3,price=108);healthy(r)
    assert resolver(r).resolve('AAA').admission_status=='LIVE'
    put(r,survivor,seconds=8,revision=4,price=109)
    assert resolver(r).resolve('AAA').price==109
    put(r,victim,seconds=9,revision=5,price=110);healthy(r)
    assert resolver(r).resolve('AAA').price==110


@pytest.mark.parametrize('uncertain',[False,True])
def test_retirement_floor_survives_repeated_sibling_retirement(rig,uncertain):
    r=rig;retire(r,uncertain=uncertain)
    assert quality(r)!='LIVE'
    # Retire the remaining lower channel; its inherited obligation must survive.
    put(r,symbol='S3',seconds=8)
    assert r.a.read('Q:AAA')[1] is None
    with pytest.raises(IntegrityRejection):put(r,seconds=4,revision=2)
    put(r,seconds=9,revision=3);assert quality(r)=='LIVE'


@pytest.mark.parametrize('cache_action',['expiry','rollback','repair'])
def test_retirement_is_not_cured_by_cache_visibility(rig,cache_action):
    r=rig;put(r,'T',seconds=4);put(r,'Q',seconds=2);cache=deepcopy(r.cache.backend.values)
    for i in range(3):put(r,symbol='S'+str(i),seconds=5+i)
    if cache_action=='expiry':r.cache.backend.values.clear()
    elif cache_action=='rollback':r.cache.backend.values=cache
    else:
        with pytest.raises(IntegrityRejection):r.state.reconcile_price('AAA','Q')
    healthy(r);assert resolver(r).resolve('AAA').admission_status!='LIVE'


def test_pending_confirmation_cannot_cross_retirement_boundary(rig):
    r=rig;put(r,'T',seconds=4);put(r,'Q',seconds=2)
    op=reserve(r,'Q',seconds=3,revision=2);r.state._stage(op,120)
    for i in range(3):put(r,symbol='S'+str(i),seconds=5+i)
    with pytest.raises(IntegrityRejection):r.a.confirm(op)
    assert quality(r)!='LIVE'
    put(r,seconds=8,revision=3);assert quality(r)=='LIVE'


def test_unavailable_authority_during_reentry_never_uses_cache(rig,monkeypatch):
    r=rig;retire(r);original=r.a.read
    monkeypatch.setattr(r.a,'read',lambda *a:(_ for _ in ()).throw(IntegrityRejection('offline')))
    assert resolver(r).resolve('AAA').admission_status=='UNKNOWN'
    monkeypatch.setattr(r.a,'read',original)
    assert resolver(r).resolve('AAA').admission_status!='LIVE'


def test_retirement_does_not_hold_unrelated_retained_symbol(rig):
    r=rig;put(r,'T',seconds=4);put(r,'Q',seconds=2);put(r,symbol='OTHER',seconds=3)
    put(r,symbol='S0',seconds=5);put(r,symbol='S1',seconds=6);healthy(r)
    assert quality(r,symbol='OTHER')=='LIVE'
    assert quality(r)!='LIVE'


def test_canonical_factory_valid_tls_and_driver_parameters(rig,monkeypatch):
    from moneybot.services.stream_postgres_connection import create_authority_engine
    engine=create_authority_engine(URL+'?sslmode=verify-full',pg_selection(rig))
    calls=[]
    try:
        def offline(**params):calls.append(params);raise OSError('synthetic transport unavailable')
        monkeypatch.setattr(engine.dialect.loaded_dbapi,'connect',offline)
        a=PostgresAuthority(engine,pg_selection(rig),clock=lambda:rig.clock.now)
        for _ in range(2):
            with pytest.raises(IntegrityRejection,match='outcome_unknown'):a.read()
        assert len(calls)==2
        assert all(p['host']=='approved.example' and p['dbname']=='approved_db' and p['port']==5432
                   and p['sslmode']=='verify-full' and p['connect_timeout']==2 for p in calls)
    finally:engine.dispose()


@pytest.mark.parametrize('route', ['host','port','dbname','hostaddr','service','dsn','conninfo'])
def test_canonical_factory_rejects_explicit_routing(rig,route):
    from moneybot.services.stream_postgres_connection import create_authority_engine
    with pytest.raises(IntegrityRejection):create_authority_engine(URL,pg_selection(rig),connect_args={route:'alternate'})


@pytest.mark.parametrize('suffix',['/approved_db?host=alternate','/host=alternate','/postgresql%3A%2F%2Fother%2Fdb','/approved_db?sslmode=require&sslmode=disable'])
def test_database_conninfo_and_repeated_options_rejected(rig,suffix):
    from moneybot.services.stream_postgres_connection import create_authority_engine
    with pytest.raises(IntegrityRejection):
        create_authority_engine('postgresql+psycopg://synthetic@approved.example:5432'+suffix,pg_selection(rig))


@pytest.mark.parametrize('operation',['read','initialize','activate','reserve'])
def test_unauthorized_endpoint_fences_all_roles_before_connection(rig,monkeypatch,operation):
    engine=create_engine(URL)
    try:
        a=PostgresAuthority(engine,pg_selection(rig),clock=lambda:rig.clock.now)
        # URL changes cannot retarget an existing authority or pooled creator.
        engine.url=make_url(URL.replace('approved.example','alternate.example'))
        monkeypatch.setattr(engine.dialect.loaded_dbapi,'connect',lambda *a,**k:pytest.fail('Unapproved connection'))
        action={'read':lambda:a.read(), 'initialize':lambda:a.initialize(dict(rig.plan,selection=a.selection)),
                'activate':lambda:a.activate(rig.plan),
                'reserve':lambda:a.reserve('Q:AAA',timestamp_us(NOW),{},OWNER,1,now=timestamp_us(NOW))}[operation]
        with pytest.raises(IntegrityRejection):action()
    finally:engine.dispose()


@pytest.mark.parametrize('bad', ['host','port','dbname','missing'])
def test_reused_pool_connection_must_match_route(rig,bad):
    engine=create_engine(URL)
    try:
        PostgresAuthority(engine,pg_selection(rig),clock=lambda:rig.clock.now)
        info=SimpleNamespace(host='approved.example',port=5432,dbname='approved_db')
        if bad=='missing':info=None
        else:setattr(info,bad,'different')
        with pytest.raises(IntegrityRejection):engine.pool.dispatch.checkout(SimpleNamespace(info=info),None,None)
    finally:engine.dispose()


def test_pool_guard_revalidates_environment_and_reuse_without_listener_growth(rig,monkeypatch):
    engine=create_engine(URL)
    try:
        for _ in range(10):PostgresAuthority(engine,pg_selection(rig),clock=lambda:rig.clock.now)
        assert len(engine.pool.dispatch.checkout)==1
        conn=SimpleNamespace(info=SimpleNamespace(host='approved.example',port=5432,dbname='approved_db'))
        engine.pool.dispatch.checkout(conn,None,None)
        monkeypatch.setenv('PGHOSTADDR','192.0.2.1')
        with pytest.raises(IntegrityRejection):engine.pool.dispatch.checkout(conn,None,None)
    finally:engine.dispose()


@pytest.mark.parametrize('newer_cache',[False,True])
def test_restore_never_adopts_old_or_newer_legacy_cache(rig,newer_cache):
    r=rig;put(r);cache=deepcopy(r.cache.backend.values)
    if newer_cache:
        put(r,seconds=20,revision=2);cache=deepcopy(r.cache.backend.values)
    a,plan=next_authority(r);plan['floor']=timestamp_us(NOW+timedelta(seconds=30))
    a.initialize(plan);a.activate(plan);r.a=a;r.state.authority=a;r.cache.backend.values=cache
    assert quality(r)=='UNKNOWN'
    with pytest.raises(IntegrityRejection):r.state.reconcile_price('AAA','Q')
    put(r,seconds=31,revision=3);assert quality(r)=='LIVE'


def test_concurrent_newer_confirmation_and_retirement_never_restore_older_live(rig):
    r=rig;put(r,'T',seconds=4);put(r,'Q',seconds=2);put(r,symbol='S0',seconds=5);put(r,symbol='S1',seconds=6)
    # Reserve both proposals before racing commits, without changing fixture time concurrently.
    op=reserve(r,'Q',seconds=8,revision=2);r.state._stage(op,120)
    from tests.test_c6_postgres_authority import event
    other=event(r,symbol='S2',seconds=9)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(r.a.confirm,op),pool.submit(r.state.set_latest,other,ttl_seconds=120)]
        for future in futures:future.result()
    healthy(r);q=resolver(r).resolve('AAA')
    assert q.admission_status=='LIVE' and q.event_timestamp>=(NOW+timedelta(seconds=8)).isoformat()


def test_repeated_multi_channel_churn_has_bounded_authority_and_cache(rig):
    r=rig
    for i in range(80):
        symbol='C'+str(i)
        put(r,'T',symbol=symbol,seconds=3*i+2)
        put(r,'Q',symbol=symbol,seconds=3*i+1)
        assert quality(r,symbol=symbol)=='LIVE'
        root,_=r.a.read()
        assert root['count']<=4
        with r.engine.connect() as conn:assert len(conn.execute(select(fields)).all())<=4
        assert len([k for k in r.cache.backend.values if ':pgcache:' in k])<=8
    with pytest.raises(IntegrityRejection):put(r,symbol='C0',seconds=2,revision=2)
    put(r,symbol='C0',seconds=250,revision=3);assert quality(r,symbol='C0')=='LIVE'


def test_sqlalchemy_codec_context_cannot_be_overridden(rig,monkeypatch):
    engine=create_engine(URL,connect_args={'context':object()})
    try:
        a=PostgresAuthority(engine,pg_selection(rig),clock=lambda:rig.clock.now)
        monkeypatch.setattr(engine.dialect.loaded_dbapi,'connect',lambda *a,**k:pytest.fail('Unapproved driver argument'))
        with pytest.raises(IntegrityRejection,match='configuration_rejected'):a.read()
    finally:engine.dispose()


def test_local_test_flag_cannot_disable_postgres_connection_guard(rig):
    engine=create_engine(URL)
    try:
        with pytest.raises(IntegrityRejection,match='offline_sqlite'):
            PostgresAuthority(engine,pg_selection(rig),clock=lambda:rig.clock.now,local_test=True)
    finally:engine.dispose()


def test_credential_bearing_driver_error_is_not_exposed(rig,monkeypatch):
    import traceback
    engine=create_engine(URL)
    try:
        a=PostgresAuthority(engine,pg_selection(rig),clock=lambda:rig.clock.now)
        def fail(**params):raise OSError('synthetic-secret-marker')
        monkeypatch.setattr(engine.dialect.loaded_dbapi,'connect',fail)
        try:a.read()
        except IntegrityRejection:
            assert 'synthetic-secret-marker' not in traceback.format_exc()
        else:pytest.fail('Expected unavailable authority')
    finally:engine.dispose()


def test_old_candidate_metadata_requires_explicit_generation_upgrade(rig):
    import json
    r=rig;put(r,'T',seconds=4);put(r,'Q',seconds=2)
    for i in range(3):put(r,symbol='S'+str(i),seconds=5+i)
    with r.engine.begin() as conn:
        root=json.loads(conn.execute(select(control.c.body)).scalar_one());root.pop('recovery_floor')
        conn.execute(update(control).values(body=encode(root)))
        for key,body in conn.execute(select(fields.c.field,fields.c.body)).all():
            row=json.loads(body);row.pop('price_floor')
            conn.execute(update(fields).where(fields.c.field==key).values(body=encode(row)))
    assert quality(r)=='UNKNOWN'
    a,plan=next_authority(r);plan['floor']=0
    a.initialize(plan);a.activate(plan);r.a=a;r.state.authority=a
    # Known retired/retained higher history must survive even a lower plan floor.
    assert r.state.integrity_floor('AAA','Q')[0]>=NOW+timedelta(seconds=7)
    with pytest.raises(IntegrityRejection):put(r,seconds=3,revision=2)
    put(r,seconds=8,revision=3);assert quality(r)=='LIVE'


def test_equal_time_reentry_cannot_clear_uncertain_retirement_floor(rig):
    r=rig;retire(r,uncertain=True)
    with pytest.raises(IntegrityRejection):put(r,seconds=4,revision=2)
    assert quality(r)!='LIVE'
    put(r,seconds=8,revision=3);put(r,seconds=8,revision=4,price=101)
    assert quality(r)=='LIVE'


def test_configuration_parser_exception_never_exposes_credentials(rig,monkeypatch):
    import traceback
    from moneybot.services import stream_postgres_connection as connection
    def malformed(_):raise ValueError('synthetic-sensitive-parser-detail')
    monkeypatch.setattr(connection,'make_url',malformed)
    try:connection.create_authority_engine(URL,pg_selection(rig))
    except IntegrityRejection:
        assert 'synthetic-sensitive-parser-detail' not in traceback.format_exc()
    else:pytest.fail('Expected rejected configuration')
