from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import moneybot.services.alpha_atlas_v4_stage_b as stage_b
from moneybot.services.alpha_atlas_v4_prospective_snapshot import CaptureError, canonical_bytes
from moneybot.services.alpha_atlas_v4_stage_b import (
    LIVE_OWNER_APPROVAL, REGISTERED_BUDGETS, REQUIRED_ROOT, SETUP_PACKAGE_FILE_SHA256,
    StageBRunner, authorization_hashes,
)
from scripts.prepare_alpha_atlas_v4_stage_b_post_deploy_authorization import prepare

REPO=Path(__file__).resolve().parents[1]
FIXTURE_NAME='alpha_atlas_v4_stage_b_operational_verification_manifest.v1.json'
FIXTURE_PATH=REPO/'docs/reports'/FIXTURE_NAME
PROXY_PATH=REPO/'docs/reports/alpha_atlas_v4_stage_b_operational_sector_proxy_binding.accepted.v1.json'
CONFIG=json.loads((REPO/'docs/reports/alpha_atlas_v4_stage_b_runtime_config.owner-bound.v1.json').read_text())
UTC=timezone.utc
VALID_FROM='2026-10-08T14:00:00Z'; VALID_UNTIL='2026-10-09T22:00:00Z'
NOW=datetime(2026,10,8,18,tzinfo=UTC)


def head() -> str:
    return subprocess.run(['git','rev-parse','HEAD'],cwd=REPO,check=True,text=True,capture_output=True).stdout.strip()


def draft(**kwargs):
    return prepare(REPO,head(),execution_valid_from=kwargs.pop('valid_from',VALID_FROM),
                   execution_valid_until=kwargs.pop('valid_until',VALID_UNTIL),
                   now=kwargs.pop('now',datetime(2026,10,7,12,tzinfo=UTC)),**kwargs)


def approve(value: dict) -> tuple[dict,str]:
    out=dict(value); out.pop('content_sha256',None)
    out.update(status='APPROVED',execution_gate_usable=True,owner_approval=LIVE_OWNER_APPROVAL,
               stage_b_execution='AUTHORIZED_NOT_EXECUTED')
    out['content_sha256']=hashlib.sha256(canonical_bytes(out)).hexdigest()
    return out,authorization_hashes(out)['external_complete_canonical_sha256']


def live_runner(pin: str, monkeypatch, now: datetime=NOW) -> StageBRunner:
    fixture=json.loads(FIXTURE_PATH.read_text())
    monkeypatch.setenv('MONEYBOT_PERSISTENT_DATA_DIR',REQUIRED_ROOT)
    monkeypatch.setattr(stage_b,'storage_preflight',lambda root:{'root':str(root),'free_bytes':99_999_999,'required_bytes':stage_b.COMBINED_CAP})
    return StageBRunner(REPO,Path(REQUIRED_ROOT),offline=False,approved_authorization_sha256=pin,
                        operational_config_sha256=CONFIG['content_sha256'],fixture_name=FIXTURE_NAME,
                        fixture_file_sha256=hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
                        fixture_content_sha256=fixture['content_sha256'],session=date(2026,10,6),
                        runtime_guard=lambda phase:{'phase':phase},now=lambda:now)


def test_real_binder_output_to_actual_live_preflight_regression(monkeypatch):
    prepared=draft(); fixture=json.loads(FIXTURE_PATH.read_text()); proxy=json.loads(PROXY_PATH.read_text())
    assert prepared['setup_package_sha256']==SETUP_PACKAGE_FILE_SHA256
    assert prepared['status']=='PREPARED_FOR_EXECUTION_APPROVAL_NOT_APPROVED'
    assert prepared['execution_gate_usable'] is False and prepared['owner_approval']=='NOT_GIVEN_FOR_EXECUTION'
    prepared_pin=authorization_hashes(prepared)['external_complete_canonical_sha256']
    with pytest.raises(CaptureError,match='EXECUTION_NOT_AUTHORIZED'):
        live_runner(prepared_pin,monkeypatch).preflight(prepared,proxy)
    approved,pin=approve(prepared)
    plan,preflight=live_runner(pin,monkeypatch).preflight(approved,proxy)
    assert plan['execution_purpose']=='OPERATIONAL_VERIFICATION_ONLY'
    assert plan['window_sessions']==[] and preflight['execution_purpose']=='OPERATIONAL_VERIFICATION_ONLY'


@pytest.mark.parametrize(('field','value','code'),[
    ('setup_package_sha256','0'*64,'AUTHORIZATION_BINDING_MISMATCH'),
    ('fixture_sha256','0'*64,'AUTHORIZATION_BINDING_MISMATCH'),
    ('operational_config_sha256','0'*64,'AUTHORIZATION_OPERATIONAL_BINDING_MISMATCH'),
    ('sector_context_sha256','0'*64,'AUTHORIZATION_OPERATIONAL_BINDING_MISMATCH'),
])
def test_altered_bindings_fail_actual_preflight(monkeypatch,field,value,code):
    prepared=draft(); prepared[field]=value; prepared.pop('content_sha256'); prepared['content_sha256']=hashlib.sha256(canonical_bytes(prepared)).hexdigest()
    approved,pin=approve(prepared)
    with pytest.raises(CaptureError,match=code): live_runner(pin,monkeypatch).preflight(approved,json.loads(PROXY_PATH.read_text()))


def test_missing_setup_and_altered_source_hash_fail_actual_preflight(monkeypatch):
    proxy=json.loads(PROXY_PATH.read_text())
    prepared=draft(); prepared.pop('setup_package_sha256'); prepared.pop('content_sha256'); prepared['content_sha256']=hashlib.sha256(canonical_bytes(prepared)).hexdigest()
    approved,pin=approve(prepared)
    with pytest.raises(CaptureError,match='AUTHORIZATION_BINDING_MISMATCH'):
        live_runner(pin,monkeypatch).preflight(approved,proxy)
    prepared=draft(); approved,pin=approve(prepared)
    approved['deployed_revision']['source_file_sha256s']['moneybot/services/alpha_atlas_v4_stage_b.py']='0'*64
    approved.pop('content_sha256'); approved['content_sha256']=hashlib.sha256(canonical_bytes(approved)).hexdigest(); pin=authorization_hashes(approved)['external_complete_canonical_sha256']
    with pytest.raises(CaptureError,match='DEPLOYED_REVISION_MISMATCH'):
        live_runner(pin,monkeypatch).preflight(approved,proxy)


def test_approval_state_internal_external_and_deployed_revision_checks(monkeypatch):
    prepared=draft(); proxy=json.loads(PROXY_PATH.read_text())
    for updates in ({'execution_gate_usable':False},{'owner_approval':'NOT_GIVEN_FOR_EXECUTION'},{'stage_b_execution':'AUTHORIZED_AND_EXECUTED'},{'budgets':{}},{'hard_runtime_minutes':54}):
        approved,pin=approve(prepared); approved.update(updates); approved.pop('content_sha256'); approved['content_sha256']=hashlib.sha256(canonical_bytes(approved)).hexdigest(); pin=authorization_hashes(approved)['external_complete_canonical_sha256']
        with pytest.raises(CaptureError,match='EXECUTION_APPROVAL_STATE_INVALID|AUTHORIZATION_BUDGET_MISMATCH'):
            live_runner(pin,monkeypatch).preflight(approved,proxy)
    approved,pin=approve(prepared); approved['deployed_revision']={**approved['deployed_revision'],'git_commit':'0'*40}; approved.pop('content_sha256'); approved['content_sha256']=hashlib.sha256(canonical_bytes(approved)).hexdigest(); pin=authorization_hashes(approved)['external_complete_canonical_sha256']
    with pytest.raises(CaptureError,match='DEPLOYED_REVISION_MISMATCH'): live_runner(pin,monkeypatch).preflight(approved,proxy)
    approved,pin=approve(prepared); approved['content_sha256']='0'*64
    with pytest.raises(CaptureError,match='AUTHORIZATION_HASH_MISMATCH'): live_runner(pin,monkeypatch).preflight(approved,proxy)
    approved,pin=approve(prepared)
    with pytest.raises(CaptureError,match='APPROVED_AUTHORIZATION_HASH_REQUIRED'): live_runner('0'*64,monkeypatch).preflight(approved,proxy)


def test_interval_validation_and_exact_expiry(monkeypatch):
    for start,end,now,code in [
        ('2026-10-08T14:00:00','2026-10-09T22:00:00Z',datetime(2026,10,7,tzinfo=UTC),'PREPARATION_INTERVAL_INVALID'),
        (VALID_UNTIL,VALID_FROM,datetime(2026,10,7,tzinfo=UTC),'PREPARATION_INTERVAL_INVALID'),
        (VALID_FROM,VALID_FROM,datetime(2026,10,7,tzinfo=UTC),'PREPARATION_INTERVAL_INVALID'),
        (VALID_FROM,VALID_UNTIL,datetime(2026,10,10,tzinfo=UTC),'PREPARATION_INTERVAL_EXPIRED'),
    ]:
        with pytest.raises(CaptureError,match=code): draft(valid_from=start,valid_until=end,now=now)
    prepared=draft(); approved,pin=approve(prepared); proxy=json.loads(PROXY_PATH.read_text())
    with pytest.raises(CaptureError,match='EXECUTION_VALIDITY_INVALID'):
        live_runner(pin,monkeypatch,datetime(2026,10,8,13,59,tzinfo=UTC)).preflight(approved,proxy)
    with pytest.raises(CaptureError,match='EXECUTION_VALIDITY_INVALID'):
        live_runner(pin,monkeypatch,datetime(2026,10,9,22,0,tzinfo=UTC)).preflight(approved,proxy)


def test_hash_semantics_formatting_and_no_overwrite(tmp_path):
    value=draft(); pretty=(json.dumps(value,indent=2,sort_keys=True)+'\n').encode(); compact=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
    a=authorization_hashes(value,file_bytes=pretty); b=authorization_hashes(json.loads(compact),file_bytes=compact)
    assert a['internal_content_sha256']==b['internal_content_sha256']==value['content_sha256']
    assert a['external_complete_canonical_sha256']==b['external_complete_canonical_sha256']
    assert a['file_byte_sha256']!=b['file_byte_sha256']
    output=tmp_path/'prepared.v2.json'; output.write_text('existing')
    env=os.environ.copy(); env.pop('PYTHONPATH',None)
    run=subprocess.run([sys.executable,'-m','scripts.prepare_alpha_atlas_v4_stage_b_post_deploy_authorization','--deployed-commit',head(),'--execution-valid-from',VALID_FROM,'--execution-valid-until',VALID_UNTIL,'--output',str(output)],cwd=REPO,env=env,text=True,capture_output=True)
    assert run.returncode!=0 and output.read_text()=='existing' and 'AUTHORIZATION_OUTPUT_EXISTS' in run.stderr


def test_cli_and_inspector_report_three_hashes(tmp_path):
    output=tmp_path/'prepared.v2.json'; env=os.environ.copy(); env.pop('PYTHONPATH',None)
    run=subprocess.run([sys.executable,'-m','scripts.prepare_alpha_atlas_v4_stage_b_post_deploy_authorization','--deployed-commit',head(),'--execution-valid-from',VALID_FROM,'--execution-valid-until',VALID_UNTIL,'--output',str(output)],cwd=REPO,env=env,text=True,capture_output=True,check=True)
    reported=json.loads(run.stdout); value=json.loads(output.read_text())
    assert reported['internal_content_sha256']==value['content_sha256'] and reported['internal_content_valid'] is True
    inspect=subprocess.run([sys.executable,'-m','scripts.inspect_alpha_atlas_v4_stage_b_authorization','--authorization',str(output)],cwd=REPO,env=env,text=True,capture_output=True,check=True)
    inspected=json.loads(inspect.stdout)
    assert inspected['external_complete_canonical_sha256']==reported['external_complete_canonical_sha256']
    assert inspected['file_byte_sha256']==reported['file_byte_sha256']
    assert inspected['inspection_only'] is True and inspected['approved_or_modified'] is False
    missing=subprocess.run([sys.executable,'-m','scripts.prepare_alpha_atlas_v4_stage_b_post_deploy_authorization','--deployed-commit',head(),'--output',str(tmp_path/'missing.json')],cwd=REPO,env=env,text=True,capture_output=True)
    assert missing.returncode==2 and '--execution-valid-from' in missing.stderr and '--execution-valid-until' in missing.stderr
