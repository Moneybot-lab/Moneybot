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
RUNTIME_CONFIG_FILE = "alpha_atlas_v4_stage_b_runtime_config.v1.json"
RUNTIME_CONFIG_FILE_SHA256 = "860e916a0d2152c603f26f0f0438308539aa4b7ede704639bd731755c9ff9463"
RUNTIME_CONFIG_CONTENT_SHA256 = "4ac339c8caa01c625b54bb53a51b7fef7f294082a2cbe1c52bf9814c7bf03a33"
BOUND_BUCKET = "moneybot-alpha-atlas-backup-20261006"
BOUND_PREFIX = "stage-b/"
AWS_ACCESS_KEY_ENV = "MONEYBOT_STAGE_B_AWS_ACCESS_KEY_ID"
AWS_SECRET_KEY_ENV = "MONEYBOT_STAGE_B_AWS_SECRET_ACCESS_KEY"
IAM_ACTIONS = {"HEAD_BUCKET":"s3:ListBucket","GET_BUCKET_LOCATION":"s3:GetBucketLocation","GET_BUCKET_VERSIONING":"s3:GetBucketVersioning","GET_PUBLIC_ACCESS_BLOCK":"s3:GetBucketPublicAccessBlock","GET_BUCKET_ENCRYPTION":"s3:GetEncryptionConfiguration","GET_OBJECT_LOCK":"s3:GetBucketObjectLockConfiguration","PUT_OBJECT":"s3:PutObject+s3:PutObjectRetention","HEAD_OBJECT":"s3:GetObjectVersion","GET_OBJECT_RETENTION":"s3:GetObjectRetention","GET_OBJECT_VERIFY":"s3:GetObjectVersion","GET_OBJECT_RESTORE":"s3:GetObjectVersion"}
REQUIRED_BUCKET_ACTIONS = {"s3:ListBucket","s3:GetBucketLocation","s3:GetBucketVersioning","s3:GetBucketPublicAccessBlock","s3:GetEncryptionConfiguration","s3:GetBucketObjectLockConfiguration"}
REQUIRED_OBJECT_ACTIONS = {"s3:PutObject","s3:PutObjectRetention","s3:GetObject","s3:GetObjectVersion","s3:GetObjectRetention"}
FORBIDDEN_ACTIONS = {"s3:DeleteObject","s3:DeleteObjectVersion","s3:BypassGovernanceRetention","s3:PutBucketPolicy","s3:PutBucketVersioning"}


def validate_iam_policy_scope(bucket_actions: set[str], object_actions: set[str]) -> dict[str,Any]:
    missing_bucket=sorted(REQUIRED_BUCKET_ACTIONS-bucket_actions); missing_object=sorted(REQUIRED_OBJECT_ACTIONS-object_actions)
    forbidden=sorted((bucket_actions|object_actions)&FORBIDDEN_ACTIONS)
    if missing_bucket or missing_object: raise CaptureError("IAM_POLICY_ACTION_MISSING",json.dumps({"bucket":missing_bucket,"object":missing_object},sort_keys=True))
    if forbidden: raise CaptureError("IAM_POLICY_FORBIDDEN_ACTION",",".join(forbidden))
    return {"status":"COMPATIBLE","bucket_actions":sorted(bucket_actions),"object_actions":sorted(object_actions),"sdk_operation_mapping":dict(sorted(IAM_ACTIONS.items()))}



def load_runtime_config(path: Path, *, require_owner: bool=True) -> dict[str,Any]:
    config=json.loads(path.read_text()); claimed=config.pop("content_sha256",None)
    if sha256_bytes(canonical_bytes(config))!=claimed: raise CaptureError("RUNTIME_CONFIG_HASH_MISMATCH")
    config["content_sha256"]=claimed
    exact={"render_service":"moneybot-market-stream","disk_capacity_gb_decimal":3,"disk_mount":"/var/data","persistent_root":REQUIRED_ROOT,"s3_bucket":BOUND_BUCKET,"s3_region":"us-east-1","s3_prefix":BOUND_PREFIX}
    if any(config.get(k)!=v for k,v in exact.items()): raise CaptureError("RUNTIME_CONFIG_BINDING_MISMATCH")
    names=config.get("credential_environment_variables",{})
    if names!={"access_key_id":AWS_ACCESS_KEY_ENV,"secret_access_key":AWS_SECRET_KEY_ENV}: raise CaptureError("RUNTIME_CREDENTIAL_NAMES_MISMATCH")
    owner=config.get("s3_expected_owner")
    if owner is None and require_owner: raise CaptureError("S3_EXPECTED_OWNER_UNRESOLVED","Supply the verified 12-digit AWS account owner ID and recompute content_sha256")
    if owner is not None and (not isinstance(owner,str) or len(owner)!=12 or not owner.isdigit()): raise CaptureError("S3_EXPECTED_OWNER_INVALID")
    return config


def validate_s3_key(prefix: str, relative: str) -> str:
    if prefix!=BOUND_PREFIX or not relative or relative.startswith(("/","\\")) or "\\" in relative or any(part in {"",".",".."} for part in relative.split("/")):
        raise CaptureError("S3_PREFIX_ESCAPE")
    key=prefix+relative
    if not key.startswith(BOUND_PREFIX): raise CaptureError("S3_PREFIX_ESCAPE")
    return key



def backup_object_budget() -> dict[str,Any]:
    expected={"attempt_bodies_and_receipts":10,"massive_ledger":1,"handoff":1,"outcome":1,"inventory":1}
    maximum={"attempt_bodies_and_receipts":36,"massive_ledger":1,"handoff":1,"outcome":1,"inventory":1}
    return {"expected":{**expected,"frozen_objects":sum(expected.values()),"completion_objects":1,"primary_objects":sum(expected.values())+1},
            "maximum":{**maximum,"frozen_objects":sum(maximum.values()),"completion_objects":1,"primary_objects":sum(maximum.values())+1,"primary_objects_if_completion_backup_fails":sum(maximum.values())+2},
            "maximum_http_body_bytes":15*262144+3*32768,"maximum_attempt_receipt_reserve_bytes":18*8192,
            "maximum_attempt_evidence_bytes":15*262144+3*32768+18*8192,"primary_cap_bytes":PRIMARY_CAP,
            "attempt_evidence_cap_conflict_bytes":15*262144+3*32768+18*8192-PRIMARY_CAP,
            "acquisition_sublimit_bytes":PRIMARY_CAP-262144}


def s3_operation_budget(object_count: int) -> dict[str,int]:
    if object_count<0 or object_count>40: raise CaptureError("S3_OBJECT_COUNT_OUT_OF_BOUNDS")
    values={"configuration":6,"uploads":object_count,"version_heads":object_count,"retention_reads":object_count,"checksum_readbacks":object_count,"exact_version_restores":object_count,"completion_upload_verify":4}
    return {**values,"total":sum(values.values())}


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
    def head_bucket(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def get_bucket_location(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def get_bucket_versioning(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def get_public_access_block(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def get_bucket_encryption(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def get_object_lock_configuration(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def put_object(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def get_object(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def head_object(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def get_object_retention(self, **kwargs: Any) -> Mapping[str, Any]: ...


class S3OperationLedger:
    """Hash-checked operation ledger; every SDK call is reserved before invocation."""
    TERMINAL = {"SUCCEEDED", "FAILED", "UNCERTAIN"}
    def __init__(self, store: ImmutableStore): self.store=store; self.path=store.root/"s3_operations.jsonl"
    def records(self) -> list[dict[str,Any]]:
        if not self.path.exists(): return []
        out=[]; previous="0"*64
        for number,line in enumerate(self.path.read_text().splitlines(),1):
            try: row=json.loads(line)
            except json.JSONDecodeError as exc: raise CaptureError("S3_LEDGER_CORRUPT",str(number)) from exc
            digest=row.pop("record_sha256",None)
            if row.get("previous_sha256")!=previous or sha256_bytes(canonical_bytes(row))!=digest: raise CaptureError("S3_LEDGER_CORRUPT",str(number))
            row["record_sha256"]=digest; out.append(row); previous=digest
        return out
    def _append(self,row: dict[str,Any]) -> None:
        records=self.records(); value={**row,"previous_sha256":records[-1]["record_sha256"] if records else "0"*64}
        value["record_sha256"]=sha256_bytes(canonical_bytes(value)); self.store.append_record("s3_operations.jsonl",value)
    def reserve(self, operation: str, **values: Any) -> str:
        with self.store.lock():
            reservations=[r for r in self.records() if r["event"]=="RESERVED"]
            if len(reservations)>=S3_OPERATION_CAP: raise CaptureError("S3_OPERATION_BUDGET_EXHAUSTED")
            operation_id=f"s3-{len(reservations)+1:04d}"
            self._append({"event":"RESERVED","operation_id":operation_id,"operation":operation,"at":datetime.now(UTC).isoformat(),**values})
        return operation_id
    def event(self, operation_id: str, state: str, **values: Any) -> None:
        self._append({"event":state,"operation_id":operation_id,"at":datetime.now(UTC).isoformat(),**values})
    def reconcile_uncertain(self) -> int:
        rows=self.records(); reserved={r["operation_id"] for r in rows if r["event"]=="RESERVED"}; terminal={r["operation_id"] for r in rows if r["event"] in self.TERMINAL}
        for operation_id in sorted(reserved-terminal): self.event(operation_id,"UNCERTAIN",reason="RESTART_WITHOUT_TERMINAL_STATE")
        return len(reserved-terminal)
    def operation_count(self) -> int: return len([r for r in self.records() if r["event"]=="RESERVED"])


class S3EvidenceBackup:
    def __init__(self, client: S3Client, bucket: str, prefix: str, ledger: S3OperationLedger, *,
                 expected_owner: str, now: Callable[[],datetime]=lambda:datetime.now(UTC)):
        if not expected_owner or len(expected_owner)!=12 or not expected_owner.isdigit(): raise CaptureError("S3_EXPECTED_OWNER_REQUIRED")
        if bucket!=BOUND_BUCKET or prefix!=BOUND_PREFIX: raise CaptureError("S3_DESTINATION_BINDING_MISMATCH")
        self.client,self.bucket,self.prefix,self.ledger,self.now=client,bucket,prefix,ledger,now
        self.expected_owner=expected_owner
    def _call(self, operation: str, method: Callable[...,Any], **kwargs: Any) -> Any:
        operation_id=self.ledger.reserve(operation,bucket=self.bucket,key=kwargs.get("Key"),version_id=kwargs.get("VersionId"))
        self.ledger.event(operation_id,"TRANSMITTING")
        try: result=method(**kwargs)
        except Exception as exc:
            self.ledger.event(operation_id,"FAILED",reason=type(exc).__name__); raise
        self.ledger.event(operation_id,"SUCCEEDED"); return result
    def _bucket_args(self) -> dict[str,str]: return {"Bucket":self.bucket,"ExpectedBucketOwner":self.expected_owner}

    def verify_configuration(self) -> None:
        self._call("HEAD_BUCKET",self.client.head_bucket,**self._bucket_args())
        location=self._call("GET_BUCKET_LOCATION",self.client.get_bucket_location,**self._bucket_args())
        if location.get("LocationConstraint") not in {None,"us-east-1"}: raise CaptureError("S3_REGION_MISMATCH")
        versioning=self._call("GET_BUCKET_VERSIONING",self.client.get_bucket_versioning,**self._bucket_args())
        if versioning.get("Status")!="Enabled": raise CaptureError("S3_VERSIONING_REQUIRED")
        access=self._call("GET_PUBLIC_ACCESS_BLOCK",self.client.get_public_access_block,**self._bucket_args()).get("PublicAccessBlockConfiguration",{})
        if not all(access.get(k) is True for k in ("BlockPublicAcls","IgnorePublicAcls","BlockPublicPolicy","RestrictPublicBuckets")): raise CaptureError("S3_PUBLIC_ACCESS_BLOCK_REQUIRED")
        encryption=self._call("GET_BUCKET_ENCRYPTION",self.client.get_bucket_encryption,**self._bucket_args())
        rules=encryption.get("ServerSideEncryptionConfiguration",{}).get("Rules",[])
        if not any(r.get("ApplyServerSideEncryptionByDefault",{}).get("SSEAlgorithm")=="AES256" for r in rules): raise CaptureError("S3_DEFAULT_ENCRYPTION_REQUIRED")
        lock=self._call("GET_OBJECT_LOCK",self.client.get_object_lock_configuration,**self._bucket_args())
        rule=lock.get("ObjectLockConfiguration",{}).get("Rule",{}).get("DefaultRetention",{})
        if lock.get("ObjectLockConfiguration",{}).get("ObjectLockEnabled")!="Enabled" or rule.get("Mode")!="GOVERNANCE" or int(rule.get("Days",0))<180: raise CaptureError("S3_RETENTION_CONFIGURATION_INVALID")

    def publish(self, relative: str, payload: bytes) -> dict[str,Any]:
        digest=sha256_bytes(payload); key=validate_s3_key(self.prefix,f"{relative}.{digest}"); retain_until=self.now().astimezone(UTC)+timedelta(days=180)
        checksum=base64.b64encode(hashlib.sha256(payload).digest()).decode()
        response=self._call("PUT_OBJECT",self.client.put_object,**self._bucket_args(),Key=key,Body=payload,ServerSideEncryption="AES256",ChecksumSHA256=checksum,ObjectLockMode="GOVERNANCE",ObjectLockRetainUntilDate=retain_until)
        version=response.get("VersionId")
        if not version: raise CaptureError("S3_VERSION_ID_MISSING")
        head=self._call("HEAD_OBJECT",self.client.head_object,**self._bucket_args(),Key=key,VersionId=version)
        if head.get("ServerSideEncryption")!="AES256": raise CaptureError("S3_ENCRYPTION_EVIDENCE_MISSING")
        retention=self._call("GET_OBJECT_RETENTION",self.client.get_object_retention,**self._bucket_args(),Key=key,VersionId=version).get("Retention",{})
        if retention.get("Mode")!="GOVERNANCE" or not retention.get("RetainUntilDate"): raise CaptureError("S3_RETENTION_EVIDENCE_MISSING")
        got=self._call("GET_OBJECT_VERIFY",self.client.get_object,**self._bucket_args(),Key=key,VersionId=version,ChecksumMode="ENABLED")
        body=got["Body"].read() if hasattr(got["Body"],"read") else bytes(got["Body"])
        if sha256_bytes(body)!=digest: raise CaptureError("S3_CHECKSUM_MISMATCH")
        return {"bucket":self.bucket,"expected_owner":self.expected_owner,"key":key,"version_id":version,"sha256":digest,"bytes":len(payload),"retention_mode":"GOVERNANCE","retain_until":retention["RetainUntilDate"].isoformat()}

    def restore(self, receipt: Mapping[str,Any], destination: ImmutableStore) -> dict[str,Any]:
        if receipt.get("bucket")!=self.bucket or receipt.get("expected_owner")!=self.expected_owner: raise CaptureError("S3_RECEIPT_DESTINATION_MISMATCH")
        got=self._call("GET_OBJECT_RESTORE",self.client.get_object,**self._bucket_args(),Key=receipt["key"],VersionId=receipt["version_id"],ChecksumMode="ENABLED")
        payload=got["Body"].read() if hasattr(got["Body"],"read") else bytes(got["Body"])
        if sha256_bytes(payload)!=receipt["sha256"] or len(payload)!=receipt["bytes"]: raise CaptureError("S3_RESTORE_CHECKSUM_MISMATCH")
        path=f"restored/{receipt['version_id']}/{Path(str(receipt['key'])).name}"
        result=destination.publish(path,payload); destination.verify(path,receipt["sha256"],receipt["bytes"]); return result


def create_boto3_s3_client(*, access_key_id: str, secret_access_key: str, session_token: str | None=None) -> Any:
    """Create an explicit-credential S3 client with one SDK attempt and no chain lookup."""
    if not access_key_id or not secret_access_key: raise CaptureError("AWS_EXPLICIT_CREDENTIALS_REQUIRED")
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc: raise CaptureError("BOTO3_DEPENDENCY_MISSING") from exc
    config=Config(region_name="us-east-1",retries={"total_max_attempts":1,"mode":"standard"},connect_timeout=3,read_timeout=6)
    return boto3.client("s3",region_name="us-east-1",aws_access_key_id=access_key_id,
                        aws_secret_access_key=secret_access_key,aws_session_token=session_token,config=config)


def storage_preflight(root: Path, *, minimum_free: int=COMBINED_CAP) -> dict[str,Any]:
    root.mkdir(parents=True,exist_ok=True)
    if not os.access(root,os.W_OK): raise CaptureError("PRIMARY_ROOT_NOT_WRITABLE")
    stats=os.statvfs(root); free=stats.f_bavail*stats.f_frsize
    if free<minimum_free: raise CaptureError("PRIMARY_CAPACITY_INSUFFICIENT")
    return {"root":str(root),"free_bytes":free,"required_bytes":minimum_free}



class RuntimeResourceGuard:
    def __init__(self, root: Path, *, max_rss_kib: int, minimum_free_bytes: int=COMBINED_CAP):
        if max_rss_kib<=0: raise CaptureError("RUNTIME_RSS_LIMIT_REQUIRED")
        self.root,self.max_rss_kib,self.minimum_free_bytes=root,max_rss_kib,minimum_free_bytes
    def __call__(self, phase: str) -> dict[str,Any]:
        usage=resource.getrusage(resource.RUSAGE_SELF); stats=os.statvfs(self.root)
        free=stats.f_bavail*stats.f_frsize
        measured={"phase":phase,"max_rss_kib":usage.ru_maxrss,"free_bytes":free}
        if usage.ru_maxrss>self.max_rss_kib: raise CaptureError("RUNTIME_HEADROOM_EXHAUSTED",phase)
        if free<self.minimum_free_bytes: raise CaptureError("RUNTIME_STORAGE_EXHAUSTED",phase)
        return measured

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
                 operational_config_sha256: str | None = None,
                 runtime_guard: Callable[[str],Mapping[str,Any]] | None = None,
                 now: Callable[[],datetime]=lambda:datetime.now(UTC)):
        self.repo,self.root,self.offline,self.now=repo,root,offline,now
        self.approved_authorization_sha256=approved_authorization_sha256
        self.operational_config_sha256=operational_config_sha256
        self.runtime_guard=runtime_guard or (lambda phase:{"phase":phase})

    def preflight(self, authorization: Mapping[str,Any], sector_evidence: Mapping[str,Any]) -> tuple[dict[str,Any],dict[str,Any]]:
        bound=verify_stage_b_documents(self.repo); fixture=load_fixture(self.repo)
        validate_authorization(authorization,fixture_sha256=FIXTURE_CONTENT_SHA256,setup_sha256=SETUP_PACKAGE_FILE_SHA256,offline=self.offline)
        authorization_sha256=sha256_bytes(canonical_bytes(authorization))
        if not self.offline and (not self.approved_authorization_sha256 or authorization_sha256!=self.approved_authorization_sha256):
            raise CaptureError("APPROVED_AUTHORIZATION_HASH_REQUIRED")
        sector_hash=validate_effective_sector_evidence(sector_evidence,FIXTURE_CONTENT_SHA256)
        if not self.offline and (authorization.get("operational_config_sha256")!=self.operational_config_sha256 or authorization.get("sector_evidence_sha256")!=sector_hash):
            raise CaptureError("AUTHORIZATION_OPERATIONAL_BINDING_MISMATCH")
        instant=self.now().astimezone(UTC)
        if instant.date()>SESSION or instant>=acquisition_clock(SESSION).cutoff:
            raise CaptureError("FIXED_SESSION_EXPIRED")
        if not self.offline and (str(self.root)!=REQUIRED_ROOT or os.environ.get("MONEYBOT_PERSISTENT_DATA_DIR")!=REQUIRED_ROOT):
            raise CaptureError("PERSISTENT_ROOT_MISMATCH")
        storage=storage_preflight(self.root); self.runtime_guard("LOCAL_PREFLIGHT")
        return plan_from_fixture(fixture,sector_hash),{"bound_documents":bound,"storage":storage}

    @staticmethod
    def _publish_outcome(primary: ImmutableStore, status: str, now: datetime, error: Exception | None=None) -> dict[str,Any]:
        value={"schema_version":"alpha-atlas-v4-stage-b-outcome.v1","status":status,"recorded_at":now.astimezone(UTC).isoformat(),
               "error_code":error.code if isinstance(error,CaptureError) else type(error).__name__ if error else None,
               "error_detail":str(error) if error else None}
        digest=sha256_bytes(canonical_bytes(value)); return primary.publish(f"run/outcome-{digest}.json",canonical_bytes(value))

    def _backup_checkpoint(self, primary: ImmutableStore, backup: S3EvidenceBackup, measurements: list[dict[str,Any]]) -> dict[str,Any]:
        # Freeze primary evidence exactly once. The S3 ledger lives outside primary,
        # preventing recursive inventory growth.
        entries=[]
        for path in sorted(p for p in primary.root.rglob("*") if p.is_file() and not p.name.startswith(".")):
            payload=path.read_bytes(); entries.append({"path":path.relative_to(primary.root).as_posix(),"sha256":sha256_bytes(payload),"bytes":len(payload)})
        inventory={"schema_version":"alpha-atlas-v4-backup-inventory.v1","frozen_at":self.now().astimezone(UTC).isoformat(),"covered":entries,
                   "excludes":["s3-operation-ledger","backup-completion-manifest"]}
        inventory_bytes=canonical_bytes(inventory); inventory_hash=sha256_bytes(inventory_bytes)
        inventory_path=f"backup/inventory-{inventory_hash}.json"; primary.publish(inventory_path,inventory_bytes)
        frozen=entries+[{"path":inventory_path,"sha256":inventory_hash,"bytes":len(inventory_bytes)}]
        primary_bytes=sum(p.stat().st_size for p in primary.root.rglob("*") if p.is_file())
        if len(frozen)>40: raise CaptureError("BACKUP_OBJECT_COUNT_LIMIT",str(len(frozen)))
        if primary_bytes>PRIMARY_CAP: raise CaptureError("PRIMARY_EVIDENCE_LIMIT")
        receipts=[]; backup_bytes=0; restore_store=ImmutableStore(self.root/"isolated-restore"); restored=[]
        for entry in frozen:
            measurements.append(dict(self.runtime_guard("BEFORE_BACKUP_OBJECT")))
            receipt=backup.publish(entry["path"],(primary.root/entry["path"]).read_bytes()); receipts.append(receipt); backup_bytes+=receipt["bytes"]
            if backup_bytes>BACKUP_CAP or primary_bytes+backup_bytes>COMBINED_CAP: raise CaptureError("STAGE_B_EVIDENCE_LIMIT")
        for receipt in receipts:
            measurements.append(dict(self.runtime_guard("BEFORE_RESTORE_OBJECT"))); restored.append(backup.restore(receipt,restore_store))
        completion={"schema_version":"alpha-atlas-v4-backup-completion.v1","inventory_path":inventory_path,"inventory_sha256":inventory_hash,
                    "covered_object_count":len(frozen),"covered_bytes":sum(e["bytes"] for e in frozen),
                    "receipts":[{k:r[k] for k in ("bucket","expected_owner","key","version_id","sha256","bytes","retention_mode","retain_until")} for r in receipts],
                    "restored_object_count":len(restored),"semantics":"Covers the frozen inventory only; S3 ledger and this completion manifest are finite checkpoint metadata."}
        completion_bytes=canonical_bytes(completion); completion_hash=sha256_bytes(completion_bytes)
        completion_path=f"backup/completion-{completion_hash}.json"; primary.publish(completion_path,completion_bytes)
        completion_receipt=backup.publish(completion_path,completion_bytes); backup_bytes+=len(completion_bytes)
        primary_bytes=sum(p.stat().st_size for p in primary.root.rglob("*") if p.is_file())
        if primary_bytes>PRIMARY_CAP or backup_bytes>BACKUP_CAP or primary_bytes+backup_bytes>COMBINED_CAP: raise CaptureError("STAGE_B_EVIDENCE_LIMIT")
        expected=s3_operation_budget(len(frozen))["total"]
        if backup.ledger.operation_count()!=expected: raise CaptureError("S3_OPERATION_ACCOUNTING_MISMATCH",f"{backup.ledger.operation_count()}!={expected}")
        return {"inventory_path":inventory_path,"inventory_sha256":inventory_hash,"covered_objects":len(frozen),
                "primary_bytes":primary_bytes,"backup_bytes":backup_bytes,"receipts":receipts,
                "completion_path":completion_path,"completion_receipt":completion_receipt,"restored_objects":len(restored),"operations":expected}

    def _acquire(self, plan: Mapping[str,Any], primary: ImmutableStore, transport: Any, measurements: list[dict[str,Any]]) -> dict[str,Any]:
        runner=AcquisitionRunner(primary,transport,synthetic=self.offline,
                                 live_authorization_sha256=None if self.offline else self.approved_authorization_sha256,
                                 evidence_limit_bytes=PRIMARY_CAP-262144,clock=self.now)
        if runner.ledger.reconcile_uncertain(self.now()): raise CaptureError("MASSIVE_UNCERTAIN_ATTEMPTS_REQUIRE_REVIEW")
        results={}; quarantined=[]; identity=None; timing=acquisition_clock(SESSION)
        for spec in plan["requests"]:
            measurements.append(dict(self.runtime_guard(f"BEFORE_{spec.request_id}")))
            results[spec.request_id]=runner.execute(spec,"operational_verification",timing)
            if spec.family=="history": quarantined.append(spec.request_id)
            if spec.family=="identity": identity=resolve_identity(results[spec.request_id])
            measurements.append(dict(self.runtime_guard(f"AFTER_{spec.request_id}")))
        if identity is None: raise CaptureError("IDENTITY_UNRESOLVED")
        rows={key:[r for obj in value["objects"] for r in obj["payload"]["results"]] for key,value in results.items() if key.startswith("history:")}
        dates=lambda values:[str(x.get("date")) for x in values if x.get("date")]
        reasons=validate_feature_window(dates(rows["history:AAPL"]),dates(rows["history:SPY"]),dates(rows["history:XLK"]))
        if reasons: raise CaptureError("FEATURE_WINDOW_INVALID",",".join(reasons))
        split_rows=[r for obj in results["splits:global"]["objects"] for r in obj["payload"]["results"] if r.get("ticker") in {"AAPL","SPY","XLK"}]
        adjusted=adjust_unadjusted_bars(rows["history:AAPL"],split_rows,SESSION,source_sha256=results["history:AAPL"]["objects"][0]["sha256"])
        handoff=build_handoff(plan,results,generated_at=self.now(),timing=timing,adjustment_bindings={"AAPL":adjusted["binding"]})
        if not handoff["eligible"]: raise CaptureError("HANDOFF_INELIGIBLE",",".join(handoff["reason_codes"]))
        handoff["resolved_identity"]=identity; handoff["quarantine_released"]=sorted(quarantined)
        handoff["handoff_sha256"]=sha256_bytes(canonical_bytes({k:v for k,v in handoff.items() if k!="handoff_sha256"}))
        payload=canonical_bytes(cache_only_handoff(handoff)); published=primary.publish("handoff/handoff.json",payload); primary.verify(published["path"],published["sha256"],published["bytes"])
        return {"runner":runner,"identity":identity,"quarantined":sorted(quarantined),"handoff_sha256":handoff["handoff_sha256"]}

    def execute(self, authorization: Mapping[str,Any], sector_evidence: Mapping[str,Any],
                transport_factory: Callable[[],Any], backup_factory: Callable[[ImmutableStore],S3EvidenceBackup]) -> dict[str,Any]:
        started=self.now(); plan,preflight=self.preflight(authorization,sector_evidence)
        primary=ImmutableStore(self.root/"primary"); measurements=[]
        # Client/credential creation occurs only after all local gates above.
        backup=backup_factory(primary); backup.verify_configuration(); transport=transport_factory()
        try:
            acquired=self._acquire(plan,primary,transport,measurements)
            self._publish_outcome(primary,"ACQUISITION_COMPLETE",self.now())
        except Exception as exc:
            self._publish_outcome(primary,"ACQUISITION_FAILED",self.now(),exc)
            try: self._backup_checkpoint(primary,backup,measurements)
            except Exception as backup_exc:
                self._publish_outcome(primary,"PARTIAL_BACKUP_FAILED",self.now(),backup_exc); raise backup_exc from exc
            raise
        try: checkpoint=self._backup_checkpoint(primary,backup,measurements)
        except Exception as backup_exc:
            self._publish_outcome(primary,"PARTIAL_BACKUP_FAILED",self.now(),backup_exc); raise
        ended=self.now(); runner=acquired["runner"]
        return {"status":"PASS","mode":"OFFLINE_SYNTHETIC" if self.offline else "LIVE_STAGE_B","session":SESSION.isoformat(),
                "synthetic_transport_attempts":len([x for x in runner.ledger.records() if x["event"]=="RESERVED"]) if self.offline else 0,
                "live_provider_requests":0 if self.offline else len([x for x in runner.ledger.records() if x["event"]=="RESERVED"]),
                "real_acquisition_authorized":not self.offline,"preflight":preflight,"identity":acquired["identity"],
                "quarantined_then_released":acquired["quarantined"],"handoff_sha256":acquired["handoff_sha256"],
                "primary":{"bytes_accounted":checkpoint["primary_bytes"],"cap":PRIMARY_CAP,"read_back":True},
                "backup":{"objects":checkpoint["covered_objects"]+1,"covered_objects":checkpoint["covered_objects"],"bytes_accounted":checkpoint["backup_bytes"],"cap":BACKUP_CAP,"completion_path":checkpoint["completion_path"]},
                "restore":{"objects":checkpoint["restored_objects"],"verified":True},
                "s3_operations":checkpoint["operations"],"resource_measurements":measurements,"telemetry":telemetry(started,ended)}
