#!/usr/bin/env python3
"""Bounded retrieval of the three confirmed missing V4 daily marks."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from moneybot.services.alpha_atlas_v4_valuation_coverage import (  # noqa: E402
    SUPPLEMENT_VERSION,
    SUPPORTED_BASIS,
)

REQUESTS = {
    "AUROW": ("2026-07-23", "2026-07-24"),
    "RNWWW": ("2026-07-23", "2026-07-23"),
}
ENDPOINT = "https://api.massive.com/v2/aggs/ticker"


def main() -> int:
    output = Path(
        os.environ.get("VALUATION_RECOVERY_DIR", "data/track_b/valuation_recovery")
    )
    output.mkdir(parents=True, exist_ok=True)
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    attempts: list[dict[str, object]] = []
    records: list[dict[str, object]] = []
    if not api_key:
        attempts.append(
            {
                "outcome": "authentication_error",
                "reason": "MASSIVE_API_KEY_not_configured",
            }
        )
    for symbol, (first, last) in REQUESTS.items():
        if not api_key:
            break
        params = {"adjusted": "false", "sort": "asc", "limit": "2", "apiKey": api_key}
        sanitized = {key: value for key, value in params.items() if key != "apiKey"}
        url = f"{ENDPOINT}/{symbol}/range/1/day/{first}/{last}?{urllib.parse.urlencode(params)}"
        body = None
        outcome = "network_error"
        for attempt in range(
            1, 4
        ):  # two symbols x three attempts is bounded to six requests.
            try:
                with urllib.request.urlopen(url, timeout=30) as response:
                    body = response.read().decode("utf-8")
                outcome = "response_received"
                break
            except urllib.error.HTTPError as exc:
                outcome = (
                    "authentication_error"
                    if exc.code in (401, 403)
                    else "provider_http_error"
                )
                if exc.code in (401, 403):
                    break
            except (urllib.error.URLError, TimeoutError):
                outcome = "timeout_or_network_error"
            if attempt < 3:
                time.sleep(attempt)
        attempt_record: dict[str, object] = {
            "provider": "massive_rest",
            "symbol": symbol,
            "from": first,
            "to": last,
            "parameters": sanitized,
            "outcome": outcome,
        }
        attempts.append(attempt_record)
        if body is None:
            continue
        response_file = output / f"{symbol}-{first}-{last}.response.json"
        response_file.write_text(body)
        try:
            payload = json.loads(body)
            results = payload.get("results") or []
        except (json.JSONDecodeError, AttributeError):
            attempt_record["outcome"] = "unsupported_or_malformed_response"
            continue
        if not results:
            attempt_record["outcome"] = "missing_provider_records"
        for bar in results:
            session = (
                datetime.fromtimestamp(float(bar["t"]) / 1000, timezone.utc)
                .astimezone(ZoneInfo("America/New_York"))
                .date()
                .isoformat()
            )
            if session < first or session > last:
                continue
            records.append(
                {
                    "provider": "massive_rest",
                    "symbol": symbol,
                    "session": session,
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "sanitized_request": {
                        "endpoint": f"{ENDPOINT}/{symbol}/range/1/day/{first}/{last}",
                        "parameters": sanitized,
                    },
                    "original_response_content": body,
                    "source_sha256": hashlib.sha256(body.encode()).hexdigest(),
                    "raw_close": bar.get("c"),
                    "adjustment_basis": SUPPORTED_BASIS,
                    "source_timestamp_ms": bar.get("t"),
                }
            )
    supplement = {
        "schema_version": SUPPLEMENT_VERSION,
        "run_identity": {
            "github_run_id": os.getenv("GITHUB_RUN_ID"),
            "checked_out_sha": os.getenv("GITHUB_SHA"),
        },
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "records": records,
        "recovery_attempts": attempts,
    }
    (output / "valuation_evidence_supplement.json").write_text(
        json.dumps(supplement, indent=2, sort_keys=True) + "\n"
    )
    expected = {
        ("AUROW", "2026-07-23"),
        ("AUROW", "2026-07-24"),
        ("RNWWW", "2026-07-23"),
    }
    found = {(str(item["symbol"]), str(item["session"])) for item in records}
    status = "complete" if expected <= found else "incomplete"
    (output / "recovery_diagnostics.json").write_text(
        json.dumps(
            {
                "schema_version": "alpha-atlas-v4-valuation-recovery-diagnostics.v1",
                "status": status,
                "expected": sorted(expected),
                "found": sorted(found),
                "attempts": attempts,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return 0 if status == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
