"""Strictly offline replay of immutable Stage B response evidence."""
from __future__ import annotations
import json
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
from typing import Any,Mapping
from moneybot.services.alpha_atlas_v4_acquisition import (
    AcquisitionClock,AttemptLedger,adjust_unadjusted_bars,build_handoff,normalize_massive_daily_history,
    prior_sessions,validate_feature_rows,
)
from moneybot.services.alpha_atlas_v4_prospective_snapshot import CaptureError,canonical_bytes,sha256_bytes
from moneybot.services.alpha_atlas_v4_stage_b import (
    S3OperationLedger,load_fixture,plan_from_fixture,resolve_identity,s3_operation_budget,
)
from moneybot.services.alpha_atlas_v4_prospective_snapshot import ImmutableStore

UTC=timezone.utc
REPLAY_VERSION="alpha-atlas-v4-offline-saved-evidence-replay.v1"
SAVED_PATHS={
 "history:AAPL":"responses/history/history_AAPL/page-1-a89349acd558b68e0306e7a69214eea4940c5066412c759f811c439116795d3b.json",
 "history:SPY":"responses/history/history_SPY/page-1-37274981bc113a54283dc62752baebf7ff4188d45f32a2aee585b1f984744fff.json",
 "history:XLK":"responses/history/history_XLK/page-1-05a685b1b688a2a90d353bee2f51a68aab56aeb8d12b9cdded0b4fd3cdfabb57.json",
 "identity:AAPL":"responses/identity/identity_AAPL/page-1-e659bdb277186657b26cabac6c279fc190b4664e3342ce2a49f46a4299b092fe.json",
 "splits:global":"responses/splits/splits_global/page-1-6aa344ab0498a6e7855d32ea37e2749c74a079a31bcad35d237dfee6c089839a.json",
}

def _json(path:Path)->dict[str,Any]:
    try:value=json.loads(path.read_text())
    except (OSError,json.JSONDecodeError) as exc: raise CaptureError("REPLAY_EVIDENCE_INVALID",str(path)) from exc
    if not isinstance(value,dict):raise CaptureError("REPLAY_EVIDENCE_INVALID",str(path))
    return value

def _logical_results(primary:Path,saved_paths:Mapping[str,str]=SAVED_PATHS)->tuple[dict[str,dict[str,Any]],dict[str,dict[str,Any]]]:
    results={}; receipts={}
    for request_id,relative in saved_paths.items():
        path=primary/relative; raw=path.read_bytes(); digest=sha256_bytes(raw)
        if digest!=relative.split("-")[-1].removesuffix(".json"):raise CaptureError("REPLAY_SOURCE_HASH_MISMATCH",request_id)
        payload=_json(path); receipt_path=Path(relative+".receipt.json"); receipt=_json(primary/receipt_path)
        if payload.get("status")!="OK" or payload.get("request_id")!=receipt.get("provider_request_id"):
            raise CaptureError("REPLAY_ENVELOPE_INVALID",request_id)
        if (receipt.get("response_sha256")!=digest or receipt.get("response_bytes")!=len(raw)
                or receipt.get("request_id")!=request_id or receipt.get("receipt_evidence_kind") == "UNKNOWN"):
            raise CaptureError("REPLAY_RECEIPT_MISMATCH",request_id)
        obj={"page":1,"path":relative,"sha256":digest,"bytes":len(raw),"receipt_path":receipt_path.as_posix(),
             "late":bool(receipt.get("late")),"payload":payload}
        if payload.get("next_url"):raise CaptureError("REPLAY_INCOMPLETE_PAGINATION",request_id)
        results[request_id]={"request_id":request_id,"complete":True,"objects":[obj]}; receipts[receipt_path.as_posix()]=receipt
    return results,receipts

def _validate_attempts(primary:Path,results:Mapping[str,Any])->dict[str,Any]:
    rows=AttemptLedger(ImmutableStore(primary),synthetic=False).records()
    by_id={}
    for row in rows:by_id.setdefault(row.get("attempt_id"),[]).append(row)
    if len(by_id)!=5:raise CaptureError("REPLAY_MASSIVE_ACCOUNTING_MISMATCH")
    for events in by_id.values():
        if [x.get("event") for x in events] != ["RESERVED","TRANSMITTING","PERSISTED"]:raise CaptureError("REPLAY_MASSIVE_ACCOUNTING_MISMATCH")
    persisted={x.get("request_id") for x in rows if x.get("event")=="RESERVED"}
    if persisted!=set(results):raise CaptureError("REPLAY_MASSIVE_ACCOUNTING_MISMATCH")
    return {"records":len(rows),"attempts":5,"states":{"RESERVED":5,"TRANSMITTING":5,"PERSISTED":5,"FAILED":0,"UNCERTAIN":0},"ledger_head_sha256":rows[-1]["record_sha256"]}

def _validate_s3(root:Path)->dict[str,Any]:
    rows=S3OperationLedger(ImmutableStore(root/"s3-operation-ledger")).records(); by_id={}
    for row in rows:by_id.setdefault(row.get("operation_id"),[]).append(row)
    if len(by_id)!=91:raise CaptureError("REPLAY_S3_ACCOUNTING_MISMATCH",str(len(by_id)))
    for operation_id,events in by_id.items():
        if [x.get("event") for x in events] != ["RESERVED","TRANSMITTING","SUCCEEDED"]:raise CaptureError("REPLAY_S3_ACCOUNTING_MISMATCH",str(operation_id))
    return {"records":len(rows),"operations":91,"states":{"RESERVED":91,"TRANSMITTING":91,"SUCCEEDED":91,"FAILED":0,"UNCERTAIN":0},"ledger_head_sha256":rows[-1]["record_sha256"]}

def _inspect_backup(root:Path,s3:Mapping[str,Any])->dict[str,Any]:
    directory=root/"primary/backup"; completions=sorted(directory.glob("completion-*.json")); inventories=sorted(directory.glob("inventory-*.json"))
    if len(completions)!=1 or len(inventories)!=1:raise CaptureError("REPLAY_BACKUP_EVIDENCE_MISSING")
    completion=_json(completions[0]); inventory=_json(inventories[0]); inventory_bytes=inventories[0].read_bytes(); inventory_hash=sha256_bytes(inventory_bytes)
    if inventories[0].stem.removeprefix("inventory-")!=inventory_hash or completions[0].stem.removeprefix("completion-")!=sha256_bytes(completions[0].read_bytes()):
        raise CaptureError("REPLAY_BACKUP_EVIDENCE_HASH_MISMATCH")
    if completion.get("inventory_sha256")!=inventory_hash or completion.get("inventory_path")!=inventories[0].relative_to(root/"primary").as_posix():raise CaptureError("REPLAY_BACKUP_INVENTORY_MISMATCH")
    covered=inventory.get("covered"); receipts=completion.get("receipts")
    if not isinstance(covered,list) or not isinstance(receipts,list) or completion.get("covered_object_count")!=len(covered)+1:raise CaptureError("REPLAY_BACKUP_INVENTORY_MISMATCH")
    for item in covered:
        path=root/"primary"/str(item.get("path"))
        if not path.is_file() or path.stat().st_size!=item.get("bytes") or sha256_bytes(path.read_bytes())!=item.get("sha256"):raise CaptureError("REPLAY_BACKUP_COVERED_OBJECT_MISMATCH",str(item.get("path")))
    for receipt in receipts:
        if not all(receipt.get(k) is not None for k in ("bucket","key","version_id","sha256","bytes","retention_mode","retain_until")):raise CaptureError("REPLAY_BACKUP_RECEIPT_INVALID")
    if 6+s3_operation_budget(len(receipts))["total"]!=s3.get("operations"):
        raise CaptureError("REPLAY_BACKUP_S3_ACCOUNTING_MISMATCH")
    restored=root/"isolated-restore"; restored_files=[p for p in restored.rglob("*") if p.is_file() and not p.name.startswith(".")] if restored.exists() else []
    return {"status":"SAVED_VERIFICATION_EVIDENCE_ONLY_NO_FRESH_AWS_CHECK","completion_path":completions[0].relative_to(root).as_posix(),
            "completion_sha256":sha256_bytes(completions[0].read_bytes()),"inventory_path":inventories[0].relative_to(root).as_posix(),
            "inventory_sha256":inventory_hash,"covered_objects":len(covered)+1,"covered_bytes":completion.get("covered_bytes"),
            "recorded_receipts":len(receipts),"recorded_restored_objects":completion.get("restored_object_count"),
            "local_restored_files_available":len(restored_files),"s3_ledger_head_sha256":s3["ledger_head_sha256"],
            "limitation":"Saved manifests and local bytes do not perform fresh remote read-back or prove ongoing retention."}

def replay_saved_evidence(repo:Path,root:Path,*,saved_paths:Mapping[str,str]=SAVED_PATHS)->dict[str,Any]:
    replayed_at=datetime.now(UTC); root=root.resolve(); primary=root/"primary"; fixture=load_fixture(repo,fixture_name="alpha_atlas_v4_stage_b_operational_verification_manifest.v1.json",fixture_content_sha256="579013c85367c75f8a821aa30c75fe9cd261080a9c8060cfcd94af3d958499e9",session=date(2026,10,6))
    results,receipts=_logical_results(primary,saved_paths); massive=_validate_attempts(primary,results); s3=_validate_s3(root)
    window=prior_sessions(date(2026,10,7),75)
    normalized={symbol:normalize_massive_daily_history(results[f"history:{symbol}"],expected_symbol=symbol,window_sessions=window,receipt_loader=lambda path:receipts[path]) for symbol in ("AAPL","SPY","XLK")}
    reasons=validate_feature_rows(normalized["AAPL"]["rows"],normalized["SPY"]["rows"],normalized["XLK"]["rows"],window)
    identity=resolve_identity(results["identity:AAPL"])
    split_payload=results["splits:global"]["objects"][0]["payload"]; split_rows=split_payload.get("results")
    if not isinstance(split_rows,list):raise CaptureError("REPLAY_SPLIT_RESPONSE_INVALID")
    split_objects=results["splits:global"]["objects"]
    adjusted={symbol:adjust_unadjusted_bars(normalized[symbol]["rows"],[row for row in split_rows if row.get("ticker")==symbol],date(2026,10,6),source_object_sha256s=normalized[symbol]["source_object_sha256s"],normalized_history_sha256=normalized[symbol]["derived_content_sha256"],split_source_object_sha256s=[obj["sha256"] for obj in split_objects],split_receipt_sha256s=[sha256_bytes(canonical_bytes(receipts[obj["receipt_path"]])) for obj in split_objects]) for symbol in normalized}
    plan=plan_from_fixture(fixture,"ba59fd773cd8be78816280e3a16a02bb3b83fcff8b3a92e5299eb78ceae28fc4",session=date(2026,10,6))
    timing=AcquisitionClock(datetime(2026,10,9,tzinfo=UTC),datetime(2026,10,9,tzinfo=UTC),datetime(2026,10,8,tzinfo=UTC))
    handoff=build_handoff(plan,results,generated_at=replayed_at,timing=timing,adjustment_bindings={s:v["binding"] for s,v in adjusted.items()},normalized_histories=normalized)
    if reasons:handoff["eligible"]=False;handoff["reason_codes"]=sorted(set(handoff.get("reason_codes",[])+reasons))
    backup=_inspect_backup(root,s3)
    result={"schema_version":REPLAY_VERSION,"status":"PASS" if handoff["eligible"] else "INELIGIBLE","replayed_at":replayed_at.isoformat(),"execution_purpose":"OFFLINE_SAVED_EVIDENCE_REPLAY",
            "live_provider_requests":0,"live_aws_requests":0,"pilot_input_allowed":False,"premarket_timing_readiness":"NOT_TESTED","prospective_snapshot_eligibility":"NOT_TESTED",
            "original_live_execution_status":"FAILED_FEATURE_WINDOW_INVALID_UNCHANGED","massive_accounting":massive,"s3_accounting":s3,"backup_restore_evidence":backup,
            "normalized_histories":{s:{"rows":len(v["rows"]),"derived_content_sha256":v["derived_content_sha256"],"source_object_sha256s":v["source_object_sha256s"],"receipt_sha256s":v["receipt_sha256s"]} for s,v in normalized.items()},
            "identity":identity,"split_rows":len(split_rows),"adjustment_bindings":{s:v["binding"] for s,v in adjusted.items()},
            "handoff":{"eligible":handoff["eligible"],"reason_codes":handoff["reason_codes"],"handoff_sha256":handoff["handoff_sha256"],"consumption_scope":"ISOLATED_REPLAY_ONLY"}}
    result["content_sha256"]=sha256_bytes(canonical_bytes(result));return result
