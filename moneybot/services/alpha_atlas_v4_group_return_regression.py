"""Frozen-contract group return regression for the Alpha Atlas V4 development set."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np

REGISTRATION_SHA256 = "c3f58f47354f07a5c0aa536344b97fb542fdde0bee1651f86852d3dd69a6469a"
EXPECTED_INPUT_SHA256 = {
    "canonical": "506073994052be852a5a00fc38e239b85e72a50494c3559640ea980fb6a52d9e",
    "plan": "f11257cff0befc1f0b46e4cab9a64678c766b6fe2abdc50b07861a18f7d6933a",
    "manifest": "ea4e55f9faa848219945d7e03c92c7a541645cd4d6df8aa3cfbd0d1334872f15",
}
PLAN_SEMANTIC_SHA256 = "bd60055adc6cb1b3f8b143c6c61031bced01c9151fcb3b67c33f75430106e8e8"
TARGET = "group_gross_split_adjusted_return_5d"
PREDICTION_SEMANTICS = "predicted_return"


class ContractError(ValueError):
    def __init__(self, code: str, details: dict[str, Any] | None = None):
        super().__init__(code); self.code = code; self.details = details or {}


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_bindings(registration: Path, authorization: Path, inputs: dict[str, Path]) -> dict:
    if file_hash(registration) != REGISTRATION_SHA256:
        raise ContractError("REGISTRATION_HASH_MISMATCH")
    auth = json.loads(authorization.read_text())
    if auth.get("registration_sha256") != REGISTRATION_SHA256 or auth.get("execution_authorized") is not True:
        raise ContractError("EXECUTION_NOT_AUTHORIZED")
    report = {}
    for role, expected in EXPECTED_INPUT_SHA256.items():
        actual = file_hash(inputs[role]); report[role] = {"path": str(inputs[role]), "expected_file_byte_sha256": expected, "computed_file_byte_sha256": actual}
        if actual != expected: raise ContractError("FROZEN_INPUT_HASH_MISMATCH", {"role": role, "expected": expected, "actual": actual})
    plan = json.loads(inputs["plan"].read_text())
    embedded = plan.get("plan_sha256")
    recomputed = canonical_hash({k: v for k, v in plan.items() if k != "plan_sha256"})
    report["plan"].update(embedded_semantic_sha256=embedded, independently_recomputed_semantic_sha256=recomputed,
                          expected_semantic_sha256=PLAN_SEMANTIC_SHA256)
    if embedded != PLAN_SEMANTIC_SHA256 or recomputed != PLAN_SEMANTIC_SHA256:
        raise ContractError("SPLIT_PLAN_SEMANTIC_HASH_MISMATCH", report["plan"])
    return report


def _time(value: Any) -> datetime:
    try: parsed=datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception as exc: raise ContractError("INVALID_REQUIRED_TIMESTAMP", {"raw_value": value, "parsing":"datetime.fromisoformat_with_Z_as_UTC"}) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError("TIMEZONE_AMBIGUOUS_TIMESTAMP", {"raw_value":value,"parsing":"explicit_UTC_offset_required"})
    return parsed.astimezone(timezone.utc)


def timestamp_evidence(value: Any) -> dict:
    """Return strict parsing evidence; naive timestamps are never assigned a zone."""
    try:
        parsed=_time(value)
        return {"raw":value,"normalized_utc":parsed.isoformat().replace("+00:00","Z"),"status":"VALID_AWARE_ISO8601",
                "parsing":"datetime.fromisoformat; Z mapped to +00:00; astimezone(UTC)"}
    except ContractError as exc:
        return {"raw":value,"normalized_utc":None,"status":exc.code,"parsing":exc.details.get("parsing")}


def audit_group_timing(assignments: list[tuple[int,str,list[dict]]]) -> dict:
    """Scan every authorized partition using identity/timing metadata only."""
    partitions=[]; all_issues=[]
    for fold,partition,rows in assignments:
        buckets=defaultdict(list)
        for row in rows:
            key=(str(row.get("event_date"))[:10],str(row.get("ticker") or row.get("symbol") or "").upper(),row.get("label_horizon_sessions"),
                 timestamp_evidence(row.get("entry_at"))["normalized_utc"],timestamp_evidence(row.get("exit_at"))["normalized_utc"])
            buckets[key].append(row)
        issues=[]
        for key,members in sorted(buckets.items(),key=lambda x:str(x[0])):
            field_evidence={}
            for field in ("decision_at","feature_cutoff_at","entry_at","exit_at"):
                evidence=[]
                for member in members:
                    item=timestamp_evidence(member.get(field)); item["canonical_observation_id"]=str(member.get("canonical_observation_id")); evidence.append(item)
                if len({x["normalized_utc"] for x in evidence}) != 1 or any(x["status"]!="VALID_AWARE_ISO8601" for x in evidence):
                    field_evidence[field]=evidence
            if field_evidence:
                ids=sorted(str(x["canonical_observation_id"]) for x in members)
                issue={"fold":fold,"partition":partition,"group_key":{"event_date":key[0],"ticker":key[1],"label_horizon_sessions":key[2],"entry_at_utc":key[3],"exit_at_utc":key[4]},
                  "affected_ids":ids,"fields":field_evidence,"label_start_at":[timestamp_evidence(x.get("label_start_at"))|{"canonical_observation_id":str(x["canonical_observation_id"])} for x in members],
                  "timestamp_sources":[{"canonical_observation_id":str(x["canonical_observation_id"]),"decision_at_source":"canonical decision_at",
                    "feature_cutoff_at_source":"canonical feature_cutoff_at","originating_decision_at_min":x.get("originating_decision_at_min"),"originating_decision_at_max":x.get("originating_decision_at_max"),
                    "feature_family_source_at":x.get("feature_family_source_at")} for x in members]}
                issues.append(issue); all_issues.append(issue)
        affected={i for issue in issues for i in issue["affected_ids"]}
        partitions.append({"fold":fold,"partition":partition,"rows_scanned":len(rows),"groups_scanned":len(buckets),"affected_groups":len(issues),"affected_unique_rows":len(affected)})
    return {"schema_version":"alpha-atlas-v4-group-return-regression-timing-audit.v1","scope":"authorized_development_metadata_only",
      "outcomes_read":False,"holdout_content_access":False,"partitions":partitions,"affected_group_assignments":len(all_issues),"issues":all_issues}


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError): return None


def stable_identity(key: tuple, ids: list[str]) -> str:
    return canonical_hash([*key, sorted(ids)])


def construct_groups(rows: Iterable[dict], features: list[str], *, outcomes_allowed: bool) -> list[dict]:
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    seen = set()
    for row in rows:
        identifier = str(row.get("canonical_observation_id") or "")
        if not identifier or identifier in seen: raise ContractError("MISSING_OR_DUPLICATE_CANONICAL_ID", {"id": identifier})
        seen.add(identifier)
        ticker = str(row.get("ticker") or row.get("symbol") or "").upper()
        entry_evidence=timestamp_evidence(row.get("entry_at")); exit_evidence=timestamp_evidence(row.get("exit_at"))
        if entry_evidence["status"]!="VALID_AWARE_ISO8601" or exit_evidence["status"]!="VALID_AWARE_ISO8601": raise ContractError("INVALID_GROUP_KEY_TIMESTAMP",{"id":identifier,"entry_at":entry_evidence,"exit_at":exit_evidence})
        key = (str(row.get("event_date"))[:10], ticker, row.get("label_horizon_sessions"), entry_evidence["normalized_utc"], exit_evidence["normalized_utc"])
        if not ticker or key[2] != 5: raise ContractError("INVALID_GROUP_KEY", {"id": identifier, "key": key})
        buckets[key].append(row)
    groups=[]
    for key, members in sorted(buckets.items()):
        ids=sorted(str(x["canonical_observation_id"]) for x in members)
        for field in ("event_date", "label_horizon_sessions"):
            values={str(x.get(field)) for x in members}
            if len(values) != 1 or "None" in values: raise ContractError("INCOMPATIBLE_GROUP_TIMING", {"ids": ids, "field": field})
        normalized={}
        for field in ("decision_at","feature_cutoff_at","entry_at","exit_at"):
            evidence=[timestamp_evidence(x.get(field))|{"canonical_observation_id":str(x["canonical_observation_id"])} for x in members]
            if any(x["status"]!="VALID_AWARE_ISO8601" for x in evidence) or len({x["normalized_utc"] for x in evidence})!=1:
                raise ContractError("INCOMPATIBLE_GROUP_TIMING",{"ids":ids,"field":field,"group_key":[*key],"timestamp_values":evidence})
            normalized[field]=evidence[0]["normalized_utc"]
        cutoff=_time(normalized["feature_cutoff_at"]); decision=_time(normalized["decision_at"]); entry=_time(key[3]); exit_at=_time(key[4])
        if not cutoff <= decision < entry < exit_at: raise ContractError("TEMPORAL_ORDER_VIOLATION", {"ids": ids})
        for member in members:
            for source in (member.get("feature_family_source_at") or {}).values():
                if _time(source) > cutoff: raise ContractError("FEATURE_SOURCE_AFTER_CUTOFF", {"id": member["canonical_observation_id"]})
        values=[]; finite_counts=[]; missing_counts=[]
        for feature in features:
            present=[v for v in (_finite(x.get(feature)) for x in members) if v is not None]
            values.append(sum(present)/len(present) if present else None); finite_counts.append(len(present)); missing_counts.append(len(members)-len(present))
        target=None
        if outcomes_allowed:
            returns=[_finite(x.get("return_5d")) for x in members]
            if any(x is None for x in returns): raise ContractError("MISSING_GROUP_OUTCOME", {"ids": ids})
            target=float(sum(returns)/len(returns))
        groups.append({"stable_group_identity":stable_identity(key,ids),"event_date":key[0],"ticker":key[1],"label_horizon_sessions":5,
          "decision_at":normalized["decision_at"],"feature_cutoff_at":normalized["feature_cutoff_at"],"entry_at":key[3],"exit_at":key[4],
          "source_canonical_observation_ids":ids,"member_count":len(ids),"features":values,"feature_finite_counts":finite_counts,
          "feature_missing_counts":missing_counts,"training_weight":1.0,"target":target})
    return groups


def check_partitions(train: list[dict], validation: list[dict]) -> None:
    train_ids={x["stable_group_identity"] for x in train}; valid_ids={x["stable_group_identity"] for x in validation}
    if train_ids & valid_ids: raise ContractError("CROSS_PARTITION_GROUP")
    operational=lambda x:(x["event_date"],x["ticker"],x["label_horizon_sessions"],x["entry_at"],x["exit_at"])
    if {operational(x) for x in train} & {operational(x) for x in validation}: raise ContractError("CROSS_PARTITION_GROUP")
    source_train={i for x in train for i in x["source_canonical_observation_ids"]}; source_valid={i for x in validation for i in x["source_canonical_observation_ids"]}
    if source_train & source_valid: raise ContractError("DUPLICATE_GROUP_ASSIGNMENT")
    if train and validation and max(_time(x["exit_at"]) for x in train) >= min(_time(x["decision_at"]) for x in validation):
        raise ContractError("PURGE_OR_EMBARGO_VIOLATION")


def fit_ridge(train: list[dict], feature_names: list[str], *, alpha: float = 1.0) -> tuple[dict, np.ndarray]:
    if alpha != 1.0: raise ContractError("UNAUTHORIZED_CONFIGURATION")
    x=np.asarray([[np.nan if v is None else v for v in g["features"]] for g in train],dtype=np.float64)
    y=np.asarray([g["target"] for g in train],dtype=np.float64)
    if x.ndim != 2 or x.shape[1] != len(feature_names) or not np.all(np.isfinite(y)): raise ContractError("INVALID_TRAINING_MATRIX")
    medians=np.empty(x.shape[1]); all_missing=[]
    for j in range(x.shape[1]):
        finite=x[np.isfinite(x[:,j]),j]
        if not len(finite): medians[j]=0.0; all_missing.append(feature_names[j])
        else: medians[j]=np.median(finite)
        x[~np.isfinite(x[:,j]),j]=medians[j]
    means=x.mean(axis=0); stds=x.std(axis=0); zero=[feature_names[j] for j,v in enumerate(stds) if not math.isfinite(v) or abs(v)<=1e-12]
    stds[~np.isfinite(stds)|(np.abs(stds)<=1e-12)]=1.0
    z=(x-means)/stds; y_mean=float(y.mean()); a=z.T@z/len(y)+np.eye(z.shape[1],dtype=np.float64)
    b=z.T@(y-y_mean)/len(y); solver="direct_solve"
    try: beta=np.linalg.solve(a,b)
    except np.linalg.LinAlgError: beta=np.linalg.pinv(a,rcond=1e-12)@b; solver="svd_pseudoinverse_rcond_1e-12"
    state={"features":feature_names,"alpha":1.0,"objective":"mean_squared_error_plus_alpha_l2","intercept":y_mean,
      "intercept_penalized":False,"medians":medians.tolist(),"means":means.tolist(),"population_standard_deviations":stds.tolist(),
      "coefficients":beta.tolist(),"all_missing_training_features":all_missing,"zero_variance_training_features":zero,"solver":solver,
      "training_groups":len(train),"training_weight_per_group":1.0,"dtype":"float64"}
    state["canonical_json_sha256"]=canonical_hash(state)
    return state,beta


def predict(groups: list[dict], state: dict) -> list[dict]:
    med=np.asarray(state["medians"]); means=np.asarray(state["means"]); std=np.asarray(state["population_standard_deviations"]); beta=np.asarray(state["coefficients"])
    output=[]
    for group in groups:
        x=np.asarray([np.nan if v is None else v for v in group["features"]],dtype=np.float64); x[~np.isfinite(x)]=med[~np.isfinite(x)]
        score=float(state["intercept"]+((x-means)/std)@beta)
        output.append({k:group[k] for k in ("stable_group_identity","source_canonical_observation_ids","event_date","ticker","label_horizon_sessions","decision_at","feature_cutoff_at","entry_at","exit_at","member_count")} | {
          "effective_training_target_name":TARGET,"effective_training_target_value_for_training_rows_only":None,"prediction":score,
          "prediction_semantics":PREDICTION_SEMANTICS,"evaluation_return_name":TARGET,"evaluation_return_value_for_released_validation_outcomes_only":None,
          "eligibility_disposition":"ELIGIBLE","eligibility_reasons":[]})
    return output


def select(predictions: list[dict], fold: int) -> tuple[list[dict],list[dict]]:
    cohorts=defaultdict(list)
    for row in predictions: cohorts[(row["event_date"],row["label_horizon_sessions"],row["entry_at"],row["exit_at"])].append(row)
    selected=[]; cohort_rows=[]
    for key, rows in sorted(cohorts.items()):
        ordered=sorted(rows,key=lambda x:(-x["prediction"],x["ticker"],x["stable_group_identity"])); count=min(5,len(ordered))
        for rank,row in enumerate(ordered,1):
            item=dict(row,fold=fold,rank=rank,selected=rank<=count,selected_group_weight=1/count if rank<=count and count else 0.0,
                      eligible_baseline_group_weight=1/len(ordered) if ordered else None)
            selected.append(item)
        cohort_rows.append({"fold":fold,"cohort_key":[fold,*key],"eligible_group_count":len(ordered),"selected_group_count":count,"cash_weight":1.0 if not ordered else 0.0})
    return selected,cohort_rows


def evaluate(selections:list[dict], validation_groups:list[dict], fold:int)->dict:
    outcomes={x["stable_group_identity"]:x["target"] for x in validation_groups}
    if set(outcomes)!=set(x["stable_group_identity"] for x in selections) or any(v is None or not math.isfinite(v) for v in outcomes.values()):
        return {"fold":fold,"status":"BLOCKED","reason_code":"MISSING_VALIDATION_OUTCOME","selections_unchanged":True}
    cohorts=defaultdict(list)
    for x in selections: cohorts[(x["event_date"],x["label_horizon_sessions"],x["entry_at"],x["exit_at"])].append(x)
    results=[]
    for key,rows in sorted(cohorts.items()):
        chosen=[x for x in rows if x["selected"]]
        selected_return=sum(outcomes[x["stable_group_identity"]] for x in chosen)/len(chosen) if chosen else 0.0
        baseline=sum(outcomes[x["stable_group_identity"]] for x in rows)/len(rows) if rows else None
        results.append({"cohort_key":[fold,*key],"selected_gross_return":selected_return,"baseline_gross_return":baseline,
                        "selected_minus_baseline":selected_return-baseline,"selected_groups":len(chosen),"eligible_groups":len(rows),
                        "selected_member_count":sum(x["member_count"] for x in chosen)})
    mean=lambda field:sum(x[field] for x in results)/len(results)
    return {"fold":fold,"status":"COMPLETE","cohort_count":len(results),"cohorts":results,"selected_gross_return":mean("selected_gross_return"),
            "baseline_gross_return":mean("baseline_gross_return"),"selected_minus_baseline":mean("selected_minus_baseline"),"empty_cohort_count":0}
