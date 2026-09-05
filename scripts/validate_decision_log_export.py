#!/usr/bin/env python3
"""Fail-closed validation for the hosted decision-log export download."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def _final_response_headers(path: Path) -> tuple[int, dict[str, str]]:
    responses: list[tuple[int, dict[str, str]]] = []
    status: int | None = None
    headers: dict[str, str] = {}
    for raw_line in path.read_text(encoding="iso-8859-1").splitlines():
        if raw_line.startswith("HTTP/"):
            if status is not None:
                responses.append((status, headers))
            fields = raw_line.split()
            if len(fields) < 2 or not fields[1].isdigit():
                raise ValueError("invalid HTTP status line in response header history")
            status, headers = int(fields[1]), {}
        elif status is not None and ":" in raw_line:
            name, value = raw_line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
    if status is not None:
        responses.append((status, headers))
    if not responses:
        raise ValueError("response header history contains no HTTP response")
    return responses[-1]


def validate_export(
    *,
    artifact: Path,
    headers: Path,
    final_status: int,
    manifest: Path | None = None,
    manifest_output: Path | None = None,
    latest_allowed_earliest_timestamp: float | None = None,
) -> dict:
    header_status, final_headers = _final_response_headers(headers)
    if final_status != 200:
        raise ValueError(f"final curl response was HTTP {final_status}, expected 200")
    if header_status != final_status:
        raise ValueError(
            f"curl final status {final_status} disagrees with final header block {header_status}"
        )
    if not artifact.is_file() or artifact.stat().st_size == 0:
        raise ValueError("downloaded decision log is empty")
    expected_bytes = final_headers.get("content-length")
    if expected_bytes is not None and artifact.stat().st_size != int(expected_bytes):
        raise ValueError("downloaded decision log content-length mismatch")
    with artifact.open("rb") as handle:
        handle.seek(-1, 2)
        if handle.read(1) != b"\n":
            raise ValueError("downloaded decision log is not newline-terminated")
    count = 0
    earliest = latest = None
    identities = set()
    duplicates = 0
    with artifact.open(encoding="utf-8") as handle:
        for count, line in enumerate(handle, 1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on decision log line {count}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"decision log line {count} is not a JSON object")
            identity = hashlib.sha256(
                json.dumps(
                    value, sort_keys=True, separators=(",", ":"), default=str
                ).encode()
            ).hexdigest()
            duplicates += identity in identities
            identities.add(identity)
            ts = value.get("ts")
            if isinstance(ts, (int, float)) and not isinstance(ts, bool):
                earliest = ts if earliest is None else min(earliest, ts)
                latest = ts if latest is None else max(latest, ts)
    expected_lines = final_headers.get("x-decision-log-lines")
    if expected_lines is not None and count != int(expected_lines):
        raise ValueError("downloaded decision log line-count mismatch")
    hasher = hashlib.sha256()
    with artifact.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    if latest_allowed_earliest_timestamp is not None and (
        earliest is None or earliest > latest_allowed_earliest_timestamp
    ):
        raise ValueError("decision log historical beginning regressed")
    response_failures = []
    if final_headers.get("x-decision-log-complete") != "true":
        response_failures.append("response_not_complete")
    if final_headers.get("x-decision-log-truncated") != "false":
        response_failures.append("response_truncated")
    if final_headers.get("x-decision-log-sha256") != digest:
        response_failures.append("response_content_hash_mismatch")
    if int(final_headers.get("x-decision-log-total", -1)) != count:
        response_failures.append("response_source_count_mismatch")
    if response_failures and (manifest is not None or manifest_output is not None):
        raise ValueError(
            "decision log completeness failure: " + ",".join(response_failures)
        )
    continuity_status = final_headers.get("x-decision-continuity-status")
    if manifest_output is not None and continuity_status not in {
        "BASELINE_CREATED",
        "PREFIX_VERIFIED",
    }:
        raise ValueError("decision log continuity was not established")
    previous_sha = final_headers.get("x-decision-previous-sha256")
    prefix_sha = final_headers.get("x-decision-current-prefix-sha256")
    if manifest_output is not None and (not previous_sha or previous_sha != prefix_sha):
        raise ValueError("decision log continuity prefix hash mismatch")
    if manifest is not None:
        metadata = json.loads(manifest.read_text())
        failures = []
        if metadata.get("pagination_complete") is not True:
            failures.append("pagination_incomplete")
        if metadata.get("truncated") is not False:
            failures.append("export_truncated")
        if metadata.get("integrity_failures"):
            failures.append("source_integrity_failure")
        if (
            metadata.get("source_total_records") != count
            or metadata.get("exported_records") != count
        ):
            failures.append("source_export_count_mismatch")
        if metadata.get("content_bytes") != artifact.stat().st_size:
            failures.append("manifest_content_length_mismatch")
        if (
            metadata.get("content_sha256") != digest
            or metadata.get("source_sha256") != digest
        ):
            failures.append("manifest_content_hash_mismatch")
        if failures:
            raise ValueError("decision log completeness failure: " + ",".join(failures))
    result = {
        "status": "VALID",
        "http_status": final_status,
        "bytes": artifact.stat().st_size,
        "lines": count,
    }
    if manifest_output is not None:
        payload = {
            "schema_version": "moneybot-decision-log-export.v2",
            "source_kind": "append_only_jsonl_file",
            "source_identity": final_headers["x-decision-source-identity"],
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
            "ordering_version": final_headers["x-decision-ordering-version"],
            "pagination_complete": True,
            "truncated": False,
            "duplicate_records_detected": duplicates,
            "source_sha256": digest,
            "content_sha256": digest,
            "content_bytes": artifact.stat().st_size,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "warnings": [],
            "integrity_failures": [],
            "continuity": {
                "status": continuity_status,
                "previous_records": int(final_headers["x-decision-previous-lines"]),
                "previous_bytes": int(final_headers["x-decision-previous-bytes"]),
                "previous_sha256": previous_sha,
                "current_prefix_sha256": prefix_sha,
                "current_records": count,
                "current_bytes": artifact.stat().st_size,
                "checkpoint_advanced": False,
            },
        }
        manifest_output.parent.mkdir(parents=True, exist_ok=True)
        manifest_output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--headers", required=True, type=Path)
    parser.add_argument("--final-status", required=True, type=int)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--manifest-output", type=Path)
    parser.add_argument("--latest-allowed-earliest-timestamp", type=float)
    args = parser.parse_args()
    print(
        json.dumps(
            validate_export(
                artifact=args.artifact,
                headers=args.headers,
                final_status=args.final_status,
                manifest=args.manifest,
                manifest_output=args.manifest_output,
                latest_allowed_earliest_timestamp=args.latest_allowed_earliest_timestamp,
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
