#!/usr/bin/env python3
"""Execute only the reviewed V4 narrow frozen-sample descriptive diagnostic."""
from __future__ import annotations
import argparse, hashlib, json, shutil, subprocess
from collections import defaultdict
from pathlib import Path
from moneybot.services.alpha_atlas_v4_baseline_registration import _load_development_rows
from moneybot.services.alpha_atlas_v4_narrow_diagnostic import DiagnosticSpecError, aggregate_candidate, grouping_audit, score_candidate_fold, validate_spec


def _sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()

def main() -> int:
    p=argparse.ArgumentParser()
    for name in ("specification","canonical","plan","manifest","capture","approved-grouping-audit","output-dir"): p.add_argument("--"+name,type=Path,required=True)
    p.add_argument("--reviewed-specification-hash",required=True)
    a=p.parse_args(); a.output_dir.mkdir(parents=True,exist_ok=True); shutil.copyfile(a.specification,a.output_dir/'executed_specification.json')
    failure=None
    try:
        inputs={"canonical":a.canonical,"plan":a.plan,"manifest":a.manifest,"capture":a.capture}; spec=validate_spec(a.specification,a.reviewed_specification_hash,inputs)
        approved=json.loads(a.approved_grouping_audit.read_text())
        expected_audit=("AUDIT_COMPLETE_NO_SCORING",a.reviewed_specification_hash,10273,421193,True,False)
        observed_audit=(approved.get("status"),approved.get("specification_sha256"),approved.get("rows"),approved.get("assignments"),approved.get("weights_reconcile_per_cohort"),approved.get("performance_scoring_executed"))
        if observed_audit!=expected_audit: raise DiagnosticSpecError("APPROVED_GROUPING_AUDIT_IDENTITY_MISMATCH")
        if approved.get("source_registration")!=spec.get("source_registration"): raise DiagnosticSpecError("APPROVED_AUDIT_SOURCE_REGISTRATION_MISMATCH")
        plan=json.loads(a.plan.read_text()); development=set(map(str,plan["train_canonical_observation_ids"])); rows,_=_load_development_rows(a.canonical,development)
        capture=json.loads(a.capture.read_text()); assignments=[{"candidate":item["model_version"],"fold":item["fold_index"],"canonical_observation_id":record["id"]} for item in capture for record in item.get("records") or []]
        assigned_ids={x["canonical_observation_id"] for x in assignments}; reproduced=grouping_audit([rows[x] for x in sorted(assigned_ids)],assignments)
        for key in ("rows","assignments","ticker_date_timing_groups","groups_by_row_multiplicity","multiple_observation_groups","cohorts","weights_reconcile_per_cohort"):
            if reproduced.get(key)!=approved.get(key): raise DiagnosticSpecError(f"GROUPING_AUDIT_REPRODUCTION_MISMATCH:{key}")
        mapping=reproduced["row_to_group_mapping"]; manifest=json.loads(a.manifest.read_text())
        manifest_by_name={str(x["model_version"]):x for x in manifest.get("challengers") or []}
        enriched=[]
        for item in capture:
            definition=manifest_by_name[str(item["model_version"])]; lane=definition.get("candidate_lane") or (definition.get("spec") or {}).get("candidate_lane") or "classification"
            enriched.append({**item,"candidate_type":lane})
        per_fold=[score_candidate_fold(item,rows,mapping) for item in enriched]
        roster=[]; by_candidate=defaultdict(list)
        for item in per_fold: by_candidate[item["candidate"]].append(item)
        for candidate in manifest.get("challengers") or []:
            name=str(candidate["model_version"]); folds=sorted(by_candidate[name],key=lambda x:x["fold"])
            roster.append({"candidate":name,"manifest":candidate,"folds":folds,"descriptive_aggregate":aggregate_candidate(folds),"winner_selected":False})
        result={"schema_version":"alpha-atlas-v4-narrow-frozen-sample-results.v1","execution_status":"COMPLETE","descriptive_findings_status":"CONDITIONAL_SAVED_SAMPLE_ONLY",
          "broader_validation_readiness":"BLOCKED_UNCHANGED","specification_sha256":a.reviewed_specification_hash,
          "source_registration":spec["source_registration"],"approved_grouping_audit":{"path":a.approved_grouping_audit.name,"sha256":_sha(a.approved_grouping_audit),"identity":observed_audit},
          "input_provenance":{role:{"path":path.name,"sha256":_sha(path),**spec["frozen_inputs"][role].get("provenance",{})} for role,path in inputs.items()},
          "implementation_commit":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),"grouping_reconciliation":{k:v for k,v in reproduced.items() if k!="row_to_group_mapping"},
          "candidate_roster_order":"frozen manifest order; not a leaderboard","candidates":roster,
          "uncertainty":{"status":"NOT_EVALUATED","reason":"BOOTSTRAP_REPLICATE_COUNT_CONFIDENCE_LEVEL_AND_SEED_UNSPECIFIED; independent-row intervals forbidden"},
          "limitations":spec["claim_limits"]+["same-security continuity unresolved","full feature-lookback start unknown","applicable development cost policy unavailable","development portfolio paths uncertified"],
          "performance_comparison_scope":"approved narrow descriptive diagnostic only","training":False,"tuning":False,"holdout_content_loaded":False,"winner_selected":False}
    except Exception as exc:
        failure=exc; result={"schema_version":"alpha-atlas-v4-narrow-frozen-sample-results.v1","execution_status":"FAILED","reason_code":getattr(exc,"code",type(exc).__name__),
          "input_role":getattr(exc,"role",None),"hash_type":getattr(exc,"hash_type",None),"expected_hash":getattr(exc,"expected",None),"actual_hash":getattr(exc,"actual",None),
          "grouping_executed":False,"scoring_completed":False,"holdout_content_loaded":False,"specification_sha256_expected":a.reviewed_specification_hash}
    target=a.output_dir/'narrow_diagnostic_results.json'; target.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    provenance={"schema_version":"alpha-atlas-v4-narrow-diagnostic-execution-provenance.v1","execution_status":result["execution_status"],
      "specification":{"path":a.specification.name,"sha256":_sha(a.specification)},"approved_grouping_audit":{"path":a.approved_grouping_audit.name,"sha256":_sha(a.approved_grouping_audit)},
      "inputs":result.get("input_provenance",{}),"implementation_commit":result.get("implementation_commit"),"training":False,"tuning":False,"holdout_content_loaded":False}
    (a.output_dir/'execution_provenance.json').write_text(json.dumps(provenance,indent=2,sort_keys=True)+'\n')
    lines=["# V4 narrow frozen-sample diagnostic",f"- Execution: `{result['execution_status']}`.",f"- Descriptive findings: `{result.get('descriptive_findings_status','NOT_PRODUCED')}`.",f"- Broader readiness: `{result.get('broader_validation_readiness','BLOCKED')}`.","- Winner selected: `false`.","- Net/portfolio claim: `not evaluated`.",""]
    for item in result.get("candidates",[]):
        lines += [f"## `{item['candidate']}`",f"- Aggregate: `{item['descriptive_aggregate']}`.",f"- Folds: `{item['folds']}`.",""]
    if failure: lines += [f"- Failure: `{result['reason_code']}`."]
    (a.output_dir/'narrow_diagnostic_results.md').write_text('\n'.join(lines)+'\n')
    paths=sorted(a.output_dir.glob('*.json'))+sorted(a.output_dir.glob('*.md')); (a.output_dir/'SHA256SUMS').write_text(''.join(f"{_sha(x)}  {x.name}\n" for x in paths))
    return 0 if not failure else 2

if __name__=='__main__': raise SystemExit(main())
