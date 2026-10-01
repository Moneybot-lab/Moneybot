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
    return S3EvidenceBackup(client,'test-only','prefix',S3OperationLedger(ImmutableStore(root/'ops')),now=lambda:NOW)

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
    assert any(x['operation']=='PUT_OBJECT' for x in adapter.ledger.records())

def test_complete_runner_backup_restore_caps_and_zero_live(tmp_path):
    report=runner(tmp_path).execute(synthetic_authorization(),effective_sector_fixture(),lambda:OfflineTransport(),lambda p:backup(tmp_path))
    assert report['status']=='PASS' and report['synthetic_transport_attempts']==5 and report['live_provider_requests']==0
    assert report['quarantined_then_released']==['history:AAPL','history:SPY','history:XLK']
    assert report['backup']['objects']==report['restore']['objects'] and report['backup']['bytes_accounted']<=BACKUP_CAP

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
