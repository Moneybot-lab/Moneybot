#!/usr/bin/env python3
"""Investigate frozen ranking selections without calculating performance."""
from __future__ import annotations
import argparse, hashlib, json, subprocess
from collections import Counter
from pathlib import Path
from typing import Any

TARGETS=("challenger-ranking-lane-full-v1","challenger-ranking-lane-recent-half-v1","challenger-ranking-top5-model-v1")

def sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()

def disposition(record: dict[str,Any], recorded: bool | None) -> str:
    if recorded is None: return "MISSING_OR_UNINTERPRETABLE_SELECTION_EVIDENCE"
    if record.get("abstained") is True: return "ABSTAINED"
    if record.get("risk_rejected") is True: return "RISK_REJECTED"
    if record.get("rule_rejected") is True: return "RULE_REJECTED"
    return "SELECTED" if recorded else "NOT_SELECTED_BY_FROZEN_PRODUCER"

def investigate(capture: list[dict[str,Any]], manifest: dict[str,Any]) -> dict[str,Any]:
    definitions={str(x["model_version"]):x for x in manifest.get("challengers") or []}
    rows=[]
    for item in capture:
        name=str(item.get("model_version"))
        if name not in TARGETS: continue
        records=item.get("records") or []; predictions=item.get("predictions")
        valid=isinstance(predictions,list) and len(predictions)==len(records) and all(isinstance(x,bool) or isinstance(x,int) and not isinstance(x,bool) and x in (0,1) for x in predictions)
        flags=[bool(x) for x in predictions] if valid else [None]*len(records)
        counts=Counter(); diagnostic=0; examples=[]
        pairs=sorted(zip(records,flags),key=lambda pair:str(pair[0].get("id")))
        for position,(record,flag) in enumerate(pairs):
            counts[disposition(record,flag)]+=1
            score=record.get("score"); threshold=item.get("decision_threshold")
            threshold_selected=isinstance(score,(int,float)) and isinstance(threshold,(int,float)) and float(score)>=float(threshold) and not any(record.get(x) is True for x in ("abstained","risk_rejected","rule_rejected"))
            diagnostic+=int(threshold_selected)
            if len(examples)<3:
                examples.append({"canonical_id":record.get("id"),"source_fields":{"score":score,"record_prediction":record.get("prediction","ABSENT"),"parallel_prediction":flag,"abstained":record.get("abstained","ABSENT"),"risk_rejected":record.get("risk_rejected","ABSENT"),"rule_rejected":record.get("rule_rejected","ABSENT")},"threshold_interpretation":threshold_selected,"final_disposition":disposition(record,flag)})
        source_selected=sum(x is True for x in flags)
        effective_selected=counts["SELECTED"]
        if not valid: classification="SAVED_SELECTION_EVIDENCE_INSUFFICIENT"
        elif effective_selected!=diagnostic: classification="DIAGNOSTIC_INTERPRETATION_DEFECT"
        elif effective_selected==0: classification="EXPECTED_FROZEN_ZERO_SELECTION"
        else: classification="UNRESOLVED"
        definition=definitions.get(name,{})
        rows.append({"candidate":name,"fold":item.get("fold_index"),"expected_assignments":len(records),"observed_assignments":len(records),"selection_contract":{"model_type":item.get("model_type"),"candidate_lane":definition.get("candidate_lane"),"score_semantics":item.get("score_semantics"),"decision_threshold":item.get("decision_threshold"),"manifest_spec":definition.get("spec")},"field_presence":{"parallel_predictions":predictions is not None,"valid_parallel_predictions":valid,"record_prediction_present":sum("prediction" in x for x in records)},"gate_counts":{"source_recorded_selected":source_selected if valid else None,"abstained":counts["ABSTAINED"],"risk_rejected":counts["RISK_REJECTED"],"rule_rejected":counts["RULE_REJECTED"],"missing_or_uninterpretable":counts["MISSING_OR_UNINTERPRETABLE_SELECTION_EVIDENCE"]},"source_recorded_selected":source_selected if valid else None,"effective_source_selected":effective_selected if valid else None,"diagnostic_threshold_selected":diagnostic,"final_dispositions":dict(sorted(counts.items())),"reconciles":sum(counts.values())==len(records),"classification":classification,"examples":examples})
    missing=sorted(set(TARGETS)-{x["candidate"] for x in rows})
    classifications={x["classification"] for x in rows}
    overall=("SAVED_SELECTION_EVIDENCE_INSUFFICIENT" if missing or "SAVED_SELECTION_EVIDENCE_INSUFFICIENT" in classifications else "DIAGNOSTIC_INTERPRETATION_DEFECT" if "DIAGNOSTIC_INTERPRETATION_DEFECT" in classifications else "EXPECTED_FROZEN_ZERO_SELECTION" if classifications=={"EXPECTED_FROZEN_ZERO_SELECTION"} else "UNRESOLVED")
    return {"schema_version":"alpha-atlas-v4-ranking-zero-selection-investigation.v1","status":"COMPLETE" if not missing else "INCOMPLETE","overall_classification":overall,"missing_candidates":missing,"candidate_folds":rows,"performance_scoring_executed":False,"corrected_scoring_executed":False}

def main()->int:
    p=argparse.ArgumentParser(); p.add_argument("--capture",type=Path,required=True); p.add_argument("--manifest",type=Path,required=True); p.add_argument("--completed-results",type=Path,required=True); p.add_argument("--approved-audit",type=Path,required=True); p.add_argument("--output-dir",type=Path,required=True); a=p.parse_args(); a.output_dir.mkdir(parents=True,exist_ok=True)
    sources={k:{"path":str(v),"size":v.stat().st_size,"sha256":sha(v)} for k,v in {"capture":a.capture,"manifest":a.manifest,"completed_results":a.completed_results,"approved_grouping_audit":a.approved_audit}.items()}
    completed=json.loads(a.completed_results.read_text()); audit=json.loads(a.approved_audit.read_text())
    if (completed.get("execution_status"),completed.get("specification_sha256")) != ("COMPLETE","206e86dcfd2fa25edfbb8b3201e84e342da55ace668b4e0c6d4d31a4132912b2"): raise SystemExit("completed diagnostic identity mismatch")
    if (audit.get("status"),audit.get("specification_sha256")) != ("AUDIT_COMPLETE_NO_SCORING","206e86dcfd2fa25edfbb8b3201e84e342da55ace668b4e0c6d4d31a4132912b2"): raise SystemExit("approved audit identity mismatch")
    result=investigate(json.loads(a.capture.read_text()),json.loads(a.manifest.read_text())); result["provenance"]={"completed_diagnostic":{"run":"35795768048-1","commit":"896d3356bba186e2c7a2ba08fc8b44ba093110a0","artifact_id":10723897652,"artifact_digest":"sha256:ff50c8455ef6859a65cd2e223e0bd8644d4c9a1b91c6404e99c4171d85d9d003"},"approved_grouping_audit":{"run":"35788348286-1","commit":"95f9255457f1567e986622c2f21b94ee666696d7","artifact_id":10721186254,"artifact_digest":"sha256:83d61c942c7568a9689108e60b93ba31dd9df36a884f215476a05e6be2eb8b34"},"specification_sha256":"206e86dcfd2fa25edfbb8b3201e84e342da55ace668b4e0c6d4d31a4132912b2","source_registration":"35755237312-1","producer_code":{"commit":"5d360cdbda802ae8527b35fe59f75920b8c827c8","capture_path":"scripts/capture_alpha_atlas_v4_development_oof.py","producer_path":"scripts/train_challenger_suite.py","producer_blob":subprocess.check_output(["git","rev-parse","5d360cdbda802ae8527b35fe59f75920b8c827c8:scripts/train_challenger_suite.py"],text=True).strip()},"implementation_commit":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),"sources":sources}
    out=a.output_dir/"ranking_zero_selection_investigation.json"; out.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    lines=["# Ranking zero-selection investigation","",f"- Status: `{result['status']}`",f"- Classification: `{result['overall_classification']}`","- Corrected scoring executed: `false`.","- The completed gross results remain preserved; zero exposure is not stock-selection evidence.","","| Candidate | Fold | Assignments | Producer selected | Prior diagnostic selected | Classification |","|---|---:|---:|---:|---:|---|"]
    for x in result["candidate_folds"]: lines.append(f"| {x['candidate']} | {x['fold']} | {x['observed_assignments']} | {x['source_recorded_selected']} | {x['diagnostic_threshold_selected']} | {x['classification']} |")
    lines += ["","## Next action","Review this evidence; if it confirms `DIAGNOSTIC_INTERPRETATION_DEFECT`, separately authorize corrected scoring against unchanged frozen inputs."]
    md=a.output_dir/"ranking_zero_selection_investigation.md"; md.write_text("\n".join(lines)+"\n")
    (a.output_dir/"provenance_manifest.json").write_text(json.dumps(result["provenance"],indent=2,sort_keys=True)+"\n")
    files=(out,md,a.output_dir/"provenance_manifest.json"); (a.output_dir/"SHA256SUMS").write_text("".join(f"{sha(x)}  {x.name}\n" for x in files))
    return 0 if result["status"]=="COMPLETE" else 2
if __name__=="__main__": raise SystemExit(main())
