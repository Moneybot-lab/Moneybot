#!/usr/bin/env python3
"""Audit frozen V4 development membership and top-five selection without outcomes."""
from __future__ import annotations
import argparse, hashlib, json, shutil, sys
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from moneybot.services.alpha_atlas_v4_baseline_registration import _load_development_rows  # noqa:E402
from moneybot.services.alpha_atlas_v4_cohort_ranking import (  # noqa:E402
    CANDIDATES, CLARIFICATION_SHA256, EVIDENCE_SHA256, PROPOSAL_SHA256,
    RankingContractError, audit_selection_membership, validate_frozen_contract,
)
from moneybot.services.alpha_atlas_v4_temporal_split import canonical_json_hash  # noqa:E402

EXPECTED_BYTES={"canonical":"506073994052be852a5a00fc38e239b85e72a50494c3559640ea980fb6a52d9e",
 "plan":"f11257cff0befc1f0b46e4cab9a64678c766b6fe2abdc50b07861a18f7d6933a",
 "manifest":"ea4e55f9faa848219945d7e03c92c7a541645cd4d6df8aa3cfbd0d1334872f15",
 "capture":"454febde2e14ca8a916222d86a6872db1c5e790a29429f2db6393d828e87e434"}
PLAN_SEMANTIC="bd60055adc6cb1b3f8b143c6c61031bced01c9151fcb3b67c33f75430106e8e8"
EXPECTED_COUNTS={1:4464,2:2479,3:3330}

def sha(path:Path)->str:
    try: return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc: raise RankingContractError("INPUT_FILE_MISSING_OR_UNREADABLE",{"path":str(path),"error":type(exc).__name__}) from exc

def normalize_fold(value:object)->int:
    """Normalize JSON integer/digit-string folds, rejecting lossy representations."""
    if isinstance(value,bool) or not (isinstance(value,int) or isinstance(value,str) and value.isdigit()):
        raise RankingContractError("INVALID_FOLD_REPRESENTATION",{"value":repr(value),"type":type(value).__name__})
    fold=int(value)
    if fold not in EXPECTED_COUNTS: raise RankingContractError("UNEXPECTED_FOLD",{"value":repr(value),"normalized":fold})
    return fold

def validate_capture_scope(manifest_roster:list[str], capture:list[dict], usable_folds:set[int])->tuple[dict,dict]:
    """Validate the complete source capture, then project the authorized roster."""
    if len(manifest_roster)!=len(set(manifest_roster)):
        raise RankingContractError("DUPLICATE_MANIFEST_CANDIDATE")
    blocks={}; duplicates=[]; fold_types=Counter()
    for position,item in enumerate(capture):
        candidate=item.get("model_version")
        if not isinstance(candidate,str) or not candidate: raise RankingContractError("INVALID_CAPTURE_CANDIDATE",{"position":position})
        raw_fold=item.get("fold_index"); fold_types[type(raw_fold).__name__]+=1; fold=normalize_fold(raw_fold)
        key=(candidate,fold)
        if key in blocks: duplicates.append({"candidate":candidate,"fold":fold,"representations":[repr(blocks[key].get("fold_index")),repr(raw_fold)]})
        else: blocks[key]=item
    expected={(candidate,fold) for candidate in manifest_roster for fold in usable_folds}; observed=set(blocks)
    missing=sorted(expected-observed); unexpected=sorted(observed-expected)
    authorized_expected={(candidate,fold) for candidate in CANDIDATES for fold in usable_folds}
    authorized_observed=observed&authorized_expected
    diagnostics={"full_capture_roster":sorted({candidate for candidate,_ in observed}),"frozen_manifest_roster":manifest_roster,
      "authorized_audit_roster":list(CANDIDATES),"outside_experiment_scope_candidates":[x for x in manifest_roster if x not in CANDIDATES],
      "expected_source_candidate_fold_pairs":[list(x) for x in sorted(expected)],"observed_source_candidate_fold_pairs":[list(x) for x in sorted(observed)],
      "missing_source_pairs":[list(x) for x in missing],"unexpected_source_pairs":[list(x) for x in unexpected],"duplicate_pairs":duplicates,
      "fold_value_types":dict(sorted(fold_types.items())),"expected_authorized_pairs":[list(x) for x in sorted(authorized_expected)],
      "observed_authorized_pairs":[list(x) for x in sorted(authorized_observed)],
      "missing_authorized_pairs":[list(x) for x in sorted(authorized_expected-authorized_observed)]}
    if duplicates: raise RankingContractError("DUPLICATE_CANDIDATE_FOLD_CAPTURE",diagnostics)
    if unexpected: raise RankingContractError("UNKNOWN_SOURCE_CANDIDATE_OR_FOLD",diagnostics)
    if missing: raise RankingContractError("MISSING_SOURCE_CANDIDATE_FOLD",diagnostics)
    if authorized_expected!=authorized_observed: raise RankingContractError("CAPTURE_CANDIDATE_FOLD_SET_MISMATCH",diagnostics)
    return {key:blocks[key] for key in authorized_expected},diagnostics

def validate_membership_structure(expected_ids:dict[int,set[str]], capture_ids:dict[tuple[str,int],list[str]],
                                  holdout:set[str], *, expected_counts:dict[int,int]=EXPECTED_COUNTS)->None:
    if set(expected_ids)!=set(expected_counts): raise RankingContractError("MISSING_OR_UNEXPECTED_FOLDS",{"observed":sorted(expected_ids)})
    if {fold:len(ids) for fold,ids in expected_ids.items()}!=expected_counts: raise RankingContractError("FOLD_VALIDATION_COUNT_MISMATCH")
    if any(expected_ids[a]&expected_ids[b] for a in expected_ids for b in expected_ids if a<b): raise RankingContractError("MULTIPLY_ASSIGNED_VALIDATION_ID")
    if set().union(*expected_ids.values())&holdout: raise RankingContractError("HOLDOUT_MEMBERSHIP_OVERLAP")
    expected_keys={(candidate,fold) for candidate in CANDIDATES for fold in expected_counts}
    if set(capture_ids)!=expected_keys: raise RankingContractError("CAPTURE_CANDIDATE_FOLD_SET_MISMATCH")
    for (candidate,fold),ids in capture_ids.items():
        if len(ids)!=len(set(ids)): raise RankingContractError("DUPLICATE_CAPTURE_ASSIGNMENT",{"candidate":candidate,"fold":fold})
        if set(ids)!=expected_ids[fold]: raise RankingContractError("INCORRECT_CANONICAL_FOLD_JOIN",{"candidate":candidate,"fold":fold})

def _summarize(selection:dict, expected:int, observed:int)->dict:
    groups=selection["groups"]; cohorts=selection["cohorts"]; rows=selection["rows"]
    multiplicity=Counter(x["observation_count"] for x in groups)
    classifications=Counter(x["eligibility_classification"] for x in groups)
    gate_counts=Counter(reason for row in rows for reason in row["gate_reasons"])
    dispositions=Counter(row["gate_disposition"] for row in rows)
    reconciled={
      "within_group_observation_weights":all(abs(x["within_group_observation_weight"]*x["observation_count"]-1)<1e-12 for x in groups),
      "selected_or_cash_per_cohort":all(abs((x["selected_weight_sum"] if not x["empty"] else x["cash_weight"])-1)<1e-12 for x in cohorts),
      "eligible_baseline_per_nonempty_cohort":all(x["empty"] or abs(x["baseline_weight_sum"]-1)<1e-12 for x in cohorts),
      "empty_baseline_handling":"undefined; empty cohort retained with cash weight one",
      "cohort_weights_within_fold":abs((1/len(cohorts))*len(cohorts)-1)<1e-12 if cohorts else False,
    }
    return {"expected_assignments":expected,"observed_assignments":observed,"unique_assignments":len(rows),
      "cohort_count":len(cohorts),"ticker_group_count":len(groups),"multiplicity_distribution":dict(sorted(multiplicity.items())),
      "group_dispositions":dict(classifications),"individual_gate_counts":dict(gate_counts),"final_dispositions":dict(dispositions),
      "eligible_ticker_groups":sum(x["eligible"] for x in groups),"selected_ticker_groups":sum(x["selected"] for x in groups),
      "selected_group_member_observations":sum(x["proposed_top5_selected"] for x in rows),
      "tie_group_count":sum(x["tie_group_count"] for x in cohorts),"fewer_than_five_cohorts":sum(x["fewer_than_five"] for x in cohorts),
      "empty_cohorts":sum(x["empty"] for x in cohorts),"cohort_weight":1/len(cohorts) if cohorts else None,
      "weight_reconciliation":reconciled,"weight_reconciliation_passed":all(v for k,v in reconciled.items() if k!="empty_baseline_handling")}

def failure_report(exc:Exception)->dict:
    details=getattr(exc,"details",{}); preserved=details.get("input_provenance",{})
    return {"schema_version":"alpha-atlas-v4-cohort-ranking-input-audit.v1","status":"AUDIT_FAILED_NO_PERFORMANCE",
      "input_verification_status":details.get("input_verification_status","FAILED"),"grouping_eligibility_audit_status":"NOT_COMPLETED",
      "selection_membership_audit_status":"NOT_COMPLETED","weight_reconciliation_status":"NOT_COMPLETED","performance_scoring":"NOT_RUN",
      "holdout_content_access":False,"reason_code":getattr(exc,"code",type(exc).__name__),"details":details,
      "input_provenance":preserved,"candidate_fold_membership_verification_status":"FAILED"}

def audit_inputs(paths:dict[str,Path], contract:dict[str,Path], provenance:dict)->tuple[dict,dict]:
    verified=validate_frozen_contract(contract["proposal"],contract["evidence"],contract["lineage"],contract["clarification"])
    inputs={}
    for role,path in paths.items():
        actual=sha(path); expected=EXPECTED_BYTES[role]
        inputs[role]={"path":str(path.resolve()),"bytes":path.stat().st_size,"expected_sha256":expected,"computed_sha256":actual,
          **(provenance.get(role) or {})}
        if actual!=expected: raise RankingContractError("FROZEN_INPUT_HASH_MISMATCH",{"role":role,"expected":expected,"actual":actual})
    plan=json.loads(paths["plan"].read_text()); embedded=plan.get("plan_sha256"); recomputed=canonical_json_hash({k:v for k,v in plan.items() if k!="plan_sha256"})
    inputs["plan"].update({"embedded_semantic_sha256":embedded,"recomputed_semantic_sha256":recomputed,"expected_semantic_sha256":PLAN_SEMANTIC})
    if embedded!=PLAN_SEMANTIC or recomputed!=PLAN_SEMANTIC: raise RankingContractError("SPLIT_PLAN_SEMANTIC_HASH_MISMATCH",inputs["plan"])
    manifest=json.loads(paths["manifest"].read_text()); capture=json.loads(paths["capture"].read_text())
    folds=manifest.get("walk_forward_windows") or []; usable=[x for x in folds if x.get("usable")]; fold_by_index={}; manifest_fold_representations={}
    for item in usable:
      raw=item.get("fold_index"); fold=normalize_fold(raw)
      if fold in fold_by_index: raise RankingContractError("COLLIDING_MANIFEST_FOLD_REPRESENTATIONS",{"fold":fold,"representations":[manifest_fold_representations[fold],repr(raw)],"input_provenance":inputs,"input_verification_status":"PASSED"})
      fold_by_index[fold]=item; manifest_fold_representations[fold]=repr(raw)
    expected_ids={fold:set(map(str,item.get("validation_canonical_observation_ids",item.get("validation_ids",[])))) for fold,item in fold_by_index.items()}
    development=set(map(str,plan.get("train_canonical_observation_ids") or [])); holdout=set(map(str,plan.get("test_canonical_observation_ids") or []))
    validation=set().union(*expected_ids.values())
    if validation-development: raise RankingContractError("VALIDATION_NOT_IN_DEVELOPMENT_MEMBERSHIP")
    rows,load_counts=_load_development_rows(paths["canonical"],validation)
    manifest_roster=[str(x.get("model_version")) for x in manifest.get("challengers") or []]
    roster=[name for name in manifest_roster if name in CANDIDATES]
    if roster!=list(CANDIDATES): raise RankingContractError("CANDIDATE_ROSTER_OR_ORDER_MISMATCH",{"observed":roster,"input_provenance":inputs,"input_verification_status":"PASSED"})
    try:
      capture_by_key,capture_scope=validate_capture_scope(manifest_roster,capture,set(EXPECTED_COUNTS))
    except RankingContractError as exc:
      exc.details={**exc.details,"input_provenance":inputs,"input_verification_status":"PASSED"}; raise
    capture_ids={key:[str(x.get("id")) for x in (item.get("records") or [])] for key,item in capture_by_key.items()}
    try: validate_membership_structure(expected_ids,capture_ids,holdout)
    except RankingContractError as exc:
      exc.details={**exc.details,"input_provenance":inputs,"input_verification_status":"PASSED","capture_scope":capture_scope}; raise
    fold_reports=[]; evidence={"rows":[],"groups":[],"cohorts":[]}; blocked=[]
    for candidate in roster:
      for fold in sorted(EXPECTED_COUNTS):
        item=capture_by_key[(candidate,fold)]; records=item.get("records")
        try:
          if not isinstance(records,list): raise RankingContractError("MISSING_CAPTURE_RECORDS")
          ids=[str(x.get("id")) for x in records]
          if len(ids)!=len(set(ids)): raise RankingContractError("DUPLICATE_CAPTURE_ASSIGNMENT")
          if set(ids)!=expected_ids[fold]: raise RankingContractError("INCORRECT_CANONICAL_FOLD_JOIN",{"missing_count":len(expected_ids[fold]-set(ids)),"unexpected_count":len(set(ids)-expected_ids[fold])})
          predictions=item.get("predictions") or []
          converted=[]
          for pos,record in enumerate(records):
            identifier=str(record["id"]); canonical=rows[identifier]
            converted.append({"canonical_observation_id":identifier,"ticker":canonical.get("symbol"),"event_date":canonical.get("event_date"),
              "label_horizon_sessions":canonical.get("label_horizon_sessions"),"entry_at":canonical.get("entry_at"),"exit_at":canonical.get("exit_at"),
              "score":record.get("score"),"abstained":record.get("abstained"),"risk_rejected":record.get("risk_rejected"),
              "rule_rejected":record.get("rule_rejected"),"source_frozen_selection":bool(predictions[pos]) if len(predictions)==len(records) else None})
          selected=audit_selection_membership(converted,candidate=candidate,fold=fold)
          summary=_summarize(selected,EXPECTED_COUNTS[fold],len(records)); status="PASSED" if summary["unique_assignments"]==EXPECTED_COUNTS[fold] and summary["weight_reconciliation_passed"] else "FAILED"
          fold_reports.append({"candidate":candidate,"score_semantics":CANDIDATES[candidate],"fold":fold,"status":status,**summary})
          for kind in evidence: evidence[kind].extend(selected[kind])
        except Exception as exc:
          blocked.append({"candidate":candidate,"fold":fold,"reason_code":getattr(exc,"code",type(exc).__name__),"details":getattr(exc,"details",{})})
          fold_reports.append({"candidate":candidate,"score_semantics":CANDIDATES[candidate],"fold":fold,"status":"BLOCKED","expected_assignments":EXPECTED_COUNTS[fold],"reason_code":blocked[-1]["reason_code"]})
    passed=not blocked and len(fold_reports)==9 and all(x["status"]=="PASSED" for x in fold_reports)
    report={"schema_version":"alpha-atlas-v4-cohort-ranking-input-audit.v1","status":"AUDIT_COMPLETE_NO_PERFORMANCE" if passed else "AUDIT_FAILED_NO_PERFORMANCE",
      "input_verification_status":"PASSED","grouping_eligibility_audit_status":"PASSED" if passed else "FAILED",
      "selection_membership_audit_status":"PASSED" if passed else "FAILED","weight_reconciliation_status":"PASSED" if passed else "FAILED",
      "performance_scoring":"NOT_RUN","holdout_content_access":False,"holdout_membership_overlap_count":0,"security_identity_claimed":False,
      "verified_contract":verified,"input_provenance":inputs,"canonical_loading":load_counts,"expected_total_assignments":30819,
      "observed_total_assignments":sum(x.get("observed_assignments",0) for x in fold_reports),"candidate_manifest_order":roster,
      "capture_scope":capture_scope,"candidate_folds":fold_reports,"blocked_candidate_folds":blocked,"selection_uses_outcomes":False}
    return report,evidence

def main()->int:
    p=argparse.ArgumentParser()
    for name in ("proposal","clarification","evidence","lineage","canonical","plan","manifest","capture","input-provenance","output-dir"): p.add_argument("--"+name,type=Path,required=True)
    a=p.parse_args(); a.output_dir.mkdir(parents=True,exist_ok=True)
    report=None; evidence={"rows":[],"groups":[],"cohorts":[]}
    try:
      provenance=json.loads(a.input_provenance.read_text())
      report,evidence=audit_inputs({k:getattr(a,k) for k in ("canonical","plan","manifest","capture")},{k:getattr(a,k) for k in ("proposal","clarification","evidence","lineage")},provenance)
      code=0 if report["status"]=="AUDIT_COMPLETE_NO_PERFORMANCE" else 2
    except Exception as exc:
      report=failure_report(exc); code=2
    (a.output_dir/"cohort_ranking_input_audit.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    (a.output_dir/"cohort_ranking_membership_evidence.json").write_text(json.dumps(evidence,indent=2,sort_keys=True)+"\n")
    (a.output_dir/"input_provenance_manifest.json").write_text(json.dumps(report.get("input_provenance",{}),indent=2,sort_keys=True)+"\n")
    shutil.copyfile(a.proposal,a.output_dir/a.proposal.name); shutil.copyfile(a.clarification,a.output_dir/a.clarification.name)
    lines=["# Alpha Atlas V4 cohort-relative ranking input audit","",f"- Status: `{report['status']}`.","- Performance scoring: `NOT_RUN`.","- Holdout content access: `false`.",f"- Input verification: `{report['input_verification_status']}`.",f"- Grouping/eligibility: `{report['grouping_eligibility_audit_status']}`.",f"- Selection membership: `{report['selection_membership_audit_status']}`.",f"- Weight reconciliation: `{report['weight_reconciliation_status']}`.",""]
    for item in report.get("candidate_folds",[]): lines.append(f"- `{item['candidate']}` fold {item['fold']}: `{item['status']}`; expected {item['expected_assignments']}, observed {item.get('observed_assignments','unavailable')}.")
    if report.get("reason_code"): lines.append(f"- Failure: `{report['reason_code']}` — `{report.get('details')}`.")
    (a.output_dir/"cohort_ranking_input_audit.md").write_text("\n".join(lines)+"\n")
    files=sorted(x for x in a.output_dir.iterdir() if x.is_file() and x.name!="SHA256SUMS")
    (a.output_dir/"SHA256SUMS").write_text("".join(f"{sha(x)}  {x.name}\n" for x in files))
    if code: print(f"cohort ranking input audit failed: {report.get('reason_code')}; see cohort_ranking_input_audit.json for details",file=sys.stderr)
    return code

if __name__=="__main__": raise SystemExit(main())
