"""Read-only inspection of the preserved Stage B initialization incident."""
from __future__ import annotations
import argparse,json
from pathlib import Path
from moneybot.services.alpha_atlas_v4_prospective_snapshot import CaptureError
from moneybot.services.alpha_atlas_v4_stage_b_recovery import inspect_runtime_evidence

def main()->int:
    p=argparse.ArgumentParser(); p.add_argument('--root',type=Path,required=True); p.add_argument('--authorization',type=Path,required=True); p.add_argument('--failure-report',type=Path,required=True); p.add_argument('--output',type=Path,required=True); a=p.parse_args()
    if a.output.exists(): raise CaptureError('RECOVERY_OUTPUT_EXISTS',str(a.output))
    result=inspect_runtime_evidence(a.root,a.authorization,a.failure_report)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x') as f: f.write(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'status':result['status'],'output':str(a.output),'content_sha256':result['content_sha256']},sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
