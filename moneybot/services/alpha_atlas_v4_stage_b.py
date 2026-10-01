"""Stage B operational components.

Live execution is deliberately unavailable without a separately supplied, hash-bound
APPROVED authorization.  The shipped CLI uses only in-memory transports/storage.
No boto SDK is imported, so offline execution cannot discover AWS credentials or
contact an instance metadata service.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import resource
import ssl
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import parse_qsl, urlencode, urlparse

from moneybot.services.alpha_atlas_v4_acquisition import (
    ADJUSTMENT_ENGINE_VERSION, ALLOWED_HOST, AcquisitionRunner, AttemptLedger,
    RequestSpec, TransportResponse, acquisition_clock, adjust_unadjusted_bars,
    build_handoff, cache_only_handoff, validate_feature_window,
)
from moneybot.services.alpha_atlas_v4_prospective_snapshot import (
    CaptureError, ImmutableStore, canonical_bytes, sha256_bytes,
)

UTC = timezone.utc
SESSION = date(2026, 10, 5)
SETUP_PACKAGE_FILE_SHA256 = "3524360467fbd47a3eeb7dfbd4252d82dfda9b407354a2807268d6acb5ba5a79"
FIXTURE_FILE_SHA256 = "ef391a01d97a3f170b434ea04022dff16922b13be677ba68996fad229a219996"
FIXTURE_CONTENT_SHA256 = "1e59160a60c0f2db6e8f268a8bc6f7eb30db7444435b8a8bd59582c77126d33b"
PRIMARY_CAP = BACKUP_CAP = 4_067_328
COMBINED_CAP = 8_134_656
S3_OPERATION_CAP = 512
REQUIRED_ROOT = "/var/data/moneybot-stage-b"


def verify_stage_b_documents(repo: Path) -> dict[str, str]:
    reports = repo / "docs" / "reports"
    expected = {
        "alpha_atlas_v4_stage_b_setup_package.v1.json": SETUP_PACKAGE_FILE_SHA256,
        "alpha_atlas_v4_stage_b_verification_manifest.v1.json": FIXTURE_FILE_SHA256,
    }
    found = {}
    for name, wanted in expected.items():
        actual = sha256_bytes((reports / name).read_bytes())
        if actual != wanted:
            raise CaptureError("BOUND_DOCUMENT_HASH_MISMATCH", name)
        found[name] = actual
    return found


def load_fixture(repo: Path) -> dict[str, Any]:
    raw = json.loads((repo / "docs/reports/alpha_atlas_v4_stage_b_verification_manifest.v1.json").read_text())
    claimed = raw.pop("content_sha256", None)
    if claimed != FIXTURE_CONTENT_SHA256 or hashlib.sha256(json.dumps(raw,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest() != claimed:
        raise CaptureError("FIXTURE_HASH_MISMATCH")
    raw["content_sha256"] = claimed
    if raw.get("verification_session") != SESSION.isoformat():
        raise CaptureError("FIXTURE_DATE_MISMATCH")
    return raw


def validate_effective_sector_evidence(evidence: Mapping[str, Any], fixture_hash: str) -> str:
    value = dict(evidence); claimed = value.pop("content_sha256", None)
    if sha256_bytes(canonical_bytes(value)) != claimed:
        raise CaptureError("SECTOR_EVIDENCE_HASH_MISMATCH")
    if value.get("fixture_sha256") != fixture_hash or value.get("ticker") != "AAPL" or value.get("sector_etf") != "XLK":
        raise CaptureError("SECTOR_EVIDENCE_MISMATCH")
    if value.get("effective_from") > SESSION.isoformat() or value.get("effective_through") < SESSION.isoformat():
        raise CaptureError("SECTOR_EVIDENCE_NOT_EFFECTIVE")
    if not value.get("source_identity") or not value.get("source_sha256"):
        raise CaptureError("SECTOR_EVIDENCE_PROVENANCE_MISSING")
    return str(claimed)


def validate_authorization(auth: Mapping[str, Any], *, fixture_sha256: str, setup_sha256: str, offline: bool) -> None:
    value = dict(auth); claimed = value.pop("content_sha256", None)
    if sha256_bytes(canonical_bytes(value)) != claimed:
        raise CaptureError("AUTHORIZATION_HASH_MISMATCH")
    wanted_status = "OFFLINE_SYNTHETIC_ONLY" if offline else "APPROVED"
    if value.get("status") != wanted_status or value.get("mode") != ("OFFLINE" if offline else "LIVE"):
        raise CaptureError("EXECUTION_NOT_AUTHORIZED")
    if value.get("fixture_sha256") != fixture_sha256 or value.get("setup_package_sha256") != setup_sha256:
        raise CaptureError("AUTHORIZATION_BINDING_MISMATCH")
    if value.get("session") != SESSION.isoformat() or value.get("maximum_attempts") != 18:
        raise CaptureError("AUTHORIZATION_PLAN_MISMATCH")


def plan_from_fixture(fixture: Mapping[str, Any], sector_evidence_sha256: str) -> dict[str, Any]:
    if sector_evidence_sha256 == "":
        raise CaptureError("SECTOR_EVIDENCE_REQUIRED")
    requests = []
    for row in fixture["requests_in_order"]:
        requests.append(RequestSpec(row["request_id"], row["family"], row["path"], row["params"],
                                    None if row["family"] == "splits" else row["request_id"].split(":", 1)[1],
                                    row["page_cap"], row["response_limit_bytes"]))
    if [x.request_id for x in requests] != ["history:AAPL", "history:SPY", "history:XLK", "identity:AAPL", "splits:global"]:
        raise CaptureError("FIXTURE_ORDER_MISMATCH")
    return {"schema_version":"alpha-atlas-v4-stage-b-plan.v1", "stage":"operational_verification",
            "session":SESSION.isoformat(), "window_sessions":[], "universe_sha256":fixture["content_sha256"],
            "sector_mapping_sha256":sector_evidence_sha256, "stocks":["AAPL"],
            "context_symbols":["SPY","XLK"], "requests":requests}


class _NoRedirect:
    """One-call HTTPS sender; redirects and library retries are forbidden."""
    def __init__(self, api_key: str, connection_factory: Callable[..., Any] = http.client.HTTPSConnection):
        if not api_key: raise CaptureError("PROVIDER_CREDENTIAL_MISSING")
        self._api_key, self._factory = api_key, connection_factory

    def __call__(self, path: str, timeout: float) -> tuple[int, bytes, Mapping[str, str]]:
        conn = self._factory(ALLOWED_HOST, timeout=timeout, context=ssl.create_default_context())
        try:
            conn.request("GET", path, headers={"Authorization": f"Bearer {self._api_key}", "Accept":"application/json"})
            response = conn.getresponse(); body = response.read()
            return int(response.status), body, {k.lower(): v for k, v in response.getheaders()}
        finally:
            conn.close()


class MassiveStageBTransport:
    """Bound Massive transport. Each send performs exactly one HTTP exchange."""
    def __init__(self, fixture: Mapping[str, Any], api_key: str, *, sender: Callable[[str, float], tuple[int, bytes, Mapping[str,str]]] | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self._allowed = {r["request_id"]:(r["path"], {str(k):str(v) for k,v in r["params"].items()}) for r in fixture["requests_in_order"]}
        self._sender = sender or _NoRedirect(api_key)
        self._clock = clock

    def send(self, request: RequestSpec, *, page_url: str | None, timeout_seconds: float) -> TransportResponse:
        if request.request_id not in self._allowed or self._allowed[request.request_id] != (request.endpoint, dict(request.params)):
            raise CaptureError("REQUEST_OUTSIDE_BOUND_PLAN")
        if timeout_seconds <= 0 or timeout_seconds > 6:
            raise CaptureError("INVALID_REQUEST_TIMEOUT")
        if page_url:
            parsed=urlparse(page_url)
            query_keys={key for key,_ in parse_qsl(parsed.query,keep_blank_values=True)}
            allowed_query=set(request.params)|{"cursor"}
            if (parsed.scheme!="https" or parsed.hostname!=ALLOWED_HOST or parsed.username or parsed.password or parsed.fragment
                    or request.family!="splits" or parsed.path!=request.endpoint or not query_keys<=allowed_query):
                raise CaptureError("UNSAFE_PAGINATION_URL")
            path=parsed.path+("?"+parsed.query if parsed.query else "")
        else:
            path=request.endpoint+"?"+urlencode(request.params)
        if any(secret in path.lower() for secret in ("apikey=","api_key=","token=","authorization=")):
            raise CaptureError("CREDENTIAL_IN_URL")
        status, body, headers = self._sender(path, timeout_seconds)
        if len(body)>request.response_limit:
            raise CaptureError("RESPONSE_TOO_LARGE")
        if 300 <= status < 400:
            raise CaptureError("HTTP_REDIRECT_FORBIDDEN")
        # Receipt time is measured after the bytes are returned, never derived from bars.
        return TransportResponse(status, body, self._clock().astimezone(UTC))


def resolve_identity(result: Mapping[str, Any]) -> dict[str, str]:
    rows=[row for obj in result.get("objects",[]) for row in obj.get("payload",{}).get("results",[])]
    if len(rows)!=1: raise CaptureError("IDENTITY_AMBIGUOUS")
    row=rows[0]
    # composite/share-class FIGI is security-level; CIK alone is explicitly rejected.
    ids={k:str(row[k]) for k in ("share_class_figi","composite_figi") if row.get(k)}
    if not ids or row.get("ticker")!="AAPL" or row.get("type") not in {"CS","COMMON_STOCK"}:
        raise CaptureError("IDENTITY_UNRESOLVED")
    return ids


class S3Client(Protocol):
    def get_bucket_location(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def get_bucket_versioning(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def get_object_lock_configuration(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def put_object(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def get_object(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def head_object(self, **kwargs: Any) -> Mapping[str, Any]: ...


class S3OperationLedger:
    def __init__(self, store: ImmutableStore): self.store=store; self.path=store.root/"s3_operations.jsonl"
    def records(self) -> list[dict[str,Any]]:
        if not self.path.exists(): return []
        out=[]
        for line in self.path.read_text().splitlines():
            row=json.loads(line); digest=row.pop("record_sha256",None)
            if sha256_bytes(canonical_bytes(row))!=digest: raise CaptureError("S3_LEDGER_CORRUPT")
            row["record_sha256"]=digest; out.append(row)
        return out
    def add(self, operation: str, **values: Any) -> None:
        if len(self.records())>=S3_OPERATION_CAP: raise CaptureError("S3_OPERATION_BUDGET_EXHAUSTED")
        row={"operation":operation,**values}; row["record_sha256"]=sha256_bytes(canonical_bytes(row))
        self.store.append_record("s3_operations.jsonl",row)


class S3EvidenceBackup:
    def __init__(self, client: S3Client, bucket: str, prefix: str, ledger: S3OperationLedger, *, now: Callable[[],datetime]=lambda:datetime.now(UTC)):
        self.client,self.bucket,self.prefix,self.ledger,self.now=client,bucket,prefix.strip("/"),ledger,now

    def verify_configuration(self) -> None:
        location=self.client.get_bucket_location(Bucket=self.bucket); self.ledger.add("GET_BUCKET_LOCATION")
        if location.get("LocationConstraint") not in {None,"us-east-1"}: raise CaptureError("S3_REGION_MISMATCH")
        versioning=self.client.get_bucket_versioning(Bucket=self.bucket); self.ledger.add("GET_BUCKET_VERSIONING")
        if versioning.get("Status")!="Enabled": raise CaptureError("S3_VERSIONING_REQUIRED")
        lock=self.client.get_object_lock_configuration(Bucket=self.bucket); self.ledger.add("GET_OBJECT_LOCK")
        rule=lock.get("ObjectLockConfiguration",{}).get("Rule",{}).get("DefaultRetention",{})
        if lock.get("ObjectLockConfiguration",{}).get("ObjectLockEnabled")!="Enabled" or rule.get("Mode")!="GOVERNANCE" or int(rule.get("Days",0))<180:
            raise CaptureError("S3_RETENTION_CONFIGURATION_INVALID")

    def publish(self, relative: str, payload: bytes) -> dict[str,Any]:
        digest=sha256_bytes(payload); key=f"{self.prefix}/{relative}.{digest}"
        retain_until=self.now().astimezone(UTC)+timedelta(days=180)
        checksum=base64.b64encode(hashlib.sha256(payload).digest()).decode()
        response=self.client.put_object(Bucket=self.bucket,Key=key,Body=payload,ServerSideEncryption="AES256",ChecksumSHA256=checksum,ObjectLockMode="GOVERNANCE",ObjectLockRetainUntilDate=retain_until)
        self.ledger.add("PUT_OBJECT",key=key,bytes=len(payload),sha256=digest)
        version=response.get("VersionId")
        if not version: raise CaptureError("S3_VERSION_ID_MISSING")
        head=self.client.head_object(Bucket=self.bucket,Key=key,VersionId=version); self.ledger.add("HEAD_OBJECT",key=key,version_id=version)
        if head.get("ServerSideEncryption")!="AES256" or head.get("ObjectLockMode")!="GOVERNANCE" or not head.get("ObjectLockRetainUntilDate"):
            raise CaptureError("S3_RETENTION_EVIDENCE_MISSING")
        got=self.client.get_object(Bucket=self.bucket,Key=key,VersionId=version); self.ledger.add("GET_OBJECT_VERIFY",key=key,version_id=version)
        body=got["Body"].read() if hasattr(got["Body"],"read") else bytes(got["Body"])
        if sha256_bytes(body)!=digest: raise CaptureError("S3_CHECKSUM_MISMATCH")
        return {"bucket":self.bucket,"key":key,"version_id":version,"sha256":digest,"bytes":len(payload),"retention_mode":"GOVERNANCE","retain_until":head["ObjectLockRetainUntilDate"].isoformat()}

    def restore(self, receipt: Mapping[str,Any], destination: ImmutableStore) -> dict[str,Any]:
        got=self.client.get_object(Bucket=self.bucket,Key=receipt["key"],VersionId=receipt["version_id"])
        self.ledger.add("GET_OBJECT_RESTORE",key=receipt["key"],version_id=receipt["version_id"])
        payload=got["Body"].read() if hasattr(got["Body"],"read") else bytes(got["Body"])
        if sha256_bytes(payload)!=receipt["sha256"] or len(payload)!=receipt["bytes"]: raise CaptureError("S3_RESTORE_CHECKSUM_MISMATCH")
        path=f"restored/{receipt['version_id']}/{Path(str(receipt['key'])).name}"
        result=destination.publish(path,payload); destination.verify(path,receipt["sha256"],receipt["bytes"]); return result


def storage_preflight(root: Path, *, minimum_free: int=COMBINED_CAP) -> dict[str,Any]:
    root.mkdir(parents=True,exist_ok=True)
    if not os.access(root,os.W_OK): raise CaptureError("PRIMARY_ROOT_NOT_WRITABLE")
    stats=os.statvfs(root); free=stats.f_bavail*stats.f_frsize
    if free<minimum_free: raise CaptureError("PRIMARY_CAPACITY_INSUFFICIENT")
    return {"root":str(root),"free_bytes":free,"required_bytes":minimum_free}


def telemetry(started: datetime, ended: datetime) -> dict[str,Any]:
    usage=resource.getrusage(resource.RUSAGE_SELF)
    return {"runtime_seconds":(ended-started).total_seconds(),"max_rss_platform_units":usage.ru_maxrss,"user_cpu_seconds":usage.ru_utime,"system_cpu_seconds":usage.ru_stime}


def synthetic_authorization() -> dict[str,Any]:
    value={"status":"OFFLINE_SYNTHETIC_ONLY","mode":"OFFLINE","fixture_sha256":FIXTURE_CONTENT_SHA256,"setup_package_sha256":SETUP_PACKAGE_FILE_SHA256,"session":SESSION.isoformat(),"maximum_attempts":18}
    value["content_sha256"]=sha256_bytes(canonical_bytes(value)); return value


def effective_sector_fixture() -> dict[str,Any]:
    value={"fixture_sha256":FIXTURE_CONTENT_SHA256,"ticker":"AAPL","sector_etf":"XLK","effective_from":"2026-10-05","effective_through":"2026-10-05","source_identity":"SYNTHETIC_STAGE_B_TEST_ONLY","source_sha256":sha256_bytes(b"synthetic-sector-source")}
    value["content_sha256"]=sha256_bytes(canonical_bytes(value)); return value

class StageBRunner:
    """One fixed-session runner. Preflight completes before transport construction."""
    def __init__(self, repo: Path, root: Path, *, offline: bool,
                 approved_authorization_sha256: str | None = None,
                 now: Callable[[],datetime]=lambda:datetime.now(UTC)):
        self.repo,self.root,self.offline,self.now=repo,root,offline,now
        self.approved_authorization_sha256=approved_authorization_sha256

    def preflight(self, authorization: Mapping[str,Any], sector_evidence: Mapping[str,Any]) -> tuple[dict[str,Any],dict[str,Any]]:
        bound=verify_stage_b_documents(self.repo); fixture=load_fixture(self.repo)
        validate_authorization(authorization,fixture_sha256=FIXTURE_CONTENT_SHA256,setup_sha256=SETUP_PACKAGE_FILE_SHA256,offline=self.offline)
        authorization_sha256=sha256_bytes(canonical_bytes(authorization))
        if not self.offline and (not self.approved_authorization_sha256 or authorization_sha256!=self.approved_authorization_sha256):
            raise CaptureError("APPROVED_AUTHORIZATION_HASH_REQUIRED")
        sector_hash=validate_effective_sector_evidence(sector_evidence,FIXTURE_CONTENT_SHA256)
        instant=self.now().astimezone(UTC)
        if instant.date()>SESSION or instant>acquisition_clock(SESSION).cutoff:
            raise CaptureError("FIXED_SESSION_EXPIRED")
        if not self.offline and (str(self.root)!=REQUIRED_ROOT or os.environ.get("MONEYBOT_PERSISTENT_DATA_DIR")!=REQUIRED_ROOT):
            raise CaptureError("PERSISTENT_ROOT_MISMATCH")
        storage=storage_preflight(self.root)
        return plan_from_fixture(fixture,sector_hash),{"bound_documents":bound,"storage":storage}

    def execute(self, authorization: Mapping[str,Any], sector_evidence: Mapping[str,Any],
                transport_factory: Callable[[],Any], backup_factory: Callable[[ImmutableStore],S3EvidenceBackup]) -> dict[str,Any]:
        started=self.now(); plan,preflight=self.preflight(authorization,sector_evidence)
        # Credential discovery and client construction happen only after all local gates pass.
        primary=ImmutableStore(self.root/"primary")
        backup=backup_factory(primary); backup.verify_configuration()
        transport=transport_factory()
        runner=AcquisitionRunner(primary,transport,synthetic=self.offline,
                                 live_authorization_sha256=None if self.offline else self.approved_authorization_sha256,
                                 clock=self.now)
        results={}; quarantined=[]; identity=None; timing=acquisition_clock(SESSION)
        for spec in plan["requests"]:
            results[spec.request_id]=runner.execute(spec,"operational_verification",timing)
            if spec.family=="history": quarantined.append(spec.request_id)
            if spec.family=="identity": identity=resolve_identity(results[spec.request_id])
        if identity is None: raise CaptureError("IDENTITY_UNRESOLVED")
        rows={key:[r for obj in value["objects"] for r in obj["payload"]["results"] for key2 in [key]] for key,value in results.items() if key.startswith("history:")}
        dates=lambda values:[str(x.get("date")) for x in values if x.get("date")]
        feature_reasons=validate_feature_window(dates(rows["history:AAPL"]),dates(rows["history:SPY"]),dates(rows["history:XLK"]))
        if feature_reasons: raise CaptureError("FEATURE_WINDOW_INVALID",",".join(feature_reasons))
        split_rows=[r for obj in results["splits:global"]["objects"] for r in obj["payload"]["results"] if r.get("ticker") in {"AAPL","SPY","XLK"}]
        source_sha=results["history:AAPL"]["objects"][0]["sha256"]
        adjusted=adjust_unadjusted_bars(rows["history:AAPL"],split_rows,SESSION,source_sha256=source_sha)
        handoff=build_handoff(plan,results,generated_at=self.now(),timing=timing,adjustment_bindings={"AAPL":adjusted["binding"]})
        if not handoff["eligible"]: raise CaptureError("HANDOFF_INELIGIBLE",",".join(handoff["reason_codes"]))
        handoff["resolved_identity"]=identity; handoff["quarantine_released"]=sorted(quarantined)
        handoff["handoff_sha256"]=sha256_bytes(canonical_bytes({k:v for k,v in handoff.items() if k!="handoff_sha256"}))
        payload=canonical_bytes(cache_only_handoff(handoff)); primary_result=primary.publish("handoff/handoff.json",payload)
        primary.verify(primary_result["path"],primary_result["sha256"],primary_result["bytes"])
        primary_bytes=sum(p.stat().st_size for p in primary.root.rglob("*") if p.is_file())
        if primary_bytes>PRIMARY_CAP: raise CaptureError("PRIMARY_EVIDENCE_LIMIT")
        backup_receipts=[]; backup_bytes=0
        for source in sorted(p for p in primary.root.rglob("*") if p.is_file() and not p.name.startswith(".")):
            relative=source.relative_to(primary.root).as_posix(); receipt=backup.publish(relative,source.read_bytes())
            backup_receipts.append(receipt); backup_bytes+=receipt["bytes"]
            if backup_bytes>BACKUP_CAP or primary_bytes+backup_bytes>COMBINED_CAP: raise CaptureError("STAGE_B_EVIDENCE_LIMIT")
        restore_store=ImmutableStore(self.root/"isolated-restore"); restored=[]
        for receipt in backup_receipts: restored.append(backup.restore(receipt,restore_store))
        ended=self.now()
        return {"status":"PASS","mode":"OFFLINE_SYNTHETIC" if self.offline else "LIVE_STAGE_B","session":SESSION.isoformat(),
                "synthetic_transport_attempts":len([x for x in runner.ledger.records() if x["event"]=="RESERVED"]) if self.offline else 0,
                "live_provider_requests":0 if self.offline else len([x for x in runner.ledger.records() if x["event"]=="RESERVED"]),
                "real_acquisition_authorized":not self.offline,"preflight":preflight,"identity":identity,
                "quarantined_then_released":sorted(quarantined),"handoff_sha256":handoff["handoff_sha256"],
                "primary":{"bytes_accounted":primary_bytes,"cap":PRIMARY_CAP,"read_back":True},
                "backup":{"objects":len(backup_receipts),"bytes_accounted":backup_bytes,"cap":BACKUP_CAP,"receipts":backup_receipts},
                "restore":{"objects":len(restored),"verified":True},
                "s3_operations":len(backup.ledger.records()),"telemetry":telemetry(started,ended)}
