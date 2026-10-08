"""Read-only incident inspection and narrowly bound Stage B continuation support."""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from moneybot.services.alpha_atlas_v4_acquisition import AttemptLedger
from moneybot.services.alpha_atlas_v4_prospective_snapshot import CaptureError, ImmutableStore, canonical_bytes, sha256_bytes
from moneybot.services.alpha_atlas_v4_stage_b import (
    PRIMARY_CAP, REGISTERED_BUDGETS, S3OperationLedger, authorization_hashes, parse_utc_timestamp,
)

UTC=timezone.utc
ORIGINAL_AUTHORIZATION_PIN="45b196c97e55a2f7d81256f641c48471a7fe1fe1fbaa0c0636a88680f302b214"
ORIGINAL_AUTHORIZATION_FILE_SHA256="20d8537ac18085ee7ee8ef539385a553c7a7f05ed0000edad4280ecbabd56ac2"
EXPECTED_S3_CONFIGURATION_OPERATIONS=["HEAD_BUCKET","GET_BUCKET_LOCATION","GET_BUCKET_VERSIONING","GET_PUBLIC_ACCESS_BLOCK","GET_BUCKET_ENCRYPTION","GET_OBJECT_LOCK"]
RECOVERY_OWNER_APPROVAL="APPROVED_FOR_SINGLE_STAGE_B_CONTINUATION"
OWNER_APPROVED_CONTINUATION_CUTOFF=datetime(2026,10,9,4,0,tzinfo=UTC)
RECOVERY_APPROVAL_SCOPE={"single_continuation":True,"maximum_runtime_minutes":55,
                         "pilot":False,"training":False,"scoring":False,"trading":False}


def _load_object(path: Path) -> tuple[dict[str,Any],bytes]:
    raw=path.read_bytes()
    try: value=json.loads(raw)
    except (UnicodeDecodeError,json.JSONDecodeError) as exc: raise CaptureError("RECOVERY_EVIDENCE_INVALID",str(path)) from exc
    if not isinstance(value,dict): raise CaptureError("RECOVERY_EVIDENCE_INVALID",str(path))
    return value,raw


def validate_s3_incident_ledger(ledger: S3OperationLedger) -> dict[str,Any]:
    rows=ledger.records()  # validates the complete hash chain
    by_id: dict[str,list[dict[str,Any]]]={}
    for row in rows: by_id.setdefault(str(row.get("operation_id")),[]).append(row)
    expected_ids=[f"s3-{n:04d}" for n in range(1,7)]
    if list(by_id)!=expected_ids: raise CaptureError("RECOVERY_S3_OPERATION_SET_MISMATCH")
    for operation_id,wanted in zip(expected_ids,EXPECTED_S3_CONFIGURATION_OPERATIONS):
        events=by_id[operation_id]
        if [x.get("event") for x in events] != ["RESERVED","TRANSMITTING","SUCCEEDED"]:
            raise CaptureError("RECOVERY_S3_STATE_INVALID",operation_id)
        if events[0].get("operation")!=wanted or any(x.get("operation_id")!=operation_id for x in events):
            raise CaptureError("RECOVERY_S3_OPERATION_SET_MISMATCH",operation_id)
    return {"record_count":len(rows),"operation_count":6,"operations":EXPECTED_S3_CONFIGURATION_OPERATIONS,
            "ledger_head_sha256":rows[-1]["record_sha256"]}


def inspect_runtime_evidence(root: Path, authorization_path: Path, failure_path: Path, *,
                             authorization_pin: str=ORIGINAL_AUTHORIZATION_PIN,
                             authorization_file_sha256: str=ORIGINAL_AUTHORIZATION_FILE_SHA256,
                             now: datetime|None=None) -> dict[str,Any]:
    root=root.resolve(); auth,auth_bytes=_load_object(authorization_path); failure,failure_bytes=_load_object(failure_path)
    hashes=authorization_hashes(auth,file_bytes=auth_bytes)
    if (hashes["external_complete_canonical_sha256"]!=authorization_pin
            or hashes["file_byte_sha256"]!=authorization_file_sha256
            or not hashes["internal_content_valid"]):
        raise CaptureError("RECOVERY_ORIGINAL_AUTHORIZATION_MISMATCH")
    primary=ImmutableStore(root/"primary")
    claim_path=primary.root/"run/execution-claim.json"
    claim,claim_bytes=_load_object(claim_path)
    if (claim.get("authorization_sha256")!=authorization_pin
            or claim.get("execution_purpose")!="OPERATIONAL_VERIFICATION_ONLY"):
        raise CaptureError("RECOVERY_ORIGINAL_CLAIM_MISMATCH")
    if (failure.get("status")!="FAIL" or failure.get("error_code")!="PROVIDER_CREDENTIAL_MISSING"
            or failure.get("stage_b_executed") is not False):
        raise CaptureError("RECOVERY_FAILURE_SHAPE_MISMATCH")
    s3=validate_s3_incident_ledger(S3OperationLedger(ImmutableStore(root/"s3-operation-ledger")))
    massive_path=primary.root/"acquisition_attempts.jsonl"
    massive_rows=AttemptLedger(primary,synthetic=False).records() if massive_path.exists() else []
    if massive_rows: raise CaptureError("RECOVERY_MASSIVE_ACTIVITY_PRESENT")
    forbidden=[]
    for relative in ("run/continuation-claim.json","handoff/handoff.json"):
        if (primary.root/relative).exists(): forbidden.append(relative)
    for directory in (primary.root/"responses",primary.root/"backup",root/"isolated-restore"):
        if directory.exists() and any(p.is_file() and not p.name.startswith(".") for p in directory.rglob("*")): forbidden.append(str(directory))
    if any((primary.root/"run").glob("outcome-*.json")): forbidden.append("run/outcome-*.json")
    if forbidden: raise CaptureError("RECOVERY_LATER_PHASE_EVIDENCE_PRESENT",",".join(forbidden))
    valid_until=parse_utc_timestamp(auth.get("execution_valid_until"))
    instant=(now or datetime.now(UTC)).astimezone(UTC)
    inventory=[]
    for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.name.startswith(".")):
        inventory.append({"path":path.relative_to(root).as_posix(),"bytes":path.stat().st_size,"sha256":sha256_bytes(path.read_bytes())})
    retained_bytes=sum(row["bytes"] for row in inventory)
    if retained_bytes+65_536>PRIMARY_CAP: raise CaptureError("RECOVERY_EVIDENCE_CAPACITY_CONFLICT")
    result={"schema_version":"alpha-atlas-v4-stage-b-recovery-inspection.v1","status":"ELIGIBLE_EVIDENCE_EXPIRED_INTERVAL" if instant>=valid_until else "ELIGIBLE_EVIDENCE",
            "read_only":True,"original_authorization_pin":authorization_pin,"original_authorization_file_sha256":authorization_file_sha256,
            "original_claim_sha256":sha256_bytes(claim_bytes),"failure_report_sha256":sha256_bytes(failure_bytes),
            "s3":s3,"massive":{"record_count":0,"reservation_count":0},"acquisition_started":False,
            "backup_upload_started":False,"restore_started":False,"successful_completion":False,
            "execution_valid_until":valid_until.isoformat(),"interval_expired":instant>=valid_until,
            "evidence_inventory":inventory,
            "retained_local_bytes":retained_bytes,"recovery_record_reserve_bytes":65_536,"primary_cap_bytes":PRIMARY_CAP,
            "retrospective_limits":"The legacy source order plus claim, valid six-operation ledger, failure report, and absence of Massive ledger/source objects establish initialization stopped before acquisition; no retrospective phase timestamp is invented."}
    result["content_sha256"]=sha256_bytes(canonical_bytes(result)); return result


def prepare_recovery_binding(repo: Path, inspection: Mapping[str,Any], *, valid_from: str, valid_until: str,
                             now: datetime|None=None) -> dict[str,Any]:
    value=dict(inspection); claimed=value.pop("content_sha256",None)
    if sha256_bytes(canonical_bytes(value))!=claimed: raise CaptureError("RECOVERY_INSPECTION_HASH_MISMATCH")
    start=parse_utc_timestamp(valid_from); end=parse_utc_timestamp(valid_until); instant=(now or datetime.now(UTC)).astimezone(UTC)
    if start>=end or end<=instant: raise CaptureError("RECOVERY_INTERVAL_INVALID")
    head=subprocess.run(["git","rev-parse","HEAD"],cwd=repo,check=True,text=True,capture_output=True).stdout.strip()
    source_names=["moneybot/services/alpha_atlas_v4_stage_b.py","moneybot/services/alpha_atlas_v4_stage_b_recovery.py","scripts/run_alpha_atlas_v4_stage_b_operational.py"]
    out={"schema_version":"alpha-atlas-v4-stage-b-recovery-authorization.v1","status":"PREPARED_FOR_CONTINUATION_APPROVAL_NOT_APPROVED",
         "execution_gate_usable":False,"owner_approval":"NOT_GIVEN_FOR_CONTINUATION","stage_b_execution":"INITIALIZATION_FAILED_CONTINUATION_NOT_AUTHORIZED",
         "execution_purpose":"OPERATIONAL_VERIFICATION_ONLY","original_authorization_pin":inspection["original_authorization_pin"],
         "original_claim_sha256":inspection["original_claim_sha256"],"failure_report_sha256":inspection["failure_report_sha256"],
         "inspection_content_sha256":claimed,"s3_ledger_head_sha256":inspection["s3"]["ledger_head_sha256"],"consumed_s3_operations":6,
         "massive_attempts_consumed":0,"budgets":dict(REGISTERED_BUDGETS),"execution_valid_from":start.isoformat(),"execution_valid_until":end.isoformat(),
         "maximum_total_runtime_minutes":55,"original_elapsed_seconds":"UNKNOWN_LEGACY_NOT_RECORDED",
         "remaining_runtime_rule":"min(approved_interval_end, continuation_start + 55 minutes); the legacy unknown elapsed time is disclosed and not reconstructed",
         "owner_accepts_unknown_legacy_elapsed":False,
         "repaired_revision":{"git_commit":head,"source_file_sha256s":{name:sha256_bytes((repo/name).read_bytes()) for name in source_names}},
         "single_continuation":True,"independent_second_run":False}
    out["content_sha256"]=sha256_bytes(canonical_bytes(out)); return out


def validate_recovery_authorization(value: Mapping[str,Any], *, expected_pin: str, now: datetime, repo: Path) -> None:
    hashes=authorization_hashes(value)
    if not hashes["internal_content_valid"] or hashes["external_complete_canonical_sha256"]!=expected_pin: raise CaptureError("RECOVERY_AUTHORIZATION_HASH_MISMATCH")
    if (value.get("status")!="APPROVED" or value.get("execution_gate_usable") is not True
            or value.get("owner_approval")!=RECOVERY_OWNER_APPROVAL or value.get("stage_b_execution")!="AUTHORIZED_CONTINUATION_NOT_EXECUTED"
            or value.get("single_continuation") is not True or value.get("independent_second_run") is not False
            or value.get("owner_accepts_unknown_legacy_elapsed") is not True):
        raise CaptureError("RECOVERY_NOT_AUTHORIZED")
    if value.get("approval_scope")!=RECOVERY_APPROVAL_SCOPE or value.get("maximum_total_runtime_minutes")!=55:
        raise CaptureError("RECOVERY_NOT_AUTHORIZED")
    if value.get("budgets")!=REGISTERED_BUDGETS or value.get("consumed_s3_operations")!=6 or value.get("massive_attempts_consumed")!=0:
        raise CaptureError("RECOVERY_BUDGET_MISMATCH")
    start=parse_utc_timestamp(value.get("execution_valid_from")); end=parse_utc_timestamp(value.get("execution_valid_until")); instant=now.astimezone(UTC)
    if start>=end or instant<start or instant>=end or end>OWNER_APPROVED_CONTINUATION_CUTOFF:
        raise CaptureError("RECOVERY_INTERVAL_INVALID")
    revision=value.get("repaired_revision",{}); head=subprocess.run(["git","rev-parse","HEAD"],cwd=repo,check=True,text=True,capture_output=True).stdout.strip()
    if revision.get("git_commit")!=head: raise CaptureError("RECOVERY_REVISION_MISMATCH")
    for name,wanted in revision.get("source_file_sha256s",{}).items():
        path=(repo/str(name)).resolve()
        if not path.is_relative_to(repo.resolve()) or not path.is_file() or sha256_bytes(path.read_bytes())!=wanted: raise CaptureError("RECOVERY_REVISION_MISMATCH",str(name))


def publish_continuation_claim(primary: ImmutableStore, recovery: Mapping[str,Any], *, now: datetime) -> dict[str,Any]:
    original=primary.root/"run/execution-claim.json"; target=primary.root/"run/continuation-claim.json"
    with primary.lock():
        if not original.is_file(): raise CaptureError("RECOVERY_ORIGINAL_CLAIM_MISSING")
        if target.exists(): raise CaptureError("CONTINUATION_ALREADY_CLAIMED")
        massive=AttemptLedger(primary,synthetic=False).records() if (primary.root/"acquisition_attempts.jsonl").exists() else []
        if massive: raise CaptureError("RECOVERY_MASSIVE_ACTIVITY_PRESENT")
        claim={"schema_version":"alpha-atlas-v4-stage-b-continuation-claim.v1","original_claim_sha256":sha256_bytes(original.read_bytes()),
               "original_authorization_pin":recovery["original_authorization_pin"],"recovery_authorization_sha256":authorization_hashes(recovery)["external_complete_canonical_sha256"],
               "s3_ledger_head_sha256":recovery["s3_ledger_head_sha256"],"claimed_at":now.astimezone(UTC).isoformat()}
        return primary.publish("run/continuation-claim.json",canonical_bytes(claim))
