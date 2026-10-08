"""Offline-only replay of saved Stage B evidence; no transport or SDK imports."""
from __future__ import annotations
import argparse,json
from pathlib import Path
from moneybot.services.alpha_atlas_v4_prospective_snapshot import CaptureError,sha256_bytes
from moneybot.services.alpha_atlas_v4_stage_b_replay import replay_saved_evidence

def main()->int:
    p=argparse.ArgumentParser();p.add_argument('--saved-evidence-root',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args()
    if a.output_dir.exists():raise CaptureError('REPLAY_OUTPUT_EXISTS',str(a.output_dir))
    result=replay_saved_evidence(Path(__file__).resolve().parents[1],a.saved_evidence_root)
    a.output_dir.mkdir(parents=True)
    (a.output_dir/'report.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    (a.output_dir/'report.md').write_text('# Alpha Atlas V4 saved-evidence replay\n\n'+f"- Status: **{result['status']}**\n- Purpose: **OFFLINE_SAVED_EVIDENCE_REPLAY**\n- Live provider/AWS requests: **0 / 0**\n- Original live result: **unchanged failed result**\n- Premarket/prospective eligibility: **NOT_TESTED / NOT_TESTED**\n")
    (a.output_dir/'run.log').write_text('OFFLINE_SAVED_EVIDENCE_REPLAY; immutable inputs; no credentials; no network; no ledger reservations\n')
    names=('report.json','report.md','run.log');(a.output_dir/'SHA256SUMS').write_text('\n'.join(f"{sha256_bytes((a.output_dir/n).read_bytes())}  {n}" for n in names)+'\n')
    print(json.dumps({'status':result['status'],'output_dir':str(a.output_dir),'live_provider_requests':0,'live_aws_requests':0},sort_keys=True));return 0 if result['status']=='PASS' else 2
if __name__=='__main__':raise SystemExit(main())
