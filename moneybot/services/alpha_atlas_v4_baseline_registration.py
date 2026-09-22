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

def _nested(row: dict, *paths: tuple[str, ...]) -> Any:
    for path in paths:
        value: Any = row
        for key in path:
            if not isinstance(value, dict) or key not in value:
                value = None; break
            value = value[key]
        if value not in (None, ""): return value
    return None

def _number(value: Any) -> float | None:
    try:
        number=float(value)
        return number if math.isfinite(number) else None
    except (TypeError,ValueError): return None

def _identity_inventory(rows: dict[str,dict], relevant_ids: set[str]) -> dict[str,dict]:
    paths={
      "point_in_time_symbol_id":(("point_in_time_symbol_id",),("security_identity","point_in_time_symbol_id")),
      "share_class_figi":(("share_class_figi",),("security_identity","share_class_figi")),
      "composite_figi":(("composite_figi",),("security_identity","composite_figi")),
      "cik":(("cik",),("security_identity","cik")),
    }
    result={}
    for kind,candidates in paths.items():
        values={identifier:_nested(row,*candidates) for identifier,row in rows.items()}
        present={identifier:str(value) for identifier,value in values.items() if value not in (None,"")}
        result[kind]={"development_rows_present":len(present),"development_rows_missing":len(rows)-len(present),
          "unique_values":len(set(present.values())),"relevant_security_or_date_rows_present":len(set(present)&relevant_ids),
          "missing_evidence":None if len(present)==len(rows) else "IDENTIFIER_ABSENT_FROM_DEVELOPMENT_OBSERVATION"}
    return result

def _point_in_time_symbol_lineage(rows: dict[str,dict]) -> dict[str,Any]:
    mappings: dict[str,set[tuple[str,str]]] = {}
    fallback=[]; missing=[]
    for canonical_id,row in rows.items():
        value=str(_nested(row,("point_in_time_symbol_id",),("security_identity","point_in_time_symbol_id")) or "")
        symbol=str(row.get("symbol") or "").upper(); event_date=str(row.get("event_date") or "")[:10]
        if not value: missing.append(canonical_id); continue
        mappings.setdefault(value,set()).add((symbol,event_date))
        if value == f"{symbol}:{event_date}": fallback.append(canonical_id)
    conflicts=[{"point_in_time_symbol_id":key,"symbol_dates":sorted(values)} for key,values in mappings.items()
               if len({symbol for symbol,_ in values})>1]
    return {"construction_trace":"event.point_in_time_symbol_id or fallback f'{symbol}:{event_day}'",
      "semantic_guarantee":"TICKER_DATE_OBSERVATION_KEY_NOT_PROVEN_SECURITY_OR_LISTING_ID",
      "rows_present":len(rows)-len(missing),"rows_missing":len(missing),"unique_values":len(mappings),
      "fallback_pattern_rows":len(fallback),"cross_symbol_collision_count":len(conflicts),"cross_symbol_collision_examples":conflicts[:10],
      "join_scope_verified":"canonical observation and OOF ticker/date joins only",
      "ticker_change_reuse_share_class_guarantee":"NOT_ESTABLISHED",
      "identity_sufficiency_for_frozen_comparison":"UNKNOWN_UNTIL_AFFECTED_SCOPE_IS_BOUNDED_OR_SECURITY_LINEAGE_IS_PROVEN"}

def _identity_mappings(path: Path | None) -> tuple[dict[str,dict[str,set[str]]],dict]:
    maps={kind:{} for kind in ("share_class_figi","composite_figi","cik")}
    if path is None or not path.is_file(): return maps,{"status":"UNKNOWN","reason":"IDENTITY_EVIDENCE_FILE_UNAVAILABLE","source":None}
    payload=json.loads(path.read_text()); stack=[payload]; records=0
    while stack:
        value=stack.pop()
        if isinstance(value,dict):
            ticker=str(value.get("ticker") or value.get("old_ticker") or "").upper()
            if ticker in UNRESOLVED_TICKERS:
                records+=1
                for kind in maps:
                    identifier=value.get(kind)
                    if identifier: maps[kind].setdefault(str(identifier),set()).add(ticker)
            stack.extend(value.values())
        elif isinstance(value,list): stack.extend(value)
    return maps,{"status":"AVAILABLE" if records else "UNKNOWN","reason":None if records else "NO_TYPED_UNRESOLVED_CASE_MAPPINGS_FOUND",
      "source":{"path":path.name,"bytes":path.stat().st_size,"sha256_computed":sha(path)},"candidate_records_scanned":records,
      "mapping_value_counts":{kind:len(values) for kind,values in maps.items()},
      "cik_usage":"INVESTIGATION_FLAG_ONLY_NOT_CONTINUITY"}

def _external_cost_evidence(path: Path | None) -> dict:
    if path is None or not path.is_file(): return {"status":"UNAVAILABLE","applicable_to_development_oof":False,"reason":"SAVED_COST_POLICY_FILE_NOT_SUPPLIED"}
    payload=json.loads(path.read_text()); return {"status":"LOCATED_DIFFERENT_SCOPE","applicable_to_development_oof":False,
      "reason":"SELECTED_FINAL_HOLDOUT_PORTFOLIO_POLICY_IS_NOT_AUTOMATICALLY_APPLICABLE_TO_DEVELOPMENT_OOF",
      "source":{"path":path.name,"bytes":path.stat().st_size,"sha256_computed":sha(path)},
      "recorded_policy":{"version":payload.get("version") or payload.get("policy_version"),"transaction_cost_bps":payload.get("transaction_cost_bps"),"slippage_bps":payload.get("slippage_bps"),"units":"basis_points","treatment":"portfolio policy; applicability intentionally not borrowed"}}

def _price_return_cost_evidence(rows: dict[str,dict], validation_ids: set[str], *, producer_trace_bound: bool) -> tuple[dict,dict,dict,dict]:
    price=Counter(); examples={}; return_counts=Counter(); cost=Counter(); versions=Counter(); valuation=Counter()
    formula=Counter(); formula_examples=[]
    for identifier,row in rows.items():
        lineage=row.get("reconstruction_lineage") or {}; execution=lineage.get("execution") or {}
        entry_raw=_nested(row,("entry_price",),("adjusted_entry_price",),("reconstruction_lineage","execution","entry_price"))
        exit_raw=_nested(row,("exit_price",),("adjusted_exit_price",),("reconstruction_lineage","execution","exit_price"))
        entry=_number(entry_raw); exit_price=_number(exit_raw)
        join_ok=(not execution or (str(execution.get("entry_at"))==str(row.get("entry_at")) and str(execution.get("exit_at"))==str(row.get("exit_at"))))
        invalid=(entry_raw is not None and (entry is None or entry<=0)) or (exit_raw is not None and (exit_price is None or exit_price<=0))
        category=("invalid" if invalid else "unresolved_join" if not join_ok else "both_present" if entry is not None and exit_price is not None else "entry_only" if entry is not None else "exit_only" if exit_price is not None else "both_missing")
        price[category]+=1
        examples.setdefault(category,{"canonical_id":identifier,"entry_source":"reconstruction_lineage.execution" if execution.get("entry_price") is not None else "top_level_or_absent","exit_source":"reconstruction_lineage.execution" if execution.get("exit_price") is not None else "top_level_or_absent"})
        return_name="return_5d" if "return_5d" in row else next((k for k in row if k.startswith("return_") and isinstance(row[k],(int,float))),None)
        if return_name and _number(row.get(return_name)) is not None: return_counts["frozen_endpoint_return_present"]+=1
        else: return_counts["frozen_endpoint_return_missing"]+=1
        net_flag=_nested(row,("return_is_net",),("return_net_of_costs",),("reconstruction_lineage","execution","return_is_net"))
        if net_flag is True: return_counts["explicitly_net"]+=1
        elif net_flag is False: return_counts["explicitly_gross"]+=1
        else: return_counts["net_or_gross_unspecified"]+=1
        if identifier in validation_ids: return_counts["validation_rows"]+=1; return_counts["validation_endpoint_return_present"]+=int(bool(return_name and _number(row.get(return_name)) is not None))
        if identifier in validation_ids and return_name and _number(row.get(return_name)) is not None:
            execution_entry=_number(execution.get("entry_price")); raw_entry=_number(row.get("raw_entry_price")); adjusted_entry=_number(row.get("adjusted_entry_price"))
            formula_entry=execution_entry if execution_entry is not None else raw_entry if raw_entry is not None else adjusted_entry if adjusted_entry is not None else _number(row.get("entry_price"))
            factor=(_number(execution.get("split_factor",row.get("label_split_adjustment_factor",1.0)))
                    if execution_entry is not None or raw_entry is not None else 1.0)
            if formula_entry is None or exit_price is None or factor is None or formula_entry*factor<=0:
                formula["unsupported"]+=1
            else:
                replay=exit_price/(formula_entry*factor)-1.0; observed=float(row[return_name])
                if math.isclose(replay,observed,rel_tol=0,abs_tol=5e-7): formula["matched"]+=1
                else:
                    formula["mismatched"]+=1
                    if len(formula_examples)<5: formula_examples.append({"canonical_id":identifier,"field":return_name,"observed":observed,"replayed":replay})
        policy=str(row.get("execution_cost_policy_version") or execution.get("execution_cost_policy_version") or "")
        if policy: versions[policy]+=1
        components=[execution.get("transaction_cost_bps",row.get("transaction_cost_bps")),execution.get("entry_slippage_bps",row.get("entry_slippage_bps")),execution.get("exit_slippage_bps",row.get("exit_slippage_bps"))]
        if all(v is None for v in components): cost["all_components_missing"]+=1
        elif all(_number(v)==0 for v in components): cost["explicit_zero_all_components"]+=1
        elif all(_number(v) is not None for v in components): cost["numeric_all_components"]+=1
        else: cost["partial_or_invalid_components"]+=1
        valuation["path_present"]+=int(isinstance(row.get("valuation_path"),list) and bool(row.get("valuation_path")))
        valuation["policy_version_present"]+=int(bool(row.get("valuation_path_policy_version")))
        cert=row.get("valuation_certification")
        if isinstance(cert,dict) and cert.get("status") in {"VERIFIED","COMPLETE"}: valuation["explicitly_certified"]+=1
        else: valuation["not_explicitly_certified"]+=1
    price_report={"row_scope":len(rows),"mutually_exclusive_counts":dict(price),"representative_examples":examples,
      "field_resolution_order":["entry_price/exit_price","adjusted_entry_price/adjusted_exit_price","reconstruction_lineage.execution.entry_price/exit_price"],
      "prices_reconstructed_from_returns":False}
    net_status=("ALREADY_NET" if return_counts["explicitly_net"]==len(rows) else "GROSS" if return_counts["explicitly_gross"]==len(rows) else "UNKNOWN_OR_MIXED")
    formula_complete=(return_counts["validation_rows"]>0 and formula["matched"]==return_counts["validation_rows"])
    semantics=("VERIFIED_GROSS_SPLIT_ADJUSTED_PRICE_RETURN" if producer_trace_bound and formula_complete and net_status!="ALREADY_NET" else "UNKNOWN_OR_MIXED")
    returns={**return_counts,"return_semantics":"frozen endpoint return; not an entry/exit-price substitute and not a portfolio path","net_status":net_status,
      "formula_consistency":{"formula":"round(exit_price / (entry_price * split_factor) - 1, 6)","units":"decimal return","absolute_tolerance":5e-7,"checked_validation_rows":sum(formula.values()),"counts":dict(formula),"examples":formula_examples},
      "economic_semantics_status":semantics,"producer_trace_bound":producer_trace_bound,
      "adjustment_scope":"split adjusted; no dividend adjustment established","cost_treatment":"no cost or slippage term in traced producer formula"}
    costs={"row_scope":len(rows),"component_counts":dict(cost),"policy_versions":dict(versions),"missing_is_zero":False,"one_way_components":["transaction_cost_bps","entry_slippage_bps","exit_slippage_bps"]}
    valuation_report={"row_scope":len(rows),**valuation,"zero_uncertified_does_not_mean_certified":True}
    return price_report,returns,costs,valuation_report

def register(canonical: Path, plan_path: Path, manifest_path: Path, capture_path: Path,
             source_provenance: dict[str, dict], *, prior_registration: Path | None = None,
             identity_evidence: Path | None = None, cost_policy_evidence: Path | None = None) -> dict[str, Any]:
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
    # Materiality: direct ticker/date, dependency windows, and separately typed mappings.
    affected=[]; relevant_ids=set()
    for identifier,row in rows.items():
        symbol=str(row.get("symbol") or "").upper(); deps=_dependencies(row)
        fold_ids=[i for i,f in fold_map.items() if identifier in set(_fold_ids(f,"train"))|set(_fold_ids(f,"validation"))]
        if symbol in {"FITBM","FITBO"}: relevant_ids.add(identifier); affected.append({"canonical_id":identifier,"folds":fold_ids,"symbol":symbol,"matching_basis":"DIRECT_TICKER","issue":"FITBM_FITBO_CLASSIFICATION","dependency_dates":deps,"impacted_claim":"universe eligibility"})
        if symbol=="KAII" and KAII_DATES & set(deps): relevant_ids.add(identifier); affected.append({"canonical_id":identifier,"folds":fold_ids,"symbol":symbol,"matching_basis":"DIRECT_TICKER_AND_DEPENDENCY_WINDOW","issue":"KAII_GAP_DATE","dependency_dates":sorted(KAII_DATES & set(deps)),"impacted_claim":"return/price/valuation"})
        if symbol in UNRESOLVED_TICKERS: relevant_ids.add(identifier); affected.append({"canonical_id":identifier,"folds":fold_ids,"symbol":symbol,"matching_basis":"DIRECT_TICKER","issue":"UNRESOLVED_EFFECTIVE_DATED_IDENTITY","dependency_dates":deps,"impacted_claim":"identity/universe join"})
    mappings,mapping_evidence=_identity_mappings(identity_evidence)
    declared_mapping_status=(source_provenance.get("materiality_evidence") or {}).get("unresolved_identity_typed_identifier_map_status")
    if mapping_evidence["status"]=="UNKNOWN" and declared_mapping_status=="VERIFIED_COMPLETE_NO_MATCH":
        mapping_evidence={**mapping_evidence,"status":"AVAILABLE","reason":None,"declared_fixture_or_external_status":declared_mapping_status}
    ambiguous=[]; cik_flags=[]
    for identifier,row in rows.items():
        fold_ids=[i for i,f in fold_map.items() if identifier in set(_fold_ids(f,"train"))|set(_fold_ids(f,"validation"))]
        share=str(_nested(row,("share_class_figi",),("security_identity","share_class_figi")) or "")
        composite=str(_nested(row,("composite_figi",),("security_identity","composite_figi")) or "")
        cik=str(_nested(row,("cik",),("security_identity","cik")) or "")
        if share and share in mappings["share_class_figi"]:
            relevant_ids.add(identifier); ambiguous.append({"canonical_id":identifier,"folds":fold_ids,"identifier_type":"share_class_figi","matched_unresolved_tickers":sorted(mappings["share_class_figi"][share]),"reason":"MAPPING_DATE_OR_TRANSITION_APPLICABILITY_NOT_BOUND_TO_DEVELOPMENT_DEPENDENCY"})
        if composite and composite in mappings["composite_figi"]:
            relevant_ids.add(identifier); ambiguous.append({"canonical_id":identifier,"folds":fold_ids,"identifier_type":"composite_figi","matched_unresolved_tickers":sorted(mappings["composite_figi"][composite]),"reason":"TEMPORAL_OR_SHARE_CLASS_CONTINUITY_NOT_ESTABLISHED"})
        if cik and cik in mappings["cik"]:
            cik_flags.append({"canonical_id":identifier,"folds":fold_ids,"matched_unresolved_tickers":sorted(mappings["cik"][cik]),"reason":"SHARED_CIK_IS_INVESTIGATION_FLAG_NOT_CONTINUITY"})
    inventory=_identity_inventory(rows,relevant_ids)
    point_in_time_lineage=_point_in_time_symbol_lineage(rows)
    typed_status=("UNKNOWN_AMBIGUOUS_TYPED_MATCHES" if ambiguous else
                  "VERIFIED_NO_TYPED_MATCHES" if mapping_evidence["status"]=="AVAILABLE" and inventory["share_class_figi"]["development_rows_missing"]==0 else
                  "PARTIAL_UNKNOWN")
    validation_ids={identifier for fold in fold_map.values() for identifier in _fold_ids(fold,"validation")}
    producer_trace={
      "track_b_commit":(source_provenance.get("canonical") or {}).get("source_head_sha"),
      "diagnostics_commit":(source_provenance.get("capture") or {}).get("source_head_sha"),
      "expected_track_b_commit":"1f8f46db584dff0881273bdeae1c56c1a8a016c5",
      "expected_diagnostics_commit":"5d360cdbda802ae8527b35fe59f75920b8c827c8",
      "files":{
       "scripts/build_massive_decision_training_rows.py":"ffa68ed72f7e01ef02ef087b83ad42c35d373c507e374b4a130ca624b42c9bd3",
       "moneybot/services/corporate_actions.py":"7e37742157356a9907fcb8ed1a41bc7a0bad4d6422aacd9fd9549103ed60d741",
       "moneybot/services/alpha_atlas_v4_canonical_observations.py":"213b07d9cc8badf1913d15ebcd61d17065b4a9d0a13213b8362874db50eb2ed3",
       "moneybot/services/alpha_atlas_v4_phase0.py":"4739f081db29f079cae1c3285c33145cf6b455b011eca58da36883554d7516e1",
       "scripts/capture_alpha_atlas_v4_development_oof.py":"5a778d7a04e799ecc8078fc22ee5122f05af4c2312d45f7dcaa934fb88409df9",
       "scripts/train_challenger_suite.py":"13fe60562efcdd93b4642c01455cff67e07d4ae1520d8443f74cdcf9ecd73951",
       "scripts/generate_alpha_atlas_v4_development_diagnostics.py":"9aabf47e3a8664c02f2ea6c6bdd4a736858312fb8df3a2b5cb494f233b51ef33"}}
    producer_trace["pinned_commits_match"]=producer_trace["track_b_commit"]==producer_trace["expected_track_b_commit"] and producer_trace["diagnostics_commit"]==producer_trace["expected_diagnostics_commit"]
    price_evidence,return_evidence,cost_evidence,valuation_evidence=_price_return_cost_evidence(rows,validation_ids,producer_trace_bound=producer_trace["pinned_commits_match"])
    cost_evidence["other_saved_policy_evidence"]=_external_cost_evidence(cost_policy_evidence)
    materiality={"status":"BLOCKED" if affected else ("UNKNOWN" if typed_status.startswith(("UNKNOWN","PARTIAL")) else "CLEAR_FOR_EXACT_INPUT"),
        "unique_affected_rows":len({x["canonical_id"] for x in affected}),"dependency_occurrences":len(affected),"affected_rows":affected,
        "direct_historical_match_rows":len({x["canonical_id"] for x in affected if x["matching_basis"].startswith("DIRECT")}),
        "typed_identifier_mapping_status":typed_status,"identifier_coverage":inventory,"point_in_time_symbol_lineage":point_in_time_lineage,"mapping_evidence":mapping_evidence,
        "ambiguous_typed_matches":ambiguous,"cik_investigation_flags":cik_flags,
        "price_evidence":price_evidence,"return_evidence":return_evidence,"cost_evidence":cost_evidence,"valuation_evidence":valuation_evidence,"producer_source_trace":producer_trace,
        "valuation_scope":"Endpoint metrics may use frozen returns; total return and drawdown remain NOT_EVALUABLE without certified paths.",
        "kaii_supplement":"human odd-lot rule confirmed; 52-quotes/no-trades DATE_ATTRIBUTION_UNCONFIRMED; historical 2023 applicability unknown"}
    interval=sorted(str(r.get("feature_cutoff_at")) for r in rows.values() if r.get("feature_cutoff_at"))
    cost_versions=sorted(cost_evidence["policy_versions"])
    fold_count=len(folds)
    integrity={"status":"BLOCKED" if failures else "VERIFIED","reason_codes":sorted(set(failures)),
        "canonical_counts":canonical_counts,"development_id_count":len(development),"holdout_id_count_metadata_only":len(holdout),
        "candidate_count":len(candidates),"fold_count":fold_count,"expected_candidate_fold_pairs":len(expected_pairs),
        "observed_candidate_fold_pairs":len(seen_pairs),"prediction_assignments":len(predicted_assignments),
        "final_holdout_overlap_count":sum(1 for c,i in predicted_assignments if i in holdout),"final_holdout_content_loaded":False,"folds":fold_reports}
    structural=list(integrity["reason_codes"])
    probability_candidates=any("probability" in {str(x.get("score_semantics")) for x in capture if str(x.get("model_version"))==name} for name in candidates)
    returns_complete=(return_evidence.get("validation_rows",0)>0 and return_evidence.get("validation_endpoint_return_present")==return_evidence.get("validation_rows"))
    gross_semantics_verified=return_evidence.get("economic_semantics_status")=="VERIFIED_GROSS_SPLIT_ADJUSTED_PRICE_RETURN"
    numeric_cost_complete=cost_evidence["component_counts"].get("numeric_all_components",0)==len(rows)
    explicit_zero_complete=cost_evidence["component_counts"].get("explicit_zero_all_components",0)==len(rows)
    already_net_complete=return_evidence["net_status"]=="ALREADY_NET"
    materiality_qualifier="CLEAR" if materiality["status"]=="CLEAR_FOR_EXACT_INPUT" else materiality["status"]
    metric_eligibility={
      "probability_calibration":{"status":"EVALUABLE" if probability_candidates and not structural else "NOT_EVALUABLE","row_scope":"OOF validation assignments for candidates with probability semantics","reason_codes":([] if probability_candidates and not structural else ["NO_VALID_PROBABILITY_OOF_EVIDENCE_OR_SPLIT_FAILURE"]),"materiality_qualification":materiality_qualifier},
      "classification_and_coverage":{"status":"EVALUABLE" if not structural else "NOT_EVALUABLE","row_scope":"all exact OOF validation assignments","reason_codes":structural,"materiality_qualification":materiality_qualifier},
      "gross_endpoint_economics":{"status":"EVALUABLE" if returns_complete and gross_semantics_verified and not structural else "NOT_EVALUABLE","row_scope":"unique development validation rows consumed by OOF candidates","reason_codes":([] if returns_complete and gross_semantics_verified else (["FROZEN_ENDPOINT_RETURN_MISSING"] if not returns_complete else ["GROSS_RETURN_SEMANTICS_UNVERIFIED"])),"returns_are_prices":False,"materiality_qualification":materiality_qualifier},
      "net_endpoint_economics":{"status":"EVALUABLE" if returns_complete and (already_net_complete or numeric_cost_complete or explicit_zero_complete) and not structural else "NOT_EVALUABLE","row_scope":"unique development validation rows consumed by OOF candidates","reason_codes":[] if already_net_complete or numeric_cost_complete or explicit_zero_complete else ["APPLICABLE_NUMERIC_COST_POLICY_UNAVAILABLE"],"already_net_return_status":return_evidence["net_status"],"cost_application":"NO_ADDITIONAL_SUBTRACTION" if already_net_complete else "APPLY_FROZEN_COMPONENTS" if numeric_cost_complete else "ZERO_ONLY_IF_EXPLICIT" if explicit_zero_complete else "UNAVAILABLE","double_cost_subtraction_forbidden":True},
      "portfolio_total_return_and_drawdown":{"status":"NOT_EVALUABLE","row_scope":"none","reason_codes":["CERTIFIED_PORTFOLIO_PATH_AND_CAPITAL_ACCOUNTING_UNAVAILABLE_FOR_DEVELOPMENT_OOF"],"endpoint_returns_compounded":False},
    }
    readiness_reasons=list(structural)
    if affected: readiness_reasons.append("UNRESOLVED_HISTORICAL_DEPENDENCY_INTERSECTION")
    if typed_status.startswith(("UNKNOWN","PARTIAL")): readiness_reasons.append("TYPED_IDENTITY_MATERIALITY_UNKNOWN")
    if fold_count != 3: readiness_reasons.append("DOCUMENTED_ADVANTAGE_RULE_FOLD_COUNT_CONFLICT")
    if len(cost_versions)>1: readiness_reasons.append("MIXED_EXECUTION_COST_POLICIES")
    ranking_present=any((row.get("candidate_lane") or (row.get("spec") or {}).get("candidate_lane"))=="ranking" for row in candidates.values())
    if ranking_present and metric_eligibility["net_endpoint_economics"]["status"]!="EVALUABLE": readiness_reasons.append("REGISTERED_NET_PRIMARY_METRIC_NOT_EVALUABLE")
    candidate_rules=[]
    for name,specrow in sorted(candidates.items()):
        captures=[x for x in capture if str(x.get("model_version"))==name]
        candidate_rules.append({"model_version":name,"candidate_lane":specrow.get("candidate_lane") or (specrow.get("spec") or {}).get("candidate_lane"),
            "spec":specrow.get("spec"),"thresholds":sorted({x.get("decision_threshold") for x in captures},key=lambda x:str(x)),
            "score_semantics":sorted({str(x.get("score_semantics")) for x in captures}),
            "target_definitions":list({json.dumps(x.get("target_definition"),sort_keys=True) for x in captures})})
    prior={"status":"NOT_SUPPLIED"}
    if prior_registration and prior_registration.is_file():
        old=json.loads(prior_registration.read_text()); prior={"status":"PRESERVED_AND_LINKED","path":prior_registration.name,"bytes":prior_registration.stat().st_size,"file_sha256":sha(prior_registration),"internal_registration_sha256":old.get("registration_sha256"),"expected_internal_registration_sha256":"019fb1387941858c4588f19bb89b8acd34144487025f0233d6e20cb4bfafb107","internal_hash_matches_expected":old.get("registration_sha256")=="019fb1387941858c4588f19bb89b8acd34144487025f0233d6e20cb4bfafb107"}
    registration={"schema_version":SCHEMA,"status":"REGISTERED_BLOCKED" if readiness_reasons else "REGISTERED_READY_FOR_REVIEW","readiness_reason_codes":sorted(set(readiness_reasons)),
      "performance_comparison_executed":False,"development_scope":{"interval_source":"derived_from_development_feature_cutoff_at","start":interval[0][:10] if interval else None,"end":interval[-1][:10] if interval else None,"fold_count":fold_count},
      "inputs":files,"prior_registration":prior,"correction_change_record":["Inventory identifier coverage and consume saved typed mappings without treating CIK as continuity","Resolve entry/exit prices from documented reconstruction_lineage.execution before reporting missing prices","Separate frozen endpoint returns from price and portfolio-path evidence","Separate missing, explicit-zero, numeric, and already-net cost evidence","Require pinned producer commits plus price-formula consistency before gross endpoint economics is EVALUABLE","Treat point_in_time_symbol_id fallback values as ticker/date observation keys, not proven security identities","Report metric-family eligibility independently from split integrity and overall readiness"],
      "candidates":candidate_rules,"split_integrity":integrity,"materiality":materiality,"metric_eligibility":metric_eligibility,
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
