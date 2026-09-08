#!/usr/bin/env python3
"""Bounded, read-only Massive entitlement audit. Dry-run unless --execute is set."""

from __future__ import annotations

import argparse
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

SCHEMA_VERSION = "alpha-atlas-v4-massive-entitlement-audit.v1"
DEFAULT_YEARS = (2004, 2006, 2010, 2015, 2020, 2022, 2026)
DATASETS = (
    "stock_daily_aggregates",
    "stock_minute_aggregates",
    "splits",
    "ticker_reference",
)
HARD_MAX_REQUESTS = 24
HARD_MAX_BYTES = 2 * 1024 * 1024


def _probe_plan(start_year: int, max_requests: int) -> list[dict[str, str]]:
    years = sorted({year for year in DEFAULT_YEARS if year >= start_year})
    probes = []
    for year in years:
        probes.append(
            {
                "dataset": "stock_daily_aggregates",
                "symbol": "SPY",
                "requested_date": f"{year}-01-05",
                "endpoint_family": "stocks/aggregates/day",
            }
        )
    for dataset, family in (
        ("stock_minute_aggregates", "stocks/aggregates/minute"),
        ("splits", "stocks/splits"),
        ("ticker_reference", "reference/tickers"),
    ):
        probes.append(
            {
                "dataset": dataset,
                "symbol": "SPY" if dataset != "splits" else "reference-category",
                "requested_date": f"{years[0] if years else start_year}-01-05",
                "endpoint_family": family,
            }
        )
    return sorted(probes, key=lambda item: (item["dataset"], item["requested_date"]))[
        :max_requests
    ]


def _url(probe: Mapping[str, str]) -> str:
    day = probe["requested_date"]
    if probe["dataset"] == "stock_daily_aggregates":
        return f"https://api.massive.com/v2/aggs/ticker/SPY/range/1/day/{day}/{day}?adjusted=false&limit=1"
    if probe["dataset"] == "stock_minute_aggregates":
        return f"https://api.massive.com/v2/aggs/ticker/SPY/range/1/minute/{day}/{day}?adjusted=false&limit=1"
    if probe["dataset"] == "splits":
        return (
            f"https://api.massive.com/stocks/v1/splits?execution_date.gte={day}&limit=1"
        )
    return f"https://api.massive.com/v3/reference/tickers?date={day}&ticker=SPY&limit=1"


def classify_http(status: int, payload: Mapping[str, Any] | None) -> str:
    text = json.dumps(payload or {}).lower()
    if status in {401}:
        return "AUTHENTICATION_FAILED"
    if status in {402, 403} or "not authorized" in text or "subscription" in text:
        return "ENTITLEMENT_DENIED"
    if status == 404:
        return "DATA_UNAVAILABLE_FOR_DATE"
    if status == 429:
        return "RATE_LIMITED"
    if status >= 500:
        return "PROVIDER_ERROR"
    if 200 <= status < 300:
        results = (payload or {}).get("results")
        if isinstance(results, list) and results:
            return "ACCESS_CONFIRMED"
        return "EMPTY_VALID_RESPONSE"
    return "UNCLASSIFIED"


def _result_timestamps(values: Any) -> tuple[int | str | None, int | str | None]:
    if not isinstance(values, list):
        return None, None
    timestamps: list[int | str] = []
    for value in values:
        if not isinstance(value, Mapping):
            continue
        timestamp = next(
            (
                value.get(field)
                for field in ("t", "timestamp", "execution_date", "date")
                if value.get(field) is not None
            ),
            None,
        )
        if isinstance(timestamp, (int, str)):
            timestamps.append(timestamp)
    if not timestamps:
        return None, None
    ordered = sorted(timestamps, key=str)
    return ordered[0], ordered[-1]


def _entitlement_conclusion(classification: str) -> str:
    if classification == "ACCESS_CONFIRMED":
        return "access_confirmed_for_this_dataset_and_probe_only"
    if classification in {"ENTITLEMENT_DENIED", "AUTHENTICATION_FAILED"}:
        return "access_not_confirmed"
    return "entitlement_unverified"


def storage_estimates(
    *, trading_days: int = 5040, symbols: int = 10000
) -> dict[str, dict[str, int]]:
    """Transparent scenario estimates; values are bytes, not provider claims."""
    daily_rows = trading_days * symbols
    daily_compressed = daily_rows * 32
    minute_rows = daily_rows * 390
    trades_rows = daily_rows * 2500
    quotes_rows = daily_rows * 7000
    scenarios = {
        "daily_only": daily_compressed,
        "daily_plus_minute": daily_compressed + minute_rows * 20,
        "daily_plus_trades": daily_compressed + trades_rows * 18,
        "daily_plus_trades_quotes": daily_compressed
        + trades_rows * 18
        + quotes_rows * 16,
    }
    return {
        name: {
            "compressed_bytes": value,
            "uncompressed_working_bytes": value * 4,
            "peak_temporary_bytes": value * 6,
            "recommended_with_backup_bytes": value * 12,
        }
        for name, value in sorted(scenarios.items())
    }


def run_audit(
    *,
    execute: bool,
    start_year: int = 2004,
    max_requests: int = 12,
    max_bytes: int = 1_048_576,
    timeout_seconds: float = 15,
    max_retries: int = 1,
    env: Mapping[str, str] | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    source = os.environ if env is None else env
    request_cap = min(max(0, max_requests), HARD_MAX_REQUESTS)
    byte_cap = min(max(0, max_bytes), HARD_MAX_BYTES)
    probes = _probe_plan(start_year, request_cap)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "retrieved_at_utc": retrieved_at
        or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "mode": "execute" if execute else "dry_run",
        "probe_caps": {
            "max_requests": request_cap,
            "max_bytes": byte_cap,
            "timeout_seconds": timeout_seconds,
            "max_retries": min(max(0, max_retries), 2),
        },
        "planned_datasets": list(DATASETS),
        "planned_probes": probes,
        "probe_results": [],
        "requests_attempted": 0,
        "requests_completed": 0,
        "bytes_received": 0,
        "credentials_configured": bool(
            str(
                source.get("MASSIVE_API_KEY") or source.get("POLYGON_API_KEY") or ""
            ).strip()
        ),
    }
    if not execute:
        return report
    api_key = str(
        source.get("MASSIVE_API_KEY") or source.get("POLYGON_API_KEY") or ""
    ).strip()
    if not api_key:
        report["execution_status"] = "AUTHENTICATION_FAILED"
        return report
    for probe in probes:
        if (
            report["requests_attempted"] >= request_cap
            or report["bytes_received"] >= byte_cap
        ):
            break
        result = {**probe, "evidence_level": "VERIFIED_PROVIDER_PROBE"}
        for attempt in range(report["probe_caps"]["max_retries"] + 1):
            if report["requests_attempted"] >= request_cap:
                result.setdefault("classification", "UNCLASSIFIED")
                result.setdefault("limitation", "request_cap_reached_before_retry")
                break
            report["requests_attempted"] += 1
            request = urllib.request.Request(
                _url(probe), headers={"Authorization": f"Bearer {api_key}"}
            )
            try:
                response = opener(request, timeout=timeout_seconds)
                raw = response.read(max(0, byte_cap - report["bytes_received"]) + 1)
                if len(raw) > byte_cap - report["bytes_received"]:
                    result["classification"] = "PROVIDER_ERROR"
                    result["limitation"] = "byte_cap_exceeded"
                    break
                report["bytes_received"] += len(raw)
                report["requests_completed"] += 1
                payload = json.loads(raw or b"{}")
                classification = classify_http(
                    int(getattr(response, "status", 200)), payload
                )
                values = payload.get("results") if isinstance(payload, dict) else None
                earliest, latest = _result_timestamps(values)
                result.update(
                    {
                        "classification": classification,
                        "rows_returned": len(values) if isinstance(values, list) else 0,
                        "bytes_received": len(raw),
                        "earliest_timestamp_returned": earliest,
                        "latest_timestamp_returned": latest,
                        "account_entitlement_conclusion": _entitlement_conclusion(
                            classification
                        ),
                        "limitation": "one bounded probe does not prove other datasets or delisted coverage",
                    }
                )
                break
            except urllib.error.HTTPError as exc:
                result["classification"] = classify_http(exc.code, None)
                result["rows_returned"] = 0
                result["bytes_received"] = 0
                result["account_entitlement_conclusion"] = _entitlement_conclusion(
                    result["classification"]
                )
                if (
                    exc.code not in {429, 500, 502, 503, 504}
                    or attempt >= report["probe_caps"]["max_retries"]
                ):
                    break
            except (TimeoutError, socket.timeout):
                result["classification"] = "TIMEOUT"
                if attempt >= report["probe_caps"]["max_retries"]:
                    break
            except urllib.error.URLError:
                result["classification"] = "NETWORK_ERROR"
                if attempt >= report["probe_caps"]["max_retries"]:
                    break
        report["probe_results"].append(result)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--start-year", type=int, default=2004)
    parser.add_argument("--max-requests", type=int, default=12)
    parser.add_argument("--max-bytes", type=int, default=1_048_576)
    parser.add_argument("--timeout-seconds", type=float, default=15)
    parser.add_argument("--max-retries", type=int, default=1)
    args = parser.parse_args()
    print(
        json.dumps(
            run_audit(
                execute=args.execute,
                start_year=args.start_year,
                max_requests=args.max_requests,
                max_bytes=args.max_bytes,
                timeout_seconds=args.timeout_seconds,
                max_retries=args.max_retries,
            ),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
