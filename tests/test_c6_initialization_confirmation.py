"""Explicit cutover and causal quality regressions; kernel-offline runner only.

Evidence receipts here are synthetic premises, never hosted durability claims.
The adapter executes production Lua and preserves failed command prefixes.
"""
import asyncio
import copy
import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from moneybot import api
from moneybot.services import market_stream as stream
from moneybot.services import stream_initialization as init
from moneybot.services.live_market import LiveQuoteResolver
from moneybot.services.stream_integrity import IntegrityRejection, Watermark, timestamp_us
from tests.test_c6_durable_integrity import NOW, repository, worker_for, raw, send, record


def empty_state():
    clock = SimpleNamespace(now=NOW)
    state = repository(clock, initialize=False)
    configuration={'maxmemory-policy':'noeviction','appendonly':'yes','appendfsync':'always','save':'','no-appendfsync-on-rewrite':'no'}
    state.client.backend.config_get = lambda name: {name:configuration[name]}
    state.client.backend.info = lambda section: ({'run_id': 'synthetic-process-1', 'redis_version': '7.2.0'} if section == 'server'
        else {'loading': 0, 'aof_enabled': 1, 'aof_last_write_status': 'ok'} if section == 'persistence'
        else {'role':'master'} if section == 'replication' else {'used_memory': 0})
    return clock, state


def manifest_for(state, target=None, contexts=None):
    snapshot = init.snapshot(state.client, state.namespace)
    return dict(schema=2, namespace=state.namespace, generation=str(uuid4()),
        expires_at=(NOW+timedelta(days=366)).isoformat(), server_run_id='synthetic-process-1', server_version='7.2.0',
        durability_evidence_sha256='1'*64, writer_fence_evidence_sha256='2'*64, history_evidence_sha256='3'*64,
        durability_contract='acknowledged_and_uncertain_bounds_survive', legacy_writers_fenced=True,
        configuration={'maxmemory-policy':'noeviction','appendonly':'yes','appendfsync':'always','save':'','no-appendfsync-on-rewrite':'no'},
        expected_snapshot_sha256=init.digest(snapshot),
        target_hash=target or {'__schema__': '2', '__retired__': '0', '__ordinal__': '0'},
        aggregate_contexts=contexts or {})


def initialized():
    clock, state = empty_state()
    manifest = manifest_for(state)
    result = state.reconcile_integrity(manifest, now=clock.now)
    assert result['status'] == 'completed'
    state.integrity_authorization = manifest
    return clock, state, manifest


def protected(when=NOW, q=100, price=100, owner=None, revision=1, symbol='AAA'):
    event = stream.MassiveStreamParser().parse_event(raw(when=when, q=q, price=price, symbol=symbol), received_at=when)
    return replace(event, stream_commit={'worker_instance_id': owner or str(uuid4()), 'revision': revision})


def test_explicit_initialization_is_verified_and_idempotent_without_rewriting_new_prices():
    clock, state, manifest = initialized()
    state.set_latest(protected(), ttl_seconds=120)
    before = init.snapshot(state.client, state.namespace)
    result = state.reconcile_integrity(manifest, now=clock.now)
    assert result['status'] == 'already_completed'
    assert init.snapshot(state.client, state.namespace) == before
    assert init.preflight(state.client, state.namespace, manifest, now=clock.now)['status'] == 'COMPATIBLE_VERIFIED_STATE'
    assert all(state.client.pttl(key) in {-1, -2} for key in init.keys(state.namespace))


@pytest.mark.parametrize('fault,expected', [
    ('no_receipt', 'UNVERIFIED_PERSISTENCE'), ('eviction', 'UNSUPPORTED_STORAGE_CONFIGURATION'),
    ('restart', 'UNVERIFIED_SERVER_GENERATION'), ('expired', 'UNVERIFIED_PERSISTENCE_OR_WRITER_FENCE'),
    ('mixed_writers', 'UNVERIFIED_PERSISTENCE_OR_WRITER_FENCE'), ('missing_proof', 'UNVERIFIED_PERSISTENCE_OR_WRITER_FENCE'),
])
def test_preflight_never_infers_storage_or_writer_guarantees(fault, expected):
    clock, state = empty_state(); manifest = manifest_for(state)
    if fault == 'no_receipt': manifest = None
    elif fault == 'eviction': state.client.backend.config_get = lambda _: {'maxmemory-policy': 'allkeys-lru'}
    elif fault == 'restart': manifest['server_run_id'] = 'different-process'
    elif fault == 'expired': manifest['expires_at'] = NOW.isoformat()
    elif fault == 'mixed_writers': manifest['legacy_writers_fenced'] = False
    elif fault == 'missing_proof': manifest.pop('durability_evidence_sha256')
    before = init.snapshot(state.client, state.namespace)
    assert init.preflight(state.client, state.namespace, manifest, now=clock.now)['status'] == expected
    assert init.snapshot(state.client, state.namespace) == before


def test_preflight_distinguishes_initialization_incomplete_corrupt_and_generation():
    clock, state = empty_state(); manifest = manifest_for(state)
    assert init.preflight(state.client,state.namespace,manifest,now=clock.now)['status'] == 'INITIALIZATION_REQUIRED'
    state.reconcile_integrity(manifest, now=clock.now)
    state.set_latest(protected(),ttl_seconds=120)
    state.client.zsets.clear()
    assert init.preflight(state.client,state.namespace,manifest,now=clock.now)['status'] == 'INCOMPLETE_METADATA'
    state.client.hashes[state.namespace+':integrity']['__schema__']='broken'
    assert init.preflight(state.client,state.namespace,manifest,now=clock.now)['status'] == 'CORRUPT_METADATA'


@pytest.mark.parametrize('phase',['pending','hash','index','quality','complete'])
def test_interrupted_initialization_resumes_same_manifest_without_false_completion(phase):
    clock, state = empty_state(); owner=str(uuid4())
    target={'__schema__':'2','__retired__':str(timestamp_us(NOW)-1),'__ordinal__':'1',
            'Q:AAA':Watermark(timestamp_us(NOW),timestamp_us(NOW),'c',owner+':1').encode()}
    manifest=manifest_for(state,target)
    original=state.client._command; fired=[]
    def fault(command,*args):
        result=original(command,*args)
        matches=(phase=='pending' and command=='SET' and str(args[1]).startswith('incomplete|')
            or phase=='hash' and command=='HSET'
            or phase=='index' and command=='ZADD'
            or phase=='quality' and command=='HSET' and str(args[0]).endswith(':integrity-quality')
            or phase=='complete' and command=='SET' and str(args[1]).startswith('ready|'))
        if matches and not fired:
            fired.append(command);raise RuntimeError('synthetic operation interruption')
        return result
    state.client._command=fault
    with pytest.raises(RuntimeError):state.reconcile_integrity(manifest,now=clock.now)
    assert fired
    state.client._command=original
    status=init.preflight(state.client,state.namespace,manifest,now=clock.now)['status']
    assert (status=='COMPATIBLE_VERIFIED_STATE') == (phase=='complete')
    result=state.reconcile_integrity(manifest,now=clock.now)
    assert result['status'] in {'completed','already_completed'}
    assert record(state).reserved_us==timestamp_us(NOW)
    assert init.preflight(state.client,state.namespace,manifest,now=clock.now)['status']=='COMPATIBLE_VERIFIED_STATE'


def test_lost_initialization_reply_reconciles_exact_completion_record():
    clock,state=empty_state(); manifest=manifest_for(state)
    original=state.client.eval
    def lost(source,*args):
        result=original(source,*args)
        if source==init.APPLY_LUA:raise RuntimeError('lost reply after complete')
        return result
    state.client.eval=lost
    with pytest.raises(RuntimeError):state.reconcile_integrity(manifest,now=clock.now)
    state.client.eval=original
    assert state.reconcile_integrity(manifest,now=clock.now)['status']=='already_completed'


def test_concurrent_initializer_cannot_replace_an_unapproved_snapshot():
    clock,state=empty_state(); first=manifest_for(state); second=manifest_for(state)
    original=state.client.eval; interleaved=[]
    def race(source,*args):
        if source==init.APPLY_LUA and not interleaved:
            interleaved.append(True)
            state.reconcile_integrity(second,now=clock.now)
        return original(source,*args)
    state.client.eval=race
    with pytest.raises(IntegrityRejection,match='concurrent_change'):
        state.reconcile_integrity(first,now=clock.now)
    assert init.snapshot(state.client,state.namespace)['control']==init.validate_authorization(second,state.namespace,clock.now)


@pytest.mark.parametrize('damage',['missing_index','orphan_index','missing_quality'])
def test_explicit_metadata_repair_preserves_authority_and_never_certifies_old_price(damage):
    clock,state,manifest=initialized(); event=protected(); state.set_latest(event,ttl_seconds=120)
    original_floor=record(state).reserved_us
    if damage=='missing_index':state.client.zsets.clear()
    elif damage=='orphan_index':state.client.zsets[state.namespace+':integrity-lru']['Q:ORPHAN']=9
    else:state.client.hashes.pop(state.namespace+':integrity-quality')
    assert state.integrity_quality('AAA','Q',NOW,stream.stream_commit_version(event.stream_commit))=='UNKNOWN'
    target=dict(state.client.hashes[state.namespace+':integrity'])
    repair=manifest_for(state,target)
    assert state.reconcile_integrity(repair,now=clock.now)['status']=='completed'
    assert record(state).reserved_us==original_floor
    assert state.integrity_quality('AAA','Q',NOW,stream.stream_commit_version(event.stream_commit))=='UNACKNOWLEDGED'
    state.set_latest(protected(NOW+timedelta(seconds=1),owner=event.stream_commit['worker_instance_id'],revision=2),ttl_seconds=120)
    assert record(state).reserved_us>original_floor


def test_partial_reservation_is_repaired_without_deleting_unknown_bound():
    clock,state,_=initialized(); state.set_latest(protected(),ttl_seconds=120)
    original=state.client._command
    def stop(command,*args):
        result=original(command,*args)
        if command=='HSET' and args[1]=='Q:BBB' and '|u|' in str(args[-1]):raise RuntimeError('before index')
        return result
    state.client._command=stop
    with pytest.raises(RuntimeError):state.set_latest(protected(NOW+timedelta(seconds=1),symbol='BBB'),ttl_seconds=120)
    state.client._command=original
    before=record(state,symbol='BBB')
    repair=manifest_for(state,dict(state.client.hashes[state.namespace+':integrity']))
    state.reconcile_integrity(repair,now=clock.now)
    assert record(state,symbol='BBB')==before and before.status=='u'
    assert state.get_latest('BBB','Q') is None
    state.set_latest(protected(NOW+timedelta(seconds=2),symbol='BBB'),ttl_seconds=120)
    assert record(state,symbol='BBB').status=='c'


@pytest.mark.parametrize('mode',['reserved','retired','missing_authority','stale_generation'])
def test_reconciliation_rejects_floor_regression_and_unapproved_history(mode):
    clock,state,_=initialized(); state.set_latest(protected(),ttl_seconds=120)
    target=dict(state.client.hashes[state.namespace+':integrity'])
    if mode=='reserved':target['Q:AAA']=Watermark(1,1,'c',str(uuid4())+':1').encode()
    elif mode=='retired':
        state.client.hashes[state.namespace+':integrity']['__retired__']='100'
        target['__retired__']='1'
    repair=manifest_for(state,target)
    if mode=='missing_authority':repair.pop('history_evidence_sha256')
    elif mode=='stale_generation':repair['expected_snapshot_sha256']='0'*64
    before=init.snapshot(state.client,state.namespace)
    with pytest.raises(IntegrityRejection):state.reconcile_integrity(repair,now=clock.now)
    assert init.snapshot(state.client,state.namespace)==before


def test_legacy_writer_and_v1_reseed_are_rejected_after_explicit_cutover():
    clock,state,_=initialized(); event=protected();state.set_latest(event,ttl_seconds=120)
    with pytest.raises(IntegrityRejection,match='legacy_writer_fenced'):
        state.set_latest(replace(event,stream_commit=None),ttl_seconds=120)
    before=state.get_latest('AAA','Q')
    state.client.hashes[state.namespace+':integrity']={'__schema__':'1','__retired__':'0','__ordinal__':'0'}
    with pytest.raises(IntegrityRejection,match='invalid_integrity_schema'):
        state.set_latest(protected(NOW-timedelta(seconds=1)),ttl_seconds=120)
    assert state.get_latest('AAA','Q')==before


@pytest.mark.parametrize('channel',['A','AM'])
def test_authoritative_aggregate_context_resumes_without_inventing_sequence_reset(channel):
    async def scenario():
        clock,state=empty_state(); owner=str(uuid4());field=channel+':AAA'
        target={'__schema__':'2','__retired__':str(timestamp_us(NOW)),'__ordinal__':'1',
                field:Watermark(timestamp_us(NOW),timestamp_us(NOW),'c',owner+':1').encode()}
        context=json.dumps(dict(timestamp_us=timestamp_us(NOW),sequence_number=100,provider_event_id=None,token=owner+':1'))
        manifest=manifest_for(state,target,{field:context});state.reconcile_integrity(manifest,now=clock.now)
        worker=worker_for(clock,state)
        try:
            clock.now+=timedelta(seconds=1)
            event=dict(ev=channel,sym='AAA',q=101,e=int(clock.now.timestamp()*1000),o=100,h=100,l=100,c=100,v=1)
            await send(worker,event)
            assert state.get_latest('AAA',channel)['sequence_number']==101
            assert worker.metrics.sequence_gaps==0
            await worker.aclose()
            restarted=worker_for(clock,state)
            try:
                clock.now+=timedelta(seconds=1);event.update(q=1,e=int(clock.now.timestamp()*1000))
                await send(restarted,event)
                assert state.get_latest('AAA',channel)['sequence_number']==101
                event['q']=102;await send(restarted,event)
                assert state.get_latest('AAA',channel)['sequence_number']==102
            finally:await restarted.aclose()
        finally:await worker.aclose()
    asyncio.run(scenario())


def test_quality_is_causal_across_health_delay_holds_late_publication_and_new_commit():
    async def scenario():
        clock,state,_=initialized();worker=worker_for(clock,state)
        resolver=LiveQuoteResolver(state=state,rest_quote=lambda _:pytest.fail('No REST'),clock=lambda:clock.now)
        try:
            await send(worker,raw());health=state.get_health()
            first=stream.stream_commit_version(state.get_latest('AAA','Q')['stream_commit'])
            clock.now+=timedelta(seconds=1)
            await worker.process_raw_message(json.dumps(raw(when=clock.now,q=101,price=101)))
            assert state.get_health()==health and resolver.resolve('AAA').admission_status=='LIVE'
            second=stream.stream_commit_version(state.get_latest('AAA','Q')['stream_commit'])
            assert state.publish_quality('AAA','Q',second,10,'held','known_hold')=='updated'
            assert resolver.resolve('AAA').admission_status=='DEGRADED'
            state.set_health(health,ttl_seconds=30)
            assert resolver.resolve('AAA').admission_status=='DEGRADED'
            assert state.publish_quality('AAA','Q',second,0,'confirmed','late_ack')=='superseded'
            assert state.publish_quality('AAA','Q',second,9,'held','older_hold')=='superseded'
            # Same timestamp with a valid greater Q sequence keeps its own revision.
            await worker.process_raw_message(json.dumps(raw(when=clock.now,q=102,price=102)))
            third=stream.stream_commit_version(state.get_latest('AAA','Q')['stream_commit'])
            assert third[1]>second[1]>first[1]
            assert state.publish_quality('AAA','Q',second,999,'held','delayed_old_price')=='superseded'
            assert resolver.resolve('AAA').price==102 and resolver.resolve('AAA').admission_status=='LIVE'
            worker._connection_state='reconnecting';await worker._publish_health(force=True)
            assert resolver.resolve('AAA').admission_status=='DEGRADED'
            worker._connection_state='connected';await worker._publish_health(force=True)
            assert resolver.resolve('AAA').admission_status=='LIVE'
            original=state.client.eval;state.client.eval=lambda *args: (_ for _ in ()).throw(RuntimeError('offline read failure'))
            assert resolver.resolve('AAA').admission_status=='UNKNOWN'
            state.client.eval=original
            clock.now+=timedelta(seconds=121)
            assert state.get_latest('AAA','Q') is None
        finally:await worker.aclose()
    asyncio.run(scenario())


def test_missing_index_quality_reaches_actual_api_and_sse_without_a_price_change(monkeypatch):
    from flask import Flask,g,session
    async def setup():
        clock,state,_=initialized();worker=worker_for(clock,state)
        try:await send(worker,raw());return clock,state
        finally:await worker.aclose()
    clock,state=asyncio.run(setup())
    resolver=LiveQuoteResolver(state=state,rest_quote=lambda _:pytest.fail('No REST'),clock=lambda:clock.now)
    monkeypatch.setattr(api,'_live_quote_resolver',lambda:resolver)
    monkeypatch.setattr(api,'_symbols_for_live_user',lambda _: {'AAA'})
    monkeypatch.setattr(api,'_decision_context_for_user',lambda _:SimpleNamespace(after_hours_alerts=False,profile_version=1))
    elapsed=[100.]
    def advance(_):elapsed[0]+=3;state.client.zsets.clear()
    monkeypatch.setattr(api,'time',SimpleNamespace(time=lambda:elapsed[0],sleep=advance))
    app=Flask(__name__);app.secret_key='offline';app.config.update(TESTING=True,LIVE_SSE_INTERVAL_SECONDS=.25,LIVE_SSE_HEARTBEAT_SECONDS=2)
    evaluated=[]
    app.extensions.update(market_stream_state=state,live_trigger_engine=SimpleNamespace(evaluate=lambda **kw:(evaluated.append(kw) or {'fire':False})))
    with app.test_request_context('/api/live-market-stream?symbols=AAA'):
        session['user_id']=1;g.request_id='offline';response=api.live_market_stream();iterator=iter(response.response)
        try:chunks=[next(iterator) for _ in range(4)]
        finally:iterator.close()
    quotes=[json.loads(next(line[6:] for line in chunk.splitlines() if line.startswith('data: ')))['quotes'][0] for chunk in chunks if 'event: quotes' in chunk]
    assert len(quotes)==2 and len(evaluated)==1
    assert quotes[0]['admission_status']=='LIVE' and quotes[1]['admission_status']=='UNKNOWN'
    assert quotes[0]['price']==quotes[1]['price']==100 and quotes[0]['stream_commit']==quotes[1]['stream_commit']
    with app.test_request_context('/quote?symbol=AAA'):
        g.request_id='offline';quote=api.api_quote().get_json()['data']
        assert quote['admission_status']=='UNKNOWN' and quote['is_degraded']


def test_startup_blocks_before_any_provider_connection_when_authorization_is_missing():
    async def scenario():
        clock,state=empty_state();worker=worker_for(clock,state)
        try:
            await worker.run()
            assert worker._connection_state=='integrity_blocked'
            assert not state.client.hashes and not state.client.strings
            assert 'UNVERIFIED_PERSISTENCE' in worker._last_error
        finally:await worker.aclose()
    asyncio.run(scenario())


def test_quality_metadata_and_explicit_context_do_not_accumulate_schema_versions(monkeypatch):
    monkeypatch.setattr(stream,'MAX_WATERMARKS',4)
    clock,state,_=initialized()
    for i in range(40):
        state.set_latest(protected(NOW+timedelta(seconds=i),symbol=f'S{i}'),ttl_seconds=1)
        assert len(state.client.hashes[state.namespace+':integrity-quality'])<=4
        assert len(state.client.zsets[state.namespace+':integrity-lru'])<=4
        assert len(state.client.strings)==1
    for _ in range(5):
        repair=manifest_for(state,dict(state.client.hashes[state.namespace+':integrity']))
        state.reconcile_integrity(repair,now=clock.now)
        assert len(state.client.strings)==1
        assert set(state.client.hashes)<={state.namespace+suffix for suffix in (':integrity',':integrity-quality',':integrity-context')}


def test_aggregate_startup_requires_explicit_context_for_retained_bounds():
    clock,state=empty_state()
    manifest=manifest_for(state,{'__schema__':'2','__retired__':str(timestamp_us(NOW)),'__ordinal__':'0'})
    state.reconcile_integrity(manifest,now=clock.now)
    result=init.preflight(state.client,state.namespace,manifest,now=clock.now,required={'AM':{'AAA'}})
    assert result['status']=='AGGREGATE_CONTEXT_REQUIRED' and result['field']=='AM:AAA'
    assert init.preflight(state.client,state.namespace,manifest,now=clock.now,required={'Q':{'AAA'}})['status']=='COMPATIBLE_VERIFIED_STATE'


@pytest.mark.parametrize('mutation',['ttl','missing_quality','wrong_token','wrong_timestamp','wrong_index_member'])
def test_reader_and_writer_fail_closed_on_incomplete_confirmation(mutation):
    clock,state,_=initialized();event=protected();state.set_latest(event,ttl_seconds=120)
    version=stream.stream_commit_version(event.stream_commit)
    if mutation=='ttl':
        original=state.client.pttl
        state.client.pttl=lambda key: 1000 if key.endswith(':integrity') else original(key)
    elif mutation=='missing_quality':state.client.hashes.pop(state.namespace+':integrity-quality')
    elif mutation=='wrong_token':state.client.hashes[state.namespace+':integrity-quality']['Q:AAA']=str(uuid4())+':1|0|confirmed|bad'
    elif mutation=='wrong_timestamp':state.client.hashes[state.namespace+':integrity']['Q:AAA']=Watermark(timestamp_us(NOW)+1,timestamp_us(NOW),'c',version[0]+':1').encode()
    else:state.client.zsets[state.namespace+':integrity-lru']={'Q:DIFFERENT':1}
    assert state.integrity_quality('AAA','Q',NOW,version)=='UNKNOWN'
    if mutation in {'ttl','missing_quality'}:
        with pytest.raises(IntegrityRejection):state.set_latest(protected(NOW+timedelta(seconds=1)),ttl_seconds=120)


def test_price_reply_loss_cannot_create_reader_confirmation():
    async def scenario():
        clock,state,_=initialized();worker=worker_for(clock,state)
        try:
            await send(worker,raw());clock.now+=timedelta(seconds=1)
            original=state.client.eval
            def lost(source,*args):
                result=original(source,*args)
                if source==stream.COMMIT_LUA:raise RuntimeError('lost price reply')
                return result
            state.client.eval=lost
            with pytest.raises(RuntimeError):await send(worker,raw(when=clock.now,q=101))
            state.client.eval=original
            latest=state.get_latest('AAA','Q');version=stream.stream_commit_version(latest['stream_commit'])
            assert record(state).status=='c'
            assert state.integrity_quality('AAA','Q',clock.now,version)=='UNACKNOWLEDGED'
            assert not state.integrity_verified('AAA','Q',clock.now,version)
        finally:await worker.aclose()
    asyncio.run(scenario())


def test_failing_hold_publication_stops_admission_and_keeps_local_diagnostic():
    async def scenario():
        clock,state,_=initialized();worker=worker_for(clock,state)
        try:
            await send(worker,raw());original=state.client.eval
            def fail(source,*args):
                if source==stream.QUALITY_LUA:raise RuntimeError('synthetic hold write failure')
                return original(source,*args)
            state.client.eval=fail
            with pytest.raises(IntegrityRejection,match='quality_publication_unknown'):
                await worker.process_raw_message(json.dumps(raw(q=1,price=101)))
            assert worker._freshness_degraded[('Q','AAA')]=='equal_time_sequence_ambiguity'
            assert state.get_latest('AAA','Q')['payload']['midpoint']==100
        finally:await worker.aclose()
    asyncio.run(scenario())


def test_old_generation_cannot_write_confirm_or_mark_stale_after_reconciliation():
    clock,state,manifest=initialized(); event=protected();state.set_latest(event,ttl_seconds=120)
    old=repository(clock,state.client.backend,initialize=False)
    old.integrity_authorization=manifest
    old_control=old._expected_integrity_control
    repair=manifest_for(state,dict(state.client.hashes[state.namespace+':integrity']))
    state.reconcile_integrity(repair,now=clock.now)
    assert state._expected_integrity_control!=old_control
    before=state.get_latest('AAA','Q');version=stream.stream_commit_version(event.stream_commit)
    with pytest.raises(IntegrityRejection,match='generation_fenced'):
        old.set_latest(protected(NOW+timedelta(seconds=1)),ttl_seconds=120)
    assert old.publish_quality('AAA','Q',version,99,'held','obsolete_writer')=='integrity_generation_fenced'
    old.mark_symbols_stale(['AAA'],reason='obsolete_writer',ttl_seconds=300)
    assert state.get_latest('AAA','Q')==before
    assert old.integrity_quality('AAA','Q',NOW,version)=='UNKNOWN'
    assert old.integrity_preflight()['status']=='INCOMPATIBLE_GENERATION'


def test_configuration_change_is_unverified_even_with_noeviction():
    clock,state=empty_state();manifest=manifest_for(state)
    original=state.client.backend.config_get
    state.client.backend.config_get=lambda name: {name:'everysec'} if name=='appendfsync' else original(name)
    assert init.preflight(state.client,state.namespace,manifest,now=clock.now)['status']=='UNVERIFIED_PERSISTENCE_CONFIGURATION_CHANGED'


def test_preflight_distinguishes_wrong_redis_type_from_unavailable_store():
    clock,state=empty_state();manifest=manifest_for(state)
    state.client.set(state.namespace+':integrity','not a hash')
    assert init.preflight(state.client,state.namespace,manifest,now=clock.now)['status']=='CORRUPT_METADATA'
    state.client.eval=lambda *args: (_ for _ in ()).throw(TimeoutError('synthetic read timeout'))
    assert init.preflight(state.client,state.namespace,manifest,now=clock.now)['status']=='UNAVAILABLE_INTEGRITY_STORE'
