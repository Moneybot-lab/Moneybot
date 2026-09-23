#!/usr/bin/env python3
"""Execute the authorized exploratory V4 ranking comparison on frozen development data."""
from __future__ import annotations
import argparse, hashlib, json, math, shutil, subprocess, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from moneybot.services.alpha_atlas_v4_baseline_registration import _load_development_rows  # noqa:E402
from moneybot.services.alpha_atlas_v4_cohort_ranking import (CANDIDATES, RankingContractError, compare_membership_evidence,  # noqa:E402
  complete_three_fold_aggregate, descriptive_returns_from_membership, validate_execution_authorization)
from scripts.audit_alpha_atlas_v4_cohort_ranking_inputs import audit_inputs  # noqa:E402

def sha(path:Path)->str: return hashlib.sha256(path.read_bytes()).hexdigest()

def execute(args:argparse.Namespace)->dict:
    authorization=validate_execution_authorization(args.authorization,args.approved_audit,args.approved_membership)
    provenance=json.loads(args.input_provenance.read_text())
    reproduced_report,reproduced=audit_inputs({k:getattr(args,k) for k in ("canonical","plan","manifest","capture")},
      {k:getattr(args,k) for k in ("proposal","clarification","evidence","lineage")},provenance)
    if reproduced_report["status"]!="AUDIT_COMPLETE_NO_PERFORMANCE": raise RankingContractError("MEMBERSHIP_REPRODUCTION_AUDIT_FAILED")
    approved=json.loads(args.approved_membership.read_text()); reconciliation=compare_membership_evidence(approved,reproduced)
    identifiers={x["canonical_observation_id"] for x in approved["rows"]}; canonical,load_counts=_load_development_rows(args.canonical,identifiers)
    outcomes={}; invalid=[]
    for identifier,row in canonical.items():
        try: value=float(row["return_5d"])
        except (KeyError,TypeError,ValueError): invalid.append(identifier); continue
        if not math.isfinite(value): invalid.append(identifier); continue
        outcomes[identifier]=value
    folds=[]; candidates=[]
    for candidate in CANDIDATES:
        candidate_folds=[]
        for fold in (1,2,3):
            try: candidate_folds.append(descriptive_returns_from_membership(approved,outcomes,candidate=candidate,fold=fold))
            except Exception as exc: candidate_folds.append({"candidate":candidate,"fold":fold,"status":"BLOCKED","reason_code":getattr(exc,"code",type(exc).__name__),"details":getattr(exc,"details",{})})
        aggregate=complete_three_fold_aggregate(candidate_folds)
        candidates.append({"candidate":candidate,"score_semantics":CANDIDATES[candidate],"folds":candidate_folds,"equal_fold_aggregate":aggregate,
          "secondary_label_metrics":{"status":"NOT_EVALUABLE","reason":"GROUP_LEVEL_LABEL_AGGREGATION_FOR_REPEATED_OBSERVATIONS_NOT_REGISTERED"},"winner_selected":False})
        folds.extend(candidate_folds)
    complete=not invalid and all(x["equal_fold_aggregate"]["status"]=="COMPLETE" for x in candidates)
    return {"schema_version":"alpha-atlas-v4-cohort-relative-ranking-results.v1","execution_status":"COMPLETE" if complete else "BLOCKED_INCOMPLETE",
      "evidence_conclusion":"EXPLORATORY_POST_DIAGNOSTIC_DEVELOPMENT_DESCRIPTION","performance_scoring":"AUTHORIZED_REGISTERED_GROSS_ENDPOINT_ARITHMETIC_ONLY",
      "authorization":authorization,"membership_reconciliation":reconciliation,"reproduced_audit":{k:v for k,v in reproduced_report.items() if k not in ("candidate_folds",)},
      "outcome_semantics":{"field":"return_5d","definition":"saved gross split-adjusted endpoint return","required_observations":len(identifiers),
        "loaded_observations":len(outcomes),"missing_or_nonfinite_count":len(invalid),"missing_or_nonfinite_sample":invalid[:10]},
      "canonical_loading":load_counts,"candidate_order":list(CANDIDATES),"candidates":candidates,"implementation_commit":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
      "training":False,"tuning":False,"holdout_content_access":False,"winner_selected":False,"portfolio_curve_computed":False,
      "limitations":["exploratory post-diagnostic analysis of already-viewed development data","ticker groups are not verified security identities",
        "gross endpoint arithmetic is not net profitability or portfolio performance","overlapping endpoint returns are not compounded into a portfolio curve",
        "historical coverage, terminal valuation, applicable development costs, and broader validation remain unresolved",
        "new selection experiment; historical zero-exposure results remain unchanged"]}

def main()->int:
    p=argparse.ArgumentParser()
    for name in ("authorization","proposal","clarification","evidence","lineage","approved-audit","approved-membership","canonical","plan","manifest","capture","input-provenance","output-dir"): p.add_argument("--"+name,type=Path,required=True)
    a=p.parse_args(); a.output_dir.mkdir(parents=True,exist_ok=True)
    try: result=execute(a); code=0 if result["execution_status"]=="COMPLETE" else 2
    except Exception as exc:
      result={"schema_version":"alpha-atlas-v4-cohort-relative-ranking-results.v1","execution_status":"FAILED","performance_scoring":"NOT_COMPLETED",
        "reason_code":getattr(exc,"code",type(exc).__name__),"details":getattr(exc,"details",{}),"training":False,"tuning":False,"holdout_content_access":False,"winner_selected":False}; code=2
    (a.output_dir/"cohort_ranking_results.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    for source in (a.authorization,a.proposal,a.clarification,a.approved_audit,a.approved_membership):
      if source.is_file(): shutil.copyfile(source,a.output_dir/source.name)
    lines=["# Alpha Atlas V4 cohort-relative ranking results","",f"- Execution: `{result['execution_status']}`.",f"- Evidence conclusion: `{result.get('evidence_conclusion','NOT_PRODUCED')}`.","- Holdout content access: `false`.","- Winner selected: `false`.",""]
    for item in result.get("candidates",[]):
      lines += [f"## `{item['candidate']}`",f"- Equal-fold aggregate: `{item['equal_fold_aggregate']}`.",f"- Folds: `{item['folds']}`.",""]
    lines += ["## Limitations"]+[f"- {x}." for x in result.get("limitations",[])]
    if result.get("reason_code"): lines.append(f"- Failure: `{result['reason_code']}` — `{result.get('details')}`.")
    (a.output_dir/"cohort_ranking_results.md").write_text("\n".join(lines)+"\n")
    files=sorted(x for x in a.output_dir.iterdir() if x.is_file() and x.name!="SHA256SUMS")
    (a.output_dir/"SHA256SUMS").write_text("".join(f"{sha(x)}  {x.name}\n" for x in files))
    if code: print(f"cohort ranking execution failed: {result.get('reason_code',result['execution_status'])}; see result artifact",file=sys.stderr)
    return code

if __name__=="__main__": raise SystemExit(main())
