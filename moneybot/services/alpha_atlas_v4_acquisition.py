"""Stage A-only, transport-injected prospective acquisition components.

There is intentionally no HTTP client, credential lookup, or live enable switch in
this module.  A caller must inject a transport; the shipped CLI injects only the
synthetic transport defined in its script.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from moneybot.services.alpha_atlas_v4_prospective_snapshot import (
    CaptureError, ImmutableStore, NY, canonical_bytes, sha256_bytes,
)
from moneybot.services.corporate_actions import canonical_splits, split_source_hash
from moneybot.services.market_data_providers import ExchangeCalendar

AMENDMENT_V3_SHA256 = "767edcee6cb234302af0339187aaa49c25af6b2a6fd21b99f6a2342f42f929f9"
AMENDMENT_V2_SHA256 = "35290cfa2338b4a82605fc8904ceea4fae11e4dd0ef56fe78d45f2c3d5122237"
CONTRACT_SHA256 = "09bf4a7308533195df5a0be4277cba7a5a3bbcd15b5a6fd28abc4321380ad692"
TIMING_SHA256 = "f92a1d99856a35556abe2b89f6b7923fbcfaf3cd1bcdb05166125935a6efc66a"
ADJUSTMENT_ENGINE_VERSION = "alpha-atlas-v4-local-unadjusted-split-basis.v1"
ALLOWED_HOST = "api.massive.com"
PRIMARY_LIMIT = BACKUP_LIMIT = 768 * 1024 * 1024
COMBINED_LIMIT = 1536 * 1024 * 1024
TOTAL_ATTEMPT_LIMIT = 3789
STAGE_LIMITS = {"operational_verification": 18, "bootstrap": 351, "recurring": 342}
PAGE_LIMITS = {"operational_verification": 2, "bootstrap": 5, "recurring": 2}
RETRIES = 2
RESPONSE_LIMITS = {"history": 262144, "identity": 32768, "splits": 262144}
UTC = timezone.utc


def verify_acquisition_documents(root: Path) -> dict[str, str]:
    expected = {
        "alpha_atlas_v4_prospective_data_acquisition_amendment.v3.json": AMENDMENT_V3_SHA256,
        "alpha_atlas_v4_prospective_data_acquisition_amendment.v2.json": AMENDMENT_V2_SHA256,
        "alpha_atlas_v4_prospective_snapshot_capture_contract.v1.json": CONTRACT_SHA256,
        "alpha_atlas_v4_prospective_snapshot_timing_clarification.v1.json": TIMING_SHA256,
    }
    result = {}
    for name, wanted in expected.items():
        actual = sha256_bytes((root / "docs" / "reports" / name).read_bytes())
        if actual != wanted:
            raise CaptureError("BOUND_DOCUMENT_HASH_MISMATCH", name)
        result[name] = actual
    return result


def _manifest_hash(manifest: Mapping[str, Any]) -> str:
    value = dict(manifest); claimed = value.pop("content_sha256", None)
    actual = sha256_bytes(canonical_bytes(value))
    if claimed != actual:
        raise CaptureError("MANIFEST_HASH_MISMATCH")
    return actual


@dataclass(frozen=True)
class RequestSpec:
    request_id: str
    family: str
    endpoint: str
    params: Mapping[str, str]
    symbol: str | None
    page_limit: int
    response_limit: int


def prior_sessions(day: date, count: int, calendar: ExchangeCalendar | None = None) -> list[date]:
    cal = calendar or ExchangeCalendar(); result = []
    cursor = day
    for _ in range(count):
        cursor = cal.previous_session(cursor)
        result.append(cursor)
    return list(reversed(result))


def build_request_plan(universe: Mapping[str, Any], sectors: Mapping[str, Any], session: date, *, stage: str = "bootstrap") -> dict[str, Any]:
    universe_hash, sector_hash = _manifest_hash(universe), _manifest_hash(sectors)
    stocks = universe.get("ordered_members")
    mapping = sectors.get("stock_to_sector_etf")
    if not isinstance(stocks, list) or not stocks or len(stocks) > 50 or stocks != sorted(set(stocks)):
        raise CaptureError("UNIVERSE_INVALID")
    if not isinstance(mapping, Mapping) or any(not mapping.get(stock) for stock in stocks):
        raise CaptureError("SECTOR_MAPPING_UNRESOLVED")
    if any(not (universe.get("typed_identifiers") or {}).get(stock) for stock in stocks):
        raise CaptureError("TYPED_IDENTITY_UNRESOLVED")
    context = sorted(set(mapping.values()) | {"SPY"})
    symbols = sorted(set(stocks) | set(context))
    window = prior_sessions(session, 75)
    page_limit = PAGE_LIMITS[stage]
    requests: list[RequestSpec] = []
    for symbol in symbols:
        params = {"adjusted":"false", "sort":"asc", "limit":"50000", "from":window[0].isoformat(), "to":window[-1].isoformat()}
        requests.append(RequestSpec(f"history:{symbol}", "history", f"/v2/aggs/ticker/{symbol}/range/1/day/{params['from']}/{params['to']}", params, symbol, 1, RESPONSE_LIMITS["history"]))
    for symbol in stocks:
        params = {"date":session.isoformat()}
        requests.append(RequestSpec(f"identity:{symbol}", "identity", f"/v3/reference/tickers/{symbol}", params, symbol, 1, RESPONSE_LIMITS["identity"]))
    params = {"execution_date.gte":window[0].isoformat(), "execution_date.lte":session.isoformat(), "sort":"execution_date.asc", "limit":"5000"}
    requests.append(RequestSpec("splits:global", "splits", "/stocks/v1/splits", params, None, page_limit, RESPONSE_LIMITS["splits"]))
    return {"schema_version":"alpha-atlas-v4-acquisition-plan.v1", "stage":stage, "session":session.isoformat(), "window_sessions":[x.isoformat() for x in window], "universe_sha256":universe_hash, "sector_mapping_sha256":sector_hash, "stocks":stocks, "context_symbols":context, "requests":requests}


def sanitize_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST or parsed.username or parsed.password:
        raise CaptureError("UNSAFE_PAGINATION_URL")
    query = [(k, "REDACTED" if k.lower() in {"apikey", "api_key", "token", "authorization"} else v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)]
    return urlunparse(("https", ALLOWED_HOST, parsed.path, "", urlencode(query), ""))


@dataclass(frozen=True)
class TransportResponse:
    status: int
    body: bytes
    received_at: datetime


class Transport(Protocol):
    def send(self, request: RequestSpec, *, page_url: str | None, timeout_seconds: float) -> TransportResponse: ...


class AttemptLedger:
    """Hash-chained append-only attempt state; reservation precedes transmission."""
    def __init__(self, store: ImmutableStore, *, synthetic: bool):
        self.store, self.synthetic = store, synthetic
        self.path = store.root / "acquisition_attempts.jsonl"

    def records(self) -> list[dict[str, Any]]:
        records=[]; previous="0"*64
        if not self.path.exists(): return records
        for number, line in enumerate(self.path.read_text().splitlines(), 1):
            try: row=json.loads(line)
            except json.JSONDecodeError as exc: raise CaptureError("LEDGER_CORRUPT", str(number)) from exc
            digest=row.pop("record_sha256", None)
            if row.get("previous_sha256") != previous or sha256_bytes(canonical_bytes(row)) != digest:
                raise CaptureError("LEDGER_CORRUPT", str(number))
            row["record_sha256"]=digest; records.append(row); previous=digest
        return records

    def _append(self, event: dict[str, Any]) -> dict[str, Any]:
        records=self.records(); row={**event, "previous_sha256":records[-1]["record_sha256"] if records else "0"*64}
        row["record_sha256"]=sha256_bytes(canonical_bytes(row))
        self.store.append_record("acquisition_attempts.jsonl", row); return row

    def reserve(self, stage: str, request_id: str, now: datetime) -> str:
        with self.store.lock():
            records=self.records(); reservations=[x for x in records if x["event"]=="RESERVED"]
            base_stage=stage.split(":",1)[0]
            if base_stage not in STAGE_LIMITS or (base_stage=="recurring" and ":" not in stage):
                raise CaptureError("INVALID_ATTEMPT_STAGE",stage)
            stage_count=sum(x["stage"]==stage for x in reservations)
            if len(reservations) >= TOTAL_ATTEMPT_LIMIT or stage_count >= STAGE_LIMITS[base_stage]:
                raise CaptureError("ATTEMPT_BUDGET_EXHAUSTED", stage)
            attempt_id=f"synthetic-{len(reservations)+1:06d}" if self.synthetic else f"live-{len(reservations)+1:06d}"
            self._append({"event":"RESERVED", "attempt_id":attempt_id, "stage":stage, "request_id":request_id, "synthetic":self.synthetic, "at":now.astimezone(UTC).isoformat()})
        return attempt_id

    def event(self, attempt_id: str, state: str, now: datetime, **extra: Any) -> None:
        self._append({"event":state, "attempt_id":attempt_id, "at":now.astimezone(UTC).isoformat(), **extra})

    def reconcile_uncertain(self, now: datetime) -> int:
        rows=self.records(); reserved={x["attempt_id"] for x in rows if x["event"]=="RESERVED"}; terminal={x["attempt_id"] for x in rows if x["event"] in {"PERSISTED","FAILED","UNCERTAIN"}}
        for attempt in sorted(reserved-terminal): self.event(attempt, "UNCERTAIN", now, reason="RESTART_WITHOUT_TERMINAL_STATE")
        return len(reserved-terminal)


@dataclass(frozen=True)
class AcquisitionClock:
    cutoff: datetime
    handoff: datetime
    binding: datetime


def acquisition_clock(day: date, calendar: ExchangeCalendar | None = None) -> AcquisitionClock:
    cal=calendar or ExchangeCalendar()
    if not cal.is_trading_day(day): raise CaptureError("INELIGIBLE_SESSION")
    at=lambda h,m: datetime.combine(day,time(h,m),NY).astimezone(UTC)
    return AcquisitionClock(at(7,30),at(7,45),at(7,45))


def remaining_timeout(now: datetime, clock: AcquisitionClock, configured: float) -> float:
    seconds=(clock.cutoff-now.astimezone(UTC)).total_seconds()
    if seconds <= 0: raise CaptureError("ACQUISITION_CLOSED")
    return min(configured, seconds)


class AcquisitionRunner:
    def __init__(self, store: ImmutableStore, transport: Transport, *, synthetic: bool = True,
                 live_authorization_sha256: str | None = None,
                 evidence_limit_bytes: int | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        if not synthetic and (not live_authorization_sha256 or len(live_authorization_sha256)!=64):
            raise CaptureError("REAL_ACQUISITION_DISABLED")
        self.store,self.transport,self.clock=store,transport,clock
        self.evidence_limit_bytes=evidence_limit_bytes
        self.ledger=AttemptLedger(store,synthetic=synthetic)

    def _stored_bytes(self) -> int:
        return sum(path.stat().st_size for path in self.store.root.rglob("*") if path.is_file())

    def _ensure_capacity(self, additional: int) -> None:
        if self.evidence_limit_bytes is not None and self._stored_bytes()+additional>self.evidence_limit_bytes:
            raise CaptureError("STAGE_B_PRIMARY_CAP_CONFLICT",f"stored={self._stored_bytes()} additional={additional} limit={self.evidence_limit_bytes}")

    def _attempt_failure(self, spec: RequestSpec, attempt: str, page: int, retry: int, at: datetime, reason: str,
                         response: TransportResponse | None = None) -> None:
        base=f"attempt-evidence/{spec.request_id.replace(':','_')}/{attempt}"
        body_record=None
        estimated_body=len(response.body) if response is not None and len(response.body)<=spec.response_limit else 0
        self._ensure_capacity(estimated_body+8192)
        if response is not None and len(response.body)<=spec.response_limit:
            digest=sha256_bytes(response.body); body_path=f"{base}.body.{digest}"
            self.store.publish(body_path,response.body); body_record={"path":body_path,"sha256":digest,"bytes":len(response.body)}
        manifest={"schema_version":"alpha-atlas-v4-attempt-failure.v1","attempt_id":attempt,"request_id":spec.request_id,
                  "page":page,"retry":retry,"reason":reason,"at":at.astimezone(UTC).isoformat(),
                  "http_status":response.status if response else None,"response_body":body_record,
                  "response_sha256":sha256_bytes(response.body) if response else None,"response_bytes":len(response.body) if response else 0,
                  "body_retained":body_record is not None,
                  "body_omission_reason":"RESPONSE_TOO_LARGE" if response is not None and body_record is None else None}
        self.store.publish(f"{base}.failure.json",canonical_bytes(manifest))

    def execute(self, spec: RequestSpec, stage: str, timing: AcquisitionClock) -> dict[str, Any]:
        validate_request_spec(spec)
        page_url=None; objects=[]; complete=False
        for page in range(1,spec.page_limit+1):
            response=None
            for retry in range(RETRIES+1):
                now=self.clock(); timeout=remaining_timeout(now,timing,6.0)
                self._ensure_capacity(spec.response_limit+16384)
                attempt=self.ledger.reserve(stage,spec.request_id,now); self.ledger.event(attempt,"TRANSMITTING",now,page=page,retry=retry)
                try: response=self.transport.send(spec,page_url=page_url,timeout_seconds=timeout)
                except Exception as exc:
                    failed_at=self.clock(); self._attempt_failure(spec,attempt,page,retry,failed_at,type(exc).__name__)
                    self.ledger.event(attempt,"FAILED",failed_at,reason=type(exc).__name__)
                    if retry==RETRIES: raise CaptureError("TRANSPORT_FAILURE",spec.request_id) from exc
                    continue
                late=response.received_at.astimezone(UTC)>timing.cutoff
                if len(response.body)>spec.response_limit:
                    self._attempt_failure(spec,attempt,page,retry,response.received_at,"RESPONSE_TOO_LARGE",response); self.ledger.event(attempt,"FAILED",response.received_at,reason="RESPONSE_TOO_LARGE"); raise CaptureError("RESPONSE_TOO_LARGE")
                if response.status>=500 and retry<RETRIES:
                    self._attempt_failure(spec,attempt,page,retry,response.received_at,f"HTTP_{response.status}",response); self.ledger.event(attempt,"FAILED",response.received_at,reason=f"HTTP_{response.status}"); continue
                if response.status!=200:
                    self._attempt_failure(spec,attempt,page,retry,response.received_at,f"HTTP_{response.status}",response); self.ledger.event(attempt,"FAILED",response.received_at,reason=f"HTTP_{response.status}"); raise CaptureError("HTTP_FAILURE",str(response.status))
                try: payload=json.loads(response.body)
                except json.JSONDecodeError as exc:
                    self._attempt_failure(spec,attempt,page,retry,response.received_at,"MALFORMED_JSON",response); self.ledger.event(attempt,"FAILED",response.received_at,reason="MALFORMED_JSON"); raise CaptureError("MALFORMED_RESPONSE") from exc
                if not isinstance(payload,dict):
                    self._attempt_failure(spec,attempt,page,retry,response.received_at,"MISSING_RESULTS",response); self.ledger.event(attempt,"FAILED",response.received_at,reason="MISSING_RESULTS"); raise CaptureError("MISSING_DATA")
                result_value=payload.get("results")
                if spec.family=="identity" and isinstance(result_value,dict):
                    payload={**payload,"results":[result_value]}
                elif not isinstance(result_value,list):
                    self._attempt_failure(spec,attempt,page,retry,response.received_at,"MISSING_RESULTS",response); self.ledger.event(attempt,"FAILED",response.received_at,reason="MISSING_RESULTS"); raise CaptureError("MISSING_DATA")
                digest=sha256_bytes(response.body)
                prior=[]
                receipt_dir=self.store.root/f"responses/{spec.family}/{spec.request_id.replace(':','_')}"
                for receipt_file in sorted(receipt_dir.glob(f"page-{page}-*.json.receipt.json")) if receipt_dir.exists() else []:
                    try: prior.append(json.loads(receipt_file.read_text()))
                    except json.JSONDecodeError: raise CaptureError("SOURCE_RECEIPT_CORRUPT",receipt_file.as_posix())
                revision=max(prior,key=lambda x:x.get("received_at","")).get("response_sha256") if prior else None
                path=f"responses/{spec.family}/{spec.request_id.replace(':','_')}/page-{page}-{digest}.json"
                self._ensure_capacity(len(response.body)+8192)
                published=self.store.publish(path,response.body)
                receipt={"schema_version":"alpha-atlas-v4-source-receipt.v1","attempt_id":attempt,"request_id":spec.request_id,"family":spec.family,"symbol":spec.symbol,"endpoint":spec.endpoint,"sanitized_parameters":dict(spec.params),"page":page,"provider_request_id":payload.get("request_id"),"received_at":response.received_at.astimezone(UTC).isoformat(),"response_sha256":digest,"response_bytes":len(response.body),"late":late,"supersedes_source_sha256":revision,"source_event_dates":sorted({str(x.get('date') or x.get('t') or x.get('execution_date')) for x in payload['results']})}
                receipt_result=self.store.publish(path+".receipt.json",canonical_bytes(receipt)); self.ledger.event(attempt,"PERSISTED",response.received_at,response_sha256=digest,late=late)
                objects.append({"page":page,"path":path,"sha256":digest,"bytes":len(response.body),"receipt_path":receipt_result["path"],"late":late,"payload":payload})
                if late: raise CaptureError("LATE_RESPONSE")
                next_url=payload.get("next_url")
                if next_url: page_url=sanitize_url(str(next_url))
                else: complete=True
                break
            if complete: break
        if not complete: raise CaptureError("INCOMPLETE_PAGINATION")
        return {"request_id":spec.request_id,"complete":True,"objects":objects}


def validate_request_spec(spec: RequestSpec) -> None:
    if spec.family == "history":
        if not spec.endpoint.startswith("/v2/aggs/ticker/") or spec.params.get("adjusted") != "false" or spec.page_limit != 1:
            raise CaptureError("INVALID_HISTORY_REQUEST")
    elif spec.family == "identity":
        if not spec.endpoint.startswith("/v3/reference/tickers/") or not spec.params.get("date") or spec.page_limit != 1:
            raise CaptureError("INVALID_IDENTITY_REQUEST")
    elif spec.family == "splits":
        if spec.endpoint != "/stocks/v1/splits" or spec.params.get("limit") != "5000" or spec.page_limit not in {2,5}:
            raise CaptureError("INVALID_SPLIT_REQUEST")
    else:
        raise CaptureError("UNSUPPORTED_REQUEST_FAMILY")


def adjust_unadjusted_bars(bars: list[Mapping[str,Any]], splits: list[Mapping[str,Any]], as_of: date, *, source_sha256: str) -> dict[str,Any]:
    normalized=canonical_splits(splits); result=[]
    for raw in bars:
        if raw.get("adjusted") not in {False,None}: raise CaptureError("INCOMPATIBLE_ADJUSTMENT_BASIS")
        day=date.fromisoformat(str(raw["date"])[:10]); factor=1.0; ids=[]
        for split in normalized:
            execution=date.fromisoformat(split["execution_date"])
            if day < execution <= as_of:
                factor*=float(split["split_from"])/float(split["split_to"]); ids.append(split["id"])
        row=dict(raw)
        for field in ("open","high","low","close","vwap"):
            if row.get(field) is not None: row[field]=float(row[field])*factor
        if row.get("volume") is not None: row["volume"]=float(row["volume"])/factor
        row["applied_split_ids"]=ids; result.append(row)
    split_hash=split_source_hash(normalized)
    binding={"engine_version":ADJUSTMENT_ENGINE_VERSION,"source_object_sha256":source_sha256,"split_manifest_sha256":split_hash,"adjustment_basis":as_of.isoformat()}
    return {"bars":result,"binding":binding,"window_sha256":sha256_bytes(canonical_bytes({"bars":result,"binding":binding}))}


def validate_feature_window(symbol_dates: list[str], spy_dates: list[str], sector_dates: list[str]) -> list[str]:
    reasons=[]
    if len(set(symbol_dates))<50: reasons.append("INSUFFICIENT_VALID_HISTORY")
    if len(set(symbol_dates)&set(spy_dates))<21: reasons.append("CONTEXT_ALIGNMENT_FAILED:SPY")
    if len(set(symbol_dates)&set(sector_dates))<6: reasons.append("CONTEXT_ALIGNMENT_FAILED:SECTOR")
    return reasons


class EvidenceStorage:
    def __init__(self, primary: ImmutableStore, backup: ImmutableStore): self.primary,self.backup=primary,backup
    @staticmethod
    def _bytes(store: ImmutableStore) -> int: return sum(x.stat().st_size for x in store.root.rglob('*') if x.is_file())
    def publish_versioned(self, relative: str, payload: bytes, *, completed_at: datetime | None = None) -> dict[str,Any]:
        digest=sha256_bytes(payload); version=f"{relative}.{digest}"
        p=self.primary.publish(version,payload)
        if self._bytes(self.primary)>PRIMARY_LIMIT: raise CaptureError("PRIMARY_EVIDENCE_LIMIT")
        b=self.backup.publish(version,payload)
        if self._bytes(self.backup)>BACKUP_LIMIT or self._bytes(self.primary)+self._bytes(self.backup)>COMBINED_LIMIT: raise CaptureError("BACKUP_EVIDENCE_LIMIT")
        self.primary.verify(version,digest,len(payload)); self.backup.verify(version,digest,len(payload))
        completed=(completed_at or datetime.now(UTC)).astimezone(UTC)
        receipt={"schema_version":"alpha-atlas-v4-evidence-completion.v1","payload_path":version,"payload_sha256":digest,"payload_bytes":len(payload),"completed_at":completed.isoformat(),"semantics":"recorded after primary and backup checksum read-back"}
        receipt_bytes=canonical_bytes(receipt); rp=self.primary.publish(version+".completion.json",receipt_bytes); rb=self.backup.publish(version+".completion.json",receipt_bytes)
        return {"path":version,"sha256":digest,"bytes":len(payload),"primary":p,"backup":b,"completion_receipt":{"primary":rp,"backup":rb}}
    def restore(self, relative: str, destination: ImmutableStore) -> dict[str,Any]:
        payload=(self.backup.root/relative).read_bytes()
        expected=relative.rsplit(".",1)[-1]
        if len(expected)!=64 or sha256_bytes(payload)!=expected:
            raise CaptureError("BACKUP_CHECKSUM_FAILURE",relative)
        result=destination.publish(relative,payload); destination.verify(relative,result["sha256"],result["bytes"]); return result

    def reconcile(self) -> dict[str,list[str]]:
        primary={x.relative_to(self.primary.root).as_posix() for x in self.primary.root.rglob('*') if x.is_file() and not x.name.startswith('.')}
        backup={x.relative_to(self.backup.root).as_posix() for x in self.backup.root.rglob('*') if x.is_file() and not x.name.startswith('.')}
        return {"missing_backup":sorted(primary-backup),"orphan_backup":sorted(backup-primary)}

    def backup_primary_evidence(self) -> list[dict[str,Any]]:
        copied=[]
        for path in sorted(self.primary.root.rglob('*')):
            if not path.is_file() or path.name.startswith('.'):
                continue
            payload=path.read_bytes(); digest=sha256_bytes(payload); relative=path.relative_to(self.primary.root).as_posix()
            target=f"primary-copy/{relative}.{digest}"
            result=self.backup.publish(target,payload); self.backup.verify(target,digest,len(payload))
            copied.append({"source_path":relative,"backup_path":target,"sha256":digest,"bytes":len(payload)})
        if self._bytes(self.backup)>BACKUP_LIMIT or self._bytes(self.primary)+self._bytes(self.backup)>COMBINED_LIMIT:
            raise CaptureError("BACKUP_EVIDENCE_LIMIT")
        return copied


def build_handoff(plan: Mapping[str,Any], results: Mapping[str,Mapping[str,Any]], *, generated_at: datetime, timing: AcquisitionClock, adjustment_bindings: Mapping[str,Mapping[str,Any]] | None = None) -> dict[str,Any]:
    expected=[x.request_id for x in plan["requests"]]; missing=sorted(set(expected)-set(results)); reasons=[]
    if missing: reasons.append("MISSING_DEPENDENCIES")
    if any(not results[x].get("complete") for x in results): reasons.append("INCOMPLETE_PAGINATION")
    if any(obj.get("late") for result in results.values() for obj in result.get("objects",[])): reasons.append("LATE_SOURCE")
    dispositions=[]
    adjustment_bindings=adjustment_bindings or {}
    for stock in plan["stocks"]:
        stock_reasons=[]; history=results.get(f"history:{stock}",{}); identity=results.get(f"identity:{stock}",{})
        history_rows=[row for obj in history.get("objects",[]) for row in obj.get("payload",{}).get("results",[])]
        identity_rows=[row for obj in identity.get("objects",[]) for row in obj.get("payload",{}).get("results",[])]
        if len({str(x.get('date')) for x in history_rows if x.get('date')})<50: stock_reasons.append("INSUFFICIENT_VALID_HISTORY")
        if not identity_rows: stock_reasons.append("IDENTITY_MISSING")
        if not results.get("splits:global",{}).get("complete"): stock_reasons.append("SPLIT_LINEAGE_INCOMPLETE")
        if stock not in adjustment_bindings: stock_reasons.append("ADJUSTMENT_BINDING_MISSING")
        dispositions.append({"ticker":stock,"disposition":"ELIGIBLE" if not stock_reasons else "INVALID","reason_codes":stock_reasons})
    if any(x["disposition"]!="ELIGIBLE" for x in dispositions): reasons.append("TICKER_DEPENDENCY_FAILURE")
    handoff={"schema_version":"alpha-atlas-v4-acquisition-handoff.v1","contract_sha256":CONTRACT_SHA256,"amendment_sha256":AMENDMENT_V3_SHA256,"universe_sha256":plan["universe_sha256"],"sector_mapping_sha256":plan["sector_mapping_sha256"],"session":plan["session"],"generated_at":generated_at.astimezone(UTC).isoformat(),"acquisition_deadline":timing.cutoff.isoformat(),"required_request_ids":expected,"source_objects":sorted([{"request_id":key,"sha256":obj["sha256"],"receipt_path":obj["receipt_path"]} for key,value in results.items() for obj in value.get("objects",[])],key=lambda x:(x["request_id"],x["sha256"])),"adjustment_bindings":{key:dict(adjustment_bindings[key]) for key in sorted(adjustment_bindings)},"missing_request_ids":missing,"dispositions":dispositions,"reason_codes":sorted(set(reasons)),"eligible":not reasons}
    handoff["handoff_sha256"]=sha256_bytes(canonical_bytes(handoff)); return handoff


def cache_only_handoff(handoff: Mapping[str,Any]) -> Mapping[str,Any]:
    """Return an eligible frozen handoff; there is deliberately no refresh hook."""
    if handoff.get("eligible") is not True or handoff.get("reason_codes"):
        raise CaptureError("HANDOFF_INELIGIBLE")
    return handoff
