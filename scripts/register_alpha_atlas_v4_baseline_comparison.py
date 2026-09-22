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
 p.add_argument("--prior-registration",type=Path); p.add_argument("--identity-evidence",type=Path); p.add_argument("--cost-policy-evidence",type=Path)
 for role, value in registration.EXPECTED.items(): p.add_argument(f"--expected-{role}-hash", default=value)
 a=p.parse_args(); a.output_dir.mkdir(parents=True,exist_ok=True)
 registration.EXPECTED={role:getattr(a,f"expected_{role}_hash") for role in registration.EXPECTED}
 try:
  source=json.loads(a.source_provenance.read_text()); report=registration.register(a.canonical,a.plan,a.manifest,a.capture,source,prior_registration=a.prior_registration,identity_evidence=a.identity_evidence,cost_policy_evidence=a.cost_policy_evidence)
 except Exception as exc:
  code=exc.code if isinstance(exc,registration.RegistrationError) else "UNEXPECTED_REGISTRATION_ERROR"
  report={"schema_version":"alpha-atlas-v4-development-baseline-registration.v1","status":"EXECUTION_BLOCKED","performance_comparison_executed":False,"reason_codes":[code],"details":getattr(exc,"details",{}),"safety":{"research_only":True,"training":False,"holdout_evaluated":False,"provider_access":False,"automatic_promotion":False,"live_routing":False}}
 (a.output_dir/'registration.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
 integrity=report.get('split_integrity',{"status":"BLOCKED","reason_codes":report.get('reason_codes',[])})
 materiality=report.get('materiality',{"status":"UNKNOWN","reason_codes":report.get('reason_codes',[])})
 (a.output_dir/'split_integrity.json').write_text(json.dumps(integrity,indent=2,sort_keys=True)+'\n')
 (a.output_dir/'materiality.json').write_text(json.dumps(materiality,indent=2,sort_keys=True)+'\n')
 (a.output_dir/'input_manifest.json').write_text(json.dumps({"inputs":report.get('inputs',[]),"source_provenance":json.loads(a.source_provenance.read_text())},indent=2,sort_keys=True)+'\n')
 eligibility=report.get('metric_eligibility') or {}; prices=materiality.get('price_evidence') or {}; returns=materiality.get('return_evidence') or {}; costs=materiality.get('cost_evidence') or {}; valuation=materiality.get('valuation_evidence') or {}
 md=f"# V4 development baseline registration\n\n- Execution: `{'COMPLETE' if report.get('status','').startswith('REGISTERED') else 'BLOCKED'}`\n- Evidence readiness: `{report.get('status')}`\n- Performance comparison executed: `false`\n- Prior registration: `{(report.get('prior_registration') or {}).get('internal_registration_sha256')}`\n- Corrected registration SHA-256: `{report.get('registration_sha256')}`\n- Fold count: `{(report.get('development_scope') or {}).get('fold_count')}`\n- Interval: `{(report.get('development_scope') or {}).get('start')}` through `{(report.get('development_scope') or {}).get('end')}`\n- Split integrity: `{integrity.get('status')}`; reasons: `{integrity.get('reason_codes',[])}`\n- Overall materiality: `{materiality.get('status')}`. Zero direct matches do not establish complete typed-identity or valuation coverage.\n\n## Metric eligibility\n\n| Family | Status | Scope/reasons |\n|---|---|---|\n"+''.join(f"| {name} | `{item.get('status')}` | {item.get('row_scope')}; `{item.get('reason_codes')}` |\n" for name,item in eligibility.items())+"\nNo performance result, fitting, tuning, provider request, holdout content, promotion, or routing change was produced.\n"
 materiality_md=f"# V4 frozen-input materiality correction\n\n## Direct historical matches\n\n- Unique affected rows: `{materiality.get('unique_affected_rows')}`.\n- Direct ticker/date rows: `{materiality.get('direct_historical_match_rows')}`.\n\n## Typed-identity coverage\n\n- Status: `{materiality.get('typed_identifier_mapping_status')}`.\n- Coverage: `{materiality.get('identifier_coverage')}`.\n- Point-in-time symbol lineage: `{materiality.get('point_in_time_symbol_lineage')}`.\n- Mapping evidence: `{materiality.get('mapping_evidence')}`.\n- CIK matches remain investigation flags, never continuity proof.\n\n## Entry/exit price evidence\n\n- Mutually exclusive counts: `{prices.get('mutually_exclusive_counts')}`.\n- Representative field-resolution examples: `{prices.get('representative_examples')}`.\n- Prices reconstructed from returns: `false`.\n\n## Frozen endpoint returns\n\n- Evidence: `{returns}`.\n- Pinned producer source trace: `{materiality.get('producer_source_trace')}`.\n\n## Frozen cost policy\n\n- Evidence: `{costs}`. Missing values are not zero. Already-net returns must not have costs subtracted again.\n\n## Portfolio-path certification\n\n- Evidence: `{valuation}`. Zero detected uncertified paths does not prove paths were inspected or certified.\n\n## Metric eligibility\n\n"+''.join(f"- `{name}`: `{item.get('status')}` — `{item.get('reason_codes')}`\n" for name,item in eligibility.items())
 (a.output_dir/'registration.md').write_text(md); (a.output_dir/'materiality.md').write_text(materiality_md)
 paths=sorted(a.output_dir.glob('*.json'))+sorted(a.output_dir.glob('*.md'))
 (a.output_dir/'SHA256SUMS').write_text(''.join(f"{hashlib.sha256(x.read_bytes()).hexdigest()}  {x.name}\n" for x in paths))
 print(json.dumps({"execution":"COMPLETE" if report.get('status','').startswith('REGISTERED') else "BLOCKED","evidence_readiness":report.get('status'),"registration_sha256":report.get('registration_sha256')}))
 return 0 if report.get('status','').startswith('REGISTERED') else 2
if __name__=='__main__': raise SystemExit(main())
