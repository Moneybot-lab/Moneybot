from __future__ import annotations
import io, json, os, subprocess, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from moneybot.services.alpha_atlas_v4_acquisition import RequestSpec, TransportResponse, acquisition_clock
from moneybot.services.alpha_atlas_v4_prospective_snapshot import CaptureError, ImmutableStore, canonical_bytes, sha256_bytes
from moneybot.services.alpha_atlas_v4_stage_b import *
from scripts.run_alpha_atlas_v4_stage_b_offline import OfflineS3, OfflineTransport, NOW
UTC=timezone.utc
REPO=Path(__file__).resolve().parents[1]

def changed(value,**updates):
    out={**value,**updates}; out.pop('content_sha256',None); out['content_sha256']=sha256_bytes(canonical_bytes(out)); return out

def runner(tmp_path,now=lambda:NOW): return StageBRunner(REPO,tmp_path,offline=True,now=now)

def backup(root,client=None):
    client=client or OfflineS3()
    return S3EvidenceBackup(client,'test-only','prefix',S3OperationLedger(ImmutableStore(root/'ops')),expected_owner='123456789012',now=lambda:NOW)

def test_authorization_hash_binding_and_expiry_reject_before_transport(tmp_path):
    calls=[]; factory=lambda:(calls.append(1) or OfflineTransport())
    auth=synthetic_authorization(); auth['fixture_sha256']='0'*64
    with pytest.raises(CaptureError,match='AUTHORIZATION_HASH'): runner(tmp_path/'a').execute(auth,effective_sector_fixture(),factory,lambda p:backup(tmp_path/'a'))
    auth=changed(synthetic_authorization(),fixture_sha256='0'*64)
    with pytest.raises(CaptureError,match='AUTHORIZATION_BINDING'): runner(tmp_path/'b').execute(auth,effective_sector_fixture(),factory,lambda p:backup(tmp_path/'b'))
    with pytest.raises(CaptureError,match='EXPIRED'): runner(tmp_path/'c',lambda:NOW+timedelta(days=1)).execute(synthetic_authorization(),effective_sector_fixture(),factory,lambda p:backup(tmp_path/'c'))
    assert calls==[]

def test_missing_or_ineffective_sector_blocks_before_transport(tmp_path):
    calls=[]; factory=lambda:(calls.append(1) or OfflineTransport())
    bad=changed(effective_sector_fixture(),effective_through='2026-10-04')
    with pytest.raises(CaptureError,match='NOT_EFFECTIVE'): runner(tmp_path).execute(synthetic_authorization(),bad,factory,lambda p:backup(tmp_path))
    assert not calls

def test_identity_history_quarantine_and_resolution_failure(tmp_path):
    class BadIdentity(OfflineTransport):
        def send(self,request,**kwargs):
            response=super().send(request,**kwargs)
            if request.family=='identity': return TransportResponse(200,canonical_bytes({'results':[{'ticker':'AAPL','type':'CS','cik':'0000320193'}]}),NOW)
            return response
    with pytest.raises(CaptureError,match='IDENTITY_UNRESOLVED'):
        runner(tmp_path).execute(synthetic_authorization(),effective_sector_fixture(),lambda:BadIdentity(),lambda p:backup(tmp_path))
    assert (tmp_path/'primary/responses/history/history_AAPL').is_dir()
    assert not (tmp_path/'primary/handoff/handoff.json').exists()

def test_massive_transport_bound_one_exchange_no_redirect_or_secret():
    fixture=load_fixture(REPO); calls=[]
    def sender(path,timeout): calls.append(path); return 302,b'',{'location':'https://evil.example'}
    transport=MassiveStageBTransport(fixture,'DO_NOT_PERSIST',sender=sender,clock=lambda:NOW)
    spec=plan_from_fixture(fixture,'x')['requests'][0]
    with pytest.raises(CaptureError,match='REDIRECT'): transport.send(spec,page_url=None,timeout_seconds=3)
    assert len(calls)==1 and 'DO_NOT_PERSIST' not in calls[0]
    wrong=RequestSpec(spec.request_id,spec.family,spec.endpoint,{**spec.params,'adjusted':'true'},spec.symbol,1,spec.response_limit)
    with pytest.raises(CaptureError,match='OUTSIDE_BOUND_PLAN'): transport.send(wrong,page_url=None,timeout_seconds=3)
    with pytest.raises(CaptureError,match='UNSAFE'): transport.send(spec,page_url='https://evil.example/x',timeout_seconds=3)

def test_s3_configuration_version_checksum_and_exact_restore(tmp_path):
    client=OfflineS3(); adapter=backup(tmp_path,client); adapter.verify_configuration()
    receipt=adapter.publish('x',b'payload'); assert receipt['version_id']=='synthetic-v1' and receipt['retention_mode']=='GOVERNANCE'
    result=adapter.restore(receipt,ImmutableStore(tmp_path/'restore')); assert result['sha256']==sha256_bytes(b'payload')
    assert not any('ETag' in row for row in adapter.ledger.records())

def test_s3_missing_config_checksum_and_partial_failure(tmp_path):
    class Bad(OfflineS3):
        def get_bucket_versioning(self,**kwargs): return {'Status':'Suspended'}
    with pytest.raises(CaptureError,match='VERSIONING'): backup(tmp_path/'a',Bad()).verify_configuration()
    class Corrupt(OfflineS3):
        def get_object(self,**kwargs): return {'Body':io.BytesIO(b'wrong')}
    adapter=backup(tmp_path/'b',Corrupt()); adapter.verify_configuration()
    with pytest.raises(CaptureError,match='CHECKSUM'): adapter.publish('x',b'right')
    assert any(x.get('operation')=='PUT_OBJECT' and x['event']=='RESERVED' for x in adapter.ledger.records())

def test_complete_runner_backup_restore_caps_and_zero_live(tmp_path):
    report=runner(tmp_path).execute(synthetic_authorization(),effective_sector_fixture(),lambda:OfflineTransport(),lambda p:backup(tmp_path))
    assert report['status']=='PASS' and report['synthetic_transport_attempts']==5 and report['live_provider_requests']==0
    assert report['quarantined_then_released']==['history:AAPL','history:SPY','history:XLK']
    assert report['backup']['objects']==report['restore']['objects'] and report['backup']['bytes_accounted']<=BACKUP_CAP
    assert report['s3_operations']==s3_operation_budget(12)['total']==54

def test_primary_and_backup_caps(tmp_path,monkeypatch):
    import moneybot.services.alpha_atlas_v4_stage_b as module
    monkeypatch.setattr(module,'PRIMARY_CAP',1)
    with pytest.raises(CaptureError,match='PRIMARY_EVIDENCE_LIMIT'):
        runner(tmp_path).execute(synthetic_authorization(),effective_sector_fixture(),lambda:OfflineTransport(),lambda p:backup(tmp_path))

def test_deadline_crossing_and_attempt_ledger_restart(tmp_path):
    timing=acquisition_clock(SESSION)
    class Late(OfflineTransport):
        def send(self,request,**kwargs):
            r=super().send(request,**kwargs); return TransportResponse(r.status,r.body,timing.cutoff+timedelta(microseconds=1))
    with pytest.raises(CaptureError,match='LATE_RESPONSE'):
        runner(tmp_path/'late',lambda:timing.cutoff-timedelta(seconds=1)).execute(synthetic_authorization(),effective_sector_fixture(),lambda:Late(),lambda p:backup(tmp_path/'late'))
    store=ImmutableStore(tmp_path/'ledger'); ledger=AttemptLedger(store,synthetic=False); attempt=ledger.reserve('operational_verification','x',NOW); ledger.event(attempt,'TRANSMITTING',NOW)
    assert AttemptLedger(store,synthetic=False).reconcile_uncertain(NOW)==1

def test_offline_cli_no_credentials_metadata_or_pythonpath(tmp_path):
    env=os.environ.copy(); env.pop('PYTHONPATH',None); env.update({'AWS_ACCESS_KEY_ID':'MUST_NOT_READ','AWS_SECRET_ACCESS_KEY':'MUST_NOT_READ','AWS_EC2_METADATA_SERVICE_ENDPOINT':'http://127.0.0.1:1','MASSIVE_API_KEY':'MUST_NOT_READ'})
    output=tmp_path/'out'
    run=subprocess.run([sys.executable,'-m','scripts.run_alpha_atlas_v4_stage_b_offline','--offline-synthetic','--output-dir',str(output)],cwd=REPO,env=env,text=True,capture_output=True,timeout=30)
    assert run.returncode==0,run.stderr
    report=json.loads((output/'report.json').read_text()); assert report['live_provider_requests']==0 and not report['real_acquisition_authorized']
    assert 'MUST_NOT_READ' not in ''.join(p.read_text() for p in output.iterdir())
    subprocess.run(['sha256sum','-c','SHA256SUMS'],cwd=output,check=True,capture_output=True)

def test_stage_a_remains_synthetic_only(tmp_path):
    from moneybot.services.alpha_atlas_v4_acquisition import AcquisitionRunner
    with pytest.raises(CaptureError,match='REAL_ACQUISITION_DISABLED'):
        AcquisitionRunner(ImmutableStore(tmp_path),OfflineTransport(),synthetic=False)

def test_identity_endpoint_mapping_shape_is_preserved_raw_and_normalized_for_gate(tmp_path):
    class MappingIdentity(OfflineTransport):
        def send(self,request,**kwargs):
            if request.family=='identity':
                return TransportResponse(200,canonical_bytes({'request_id':'x','results':{'ticker':'AAPL','type':'CS','share_class_figi':'BBG001S5N8V8'}}),NOW)
            return super().send(request,**kwargs)
    report=runner(tmp_path).execute(synthetic_authorization(),effective_sector_fixture(),lambda:MappingIdentity(),lambda p:backup(tmp_path))
    assert report['identity']['share_class_figi']=='BBG001S5N8V8'
    raw=next(p for p in (tmp_path/'primary/responses/identity/identity_AAPL').glob('*.json') if not p.name.endswith('.receipt.json'))
    assert isinstance(json.loads(raw.read_text())['results'],dict)

def test_operational_attempt_cap_and_credential_redaction(tmp_path):
    ledger=AttemptLedger(ImmutableStore(tmp_path),synthetic=False)
    for n in range(18): ledger.reserve('operational_verification',f'r{n}',NOW)
    with pytest.raises(CaptureError,match='ATTEMPT_BUDGET_EXHAUSTED'):
        ledger.reserve('operational_verification','overflow',NOW)
    serialized=(tmp_path/'acquisition_attempts.jsonl').read_text()
    assert 'apikey' not in serialized.lower() and 'authorization' not in serialized.lower()

def test_concrete_boto_client_uses_explicit_credentials_and_disables_sdk_retries(monkeypatch):
    import socket, types
    monkeypatch.setattr(socket.socket,"connect",lambda *args,**kwargs: (_ for _ in ()).throw(AssertionError("network forbidden")))
    captured={}
    class Config:
        def __init__(self,**kwargs): self.kwargs=kwargs
    def client(service,**kwargs): captured.update(service=service,**kwargs); return object()
    monkeypatch.setitem(sys.modules,'boto3',types.SimpleNamespace(client=client))
    monkeypatch.setitem(sys.modules,'botocore.config',types.SimpleNamespace(Config=Config))
    result=create_boto3_s3_client(access_key_id='explicit-id',secret_access_key='explicit-secret',session_token='explicit-token')
    assert result is not None and captured['service']=='s3'
    assert captured['aws_access_key_id']=='explicit-id' and captured['aws_secret_access_key']=='explicit-secret'
    assert captured['config'].kwargs['retries']=={'total_max_attempts':1,'mode':'standard'}
    assert captured['region_name']=='us-east-1'


def test_every_s3_call_is_reserved_before_sdk_invocation_and_exact_count(tmp_path):
    ledger=S3OperationLedger(ImmutableStore(tmp_path/'ops'))
    class Observed(OfflineS3):
        def _reserved(self,operation):
            rows=ledger.records(); assert rows[-2]['event']=='RESERVED' and rows[-2]['operation']==operation
            assert rows[-1]['event']=='TRANSMITTING'
        def head_bucket(self,**kwargs): self._reserved('HEAD_BUCKET'); return super().head_bucket(**kwargs)
        def get_bucket_location(self,**kwargs): self._reserved('GET_BUCKET_LOCATION'); return super().get_bucket_location(**kwargs)
        def get_bucket_versioning(self,**kwargs): self._reserved('GET_BUCKET_VERSIONING'); return super().get_bucket_versioning(**kwargs)
        def get_public_access_block(self,**kwargs): self._reserved('GET_PUBLIC_ACCESS_BLOCK'); return super().get_public_access_block(**kwargs)
        def get_bucket_encryption(self,**kwargs): self._reserved('GET_BUCKET_ENCRYPTION'); return super().get_bucket_encryption(**kwargs)
        def get_object_lock_configuration(self,**kwargs): self._reserved('GET_OBJECT_LOCK'); return super().get_object_lock_configuration(**kwargs)
    adapter=S3EvidenceBackup(Observed(),'bucket','prefix',ledger,expected_owner='123456789012',now=lambda:NOW)
    adapter.verify_configuration(); assert ledger.operation_count()==6


def test_s3_uncertain_restart_is_preserved_without_reissue(tmp_path):
    ledger=S3OperationLedger(ImmutableStore(tmp_path)); operation=ledger.reserve('PUT_OBJECT',key='x'); ledger.event(operation,'TRANSMITTING')
    restarted=S3OperationLedger(ImmutableStore(tmp_path)); assert restarted.reconcile_uncertain()==1
    assert restarted.operation_count()==1 and any(r['event']=='UNCERTAIN' for r in restarted.records())


def test_s3_public_access_encryption_and_owner_are_required(tmp_path):
    class Public(OfflineS3):
        def get_public_access_block(self,**kwargs): return {'PublicAccessBlockConfiguration':{}}
    with pytest.raises(CaptureError,match='PUBLIC_ACCESS'): backup(tmp_path/'public',Public()).verify_configuration()
    class Encryption(OfflineS3):
        def get_bucket_encryption(self,**kwargs): return {'ServerSideEncryptionConfiguration':{'Rules':[]}}
    with pytest.raises(CaptureError,match='ENCRYPTION'): backup(tmp_path/'encryption',Encryption()).verify_configuration()
    with pytest.raises(CaptureError,match='EXPECTED_OWNER'):
        S3EvidenceBackup(OfflineS3(),'bucket','prefix',S3OperationLedger(ImmutableStore(tmp_path/'owner')),expected_owner='')


def test_fixed_fixture_is_expired_after_deadline_before_any_client_creation(tmp_path):
    created=[]
    expired=datetime(2026,10,5,11,30,0,tzinfo=UTC)
    with pytest.raises(CaptureError,match='FIXED_SESSION_EXPIRED'):
        runner(tmp_path,lambda:expired).execute(synthetic_authorization(),effective_sector_fixture(),lambda:(created.append('massive') or OfflineTransport()),lambda p:(created.append('s3') or backup(tmp_path)))
    assert created==[]


def test_runtime_guard_stops_unsafe_headroom(tmp_path):
    root=tmp_path/'root'; root.mkdir()
    guard=RuntimeResourceGuard(root,max_rss_kib=1)
    with pytest.raises(CaptureError,match='RUNTIME_HEADROOM_EXHAUSTED'): guard('DURING_ACQUISITION')


def test_s3_operation_budget_expected_maximum_and_cap():
    assert s3_operation_budget(12)=={'configuration':6,'uploads':12,'retention_version_heads':12,'checksum_readbacks':12,'exact_version_restores':12,'total':54}
    assert s3_operation_budget(14)['total']==62<S3_OPERATION_CAP
    with pytest.raises(CaptureError,match='OUT_OF_BOUNDS'): s3_operation_budget(15)

def test_operational_cli_rejects_unapproved_authorization_without_clients(tmp_path):
    auth=tmp_path/'auth.json'; auth.write_text(json.dumps(synthetic_authorization()))
    sector=tmp_path/'sector.json'; sector.write_text(json.dumps(effective_sector_fixture()))
    config=tmp_path/'config.json'; config.write_text(json.dumps({'persistent_root':REQUIRED_ROOT,'s3_bucket':'never-contact','s3_prefix':'x','s3_expected_owner':'123456789012','s3_region':'us-east-1','max_rss_kib':999999}))
    output=tmp_path/'result.json'; env=os.environ.copy(); env.pop('PYTHONPATH',None)
    env.update({'AWS_EC2_METADATA_SERVICE_ENDPOINT':'http://127.0.0.1:1','ALPHA_ATLAS_V4_AWS_ACCESS_KEY_ID':'MUST_NOT_READ','ALPHA_ATLAS_V4_AWS_SECRET_ACCESS_KEY':'MUST_NOT_READ','ALPHA_ATLAS_V4_MASSIVE_API_KEY':'MUST_NOT_READ'})
    run=subprocess.run([sys.executable,'-m','scripts.run_alpha_atlas_v4_stage_b_operational','--authorization',str(auth),'--approved-authorization-sha256','0'*64,'--sector-evidence',str(sector),'--config',str(config),'--output',str(output)],cwd=REPO,env=env,text=True,capture_output=True,timeout=30)
    assert run.returncode==2
    result=json.loads(output.read_text()); assert result['error_code']=='EXECUTION_NOT_AUTHORIZED' and result['stage_b_executed'] is False
    assert 'MUST_NOT_READ' not in output.read_text()
