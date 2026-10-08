"""Prepare an unapproved continuation binding from read-only incident evidence."""
from __future__ import annotations
import argparse,json
from pathlib import Path
from moneybot.services.alpha_atlas_v4_prospective_snapshot import CaptureError
from moneybot.services.alpha_atlas_v4_stage_b import authorization_hashes
from moneybot.services.alpha_atlas_v4_stage_b_recovery import prepare_recovery_binding

def main()->int:
    p=argparse.ArgumentParser(); p.add_argument('--inspection',type=Path,required=True); p.add_argument('--execution-valid-from',required=True); p.add_argument('--execution-valid-until',required=True); p.add_argument('--output',type=Path,required=True); a=p.parse_args()
    if a.output.exists(): raise CaptureError('RECOVERY_OUTPUT_EXISTS',str(a.output))
    inspection=json.loads(a.inspection.read_text()); repo=Path(__file__).resolve().parents[1]
    result=prepare_recovery_binding(repo,inspection,valid_from=a.execution_valid_from,valid_until=a.execution_valid_until)
    raw=(json.dumps(result,indent=2,sort_keys=True)+'\n').encode(); a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('xb') as f: f.write(raw)
    print(json.dumps({'status':result['status'],'output':str(a.output),**authorization_hashes(result,file_bytes=raw)},sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
