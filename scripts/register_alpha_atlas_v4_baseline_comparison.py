#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from moneybot.services import alpha_atlas_v4_baseline_registration as registration

def main()->int:
 p=argparse.ArgumentParser();
 for name in ("canonical","plan","manifest","capture","source-provenance","output-dir"): p.add_argument("--"+name,type=Path,required=True)
 for role, value in registration.EXPECTED.items(): p.add_argument(f"--expected-{role}-hash", default=value)
 a=p.parse_args(); a.output_dir.mkdir(parents=True,exist_ok=True)
 registration.EXPECTED={role:getattr(a,f"expected_{role}_hash") for role in registration.EXPECTED}
 try:
  source=json.loads(a.source_provenance.read_text()); report=registration.register(a.canonical,a.plan,a.manifest,a.capture,source)
 except Exception as exc:
  code=exc.code if isinstance(exc,registration.RegistrationError) else "UNEXPECTED_REGISTRATION_ERROR"
  report={"schema_version":"alpha-atlas-v4-development-baseline-registration.v1","status":"EXECUTION_BLOCKED","performance_comparison_executed":False,"reason_codes":[code],"details":getattr(exc,"details",{}),"safety":{"research_only":True,"training":False,"holdout_evaluated":False,"provider_access":False,"automatic_promotion":False,"live_routing":False}}
 (a.output_dir/'registration.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
 integrity=report.get('split_integrity',{"status":"BLOCKED","reason_codes":report.get('reason_codes',[])})
 materiality=report.get('materiality',{"status":"UNKNOWN","reason_codes":report.get('reason_codes',[])})
 (a.output_dir/'split_integrity.json').write_text(json.dumps(integrity,indent=2,sort_keys=True)+'\n')
 (a.output_dir/'materiality.json').write_text(json.dumps(materiality,indent=2,sort_keys=True)+'\n')
 (a.output_dir/'input_manifest.json').write_text(json.dumps({"inputs":report.get('inputs',[]),"source_provenance":json.loads(a.source_provenance.read_text())},indent=2,sort_keys=True)+'\n')
 md=f"# V4 development baseline registration\n\n- Execution: `{'COMPLETE' if report.get('status','').startswith('REGISTERED') else 'BLOCKED'}`\n- Evidence readiness: `{report.get('status')}`\n- Performance comparison executed: `false`\n- Registration SHA-256: `{report.get('registration_sha256')}`\n- Fold count: `{(report.get('development_scope') or {}).get('fold_count')}`\n- Interval: `{(report.get('development_scope') or {}).get('start')}` through `{(report.get('development_scope') or {}).get('end')}`\n- Integrity: `{integrity.get('status')}`; reasons: `{integrity.get('reason_codes',[])}`\n- Materiality: `{materiality.get('status')}`; affected rows/occurrences: `{materiality.get('unique_affected_rows')}` / `{materiality.get('dependency_occurrences')}`\n\nNo performance result, fitting, tuning, provider request, holdout content, promotion, or routing change was produced.\n"
 (a.output_dir/'registration.md').write_text(md); (a.output_dir/'materiality.md').write_text(md.replace('baseline registration','materiality review'))
 paths=sorted(a.output_dir.glob('*.json'))+sorted(a.output_dir.glob('*.md'))
 (a.output_dir/'SHA256SUMS').write_text(''.join(f"{hashlib.sha256(x.read_bytes()).hexdigest()}  {x.name}\n" for x in paths))
 print(json.dumps({"execution":"COMPLETE" if report.get('status','').startswith('REGISTERED') else "BLOCKED","evidence_readiness":report.get('status'),"registration_sha256":report.get('registration_sha256')}))
 return 0 if report.get('status','').startswith('REGISTERED') else 2
if __name__=='__main__': raise SystemExit(main())
