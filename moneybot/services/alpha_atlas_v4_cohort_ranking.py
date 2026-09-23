"""Research-only arithmetic for the frozen Alpha Atlas V4 cohort-ranking proposal.

This module deliberately has no data-loading or production-routing integration.  A
caller must first validate the frozen evidence.  In particular, ``REAL_SCORING``
is a source-code gate, rather than a workflow input or environment variable.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
from typing import Any

PROPOSAL_SHA256 = "000a0f8e2ba0fbaf563eb64dc3589fe4b2ae0695fd6c63fa8c536675d9b26f4d"
EVIDENCE_SHA256 = "206e86dcfd2fa25edfbb8b3201e84e342da55ace668b4e0c6d4d31a4132912b2"
EXPECTED_LINEAGE = {"diagnostic_run": "35795768048-1", "audit_run": "35788348286-1", "source_registration": "35755237312-1"}
REAL_SCORING_ENABLED = False
CANDIDATES = {
    "challenger-ranking-lane-full-v1": "ranking_score_not_buy_probability",
    "challenger-ranking-lane-recent-half-v1": "ranking_score_not_buy_probability",
    "challenger-ranking-top5-model-v1": "daily_top5_ordinal_probability",
}
COHORT_FIELDS = ("candidate", "fold", "event_date", "label_horizon_sessions", "entry_at", "exit_at")


class RankingContractError(ValueError):
    """A closed-gate result with a stable, reviewable reason code."""
    def __init__(self, code: str, details: dict[str, Any] | None = None):
        super().__init__(code)
        self.code, self.details = code, details or {}


def _sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise RankingContractError("MALFORMED_OR_MISSING_INPUT", {"path": str(path), "error": type(exc).__name__}) from exc


def _json(path: Path, role: str) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RankingContractError("MALFORMED_OR_MISSING_JSON", {"role": role, "error": type(exc).__name__}) from exc


def validate_frozen_contract(proposal: Path, evidence: Path, provenance: Path) -> dict[str, Any]:
    """Validate byte pins and the saved lineage before any scoring can occur."""
    actual = {"proposal": _sha(proposal), "evidence": _sha(evidence)}
    expected = {"proposal": PROPOSAL_SHA256, "evidence": EVIDENCE_SHA256}
    if actual != expected:
        raise RankingContractError("FROZEN_HASH_MISMATCH", {"expected": expected, "actual": actual})
    spec, diagnostic, lineage = _json(proposal, "proposal"), _json(evidence, "evidence"), _json(provenance, "provenance")
    if spec.get("schema_version") != "alpha-atlas-v4-ranking-cohort-relative-experiment-proposal.v1":
        raise RankingContractError("PROPOSAL_SCHEMA_MISMATCH")
    if diagnostic.get("source_registration", {}).get("run") != EXPECTED_LINEAGE["source_registration"]:
        raise RankingContractError("EVIDENCE_SOURCE_REGISTRATION_MISMATCH")
    observed = {
        "diagnostic_run": lineage.get("completed_diagnostic", {}).get("run"),
        "audit_run": lineage.get("approved_grouping_audit", {}).get("run"),
        "source_registration": lineage.get("source_registration"),
    }
    if observed != EXPECTED_LINEAGE or lineage.get("specification_sha256") != EVIDENCE_SHA256:
        raise RankingContractError("AUDIT_LINEAGE_MISMATCH", {"expected": EXPECTED_LINEAGE, "actual": observed})
    return {"status": "VALIDATED", "verified_hashes": actual, "lineage": observed,
            "frozen_inputs": spec["frozen_inputs"], "real_scoring_enabled": REAL_SCORING_ENABLED}


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise RankingContractError("MISSING_OR_NONFINITE_FIELD", {"field": field})
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RankingContractError("MISSING_OR_NONFINITE_FIELD", {"field": field}) from exc
    if not math.isfinite(result):
        raise RankingContractError("MISSING_OR_NONFINITE_FIELD", {"field": field})
    return result


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def score_candidate_fold(records: list[dict[str, Any]], *, candidate: str, fold: int,
                         training_labels: list[int] | None = None) -> dict[str, Any]:
    """Apply fixed top-five ranking to one candidate/fold synthetic or prevalidated slice.

    The proposal does not explicitly settle partially passing repeated-observation
    groups.  The conservative resolution is all-members-must-pass: if any member
    fails any saved gate, the whole ticker group is ineligible.  All observations
    (including rejected ones) remain in the group's averaging denominator.
    """
    if candidate not in CANDIDATES:
        raise RankingContractError("CANDIDATE_NOT_IN_FROZEN_ROSTER")
    if not records:
        return _fold_result(candidate, fold, [], training_labels)
    seen: set[str] = set(); groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for raw in records:
        row = dict(raw)
        required = ("canonical_observation_id", "ticker", "event_date", "label_horizon_sessions", "entry_at", "exit_at", "score", "return", "label", "abstained", "risk_rejected", "rule_rejected")
        missing = [key for key in required if key not in row or row[key] is None]
        if missing:
            raise RankingContractError("MISSING_REQUIRED_EVIDENCE", {"fields": missing})
        identifier = str(row["canonical_observation_id"])
        if identifier in seen:
            raise RankingContractError("DUPLICATE_CANONICAL_OBSERVATION_ID", {"id": identifier})
        seen.add(identifier)
        for gate in ("abstained", "risk_rejected", "rule_rejected"):
            if type(row[gate]) is not bool:
                raise RankingContractError("MALFORMED_GATE", {"id": identifier, "field": gate})
        row["score"] = _finite(row["score"], "score")
        row["return"] = _finite(row["return"], "return")
        if row["label"] not in (0, 1):
            raise RankingContractError("MALFORMED_LABEL", {"id": identifier})
        cohort = (candidate, int(fold), str(row["event_date"]), int(row["label_horizon_sessions"]), str(row["entry_at"]), str(row["exit_at"]))
        groups[cohort + (str(row["ticker"]).upper(),)].append(row)
    cohorts: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for key, members in groups.items():
        eligible = all(not (m["abstained"] or m["risk_rejected"] or m["rule_rejected"]) for m in members)
        cohorts[key[:-1]].append({"ticker": key[-1], "ids": sorted(str(m["canonical_observation_id"]) for m in members),
            "observations": len(members), "eligible": eligible, "score": _mean([m["score"] for m in members]),
            "return": _mean([m["return"] for m in members]), "positive": _mean([float(m["label"]) for m in members])})
    details=[]
    for cohort, ticker_groups in sorted(cohorts.items()):
        eligible=[g for g in ticker_groups if g["eligible"]]
        ranked=sorted(eligible,key=lambda g:(-g["score"],g["ticker"],g["ids"]))
        selected=ranked[:5]
        selected_return=_mean([g["return"] for g in selected]) if selected else 0.0
        baseline=_mean([g["return"] for g in eligible])
        precision=_mean([g["positive"] for g in selected]) if selected else None
        details.append({"cohort_key": list(cohort), "eligible_group_count": len(eligible), "selected_group_count": len(selected),
            "abstained_group_count": len(ticker_groups)-len(eligible), "empty_selection": not selected,
            "selected_tickers": [g["ticker"] for g in selected], "selected_gross_return": selected_return,
            "eligible_baseline_return": baseline, "signed_difference": selected_return-baseline if baseline is not None else None,
            "difference_versus_cash": selected_return, "top_k_positive_label_precision": precision,
            "groups": ticker_groups, "selected_group_weight": 1/len(selected) if selected else None,
            "eligible_baseline_group_weight": 1/len(eligible) if eligible else None})
    return _fold_result(candidate, fold, details, training_labels)


def _fold_result(candidate: str, fold: int, cohorts: list[dict[str, Any]], training_labels: list[int] | None) -> dict[str, Any]:
    labels = training_labels or []
    if any(x not in (0, 1) for x in labels):
        raise RankingContractError("MALFORMED_TRAINING_LABEL")
    metric_names=("selected_gross_return","eligible_baseline_return","signed_difference","difference_versus_cash","top_k_positive_label_precision")
    metrics={name:_mean([float(x[name]) for x in cohorts if x[name] is not None]) for name in metric_names}
    return {"candidate":candidate,"score_semantics":CANDIDATES[candidate],"fold":int(fold),"status":"EVALUABLE",
        "group_eligibility_rule":"all_members_must_pass_saved_gates","group_averaging_denominator":"all_canonical_observations_in_group",
        "training_prevalence":_mean([float(x) for x in labels]),"training_prevalence_denominator":len(labels),
        "cohort_count":len(cohorts),"cohort_weight":1/len(cohorts) if cohorts else None,"empty_cohort_count":sum(x["empty_selection"] for x in cohorts),
        "metrics":metrics,"metric_denominators":{name:sum(x[name] is not None for x in cohorts) for name in metric_names},"cohorts":cohorts}


def aggregate_folds(folds: list[dict[str, Any]]) -> dict[str, Any]:
    """Equal-weight exactly defined folds; undefined metrics remain explicit."""
    if len({x["fold"] for x in folds}) != len(folds):
        raise RankingContractError("DUPLICATE_FOLD")
    names=("selected_gross_return","eligible_baseline_return","signed_difference","difference_versus_cash","top_k_positive_label_precision")
    result={}
    for name in names:
        defined=[x for x in folds if x["metrics"].get(name) is not None]
        result[name]={"value":_mean([x["metrics"][name] for x in defined]),"defined_folds":[x["fold"] for x in defined],
            "undefined_folds":[x["fold"] for x in folds if x not in defined],"fold_weight":1/len(defined) if defined else None,
            "source_denominators":{str(x["fold"]):x["metric_denominators"][name] for x in folds}}
    return {"fold_count":len(folds),"metrics":result,"reconciles":all(v["value"] is None or math.isclose(v["value"],sum(next(x for x in folds if x["fold"]==f)["metrics"][k] for f in v["defined_folds"])/len(v["defined_folds"])) for k,v in result.items())}
