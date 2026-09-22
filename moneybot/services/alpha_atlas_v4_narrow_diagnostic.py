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
                 expected: str | None = None, actual: str | None = None):
        super().__init__(code); self.code=code; self.role=role; self.hash_type=hash_type
        self.expected=expected; self.actual=actual


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
