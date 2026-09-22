"""Pre-registered, research-only frozen-sample descriptive diagnostic helpers."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
from typing import Any
from moneybot.services.alpha_atlas_v4_temporal_split import canonical_json_hash


class DiagnosticSpecError(ValueError):
    def __init__(self, code: str, *, role: str | None = None, hash_type: str | None = None,
                 expected: str | None = None, actual: str | None = None, details: dict[str, Any] | None = None):
        super().__init__(code); self.code=code; self.role=role; self.hash_type=hash_type
        self.expected=expected; self.actual=actual; self.details=details or {}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_spec(spec_path: Path, reviewed_hash: str, inputs: dict[str, Path]) -> dict[str, Any]:
    actual_spec=sha256(spec_path)
    if actual_spec != reviewed_hash:
        raise DiagnosticSpecError("REVIEWED_SPECIFICATION_HASH_MISMATCH",role="specification",hash_type="file_byte_sha256",expected=reviewed_hash,actual=actual_spec)
    spec=json.loads(spec_path.read_text())
    for role, expected in spec["frozen_inputs"].items():
        if role not in inputs: raise DiagnosticSpecError("FROZEN_INPUT_MISSING",role=role)
        actual_bytes=sha256(inputs[role]); expected_bytes=expected["file_byte_sha256"]
        if actual_bytes != expected_bytes:
            raise DiagnosticSpecError("FROZEN_INPUT_HASH_MISMATCH",role=role,hash_type="file_byte_sha256",expected=expected_bytes,actual=actual_bytes)
        if role == "plan":
            plan=json.loads(inputs[role].read_text()); embedded=str(plan.get("plan_sha256") or "")
            expected_content=expected["semantic_content_sha256"]
            if embedded != expected_content:
                raise DiagnosticSpecError("SPLIT_PLAN_EMBEDDED_HASH_MISMATCH",role=role,hash_type="embedded_plan_sha256",expected=expected_content,actual=embedded)
            core={key:value for key,value in plan.items() if key!="plan_sha256"}; actual_content=canonical_json_hash(core)
            if actual_content != expected_content:
                raise DiagnosticSpecError("SPLIT_PLAN_CONTENT_HASH_MISMATCH",role=role,hash_type="canonical_json_sha256_excluding_plan_sha256",expected=expected_content,actual=actual_content)
    if spec.get("execution",{}).get("default") != "OFF" or spec.get("broader_registration",{}).get("status") != "REGISTERED_BLOCKED":
        raise DiagnosticSpecError("SPECIFICATION_SAFETY_BOUNDARY_MISMATCH")
    return spec


def grouping_audit(rows: list[dict[str, Any]], assignments: list[dict[str, Any]]) -> dict[str, Any]:
    by_id={str(row["canonical_observation_id"]):row for row in rows}
    if len(by_id)!=len(rows): raise DiagnosticSpecError("DUPLICATE_CANONICAL_OBSERVATION_ID")
    seen=set(); groups: dict[tuple, list[str]]=defaultdict(list); multiplicity=Counter()
    mapping=[]
    for assignment in assignments:
        key=(str(assignment["candidate"]),int(assignment["fold"]),str(assignment["canonical_observation_id"]))
        if key in seen: raise DiagnosticSpecError("DUPLICATE_CANDIDATE_FOLD_ASSIGNMENT")
        seen.add(key); row=by_id.get(key[2])
        if row is None: raise DiagnosticSpecError("ASSIGNMENT_WITHOUT_CANONICAL_ROW")
        horizon=int(row["label_horizon_sessions"]); event=str(row["event_date"])[:10]
        ticker=str(row["symbol"]).upper(); entry=str(row["entry_at"]); exit_at=str(row["exit_at"])
        cohort=(key[0],key[1],event,horizon,entry,exit_at); ticker_group=cohort+(ticker,)
        groups[ticker_group].append(key[2])
    cohort_group_counts=Counter(group[:-1] for group in groups)
    for group,ids in sorted(groups.items()):
        within=1/len(ids); cohort_weight=1/cohort_group_counts[group[:-1]]
        for identifier in ids:
            mapping.append({"candidate":group[0],"fold":group[1],"canonical_observation_id":identifier,
              "cohort_key":list(group[:-1]),"ticker_date_group":group[-1],"within_ticker_date_weight":within,
              "ticker_group_cohort_weight":cohort_weight,"final_cohort_row_weight":within*cohort_weight})
        multiplicity[len(ids)]+=1
    reconciled=all(math.isclose(sum(x["final_cohort_row_weight"] for x in mapping if tuple(x["cohort_key"])==cohort),1.0,abs_tol=1e-12)
                   for cohort in cohort_group_counts)
    return {"rows":len(rows),"assignments":len(assignments),"ticker_date_timing_groups":len(groups),
      "groups_by_row_multiplicity":dict(sorted(multiplicity.items())),"multiple_observation_groups":sum(v for k,v in multiplicity.items() if k>1),
      "cohorts":len(cohort_group_counts),"weights_reconcile_per_cohort":reconciled,"row_to_group_mapping":mapping,
      "security_identity_claimed":False,"group_key_fields":["candidate","fold","event_date","horizon","entry_at","exit_at","ticker"]}


def normalize_multiplicity_histogram(value: Any, *, source: str) -> dict[int, int]:
    if not isinstance(value,dict): raise DiagnosticSpecError("MALFORMED_MULTIPLICITY_HISTOGRAM",details={"source":source,"reason":"NOT_OBJECT"})
    normalized={}; original={}
    for key,count in value.items():
        if isinstance(key,bool) or not (isinstance(key,int) or isinstance(key,str) and key.isdigit()):
            raise DiagnosticSpecError("MALFORMED_MULTIPLICITY_KEY",details={"source":source,"key":repr(key),"key_type":type(key).__name__})
        multiplicity=int(key)
        if multiplicity<=0:
            raise DiagnosticSpecError("MALFORMED_MULTIPLICITY_KEY",details={"source":source,"key":repr(key),"reason":"NOT_POSITIVE"})
        if isinstance(count,bool) or not isinstance(count,int) or count<0:
            raise DiagnosticSpecError("MALFORMED_MULTIPLICITY_COUNT",details={"source":source,"key":repr(key),"count":repr(count),"count_type":type(count).__name__})
        if multiplicity in normalized:
            raise DiagnosticSpecError("AMBIGUOUS_MULTIPLICITY_KEYS",details={"source":source,"normalized_key":multiplicity,"keys":[original[multiplicity],repr(key)]})
        normalized[multiplicity]=count; original[multiplicity]=repr(key)
    return normalized


def compare_grouping_reproduction(approved: dict[str, Any], reproduced: dict[str, Any]) -> dict[str, Any]:
    field="groups_by_row_multiplicity"
    expected=normalize_multiplicity_histogram(approved.get(field),source="approved_json")
    actual=normalize_multiplicity_histogram(reproduced.get(field),source="reproduced_memory")
    keys=sorted(set(expected)|set(actual)); missing=sorted(set(expected)-set(actual)); extra=sorted(set(actual)-set(expected))
    differing={str(key):{"expected":expected.get(key),"actual":actual.get(key)} for key in keys if key in expected and key in actual and expected[key]!=actual[key]}
    diagnostics={"field":field,"approved_key_types":sorted({type(key).__name__ for key in (approved.get(field) or {})}),
      "reproduced_key_types":sorted({type(key).__name__ for key in (reproduced.get(field) or {})}),
      "approved_normalized":{str(k):expected[k] for k in sorted(expected)},"reproduced_normalized":{str(k):actual[k] for k in sorted(actual)},
      "missing_multiplicities":missing,"extra_multiplicities":extra,"differing_counts":differing,
      "representation_only":expected==actual and approved.get(field)!=reproduced.get(field)}
    if expected!=actual:
        raise DiagnosticSpecError(f"GROUPING_AUDIT_REPRODUCTION_MISMATCH:{field}",details=diagnostics)
    for key in ("rows","assignments","ticker_date_timing_groups","multiple_observation_groups","cohorts","weights_reconcile_per_cohort"):
        if reproduced.get(key)!=approved.get(key):
            raise DiagnosticSpecError(f"GROUPING_AUDIT_REPRODUCTION_MISMATCH:{key}",details={"field":key,"expected":approved.get(key),"actual":reproduced.get(key)})
    return diagnostics


def descriptive_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Synthetic-testable arithmetic only; callers must pass reviewed frozen records."""
    n=len(records); selected=[r for r in records if not r.get("abstained") and bool(r.get("prediction"))]
    positives=sum(int(r["label"]==1) for r in records); true_positive=sum(int(r["label"]==1) for r in selected)
    precision=true_positive/len(selected) if selected else None; recall=true_positive/positives if positives else None
    return {"denominator":n,"selected":len(selected),"abstained":sum(bool(r.get("abstained")) for r in records),
      "coverage":len(selected)/n if n else None,"precision":precision,"recall":recall,
      "undefined":{"precision":None if selected else "NO_SELECTED_POSITIVES","recall":None if positives else "NO_POSITIVE_LABELS"}}


def equal_fold_aggregate(values: dict[int, float | None]) -> dict[str, Any]:
    defined={fold:value for fold,value in values.items() if value is not None and math.isfinite(float(value))}
    return {"value":sum(map(float,defined.values()))/len(defined) if defined else None,"defined_folds":sorted(defined),
      "undefined_folds":sorted(set(values)-set(defined)),"weight_per_defined_fold":1/len(defined) if defined else None}


def _mean(values: list[float]) -> float | None:
    return sum(values)/len(values) if values else None


def score_candidate_fold(item: dict[str, Any], rows: dict[str, dict[str, Any]], mapping: list[dict[str, Any]]) -> dict[str, Any]:
    """Score one frozen candidate/fold under the reviewed descriptive rules."""
    records=item.get("records") or []; threshold=float(item["decision_threshold"]); target=item.get("target_definition") or {}
    target_name=str(target.get("target_name") or target.get("name") or "label_up_5d")
    train_ids=[str(x) for x in item.get("train_ids") or []]
    train_labels=[float(rows[x][target_name]) for x in train_ids if x in rows and rows[x].get(target_name) in (0,1)]
    prevalence=_mean(train_labels); scored=[]
    weights={x["canonical_observation_id"]:float(x["final_cohort_row_weight"]) for x in mapping
             if x["candidate"]==str(item["model_version"]) and x["fold"]==int(item["fold_index"])}
    cohort_keys={x["canonical_observation_id"]:tuple(x["cohort_key"]) for x in mapping
                 if x["candidate"]==str(item["model_version"]) and x["fold"]==int(item["fold_index"])}
    cohort_candidate=defaultdict(float); cohort_baseline=defaultdict(float); cohort_selected=Counter()
    for record in records:
        identifier=str(record["id"]); row=rows[identifier]; label=float(record["label"]); score=float(record["score"])
        rejected=bool(record.get("abstained") or record.get("risk_rejected") or record.get("rule_rejected"))
        selected=score>=threshold and not rejected; ret=record.get("return",row.get("return_5d")); ret=float(ret) if ret is not None else None
        scored.append({"id":identifier,"label":label,"score":score,"selected":selected,"abstained":bool(record.get("abstained")),"return":ret})
        if ret is not None:
            cohort=cohort_keys[identifier]; weight=weights[identifier]
            cohort_baseline[cohort]+=weight*ret
            cohort_candidate[cohort]+=weight*ret*int(selected)
            cohort_selected[cohort]+=int(selected)
    semantics=str(item.get("score_semantics") or ""); candidate_type=str(item.get("candidate_type") or "classification")
    probability=semantics=="probability" and prevalence is not None
    gross_applicable=candidate_type in {"ranking","selection","ranking_or_selection"}
    eps=1e-15
    brier=_mean([(x["score"]-x["label"])**2 for x in scored]) if probability else None
    baseline_brier=_mean([(prevalence-x["label"])**2 for x in scored]) if probability else None
    log_loss=_mean([-(x["label"]*math.log(min(1-eps,max(eps,x["score"])))+(1-x["label"])*math.log(min(1-eps,max(eps,1-x["score"])))) for x in scored]) if probability else None
    baseline_log_loss=_mean([-(x["label"]*math.log(min(1-eps,max(eps,prevalence)))+(1-x["label"])*math.log(min(1-eps,max(eps,1-prevalence)))) for x in scored]) if probability else None
    classification=descriptive_metrics([{"label":x["label"],"prediction":x["selected"],"abstained":x["abstained"]} for x in scored])
    candidate_gross=_mean(list(cohort_candidate.values())) if gross_applicable else None; baseline_gross=_mean(list(cohort_baseline.values())) if gross_applicable else None
    return {"candidate":str(item["model_version"]),"fold":int(item["fold_index"]),"target":target,"score_semantics":semantics,"candidate_type":candidate_type,
      "threshold":threshold,"denominators":{"validation_observations":len(scored),"training_prevalence_rows":len(train_labels),"gross_cohorts":len(cohort_baseline),"empty_selection_cohorts":sum(v==0 for v in cohort_selected.values())},
      "training_prevalence":prevalence,"probability":{"applicability":"EVALUABLE" if probability else "INAPPLICABLE_SCORE_SEMANTICS_NOT_PROBABILITY_OR_TRAINING_PREVALENCE_UNAVAILABLE","brier":brier,"prevalence_brier":baseline_brier,"signed_difference":brier-baseline_brier if brier is not None else None,
        "log_loss":log_loss,"prevalence_log_loss":baseline_log_loss,"log_loss_signed_difference":log_loss-baseline_log_loss if log_loss is not None else None,
        "calibration":"NOT_EVALUABLE_CALIBRATION_BIN_COUNT_UNSPECIFIED"},
      "classification":classification,
      "gross_endpoint":{"applicability":"EVALUABLE" if gross_applicable else "INAPPLICABLE_CANDIDATE_TYPE_NOT_RANKING_OR_SELECTION","candidate":candidate_gross,"equal_weight_ticker_date_timing_baseline":baseline_gross,
        "signed_difference":candidate_gross-baseline_gross if candidate_gross is not None and baseline_gross is not None else None,"cash_reference":0.0,
        "partial_selection_rule":"each canonical observation retains its preregistered within-group weight; unselected/rejected observations contribute zero cash return"}}


def aggregate_candidate(folds: list[dict[str, Any]]) -> dict[str, Any]:
    paths=(("probability","brier"),("probability","signed_difference"),("classification","precision"),("classification","recall"),("classification","coverage"),("gross_endpoint","candidate"),("gross_endpoint","signed_difference"))
    aggregate={}
    for section,metric in paths:
        aggregate[f"{section}.{metric}"]=equal_fold_aggregate({x["fold"]:x[section].get(metric) for x in folds})
    return aggregate
