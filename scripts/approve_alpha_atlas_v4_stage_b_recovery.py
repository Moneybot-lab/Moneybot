"""Finalize the owner's single bounded continuation approval without executing it."""
from __future__ import annotations
import argparse,json,subprocess
from datetime import datetime,timezone
from pathlib import Path
from typing import Any,Mapping
from moneybot.services.alpha_atlas_v4_prospective_snapshot import CaptureError,canonical_bytes,sha256_bytes
from moneybot.services.alpha_atlas_v4_stage_b import REGISTERED_BUDGETS,authorization_hashes,parse_utc_timestamp
from moneybot.services.alpha_atlas_v4_stage_b_recovery import (
    OWNER_APPROVED_CONTINUATION_CUTOFF,RECOVERY_APPROVAL_SCOPE,RECOVERY_OWNER_APPROVAL,
)
UTC=timezone.utc

def approve_recovery(repo:Path,prepared_bytes:bytes,*,expected_prepared_pin:str,now:datetime|None=None)->dict[str,Any]:
    try: value=json.loads(prepared_bytes)
    except (UnicodeDecodeError,json.JSONDecodeError) as exc: raise CaptureError('RECOVERY_AUTHORIZATION_NOT_JSON') from exc
    if not isinstance(value,dict): raise CaptureError('RECOVERY_AUTHORIZATION_NOT_OBJECT')
    hashes=authorization_hashes(value)
    if not hashes['internal_content_valid'] or hashes['external_complete_canonical_sha256']!=expected_prepared_pin:
        raise CaptureError('RECOVERY_PREPARED_PIN_MISMATCH')
    exact={'status':'PREPARED_FOR_CONTINUATION_APPROVAL_NOT_APPROVED','execution_gate_usable':False,
           'owner_approval':'NOT_GIVEN_FOR_CONTINUATION','stage_b_execution':'INITIALIZATION_FAILED_CONTINUATION_NOT_AUTHORIZED',
           'execution_purpose':'OPERATIONAL_VERIFICATION_ONLY','consumed_s3_operations':6,'massive_attempts_consumed':0,
           'maximum_total_runtime_minutes':55,'original_elapsed_seconds':'UNKNOWN_LEGACY_NOT_RECORDED',
           'owner_accepts_unknown_legacy_elapsed':False,'single_continuation':True,'independent_second_run':False}
    if any(value.get(k)!=v for k,v in exact.items()) or value.get('budgets')!=REGISTERED_BUDGETS:
        raise CaptureError('RECOVERY_PREPARED_SCOPE_MISMATCH')
    start=parse_utc_timestamp(value.get('execution_valid_from')); end=parse_utc_timestamp(value.get('execution_valid_until'))
    instant=(now or datetime.now(UTC)).astimezone(UTC)
    if start>=end or end<=instant or end>OWNER_APPROVED_CONTINUATION_CUTOFF:
        raise CaptureError('RECOVERY_INTERVAL_OUTSIDE_OWNER_APPROVAL')
    revision=value.get('repaired_revision',{}); head=subprocess.run(['git','rev-parse','HEAD'],cwd=repo,check=True,text=True,capture_output=True).stdout.strip()
    if revision.get('git_commit')!=head: raise CaptureError('RECOVERY_REVISION_MISMATCH')
    wanted_names={'moneybot/services/alpha_atlas_v4_stage_b.py','moneybot/services/alpha_atlas_v4_stage_b_recovery.py','scripts/run_alpha_atlas_v4_stage_b_operational.py'}
    if set(revision.get('source_file_sha256s',{}))!=wanted_names: raise CaptureError('RECOVERY_REVISION_MISMATCH')
    for name,wanted in revision['source_file_sha256s'].items():
        path=(repo/name).resolve()
        if not path.is_relative_to(repo.resolve()) or not path.is_file() or sha256_bytes(path.read_bytes())!=wanted:
            raise CaptureError('RECOVERY_REVISION_MISMATCH',name)
    approved=dict(value); approved.pop('content_sha256',None)
    approved.update(status='APPROVED',execution_gate_usable=True,owner_approval=RECOVERY_OWNER_APPROVAL,
                    stage_b_execution='AUTHORIZED_CONTINUATION_NOT_EXECUTED',owner_accepts_unknown_legacy_elapsed=True,
                    approval_scope=dict(RECOVERY_APPROVAL_SCOPE),owner_approved_until=OWNER_APPROVED_CONTINUATION_CUTOFF.isoformat(),
                    approved_from_prepared_external_complete_canonical_sha256=expected_prepared_pin)
    approved['content_sha256']=sha256_bytes(canonical_bytes(approved)); return approved

def main()->int:
    p=argparse.ArgumentParser(); p.add_argument('--prepared-recovery',type=Path,required=True); p.add_argument('--prepared-recovery-sha256',required=True); p.add_argument('--output',type=Path,required=True); a=p.parse_args()
    if a.output.exists(): raise CaptureError('RECOVERY_OUTPUT_EXISTS',str(a.output))
    repo=Path(__file__).resolve().parents[1]
    approved=approve_recovery(repo,a.prepared_recovery.read_bytes(),expected_prepared_pin=a.prepared_recovery_sha256)
    raw=(json.dumps(approved,indent=2,sort_keys=True)+'\n').encode(); a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('xb') as handle: handle.write(raw)
    print(json.dumps({'status':approved['status'],'stage_b_execution':approved['stage_b_execution'],'output':str(a.output),**authorization_hashes(approved,file_bytes=raw)},sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
