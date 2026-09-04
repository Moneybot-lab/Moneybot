from __future__ import annotations

import fcntl
import hashlib
import json
import os
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

EXPORT_MANIFEST_VERSION = "moneybot-decision-log-export.v2"
CHECKPOINT_VERSION = "moneybot-decision-export-continuity.v1"
DUPLICATE_AUDIT_VERSION = "moneybot-decision-log-duplicate-audit.v1"
ORDERING_VERSION = "moneybot-decision-log-append-order.v1"
VERIFIED_SEED = {
    "content_bytes": 125_370_346,
    "record_count": 50_210,
    "sha256": "4d11f3fba1ea1a6217643fa1c9c5249daf367ba254f0428536f2b83121cddf27",
    "earliest_timestamp": 1_775_268_201,
    "latest_timestamp": 1_788_544_732,
    "manifest_created_at": "2026-09-04T17:58:52+00:00",
}


class ContinuityError(ValueError):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


def default_continuity_state_path(source_path: Path) -> Path:
    configured = os.environ.get("DECISION_EXPORT_CONTINUITY_STATE_PATH")
    return (
        Path(configured).expanduser()
        if configured
        else source_path.parent / "decision_export_continuity.json"
    )


def _source_identity(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode()).hexdigest()


def _bootstrap_marker_path(state_path: Path) -> Path:
    return state_path.with_suffix(state_path.suffix + ".initialized")


def _canonical_hash(event: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(event, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _scan(path: Path, *, byte_limit: int | None = None) -> dict[str, Any]:
    digest = hashlib.sha256()
    normalized: set[str] = set()
    duplicates = count = size = 0
    earliest = latest = None
    with path.open("rb") as handle:
        while byte_limit is None or size < byte_limit:
            remaining = None if byte_limit is None else byte_limit - size
            line = handle.readline(-1 if remaining is None else remaining)
            if not line:
                break
            size += len(line)
            digest.update(line)
            if not line.endswith(b"\n"):
                raise ContinuityError(
                    "SOURCE_REGRESSION", f"line {count + 1} is incomplete"
                )
            try:
                event = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ContinuityError(
                    "SOURCE_REGRESSION", f"line {count + 1} is invalid"
                ) from exc
            if not isinstance(event, dict):
                raise ContinuityError(
                    "SOURCE_REGRESSION", f"line {count + 1} is not an object"
                )
            identity = _canonical_hash(event)
            duplicates += identity in normalized
            normalized.add(identity)
            ts = event.get("ts")
            if isinstance(ts, (int, float)) and not isinstance(ts, bool):
                earliest = ts if earliest is None else min(earliest, ts)
                latest = ts if latest is None else max(latest, ts)
            count += 1
    if byte_limit is not None and size != byte_limit:
        raise ContinuityError(
            "SOURCE_REGRESSION", "source is shorter than checkpoint bytes"
        )
    return {
        "bytes": size,
        "records": count,
        "sha256": digest.hexdigest(),
        "earliest": earliest,
        "latest": latest,
        "duplicates": duplicates,
    }


def _load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ContinuityError("CHECKPOINT_MISSING", "continuity checkpoint is missing")
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ContinuityError(
            "CHECKPOINT_CORRUPT", "continuity checkpoint is unreadable"
        ) from exc
    required = {
        "schema_version",
        "source_identity",
        "previous_complete_bytes",
        "previous_complete_records",
        "previous_sha256",
        "earliest_timestamp",
        "latest_timestamp",
        "complete",
        "truncated",
        "integrity_clean",
        "manifest_created_at",
        "checkpoint_updated_at",
        "ordering_version",
    }
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != CHECKPOINT_VERSION
        or not required <= value.keys()
        or value.get("complete") is not True
        or value.get("truncated") is not False
        or value.get("integrity_clean") is not True
        or value.get("ordering_version") != ORDERING_VERSION
    ):
        raise ContinuityError(
            "CHECKPOINT_CORRUPT", "continuity checkpoint schema is invalid"
        )
    return value


@contextmanager
def _state_lock(state_path: Path):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def analyze_decision_log(
    path: Path, *, state_path: Path | None = None, bootstrap: bool = False
) -> dict[str, Any]:
    state_path = state_path or default_continuity_state_path(path)
    with _state_lock(state_path):
        current = _scan(path)
        baseline_created = False
        try:
            previous = _load_checkpoint(state_path)
        except ContinuityError as exc:
            if exc.status != "CHECKPOINT_MISSING" or not bootstrap:
                raise
            if _bootstrap_marker_path(state_path).exists():
                raise ContinuityError(
                    "CHECKPOINT_MISSING",
                    "established continuity checkpoint is missing",
                )
            previous = {
                "source_identity": _source_identity(path),
                "previous_complete_bytes": VERIFIED_SEED["content_bytes"],
                "previous_complete_records": VERIFIED_SEED["record_count"],
                "previous_sha256": VERIFIED_SEED["sha256"],
                "earliest_timestamp": VERIFIED_SEED["earliest_timestamp"],
                "latest_timestamp": VERIFIED_SEED["latest_timestamp"],
                "manifest_created_at": VERIFIED_SEED["manifest_created_at"],
                "checkpoint_updated_at": None,
                "ordering_version": ORDERING_VERSION,
                "complete": True,
                "truncated": False,
                "integrity_clean": True,
            }
            baseline_created = True
        if previous["source_identity"] != _source_identity(path):
            raise ContinuityError(
                "CHECKPOINT_CORRUPT", "checkpoint source identity differs"
            )
        if (
            current["bytes"] < previous["previous_complete_bytes"]
            or current["records"] < previous["previous_complete_records"]
        ):
            raise ContinuityError(
                "SOURCE_REGRESSION", "decision log is shorter than checkpoint"
            )
        prefix = _scan(path, byte_limit=int(previous["previous_complete_bytes"]))
        if prefix["sha256"] != previous["previous_sha256"]:
            raise ContinuityError(
                "PREFIX_MISMATCH", "decision log no longer has the checkpoint prefix"
            )
        if current["earliest"] != previous["earliest_timestamp"]:
            raise ContinuityError("SOURCE_REGRESSION", "historical beginning changed")
        status = "BASELINE_CREATED" if baseline_created else "PREFIX_VERIFIED"
        now = datetime.now(timezone.utc).isoformat()
        return {
            "schema_version": EXPORT_MANIFEST_VERSION,
            "source_kind": "append_only_jsonl_file",
            "source_identity": _source_identity(path),
            "source_total_records": current["records"],
            "exported_records": current["records"],
            "page_count": 1,
            "page_size": None,
            "earliest_timestamp": current["earliest"],
            "latest_timestamp": current["latest"],
            "deterministic_ordering_fields": [
                "append_ordinal",
                "ts",
                "immutable_record_sha256",
            ],
            "ordering_version": ORDERING_VERSION,
            "duplicate_records_detected": current["duplicates"],
            "pagination_complete": True,
            "truncated": False,
            "source_sha256": current["sha256"],
            "content_sha256": current["sha256"],
            "content_bytes": current["bytes"],
            "created_at": now,
            "warnings": [],
            "integrity_failures": [],
            "continuity": {
                "status": status,
                "previous_records": previous["previous_complete_records"],
                "previous_bytes": previous["previous_complete_bytes"],
                "previous_sha256": previous["previous_sha256"],
                "current_prefix_sha256": prefix["sha256"],
                "current_records": current["records"],
                "current_bytes": current["bytes"],
                "checkpoint_advanced": False,
                "checkpoint_current": False,
            },
        }


def advance_checkpoint(
    source_path: Path,
    manifest: dict[str, Any],
    *,
    state_path: Path | None = None,
    bootstrap: bool = False,
) -> dict[str, Any]:
    state_path = state_path or default_continuity_state_path(source_path)
    verified = analyze_decision_log(
        source_path, state_path=state_path, bootstrap=bootstrap
    )
    with _state_lock(state_path):
        if state_path.exists():
            concurrent = _load_checkpoint(state_path)
            if concurrent["previous_complete_records"] > manifest.get(
                "exported_records", -1
            ):
                return {
                    **verified["continuity"],
                    "checkpoint_advanced": False,
                    "checkpoint_current": True,
                }
            if concurrent["previous_complete_records"] == manifest.get(
                "exported_records"
            ) and concurrent["previous_sha256"] == manifest.get("content_sha256"):
                return {
                    **verified["continuity"],
                    "checkpoint_advanced": False,
                    "checkpoint_current": True,
                }
        if not state_path.exists() and bootstrap:
            marker = _bootstrap_marker_path(state_path)
            try:
                descriptor = os.open(
                    marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            except FileExistsError as exc:
                raise ContinuityError(
                    "CHECKPOINT_MISSING", "bootstrap was already consumed"
                ) from exc
            except OSError as exc:
                raise ContinuityError(
                    "CHECKPOINT_WRITE_FAILED", "bootstrap marker write failed"
                ) from exc
        for field in (
            "content_bytes",
            "exported_records",
            "content_sha256",
            "earliest_timestamp",
            "latest_timestamp",
            "source_identity",
        ):
            if verified.get(field) != manifest.get(field):
                raise ContinuityError(
                    "SOURCE_REGRESSION", f"commit manifest disagrees on {field}"
                )
        now = datetime.now(timezone.utc).isoformat()
        checkpoint = {
            "schema_version": CHECKPOINT_VERSION,
            "source_identity": verified["source_identity"],
            "previous_complete_bytes": verified["content_bytes"],
            "previous_complete_records": verified["exported_records"],
            "previous_sha256": verified["content_sha256"],
            "earliest_timestamp": verified["earliest_timestamp"],
            "latest_timestamp": verified["latest_timestamp"],
            "manifest_created_at": manifest.get("created_at"),
            "checkpoint_updated_at": now,
            "ordering_version": ORDERING_VERSION,
            "complete": True,
            "truncated": False,
            "integrity_clean": True,
        }
        temporary = state_path.with_suffix(state_path.suffix + f".{os.getpid()}.tmp")
        try:
            with temporary.open("w") as handle:
                json.dump(checkpoint, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, state_path)
            directory_fd = os.open(state_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise ContinuityError(
                "CHECKPOINT_WRITE_FAILED", "checkpoint atomic write failed"
            ) from exc
        return {**verified["continuity"], "checkpoint_advanced": True, "checkpoint_current": True}


def stream_exact_prefix(path: Path, expected_bytes: int) -> Iterator[bytes]:
    remaining = expected_bytes
    with path.open("rb") as handle:
        while remaining:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                raise RuntimeError("decision log changed during export")
            remaining -= len(chunk)
            yield chunk


def audit_decision_log(path: Path) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    raw_hashes: dict[str, set[str]] = defaultdict(set)
    id_hashes: dict[str, set[str]] = defaultdict(set)
    by_date: Counter[str] = Counter()
    by_type: Counter[str] = Counter()
    by_model: Counter[str] = Counter()
    previous = None
    adjacent = total = 0
    with path.open("rb") as handle:
        for ordinal, line in enumerate(handle):
            event = json.loads(line)
            normalized = _canonical_hash(event)
            raw = hashlib.sha256(line).hexdigest()
            ts = event.get("ts") if isinstance(event.get("ts"), (int, float)) else None
            endpoint = str(
                event.get("event_type") or event.get("type") or event.get("endpoint") or "unknown"
            )
            model = (
                event.get("model_version")
                or event.get("producer_version")
                or (event.get("snapshot") or {}).get("model_version")
                or (event.get("payload") or {}).get("model_version")
                or "unknown"
            )
            groups[normalized].append(
                {
                    "ordinal": ordinal,
                    "ts": ts,
                    "raw": raw,
                    "endpoint": endpoint,
                    "model": str(model),
                }
            )
            raw_hashes[normalized].add(raw)
            adjacent += normalized == previous
            previous = normalized
            total += 1
            immutable = (
                event.get("decision_id")
                or event.get("event_id")
                or event.get("request_id")
            )
            if immutable:
                id_hashes[str(immutable)].add(normalized)
    duplicate_groups = {
        key: values for key, values in groups.items() if len(values) > 1
    }
    duplicates = sum(len(values) - 1 for values in duplicate_groups.values())
    duplicate_occurrences = [
        item for values in duplicate_groups.values() for item in values[1:]
    ]
    for item in duplicate_occurrences:
        if item["ts"] is not None:
            by_date[
                datetime.fromtimestamp(item["ts"], tz=timezone.utc).date().isoformat()
            ] += 1
        by_type[item["endpoint"]] += 1
        by_model[item["model"]] += 1
    duplicate_times = [
        item["ts"]
        for values in duplicate_groups.values()
        for item in values
        if item["ts"] is not None
    ]
    conflicts = sum(len(values) > 1 for values in id_hashes.values())
    return {
        "schema_version": DUPLICATE_AUDIT_VERSION,
        "implementation_version": "normalized-canonical-json-sha256.v1",
        "status": (
            "FAILED_CONFLICTING_IDENTITIES"
            if conflicts
            else (
                "AUDITED_WITH_EXACT_DUPLICATE_WARNINGS"
                if duplicates
                else "AUDITED_UNIQUE"
            )
        ),
        "total_physical_records": total,
        "unique_records": len(groups),
        "duplicate_physical_records": duplicates,
        "duplicate_group_count": len(duplicate_groups),
        "maximum_multiplicity": max(map(len, groups.values()), default=0),
        "duplicate_rate": round(duplicates / max(1, total), 8),
        "adjacent_duplicate_records": adjacent,
        "non_adjacent_duplicate_records": duplicates - adjacent,
        "duplicate_counts_by_date": dict(sorted(by_date.items())),
        "duplicate_counts_by_event_type": dict(sorted(by_type.items())),
        "duplicate_counts_by_model_or_producer_version": dict(sorted(by_model.items())),
        "exact_normalized_duplicates": duplicates,
        "exact_byte_duplicate_records": sum(
            len(values) - len(raw_hashes[key])
            for key, values in duplicate_groups.items()
        ),
        "byte_identical_duplicate_groups": sum(
            len(raw_hashes[key]) == 1 for key in duplicate_groups
        ),
        "same_immutable_identity_with_different_payload": conflicts,
        "same_decision_event_id_with_different_payload": conflicts,
        "earliest_duplicate_timestamp": min(duplicate_times, default=None),
        "latest_duplicate_timestamp": max(duplicate_times, default=None),
        "likely_replay_retry_bursts": adjacent,
        "sanitized_duplicate_group_hashes": sorted(duplicate_groups)[:20],
        "raw_classification_rows": total,
        "exact_deduplicated_classification_rows": len(groups),
        "maximum_exact_duplicate_weight_multiplier": max(
            map(len, groups.values()), default=0
        ),
        "integrity_failures": (["conflicting_immutable_identity"] if conflicts else []),
        "warnings": (["exact_normalized_duplicates_present"] if duplicates else []),
    }
