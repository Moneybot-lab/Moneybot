"""Register (without executing) a frozen-development baseline comparison."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

SCHEMA = "alpha-atlas-v4-development-baseline-registration.v1"
EXPECTED = {
    "canonical": "506073994052be852a5a00fc38e239b85e72a50494c3559640ea980fb6a52d9e",
    "plan": "bd60055adc6cb1b3f8b143c6c61031bced01c9151fcb3b67c33f75430106e8e8",
    "manifest": "ea4e55f9faa848219945d7e03c92c7a541645cd4d6df8aa3cfbd0d1334872f15",
    "capture": "454febde2e14ca8a916222d86a6872db1c5e790a29429f2db6393d828e87e434",
}
KAII_DATES = {"2023-01-19", "2023-02-17", "2023-02-24"}
UNRESOLVED_TICKERS = {"BWINA", "BWINB", "PTVCA", "PTVCB", "KHD", "MFCB", "MIL", "TRY", "TRY.B",
                      "FITBM", "FITBO", "HUB.A", "HUB.B", "ANDV", "TSO", "TSOW", "FRM", "XNR", "KV.A", "KV.B"}
ID_RE = re.compile(rb'"canonical_observation_id"\s*:\s*"([^"\\]+)"')

class RegistrationError(ValueError):
    def __init__(self, code: str, details: dict[str, Any] | None = None):
        super().__init__(code); self.code = code; self.details = details or {}

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def _iso(value: Any) -> datetime:
    try: return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception as exc: raise RegistrationError("INVALID_OR_MISSING_TIMESTAMP", {"value": value}) from exc

def _fold_ids(fold: dict, kind: str) -> list[str]:
    return [str(x) for x in fold.get(f"{kind}_canonical_observation_ids", fold.get(f"{kind}_ids", []))]

def _safe_id(line: bytes) -> str:
    found = ID_RE.findall(line)
    if len(found) != 1: raise RegistrationError("CANONICAL_ID_UNAVAILABLE_WITHOUT_LOADING_ROW")
    return found[0].decode("unicode_escape")

def _load_development_rows(path: Path, development: set[str]) -> tuple[dict[str, dict], dict[str, int]]:
    rows: dict[str, dict] = {}; all_ids: set[str] = set(); counts = Counter()
    with path.open("rb") as handle:
        for line_number, line in enumerate(handle, 1):
            identifier = _safe_id(line)
            if identifier in all_ids: raise RegistrationError("DUPLICATE_CANONICAL_ID", {"id": identifier, "line": line_number})
            all_ids.add(identifier)
            if identifier not in development: continue  # holdout content is never decoded or retained
            row = json.loads(line); rows[identifier] = row
            counts["development_rows_loaded"] += 1
    missing = development - rows.keys()
    if missing: raise RegistrationError("DEVELOPMENT_IDS_MISSING", {"count": len(missing), "sample": sorted(missing)[:10]})
    return rows, {"source_ids": len(all_ids), **counts}

def _provenance(path: Path, role: str, source: dict) -> dict:
    expected = None if role == "plan" else EXPECTED.get(role)
    parts=path.parts; anchors=[i for i,value in enumerate(parts) if value in {"source-track-b","source-diagnostics"}]
    portable=str(Path(*parts[anchors[-1]:])) if anchors else path.name
    return {"role": role, "path": portable, "bytes": path.stat().st_size, "sha256_computed": sha(path),
            "expected_sha256": expected, "hash_matches_expected": expected in {None, sha(path)},
            "embedded_content_sha256_expected": EXPECTED["plan"] if role == "plan" else None,
            "source_run": source.get("source_run"), "source_attempt": source.get("source_attempt"),
            "source_head_sha": source.get("source_head_sha"), "artifact_id": source.get("artifact_id"),
            "artifact_name": source.get("artifact_name"), "artifact_digest": source.get("artifact_digest"),
            "provenance_status": "NEWLY_COMPUTED_AND_COMPARED_TO_REPOSITORY_EXPECTATION"}

def _dependencies(row: dict) -> list[str]:
    values = set()
    for key in ("event_date", "entry_session_date", "exit_session_date"):
        if row.get(key): values.add(str(row[key])[:10])
    for key in ("feature_cutoff_at", "decision_at", "entry_at", "exit_at", "label_start_at"):
        if row.get(key): values.add(str(row[key])[:10])
    for item in row.get("valuation_path") or []:
        if isinstance(item, dict) and item.get("session"): values.add(str(item["session"])[:10])
    start = min(values) if values else None; end = max(values) if values else None
    return sorted(values | ({d for d in KAII_DATES if start and start <= d <= end}))

def register(canonical: Path, plan_path: Path, manifest_path: Path, capture_path: Path,
             source_provenance: dict[str, dict]) -> dict[str, Any]:
    files = [_provenance(canonical, "canonical", source_provenance["canonical"]),
             _provenance(plan_path, "plan", source_provenance["plan"]),
             _provenance(manifest_path, "manifest", source_provenance["manifest"]),
             _provenance(capture_path, "capture", source_provenance["capture"])]
    mismatches = [x["role"] for x in files if not x["hash_matches_expected"]]
    if mismatches: raise RegistrationError("INPUT_HASH_MISMATCH", {"roles": mismatches, "files": files})
    plan=json.loads(plan_path.read_text()); manifest=json.loads(manifest_path.read_text()); capture=json.loads(capture_path.read_text())
    if plan.get("plan_sha256") != EXPECTED["plan"]: raise RegistrationError("PLAN_CONTENT_HASH_MISMATCH")
    development=set(map(str, plan.get("train_canonical_observation_ids") or [])); holdout=set(map(str, plan.get("test_canonical_observation_ids") or []))
    if not development or not holdout: raise RegistrationError("SPLIT_MEMBERSHIP_MISSING")
    if development & holdout: raise RegistrationError("DEVELOPMENT_HOLDOUT_ID_OVERLAP", {"count":len(development & holdout)})
    rows, canonical_counts = _load_development_rows(canonical, development)
    folds=[f for f in manifest.get("walk_forward_windows",[]) if f.get("usable")]
    if not folds: raise RegistrationError("NO_USABLE_FOLDS")
    fold_map={int(f["fold_index"]):f for f in folds}; candidates={str(x["model_version"]):x for x in manifest.get("challengers",[])}
    expected_pairs={(c, i) for c in candidates for i in fold_map}; seen_pairs=set(); predicted_assignments=Counter(); failures=[]
    fold_reports=[]
    for idx, fold in fold_map.items():
        train=set(_fold_ids(fold,"train")); validation=set(_fold_ids(fold,"validation"))
        latest=max((_iso(rows[x]["exit_at"]) for x in train), default=None); earliest=min((_iso(rows[x]["decision_at"]) for x in validation), default=None)
        fr={"fold_index":idx,"train_count":len(train),"validation_count":len(validation),
            "train_validation_overlap_count":len(train&validation),"nondevelopment_count":len((train|validation)-development),
            "latest_train_exit_at":latest.isoformat() if latest else None,"earliest_validation_decision_at":earliest.isoformat() if earliest else None,
            "purge_passed":bool(latest and earliest and latest < earliest),"embargo_sessions":fold.get("embargo_sessions"),
            "embargo_dates":fold.get("embargo_session_dates"),"timing_boundary_reported":fold.get("timing_boundary_passed")}
        if fr["train_validation_overlap_count"]: failures.append("TRAIN_VALIDATION_OVERLAP")
        if fr["nondevelopment_count"]: failures.append("FOLD_CONTAINS_NONDEVELOPMENT_ID")
        if not fr["purge_passed"]: failures.append("PURGE_OR_LABEL_WINDOW_VIOLATION")
        if fold.get("complete_group_integrity") is not True or fold.get("invalid_session_group_count") not in (0,None): failures.append("EMBARGO_OR_GROUP_INTEGRITY_UNVERIFIED")
        fold_reports.append(fr)
    if not isinstance(capture,list): raise RegistrationError("CAPTURE_NOT_ARRAY")
    for item in capture:
        pair=(str(item.get("model_version")),int(item.get("fold_index",-1)))
        if pair in seen_pairs: failures.append("DUPLICATE_CANDIDATE_FOLD_CAPTURE")
        seen_pairs.add(pair); fold=fold_map.get(pair[1]); records=item.get("records") or []
        if pair not in expected_pairs or not fold: failures.append("UNEXPECTED_CANDIDATE_FOLD_CAPTURE"); continue
        planned_train=set(_fold_ids(fold,"train")); planned_validation=set(_fold_ids(fold,"validation"))
        if set(map(str,item.get("train_ids") or [])) != planned_train or set(map(str,item.get("validation_ids") or [])) != planned_validation: failures.append("OOF_FOLD_MEMBERSHIP_MISMATCH")
        record_ids=[str(x.get("id")) for x in records]
        if len(record_ids)!=len(set(record_ids)): failures.append("DUPLICATE_OOF_RECORD")
        if set(record_ids)!=planned_validation: failures.append("MISSING_OR_UNEXPECTED_OOF_RECORD")
        target=item.get("target_definition") or {}; target_name=str(target.get("target_name") or target.get("name") or manifest.get("target_column") or "")
        return_name=str(target.get("return_column") or "return_5d")
        threshold=item.get("decision_threshold")
        if not isinstance(threshold,(int,float)) or not math.isfinite(float(threshold)): failures.append("INVALID_OR_MISSING_FROZEN_THRESHOLD")
        if not target_name or not target.get("forecast_horizon") and not target.get("horizon_days") and not target.get("horizon_sessions"): failures.append("TARGET_OR_HORIZON_SEMANTICS_MISSING")
        for record in records:
            identifier=str(record.get("id")); row=rows.get(identifier)
            if row is None: continue
            if target_name in row and int(record.get("label")) != int(row[target_name]): failures.append("OOF_LABEL_MISMATCH")
            if record.get("security") is not None and str(record.get("security")).upper()!=str(row.get("symbol")).upper(): failures.append("OOF_SECURITY_MISMATCH")
            if record.get("session") is not None and str(record.get("session"))[:10]!=str(row.get("event_date"))[:10]: failures.append("OOF_SESSION_MISMATCH")
            if record.get("return") is not None and return_name in row and not math.isclose(float(record["return"]),float(row[return_name]),rel_tol=0,abs_tol=1e-12): failures.append("OOF_RETURN_MISMATCH")
        for identifier in record_ids: predicted_assignments[(pair[0],identifier)]+=1
        if set(record_ids)&holdout: failures.append("FINAL_HOLDOUT_OVERLAP")
    if seen_pairs != expected_pairs: failures.append("MISSING_OR_UNEXPECTED_CANDIDATE_FOLD")
    if any(v!=1 for v in predicted_assignments.values()): failures.append("MULTIPLY_ASSIGNED_PREDICTION")
    # Materiality: direct ticker/date and dependency-window evidence; typed mapping absence remains UNKNOWN.
    affected=[]; missing_prices=[]; valuation_unknown=[]
    for identifier,row in rows.items():
        symbol=str(row.get("symbol") or "").upper(); deps=_dependencies(row)
        fold_ids=[i for i,f in fold_map.items() if identifier in set(_fold_ids(f,"train"))|set(_fold_ids(f,"validation"))]
        if symbol in {"FITBM","FITBO"}: affected.append({"canonical_id":identifier,"folds":fold_ids,"symbol":symbol,"matching_basis":"DIRECT_TICKER","issue":"FITBM_FITBO_CLASSIFICATION","dependency_dates":deps,"impacted_claim":"universe eligibility"})
        if symbol=="KAII" and KAII_DATES & set(deps): affected.append({"canonical_id":identifier,"folds":fold_ids,"symbol":symbol,"matching_basis":"DIRECT_TICKER_AND_DEPENDENCY_WINDOW","issue":"KAII_GAP_DATE","dependency_dates":sorted(KAII_DATES & set(deps)),"impacted_claim":"return/price/valuation"})
        if symbol in UNRESOLVED_TICKERS: affected.append({"canonical_id":identifier,"folds":fold_ids,"symbol":symbol,"matching_basis":"DIRECT_TICKER","issue":"UNRESOLVED_EFFECTIVE_DATED_IDENTITY","dependency_dates":deps,"impacted_claim":"identity/universe join"})
        if row.get("entry_price") is None or row.get("exit_price") is None: missing_prices.append(identifier)
        if not row.get("valuation_certification") and not row.get("valuation_path_policy_version"): valuation_unknown.append(identifier)
    mapping_status=(source_provenance.get("materiality_evidence") or {}).get("unresolved_identity_typed_identifier_map_status")
    typed_status=("CONFIRMED_INTERSECTION" if affected else
                  "VERIFIED_ZERO_DIRECT_AND_TYPED" if mapping_status=="VERIFIED_COMPLETE_NO_MATCH" else
                  "UNKNOWN_UNRESOLVED_CASE_TYPED_IDENTIFIER_MAP_UNAVAILABLE")
    materiality={"status":"BLOCKED" if affected else ("UNKNOWN" if typed_status.startswith("UNKNOWN") else "CLEAR_FOR_EXACT_INPUT"),
        "unique_affected_rows":len({x["canonical_id"] for x in affected}),"dependency_occurrences":len(affected),"affected_rows":affected,
        "typed_identifier_mapping_status":typed_status,"missing_entry_or_exit_price_rows":len(set(missing_prices)),
        "missing_entry_or_exit_price_ids":sorted(set(missing_prices)),"uncertified_valuation_path_rows":len(set(valuation_unknown)),
        "valuation_scope":"Endpoint metrics may use frozen returns; total return and drawdown remain NOT_EVALUABLE without certified paths.",
        "kaii_supplement":"human odd-lot rule confirmed; 52-quotes/no-trades DATE_ATTRIBUTION_UNCONFIRMED; historical 2023 applicability unknown"}
    if affected: failures.append("UNRESOLVED_HISTORICAL_DEPENDENCY_INTERSECTION")
    if typed_status.startswith("UNKNOWN"): failures.append("TYPED_IDENTITY_MATERIALITY_UNKNOWN")
    interval=sorted(str(r.get("feature_cutoff_at")) for r in rows.values() if r.get("feature_cutoff_at"))
    cost_versions=sorted({str(r.get("execution_cost_policy_version")) for r in rows.values() if r.get("execution_cost_policy_version")})
    if len(cost_versions)>1: failures.append("MIXED_EXECUTION_COST_POLICIES")
    fold_count=len(folds)
    if fold_count != 3: failures.append("DOCUMENTED_ADVANTAGE_RULE_FOLD_COUNT_CONFLICT")
    integrity={"status":"BLOCKED" if failures else "VERIFIED","reason_codes":sorted(set(failures)),
        "canonical_counts":canonical_counts,"development_id_count":len(development),"holdout_id_count_metadata_only":len(holdout),
        "candidate_count":len(candidates),"fold_count":fold_count,"expected_candidate_fold_pairs":len(expected_pairs),
        "observed_candidate_fold_pairs":len(seen_pairs),"prediction_assignments":len(predicted_assignments),
        "final_holdout_overlap_count":sum(1 for c,i in predicted_assignments if i in holdout),"final_holdout_content_loaded":False,"folds":fold_reports}
    candidate_rules=[]
    for name,specrow in sorted(candidates.items()):
        captures=[x for x in capture if str(x.get("model_version"))==name]
        candidate_rules.append({"model_version":name,"candidate_lane":specrow.get("candidate_lane") or (specrow.get("spec") or {}).get("candidate_lane"),
            "spec":specrow.get("spec"),"thresholds":sorted({x.get("decision_threshold") for x in captures},key=lambda x:str(x)),
            "score_semantics":sorted({str(x.get("score_semantics")) for x in captures}),
            "target_definitions":list({json.dumps(x.get("target_definition"),sort_keys=True) for x in captures})})
    registration={"schema_version":SCHEMA,"status":"REGISTERED_BLOCKED" if integrity["status"]=="BLOCKED" or materiality["status"]!="CLEAR_FOR_EXACT_INPUT" else "REGISTERED_READY_FOR_REVIEW",
      "performance_comparison_executed":False,"development_scope":{"interval_source":"derived_from_development_feature_cutoff_at","start":interval[0][:10] if interval else None,"end":interval[-1][:10] if interval else None,"fold_count":fold_count},
      "inputs":files,"candidates":candidate_rules,"split_integrity":integrity,"materiality":materiality,
      "baselines":{"training_fold_prevalence":{"scope":"each candidate/fold training partition only","probability_metrics":["brier","log_loss","calibration"],"direction":"lower_is_better"},
        "equal_weight_date_cohort":{"eligibility":"same frozen eligible validation rows","allocation":"equal weight unique security per date, then equal weight dates","comparator":"net endpoint excess return"},
        "cash_no_selection":{"cash_return":0.0,"transaction_cost":0.0,"slippage":0.0,"intentional_difference":"no positions, turnover, or costs"}},
      "execution_policy":{"timing":"frozen canonical feature cutoff, decision, entry, and exit timestamps","cost_policy_versions":cost_versions,"numeric_cost_assumption":"USE_ONLY_IF_PRESENT_IN_FROZEN_POLICY_EVIDENCE; OTHERWISE_NET_METRICS_NOT_EVALUABLE","tie_breaking":"frozen candidate configuration; equal-score cohort ties include the complete eligible tied set unless the manifest specifies a deterministic rule"},
      "metrics":{"classification":"Brier/log loss only for certified probabilities; precision/recall denominators include all frozen validation rows","coverage":"selected / all eligible; report abstention, risk, and rule rejection separately","economics":"gross endpoint return; net subtracts the same frozen round-trip cost if numeric policy evidence exists","aggregation":"one canonical ID; per fold plus equal-fold aggregate; date cohorts equal-weight unique securities","uncertainty":"non-overlapping label-horizon date-block bootstrap only when horizon and dates support it","undefined":"emit null plus reason; never drop","total_return":"NOT_EVALUABLE_WITHOUT_CERTIFIED_PORTFOLIO_PATH","drawdown":"NOT_EVALUABLE_WITHOUT_CERTIFIED_CAPITAL_ACCOUNTING"},
      "advantage_rule":{"scope":"development_screen_only","primary_metric":"candidate-lane appropriate: probability Brier vs training prevalence; ranking/selection net endpoint excess vs equal-weight date cohort","improvement_direction":"lower Brier; higher net endpoint excess","aggregate_requirement":"must improve aggregate primary metric","fold_requirement":"must improve at least 2 of exactly 3 chronological folds","observed_fold_count":fold_count,"rule_conflict":fold_count!=3},
      "prior_exposure":"Registration precedes this new comparison, not prior development diagnostics or the unfavorable weighting ablation.",
      "safety":{"research_only":True,"training":False,"tuning":False,"holdout_evaluated":False,"provider_access":False,"automatic_promotion":False,"live_routing":False}}
    core=json.dumps(registration,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode(); registration["registration_sha256"]=hashlib.sha256(core).hexdigest()
    return registration
