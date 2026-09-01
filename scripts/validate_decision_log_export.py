#!/usr/bin/env python3
"""Fail-closed validation for the hosted decision-log export download."""

from __future__ import annotations

import argparse
import json
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


def validate_export(*, artifact: Path, headers: Path, final_status: int) -> dict:
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
    with artifact.open(encoding="utf-8") as handle:
        for count, line in enumerate(handle, 1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on decision log line {count}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"decision log line {count} is not a JSON object")
    expected_lines = final_headers.get("x-decision-log-lines")
    if expected_lines is not None and count != int(expected_lines):
        raise ValueError("downloaded decision log line-count mismatch")
    return {
        "status": "VALID",
        "http_status": final_status,
        "bytes": artifact.stat().st_size,
        "lines": count,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--headers", required=True, type=Path)
    parser.add_argument("--final-status", required=True, type=int)
    args = parser.parse_args()
    print(
        json.dumps(
            validate_export(
                artifact=args.artifact,
                headers=args.headers,
                final_status=args.final_status,
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
