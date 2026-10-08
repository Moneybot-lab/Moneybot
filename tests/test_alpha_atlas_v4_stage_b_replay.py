from __future__ import annotations
import hashlib,json,math,os,socket,subprocess,sys
from datetime import date,datetime,time,timezone
from pathlib import Path
import pytest
from moneybot.services.alpha_atlas_v4_acquisition import *
from moneybot.services.alpha_atlas_v4_prospective_snapshot import CaptureError,ImmutableStore,NY,canonical_bytes,sha256_bytes
from moneybot.services.alpha_atlas_v4_stage_b import S3OperationLedger
from moneybot.services.alpha_atlas_v4_stage_b_replay import replay_saved_evidence

REPO=Path(__file__).resolve().parents[1]; UTC=timezone.utc; RECEIVED=datetime(2026,10,8,1,tzinfo=UTC)

def ms(day:date)->int:return int(datetime.combine(day,time(0),NY).timestamp()*1000)
def rows(days):return [{'o':100+i,'h':101+i,'l':99+i,'c':100+i,'v':1000+i,'vw':100+i,'n':10+i,'t':ms(d)} for i,d in enumerate(days)]
def normalized_result(symbol,days,*,adjusted=False,values=None,objects=1):
    values=list(values if values is not None else rows(days)); chunks=[values[i::objects] for i in range(objects)]; result_objects=[]; receipts={}
    for page,chunk in enumerate(chunks,1):
        payload={'status':'OK','ticker':symbol,'adjusted':adjusted,'results':chunk,'resultsCount':len(chunk),'count':len(chunk),'queryCount':len(chunk),'request_id':f'provider-{page}'}
        if page<objects:payload['next_url']='https://api.massive.com/v2/aggs/ticker/AAPL/range/1/day/x/y?cursor=synthetic'
        raw=canonical_bytes(payload); digest=sha256_bytes(raw); receipt_path=f'r{page}.json'
        receipt={'request_id':f'history:{symbol}','response_sha256':digest,'response_bytes':len(raw),'received_at':RECEIVED.isoformat(),'late':False,'provider_request_id':f'provider-{page}'}
        result_objects.append({'page':page,'sha256':digest,'bytes':len(raw),'receipt_path':receipt_path,'payload':payload});receipts[receipt_path]=receipt
    return {'request_id':f'history:{symbol}','complete':True,'objects':result_objects},lambda p:receipts[p]

def test_demonstrated_abbreviated_rows_normalize_with_provenance_and_dst():
    days=[date(2026,1,2),date(2026,7,6)];result,loader=normalized_result('AAPL',days,objects=2)
    out=normalize_massive_daily_history(result,expected_symbol='AAPL',window_sessions=days,receipt_loader=loader)
    assert [x['date'] for x in out['rows']]==['2026-01-02','2026-07-06']
    assert all('date' not in raw for obj in result['objects'] for raw in obj['payload']['results'])
    assert out['rows'][0]['source_timestamp_utc'].endswith('05:00:00+00:00')
    assert out['rows'][1]['source_timestamp_utc'].endswith('04:00:00+00:00')
    assert len(out['source_object_sha256s'])==2 and {p['source_row_index'] for p in out['row_provenance']}=={0}

def test_normalizer_rejects_units_sessions_duplicates_adjustment_and_bad_values():
    session=date(2026,10,6)
    cases=[]
    base=rows([session])[0]
    for mutation,code in [({'t':int(base['t']/1000)},'TIMESTAMP_UNIT'),({'t':ms(date(2026,10,4))},'NON_SESSION'),({'c':float('nan')},'ROW_INVALID'),({'vw':None},'ROW_INVALID')]:
        row={**base,**mutation};result,loader=normalized_result('AAPL',[session],values=[row]);cases.append((result,loader,code))
    for result,loader,code in cases:
        with pytest.raises(CaptureError,match=code):normalize_massive_daily_history(result,expected_symbol='AAPL',window_sessions=[session],receipt_loader=loader)
    result,loader=normalized_result('AAPL',[session],values=[base,{**base,'c':101}])
    with pytest.raises(CaptureError,match='DUPLICATE'):normalize_massive_daily_history(result,expected_symbol='AAPL',window_sessions=[session],receipt_loader=loader)
    result,loader=normalized_result('AAPL',[session],adjusted=True)
    with pytest.raises(CaptureError,match='ENVELOPE'):normalize_massive_daily_history(result,expected_symbol='AAPL',window_sessions=[session],receipt_loader=loader)
    result,loader=normalized_result('AAPL',[session]);result['objects'][0]['payload']['next_url']='https://api.massive.com/x?cursor=more'
    with pytest.raises(CaptureError,match='PAGINATION_INCOMPLETE'):normalize_massive_daily_history(result,expected_symbol='AAPL',window_sessions=[session],receipt_loader=loader)

def test_feature_alignment_and_split_adjustment_share_canonical_rows():
    days=prior_sessions(date(2026,10,7),75); result,loader=normalized_result('AAPL',days); normalized=normalize_massive_daily_history(result,expected_symbol='AAPL',window_sessions=days,receipt_loader=loader)
    misaligned=normalized['rows'][:-1]
    reasons=validate_feature_rows(normalized['rows'],misaligned,misaligned[:-20],days)
    assert 'CONTEXT_ALIGNMENT_FAILED:SPY' in reasons and 'CONTEXT_ALIGNMENT_FAILED:SECTOR' in reasons
    splits=[{'id':'f','ticker':'AAPL','execution_date':'2026-08-03','adjustment_type':'forward_split','split_from':1,'split_to':4},{'id':'r','ticker':'AAPL','execution_date':'2026-09-01','adjustment_type':'reverse_split','split_from':2,'split_to':1}]
    adjusted=adjust_unadjusted_bars(normalized['rows'],splits,date(2026,10,6),source_object_sha256s=normalized['source_object_sha256s'],normalized_history_sha256=normalized['derived_content_sha256'])
    first=adjusted['bars'][0];factor=(1/4)*2
    assert first['open']==pytest.approx(normalized['rows'][0]['open']*factor)
    assert first['vwap']==pytest.approx(normalized['rows'][0]['vwap']*factor)
    assert first['volume']==pytest.approx(normalized['rows'][0]['volume']/factor)
    assert adjusted['binding']['application']=='EXACTLY_ONCE_FROM_PROVIDER_UNADJUSTED'
    with pytest.raises(CaptureError,match='INCOMPATIBLE_ADJUSTMENT_BASIS'):
        adjust_unadjusted_bars(adjusted['bars'],splits,date(2026,10,6),source_sha256='a'*64)

def make_runtime(tmp_path):
    root=tmp_path/'runtime';primary=ImmutableStore(root/'primary');days=prior_sessions(date(2026,10,7),75);paths={}
    payloads={s:{'status':'OK','ticker':s,'adjusted':False,'results':rows(days),'resultsCount':75,'count':75,'queryCount':75,'request_id':f'p-{s}'} for s in ('AAPL','SPY','XLK')}
    payloads['identity']={'status':'OK','request_id':'p-id','results':{'ticker':'AAPL','type':'CS','cik':'0000320193','share_class_figi':'BBG001S5N8V8'}}
    payloads['splits']={'status':'OK','request_id':'p-splits','results':[]}
    definitions=[('history:AAPL','history/history_AAPL',payloads['AAPL']),('history:SPY','history/history_SPY',payloads['SPY']),('history:XLK','history/history_XLK',payloads['XLK']),('identity:AAPL','identity/identity_AAPL',payloads['identity']),('splits:global','splits/splits_global',payloads['splits'])]
    attempt=AttemptLedger(primary,synthetic=False)
    for request_id,folder,payload in definitions:
        raw=canonical_bytes(payload);digest=sha256_bytes(raw);relative=f'responses/{folder}/page-1-{digest}.json';primary.publish(relative,raw)
        aid=attempt.reserve('operational_verification',request_id,RECEIVED);attempt.event(aid,'TRANSMITTING',RECEIVED,page=1,retry=0)
        receipt={'request_id':request_id,'response_sha256':digest,'response_bytes':len(raw),'received_at':RECEIVED.isoformat(),'late':False,'provider_request_id':payload['request_id']}
        primary.publish(relative+'.receipt.json',canonical_bytes(receipt));attempt.event(aid,'PERSISTED',RECEIVED,response_sha256=digest,late=False);paths[request_id]=relative
    s3=S3OperationLedger(ImmutableStore(root/'s3-operation-ledger'))
    for i in range(91):oid=s3.reserve('SYNTHETIC_REPLAY_EVIDENCE');s3.event(oid,'TRANSMITTING');s3.event(oid,'SUCCEEDED')
    for relative in ('run/execution-claim.json','run/continuation-claim.json','run/outcome.json'):
        primary.publish(relative,canonical_bytes({'synthetic_replay_fixture':relative}))
    covered=[]
    for path in sorted(p for p in primary.root.rglob('*') if p.is_file() and not p.name.startswith('.') and 'backup/' not in p.as_posix()):
        covered.append({'path':path.relative_to(primary.root).as_posix(),'sha256':sha256_bytes(path.read_bytes()),'bytes':path.stat().st_size})
    inventory={'schema_version':'alpha-atlas-v4-backup-inventory.v1','covered':covered};ib=canonical_bytes(inventory);ih=sha256_bytes(ib);ip=f'backup/inventory-{ih}.json';primary.publish(ip,ib)
    frozen=covered+[{'path':ip,'sha256':ih,'bytes':len(ib)}]
    receipts=[{'bucket':'b','key':f'k/{i}','version_id':f'v{i}','sha256':x['sha256'],'bytes':x['bytes'],'retention_mode':'GOVERNANCE','retain_until':'2027-04-06T00:00:00+00:00'} for i,x in enumerate(frozen)]
    completion={'inventory_path':ip,'inventory_sha256':ih,'covered_object_count':len(frozen),'covered_bytes':sum(x['bytes'] for x in frozen),'receipts':receipts,'restored_object_count':0}
    cb=canonical_bytes(completion);primary.publish(f'backup/completion-{sha256_bytes(cb)}.json',cb)
    return root,paths

def test_end_to_end_saved_replay_is_offline_immutable_and_tamper_fails(tmp_path,monkeypatch):
    root,paths=make_runtime(tmp_path);before={p.relative_to(root).as_posix():sha256_bytes(p.read_bytes()) for p in root.rglob('*') if p.is_file()}
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**k:(_ for _ in ()).throw(AssertionError('network forbidden')))
    for name in ('ALPHA_ATLAS_V4_MASSIVE_API_KEY','MONEYBOT_STAGE_B_AWS_ACCESS_KEY_ID','MONEYBOT_STAGE_B_AWS_SECRET_ACCESS_KEY'):monkeypatch.setenv(name,'MUST_NOT_READ')
    report=replay_saved_evidence(REPO,root,saved_paths=paths)
    assert report['status']=='PASS' and report['live_provider_requests']==report['live_aws_requests']==0
    assert all(x['rows']==75 for x in report['normalized_histories'].values())
    assert report['massive_accounting']['attempts']==5 and report['s3_accounting']['operations']==91
    after={p.relative_to(root).as_posix():sha256_bytes(p.read_bytes()) for p in root.rglob('*') if p.is_file()};assert before==after
    source=root/'primary'/paths['history:AAPL'];source.write_bytes(source.read_bytes()+b'x')
    with pytest.raises(CaptureError,match='SOURCE_HASH'):replay_saved_evidence(REPO,root,saved_paths=paths)

def test_replay_cli_no_clobber_before_reading_inputs(tmp_path):
    output=tmp_path/'out';output.mkdir();marker=output/'keep';marker.write_text('x');env=os.environ.copy();env.pop('PYTHONPATH',None)
    run=subprocess.run([sys.executable,'-m','scripts.replay_alpha_atlas_v4_stage_b_saved_evidence','--saved-evidence-root',str(tmp_path/'missing'),'--output-dir',str(output)],cwd=REPO,env=env,text=True,capture_output=True)
    assert run.returncode!=0 and 'REPLAY_OUTPUT_EXISTS' in run.stderr and marker.read_text()=='x'
