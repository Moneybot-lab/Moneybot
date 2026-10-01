"""Cache-only, prospective Alpha Atlas V4 snapshot primitives.

This module has deliberately no provider import or collection entry point.  It
turns caller-supplied, already-received objects into immutable evidence.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping
from zoneinfo import ZoneInfo

from moneybot.services.market_data_providers import ExchangeCalendar

CONTRACT_SHA256 = "09bf4a7308533195df5a0be4277cba7a5a3bbcd15b5a6fd28abc4321380ad692"
SCHEMA_SHA256 = "39726b86e5964130ba714cc1075a8c10c67b01b64e29b1b87386e8857fd8774d"
CLARIFICATION_VERSION = "alpha-atlas-v4-prospective-snapshot-timing-clarification.v1"
# Updated only when the reviewed clarification bytes change.
CLARIFICATION_SHA256 = "f92a1d99856a35556abe2b89f6b7923fbcfaf3cd1bcdb05166125935a6efc66a"
MAX_SESSIONS, MAX_TICKERS, MAX_ASSIGNMENTS = 10, 50, 500
MAX_STORAGE_BYTES = 250 * 1024 * 1024
MAX_ASSEMBLY_MINUTES, MAX_RUNTIME_MINUTES = 45, 55
FORBIDDEN = {"outcome", "outcomes", "future_price", "future_prices", "prediction", "predictions", "score", "scores", "rank", "ranks", "selection", "selections", "api_key", "authorization", "password", "secret", "token"}
DISPOSITION_PRECEDENCE = ("INVALID", "UNKNOWN", "STALE", "LATE", "MISSING", "ELIGIBLE")
UTC = timezone.utc
NY = ZoneInfo("America/New_York")


class CaptureError(RuntimeError):
    """Structured fail-closed capture error."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}" if detail else code)


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def verify_bound_documents(repository_root: Path) -> dict[str, str]:
    expected = {
        "alpha_atlas_v4_prospective_snapshot_capture_contract.v1.json": CONTRACT_SHA256,
        "alpha_atlas_v4_prospective_feature_snapshot_schema.v1.json": SCHEMA_SHA256,
        "alpha_atlas_v4_prospective_snapshot_timing_clarification.v1.json": CLARIFICATION_SHA256,
    }
    report = {}
    for name, digest in expected.items():
        path = repository_root / "docs" / "reports" / name
        actual = sha256_bytes(path.read_bytes())
        if actual != digest: raise CaptureError("BOUND_DOCUMENT_HASH_MISMATCH", name)
        report[name] = actual
    return report


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise CaptureError("UNKNOWN_CLOCK", "naive timestamp")
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class SessionTimes:
    freeze: datetime
    assembly_end: datetime
    deadline: datetime
    decision: datetime
    official_open: datetime


def session_times(day: date, calendar: ExchangeCalendar | None = None) -> SessionTimes:
    cal = calendar or ExchangeCalendar()
    if not cal.is_trading_day(day):
        raise CaptureError("INELIGIBLE_SESSION", day.isoformat())
    local = lambda h, m: datetime.combine(day, time(h, m), NY).astimezone(UTC)
    return SessionTimes(local(7, 45), local(8, 30), local(8, 40), local(8, 45), cal.session_open(day))


def validate_universe(manifest: Mapping[str, Any], expected_hash: str | None = None) -> str:
    members = manifest.get("ordered_members")
    if not isinstance(members, list) or not members or len(members) > MAX_TICKERS:
        raise CaptureError("UNIVERSE_INVALID", "requires 1..50 members")
    if members != sorted(set(members)) or any(not re.fullmatch(r"[A-Z0-9.-]+", str(x)) for x in members):
        raise CaptureError("UNIVERSE_DRIFT", "members must be unique, uppercase, and lexical")
    normalized = dict(manifest)
    claimed = normalized.pop("content_sha256", None)
    digest = sha256_bytes(canonical_bytes(normalized))
    if (claimed and claimed != digest) or (expected_hash and expected_hash != digest):
        raise CaptureError("UNIVERSE_HASH_MISMATCH")
    return digest


def cache_only_adapt(ticker: str, cache: Mapping[str, Any], required_families: set[str]) -> dict[str, Any]:
    """Adapt an eager mapping; never call values, loaders, or missing hooks."""
    raw = dict.get(cache, ticker) if type(cache) is dict else None
    if not isinstance(raw, Mapping):
        return {"status": "MISSING", "features": {}, "families": {}, "reasons": ["CACHE_MISS"]}
    supplied = raw.get("families", {})
    families, reasons = {}, []
    for name in sorted(required_families):
        item = supplied.get(name) if isinstance(supplied, Mapping) else None
        if not isinstance(item, Mapping):
            families[name] = {"freshness_status": "UNKNOWN", "error_codes": ["UNSUPPORTED_OR_MISSING_FAMILY"]}
            reasons.append("MISSING_FAMILY:" + name)
            continue
        copied = dict(item)
        if not copied.get("source_received_at") or copied.get("receipt_evidence_kind") in (None, "UNKNOWN"):
            copied.setdefault("error_codes", []).append("RECEIPT_PROVENANCE_UNAVAILABLE")
            reasons.append("UNKNOWN_PROVENANCE:" + name)
        families[name] = copied
    return {"status": "COMPLETE" if not reasons else "PARTIAL", "features": dict(raw.get("features", {})), "families": families, "reasons": sorted(set(reasons))}


def _walk_keys(value: Any) -> Iterator[str]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key).lower()
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def validate_snapshot(snapshot: Mapping[str, Any], times: SessionTimes) -> list[str]:
    reasons = list(snapshot.get("eligibility_reason_codes", []))
    bad = sorted(FORBIDDEN.intersection(_walk_keys(snapshot)))
    if bad:
        reasons.append("FORBIDDEN_FIELD:" + ",".join(bad))
    if snapshot.get("status") != "COMPLETE":
        reasons.append("INCOMPLETE")
    ready = parse_timestamp(str(snapshot["ready_for_use_at"]))
    completed = parse_timestamp(str(snapshot["assembly_completed_at"]))
    if completed > times.deadline or ready > times.deadline:
        reasons.append("LATE_AFTER_0840")
    if ready > times.decision:
        reasons.append("AFTER_DECISION_BOUNDARY")
    for name, family in snapshot.get("families", {}).items():
        if not family.get("source_received_at") or family.get("receipt_evidence_kind") == "UNKNOWN":
            reasons.append(f"UNKNOWN_PROVENANCE:{name}")
        state = family.get("freshness_status")
        if state == "STALE": reasons.append(f"STALE:{name}")
        if state in (None, "UNKNOWN"): reasons.append(f"UNKNOWN_FRESHNESS:{name}")
    return sorted(set(reasons))


def disposition(reasons: list[str], *, has_attempt: bool) -> str:
    if not has_attempt: return "MISSING"
    categories = set()
    for reason in reasons:
        if reason.startswith(("FORBIDDEN_FIELD", "INCOMPLETE", "IDENTITY", "SCHEMA")): categories.add("INVALID")
        elif reason.startswith(("UNKNOWN", "CLOCK")): categories.add("UNKNOWN")
        elif reason.startswith("STALE"): categories.add("STALE")
        elif reason.startswith(("LATE", "AFTER_")): categories.add("LATE")
        elif reason.startswith(("MISSING", "CACHE_MISS")): categories.add("MISSING")
    return next((x for x in DISPOSITION_PRECEDENCE if x in categories), "ELIGIBLE")


def select_eligible(snapshots: list[Mapping[str, Any]], times: SessionTimes) -> Mapping[str, Any] | None:
    eligible = []
    for snap in snapshots:
        if disposition(validate_snapshot(snap, times), has_attempt=True) == "ELIGIBLE":
            eligible.append(snap)
    if not eligible: return None
    return max(eligible, key=lambda x: (parse_timestamp(str(x["ready_for_use_at"])), str(x["snapshot_id"])))


def cohort_size_label(count: int) -> str:
    return "zero" if count == 0 else "one" if count == 1 else "two_through_four" if count < 5 else "exactly_five" if count == 5 else "more_than_five"


def reconcile(expected: list[str], attempts: Mapping[str, list[Mapping[str, Any]]], times: SessionTimes) -> dict[str, Any]:
    """Return exactly one disposition per member and retain every attempt reason."""
    rows, selected = [], {}
    for ticker in expected:
        ticker_attempts = list(attempts.get(ticker, []))
        chosen = select_eligible(ticker_attempts, times)
        all_reasons = sorted({r for item in ticker_attempts for r in validate_snapshot(item, times)})
        final = "ELIGIBLE" if chosen else disposition(all_reasons, has_attempt=bool(ticker_attempts))
        rows.append({"ticker": ticker, "disposition": final, "reason_codes": all_reasons,
                     "attempts": [{"snapshot_id": x.get("snapshot_id"), "reason_codes": validate_snapshot(x, times)} for x in ticker_attempts]})
        if chosen: selected[ticker] = chosen["snapshot_id"]
    eligible = len(selected)
    return {"dispositions": rows, "selected": selected, "expected_count": len(expected),
            "reconciled_count": len(rows), "eligible_count": eligible, "cohort_size": cohort_size_label(eligible)}


def enforce_elapsed(started: datetime, now: datetime, *, assembly: bool = False) -> None:
    elapsed = (now - started).total_seconds()
    limit = (MAX_ASSEMBLY_MINUTES if assembly else MAX_RUNTIME_MINUTES) * 60
    if elapsed < 0: raise CaptureError("UNKNOWN_CLOCK", "negative elapsed time")
    if elapsed > limit: raise CaptureError("ASSEMBLY_WINDOW_EXHAUSTED" if assembly else "HARD_RUNTIME_EXHAUSTED")


class ImmutableStore:
    """Atomic append-only storage with a process lock and verified read-back."""
    def __init__(self, root: Path):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def lock(self) -> Iterator[None]:
        path = self.root / ".capture.lock"
        with path.open("a+b") as handle:
            try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc: raise CaptureError("CONCURRENT_RUN") from exc
            yield

    def publish(self, relative: str, payload: bytes) -> dict[str, Any]:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            existing = target.read_bytes()
            if existing == payload: return {"path": relative, "bytes": len(payload), "sha256": sha256_bytes(payload), "duplicate": True}
            raise CaptureError("IMMUTABLE_CONFLICT", relative)
        fd, temp_name = tempfile.mkstemp(prefix=".tmp-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload); handle.flush(); os.fsync(handle.fileno())
            os.link(temp_name, target)  # no-clobber atomic publication
            os.unlink(temp_name)
            dfd = os.open(target.parent, os.O_RDONLY)
            try: os.fsync(dfd)
            finally: os.close(dfd)
        except Exception:
            try: os.unlink(temp_name)
            except FileNotFoundError: pass
            raise
        readback = target.read_bytes()
        if readback != payload: raise CaptureError("READBACK_CHECKSUM_FAILURE", relative)
        return {"path": relative, "bytes": len(payload), "sha256": sha256_bytes(payload), "duplicate": False}

    def verify(self, relative: str, expected_sha256: str, expected_bytes: int) -> None:
        try: payload = (self.root / relative).read_bytes()
        except OSError as exc: raise CaptureError("RETRIEVAL_UNVERIFIED", relative) from exc
        if len(payload) != expected_bytes or sha256_bytes(payload) != expected_sha256:
            raise CaptureError("READBACK_CHECKSUM_FAILURE", relative)

    def append_record(self, stream: str, record: Mapping[str, Any]) -> dict[str, Any]:
        data = canonical_bytes(record)
        path = self.root / stream; path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        dfd = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
        return {"path": stream, "record_sha256": sha256_bytes(data), "bytes": len(data)}

    def publish_snapshot(self, relative: str, snapshot: Mapping[str, Any], completed_at: datetime) -> dict[str, Any]:
        """Publish payload then a separate receipt, avoiding circular mutation."""
        payload_result = self.publish(relative, canonical_bytes(snapshot))
        receipt = {"schema_version": "alpha-atlas-v4-snapshot-completion-receipt.v1",
                   "payload_path": relative, "payload_sha256": payload_result["sha256"],
                   "payload_bytes": payload_result["bytes"], "persistence_completed_at": completed_at.astimezone(UTC).isoformat(),
                   "semantics": "timestamp recorded after durable payload publication and immediate verified read-back"}
        receipt_path = relative + ".completion.json"
        receipt_result = self.publish(receipt_path, canonical_bytes(receipt))
        return {"payload": payload_result, "completion_receipt": receipt_result}


class PilotBudget:
    """Persisted cumulative accounting. Retries never erase consumption."""
    def __init__(self, store: ImmutableStore): self.store = store

    def totals(self) -> dict[str, int]:
        totals = {"sessions": 0, "assignments": 0, "storage_bytes": 0, "provider_requests": 0}
        path = self.store.root / "budget.jsonl"
        if path.exists():
            for line in path.read_text().splitlines():
                row = json.loads(line)
                for key in totals: totals[key] += int(row.get(key, 0))
        return totals

    def consume(self, **delta: int) -> dict[str, int]:
        totals = self.totals()
        proposed = {k: totals[k] + int(delta.get(k, 0)) for k in totals}
        if proposed["sessions"] > MAX_SESSIONS or proposed["assignments"] > MAX_ASSIGNMENTS or proposed["storage_bytes"] > MAX_STORAGE_BYTES:
            raise CaptureError("PILOT_BUDGET_EXHAUSTED", json.dumps(proposed, sort_keys=True))
        if proposed["provider_requests"] != 0: raise CaptureError("PROVIDER_REQUEST_FORBIDDEN")
        self.store.append_record("budget.jsonl", delta)
        return proposed
