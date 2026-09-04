from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

EXPORT_MANIFEST_VERSION = "moneybot-decision-log-export.v1"


def analyze_decision_log(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    identities: set[str] = set()
    duplicates = 0
    count = 0
    earliest: int | float | None = None
    latest: int | float | None = None
    size = 0
    with path.open("rb") as handle:
        for number, line in enumerate(handle, 1):
            size += len(line)
            digest.update(line)
            if not line.endswith(b"\n"):
                raise ValueError(
                    f"decision log line {number} is not newline-terminated"
                )
            try:
                event = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"decision log line {number} is invalid JSON") from exc
            if not isinstance(event, dict):
                raise ValueError(f"decision log line {number} is not an object")
            identity = hashlib.sha256(
                json.dumps(
                    event, sort_keys=True, separators=(",", ":"), default=str
                ).encode()
            ).hexdigest()
            duplicates += identity in identities
            identities.add(identity)
            ts = event.get("ts")
            if isinstance(ts, (int, float)) and not isinstance(ts, bool):
                earliest = ts if earliest is None else min(earliest, ts)
                latest = ts if latest is None else max(latest, ts)
            count += 1
    return {
        "schema_version": EXPORT_MANIFEST_VERSION,
        "source_kind": "append_only_jsonl_file",
        "source_total_records": count,
        "exported_records": count,
        "page_count": 1,
        "page_size": None,
        "earliest_timestamp": earliest,
        "latest_timestamp": latest,
        "deterministic_ordering_fields": [
            "append_ordinal",
            "ts",
            "immutable_record_sha256",
        ],
        "duplicate_records_detected": duplicates,
        "pagination_complete": True,
        "truncated": False,
        "source_sha256": digest.hexdigest(),
        "content_sha256": digest.hexdigest(),
        "content_bytes": size,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "warnings": [],
        "integrity_failures": [],
    }


def stream_exact_prefix(path: Path, expected_bytes: int) -> Iterator[bytes]:
    remaining = expected_bytes
    with path.open("rb") as handle:
        while remaining:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                raise RuntimeError("decision log changed during export")
            remaining -= len(chunk)
            yield chunk
