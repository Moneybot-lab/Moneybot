from __future__ import annotations
import hashlib,json,os,subprocess,sys
from datetime import date,datetime,timezone
from pathlib import Path
import pytest
import moneybot.services.alpha_atlas_v4_stage_b as stage_b
from moneybot.services.alpha_atlas_v4_prospective_snapshot import CaptureError,ImmutableStore,canonical_bytes
from moneybot.services.alpha_atlas_v4_stage_b import *
from moneybot.services.alpha_atlas_v4_stage_b_recovery import *
from scripts.approve_alpha_atlas_v4_stage_b_authorization import approve
from scripts.prepare_alpha_atlas_v4_stage_b_post_deploy_authorization import prepare
from scripts.approve_alpha_atlas_v4_stage_b_recovery import approve_recovery
from scripts.run_alpha_atlas_v4_stage_b_offline import OfflineS3,OfflineTransport

REPO=Path(__file__).resolve().parents[1]; UTC=timezone.utc
FIXTURE_NAME='alpha_atlas_v4_stage_b_operational_verification_manifest.v1.json'
FIXTURE_PATH=REPO/'docs/reports'/FIXTURE_NAME
PROXY=json.loads((REPO/'docs/reports/alpha_atlas_v4_stage_b_operational_sector_proxy_binding.accepted.v1.json').read_text())
CONFIG=json.loads((REPO/'docs/reports/alpha_atlas_v4_stage_b_runtime_config.owner-bound.v1.json').read_text())
LEGACY_NOW=datetime(2026,10,7,3,tzinfo=UTC); RECOVERY_NOW=datetime(2026,10,8,12,tzinfo=UTC)

def head(): return subprocess.run(['git','rev-parse','HEAD'],cwd=REPO,check=True,text=True,capture_output=True).stdout.strip()

def approved_original():
    draft=prepare(REPO,head(),execution_valid_from='2026-10-07T04:00:00Z',execution_valid_until='2026-10-08T04:00:00Z',now=LEGACY_NOW)
    pin=authorization_hashes(draft)['external_complete_canonical_sha256']
    auth=approve(REPO,canonical_bytes(draft),expected_external_sha256=pin,now=LEGACY_NOW)
    raw=(json.dumps(auth,indent=2,sort_keys=True)+'\n').encode()
    return auth,raw,authorization_hashes(auth)['external_complete_canonical_sha256']

def incident(tmp_path):
    root=tmp_path/'runtime'; (root/'authorization').mkdir(parents=True); (root/'primary/run').mkdir(parents=True)
    auth,raw,pin=approved_original(); auth_path=root/'authorization/approved.json'; auth_path.write_bytes(raw)
    claim={'schema_version':'alpha-atlas-v4-stage-b-execution-claim.v1','authorization_sha256':pin,'execution_purpose':'OPERATIONAL_VERIFICATION_ONLY','claimed_at':'2026-10-07T05:00:00+00:00'}
    (root/'primary/run/execution-claim.json').write_bytes(canonical_bytes(claim))
    failure={'status':'FAIL','error_code':'PROVIDER_CREDENTIAL_MISSING','detail':'PROVIDER_CREDENTIAL_MISSING','stage_b_executed':False}
    fail=root/'stage-b-operational-2026-10-06.result.v1.json'; fail.write_text(json.dumps(failure,sort_keys=True)+'\n')
    ledger=S3OperationLedger(ImmutableStore(root/'s3-operation-ledger'))
    for op in EXPECTED_S3_CONFIGURATION_OPERATIONS:
        oid=ledger.reserve(op); ledger.event(oid,'TRANSMITTING'); ledger.event(oid,'SUCCEEDED')
    return root,auth_path,fail,auth,raw,pin

def test_credentials_fail_before_claim_factory_or_ledger(tmp_path,monkeypatch):
    auth,_,pin=approved_original(); fixture=json.loads(FIXTURE_PATH.read_text()); root=tmp_path/'root'
    monkeypatch.setattr(stage_b,'REQUIRED_ROOT',str(root)); monkeypatch.setenv('MONEYBOT_PERSISTENT_DATA_DIR',str(root))
    monkeypatch.setattr(stage_b,'storage_preflight',lambda root:{'root':str(root),'free_bytes':99999999})
    made=[]
    runner=StageBRunner(REPO,root,offline=False,approved_authorization_sha256=pin,operational_config_sha256=CONFIG['content_sha256'],fixture_name=FIXTURE_NAME,fixture_file_sha256=hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),fixture_content_sha256=fixture['content_sha256'],session=date(2026,10,6),now=lambda:datetime(2026,10,7,5,tzinfo=UTC))
    with pytest.raises(CaptureError,match='PROVIDER_CREDENTIAL_MISSING'):
        runner.execute(auth,PROXY,lambda *a:made.append('transport'),lambda *a:made.append('s3'),lambda:validate_explicit_credentials({AWS_ACCESS_KEY_ENV:'x',AWS_SECRET_KEY_ENV:'y'}))
    assert made==[] and not (root/'primary/run/execution-claim.json').exists() and not (root/'s3-operation-ledger/s3_operations.jsonl').exists()
    with pytest.raises(CaptureError,match='AWS_CREDENTIAL_MISSING'):
        runner.execute(auth,PROXY,lambda *a:made.append('transport'),lambda *a:made.append('s3'),lambda:validate_explicit_credentials({MASSIVE_KEY_ENV:'x'}))
    assert made==[] and not (root/'primary/run/execution-claim.json').exists()

def test_exact_legacy_incident_inspection_and_corruption_blocks(tmp_path):
    root,auth_path,fail,_,raw,pin=incident(tmp_path)
    report=inspect_runtime_evidence(root,auth_path,fail,authorization_pin=pin,authorization_file_sha256=hashlib.sha256(raw).hexdigest(),now=RECOVERY_NOW)
    assert report['status']=='ELIGIBLE_EVIDENCE_EXPIRED_INTERVAL' and report['s3']['record_count']==18
    assert report['s3']['operation_count']==6 and report['massive']['reservation_count']==0
    ledger=root/'s3-operation-ledger/s3_operations.jsonl'; lines=ledger.read_text().splitlines(); row=json.loads(lines[-1]); row['event']='FAILED'; lines[-1]=json.dumps(row); ledger.write_text('\n'.join(lines)+'\n')
    with pytest.raises(CaptureError,match='S3_LEDGER_CORRUPT'):
        inspect_runtime_evidence(root,auth_path,fail,authorization_pin=pin,authorization_file_sha256=hashlib.sha256(raw).hexdigest(),now=RECOVERY_NOW)

def test_prior_massive_or_continuation_blocks_and_claim_is_one_use(tmp_path):
    root,auth_path,fail,_,raw,pin=incident(tmp_path); primary=ImmutableStore(root/'primary')
    ledger=AttemptLedger(primary,synthetic=False); ledger.reserve('operational_verification','history:AAPL',RECOVERY_NOW)
    with pytest.raises(CaptureError,match='MASSIVE_ACTIVITY'):
        inspect_runtime_evidence(root,auth_path,fail,authorization_pin=pin,authorization_file_sha256=hashlib.sha256(raw).hexdigest(),now=RECOVERY_NOW)
    (root/'primary/acquisition_attempts.jsonl').unlink()
    report=inspect_runtime_evidence(root,auth_path,fail,authorization_pin=pin,authorization_file_sha256=hashlib.sha256(raw).hexdigest(),now=RECOVERY_NOW)
    recovery=prepare_recovery_binding(REPO,report,valid_from='2026-10-08T11:00:00Z',valid_until='2026-10-09T03:00:00Z',now=RECOVERY_NOW)
    recovery.pop('content_sha256'); recovery.update(status='APPROVED',execution_gate_usable=True,owner_approval=RECOVERY_OWNER_APPROVAL,stage_b_execution='AUTHORIZED_CONTINUATION_NOT_EXECUTED',owner_accepts_unknown_legacy_elapsed=True,approval_scope=dict(RECOVERY_APPROVAL_SCOPE),owner_approved_until=OWNER_APPROVED_CONTINUATION_CUTOFF.isoformat()); recovery['content_sha256']=hashlib.sha256(canonical_bytes(recovery)).hexdigest()
    publish_continuation_claim(primary,recovery,now=RECOVERY_NOW)
    with pytest.raises(CaptureError,match='CONTINUATION_ALREADY_CLAIMED'): publish_continuation_claim(primary,recovery,now=RECOVERY_NOW)

def test_end_to_end_preparation_and_guarded_synthetic_continuation(tmp_path,monkeypatch):
    root,auth_path,fail,auth,raw,pin=incident(tmp_path)
    report=inspect_runtime_evidence(root,auth_path,fail,authorization_pin=pin,authorization_file_sha256=hashlib.sha256(raw).hexdigest(),now=RECOVERY_NOW)
    recovery=prepare_recovery_binding(REPO,report,valid_from='2026-10-08T11:00:00Z',valid_until='2026-10-09T03:00:00Z',now=RECOVERY_NOW)
    recovery.pop('content_sha256'); recovery.update(status='APPROVED',execution_gate_usable=True,owner_approval=RECOVERY_OWNER_APPROVAL,stage_b_execution='AUTHORIZED_CONTINUATION_NOT_EXECUTED',owner_accepts_unknown_legacy_elapsed=True,approval_scope=dict(RECOVERY_APPROVAL_SCOPE),owner_approved_until=OWNER_APPROVED_CONTINUATION_CUTOFF.isoformat()); recovery['content_sha256']=hashlib.sha256(canonical_bytes(recovery)).hexdigest()
    recovery_pin=authorization_hashes(recovery)['external_complete_canonical_sha256']; fixture=json.loads(FIXTURE_PATH.read_text())
    monkeypatch.setattr(stage_b,'REQUIRED_ROOT',str(root)); monkeypatch.setenv('MONEYBOT_PERSISTENT_DATA_DIR',str(root)); monkeypatch.setattr(stage_b,'storage_preflight',lambda root:{'root':str(root),'free_bytes':99999999})
    runner=StageBRunner(REPO,root,offline=False,approved_authorization_sha256=pin,operational_config_sha256=CONFIG['content_sha256'],fixture_name=FIXTURE_NAME,fixture_file_sha256=hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),fixture_content_sha256=fixture['content_sha256'],session=date(2026,10,6),runtime_guard=lambda p:{'phase':p},now=lambda:RECOVERY_NOW)
    def backup(primary,credentials): return S3EvidenceBackup(OfflineS3(),BOUND_BUCKET,BOUND_PREFIX,S3OperationLedger(ImmutableStore(root/'s3-operation-ledger')),expected_owner='123456789012',now=lambda:RECOVERY_NOW)
    result=runner.execute_continuation(auth,recovery,recovery_pin,PROXY,
        lambda credentials:OfflineTransport(sessions=fixture['history_window']['ordered_sessions']),backup,
        lambda:{MASSIVE_KEY_ENV:'secret',AWS_ACCESS_KEY_ENV:'id',AWS_SECRET_KEY_ENV:'secret'})
    assert result['status']=='PASS' and result['continuation'] is True and result['s3_operations']>6
    assert (root/'primary/run/execution-claim.json').exists() and (root/'primary/run/continuation-claim.json').exists()

def test_recovery_wrong_pin_revision_interval_and_no_overwrite(tmp_path):
    root,auth_path,fail,_,raw,pin=incident(tmp_path); report=inspect_runtime_evidence(root,auth_path,fail,authorization_pin=pin,authorization_file_sha256=hashlib.sha256(raw).hexdigest(),now=RECOVERY_NOW)
    with pytest.raises(CaptureError,match='RECOVERY_INTERVAL_INVALID'): prepare_recovery_binding(REPO,report,valid_from='2026-10-09T00:00:00Z',valid_until='2026-10-08T00:00:00Z',now=RECOVERY_NOW)
    recovery=prepare_recovery_binding(REPO,report,valid_from='2026-10-08T11:00:00Z',valid_until='2026-10-09T03:00:00Z',now=RECOVERY_NOW)
    recovery.pop('content_sha256'); recovery.update(status='APPROVED',execution_gate_usable=True,owner_approval=RECOVERY_OWNER_APPROVAL,stage_b_execution='AUTHORIZED_CONTINUATION_NOT_EXECUTED',owner_accepts_unknown_legacy_elapsed=True,approval_scope=dict(RECOVERY_APPROVAL_SCOPE),owner_approved_until=OWNER_APPROVED_CONTINUATION_CUTOFF.isoformat()); recovery['content_sha256']=hashlib.sha256(canonical_bytes(recovery)).hexdigest()
    with pytest.raises(CaptureError,match='HASH_MISMATCH'): validate_recovery_authorization(recovery,expected_pin='0'*64,now=RECOVERY_NOW,repo=REPO)

def test_initialization_failure_reports_completed_s3_without_masking(tmp_path):
    runner=StageBRunner(REPO,tmp_path,offline=True,now=lambda:datetime(2026,10,5,10,tzinfo=UTC))
    def missing_transport(): raise CaptureError('PROVIDER_CREDENTIAL_MISSING')
    with pytest.raises(StageBPhaseFailure) as caught:
        runner.execute(synthetic_authorization(),effective_sector_fixture(),missing_transport,
                       lambda primary:S3EvidenceBackup(OfflineS3(),BOUND_BUCKET,BOUND_PREFIX,
                           S3OperationLedger(ImmutableStore(tmp_path/'s3-operation-ledger')),
                           expected_owner='123456789012',now=lambda:RECOVERY_NOW))
    report=caught.value.report
    assert report['error_code']=='PROVIDER_CREDENTIAL_MISSING'
    assert report['last_completed_phase']=='S3_CONFIGURATION_VERIFIED'
    assert report['failing_phase']=='MASSIVE_TRANSPORT_INITIALIZATION'
    assert report['execution_claim_exists'] is True and report['acquisition_started'] is False
    assert report['activity']['s3']['RESERVED']==6 and report['activity']['massive']['RESERVED']==0

def test_owner_approved_recovery_finalizer_binds_cutoff_scope_and_real_preflight(tmp_path,monkeypatch):
    root,auth_path,fail,auth,raw,pin=incident(tmp_path)
    report=inspect_runtime_evidence(root,auth_path,fail,authorization_pin=pin,
                                    authorization_file_sha256=hashlib.sha256(raw).hexdigest(),now=RECOVERY_NOW)
    prepared=prepare_recovery_binding(REPO,report,valid_from='2026-10-08T13:00:00Z',
                                      valid_until='2026-10-09T04:00:00Z',now=RECOVERY_NOW)
    prepared_pin=authorization_hashes(prepared)['external_complete_canonical_sha256']
    approved=approve_recovery(REPO,canonical_bytes(prepared),expected_prepared_pin=prepared_pin,now=RECOVERY_NOW)
    prepared_path=tmp_path/'recovery.prepared.json'; prepared_path.write_text(json.dumps(prepared,indent=2,sort_keys=True)+'\n')
    cli_output=tmp_path/'recovery.approved.json'; env=os.environ.copy(); env.pop('PYTHONPATH',None)
    cli=subprocess.run([sys.executable,'-m','scripts.approve_alpha_atlas_v4_stage_b_recovery',
        '--prepared-recovery',str(prepared_path),'--prepared-recovery-sha256',prepared_pin,
        '--output',str(cli_output)],cwd=REPO,env=env,text=True,capture_output=True,check=True)
    assert json.loads(cli.stdout)['status']=='APPROVED' and json.loads(cli_output.read_text())['stage_b_execution']=='AUTHORIZED_CONTINUATION_NOT_EXECUTED'
    approved_pin=authorization_hashes(approved)['external_complete_canonical_sha256']
    assert approved['approval_scope']==RECOVERY_APPROVAL_SCOPE
    assert approved['owner_accepts_unknown_legacy_elapsed'] is True
    assert approved['execution_valid_until']=='2026-10-09T04:00:00+00:00'
    fixture=json.loads(FIXTURE_PATH.read_text())
    monkeypatch.setattr(stage_b,'REQUIRED_ROOT',str(root)); monkeypatch.setenv('MONEYBOT_PERSISTENT_DATA_DIR',str(root))
    monkeypatch.setattr(stage_b,'storage_preflight',lambda root:{'root':str(root),'free_bytes':99999999})
    runner=StageBRunner(REPO,root,offline=False,approved_authorization_sha256=pin,
                        operational_config_sha256=CONFIG['content_sha256'],fixture_name=FIXTURE_NAME,
                        fixture_file_sha256=hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
                        fixture_content_sha256=fixture['content_sha256'],session=date(2026,10,6),
                        runtime_guard=lambda p:{'phase':p},now=lambda:datetime(2026,10,8,14,tzinfo=UTC))
    plan,_=runner.preflight_continuation(auth,approved,approved_pin,PROXY)
    assert plan['hard_runtime_minutes']==55

def test_owner_approval_rejects_later_cutoff_and_wrong_pin(tmp_path):
    root,auth_path,fail,_,raw,pin=incident(tmp_path)
    report=inspect_runtime_evidence(root,auth_path,fail,authorization_pin=pin,
                                    authorization_file_sha256=hashlib.sha256(raw).hexdigest(),now=RECOVERY_NOW)
    prepared=prepare_recovery_binding(REPO,report,valid_from='2026-10-08T13:00:00Z',
                                      valid_until='2026-10-09T05:00:00Z',now=RECOVERY_NOW)
    prepared_pin=authorization_hashes(prepared)['external_complete_canonical_sha256']
    with pytest.raises(CaptureError,match='OUTSIDE_OWNER_APPROVAL'):
        approve_recovery(REPO,canonical_bytes(prepared),expected_prepared_pin=prepared_pin,now=RECOVERY_NOW)
    with pytest.raises(CaptureError,match='PIN_MISMATCH'):
        approve_recovery(REPO,canonical_bytes(prepared),expected_prepared_pin='0'*64,now=RECOVERY_NOW)

def test_recovery_approval_cli_never_overwrites(tmp_path):
    prepared=tmp_path/'prepared.json'; prepared.write_text('{}')
    output=tmp_path/'approved.json'; output.write_text('preserve')
    env=os.environ.copy(); env.pop('PYTHONPATH',None)
    run=subprocess.run([sys.executable,'-m','scripts.approve_alpha_atlas_v4_stage_b_recovery',
        '--prepared-recovery',str(prepared),'--prepared-recovery-sha256','0'*64,
        '--output',str(output)],cwd=REPO,env=env,text=True,capture_output=True)
    assert run.returncode!=0 and 'RECOVERY_OUTPUT_EXISTS' in run.stderr
    assert output.read_text()=='preserve'
