"""Bounded metadata-only coverage discovery for private Alpha Atlas V4 research."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from moneybot.services.alpha_atlas_v4_phase1 import sanitize

DISCOVERY_VERSION = "alpha-atlas-v4-phase1-coverage-discovery.v1"
EVIDENCE_CLASSES = {
    "STATIC_ONLY",
    "LIVE_BOUNDED",
    "PARTIAL_LIMIT_REACHED",
    "COMPLETE_DISCOVERY",
    "FAILED_VALIDATION",
}
HARD_LIMITS = {
    "max_requests": 200,
    "max_pages": 200,
    "max_response_bytes": 5 * 1024 * 1024,
    "max_total_bytes": 100 * 1024 * 1024,
    "max_elapsed_seconds": 900,
    "max_retries": 3,
    "max_backoff_seconds": 30,
}
DEFAULT_LIMITS = {
    "max_requests": 40,
    "max_pages": 10,
    "max_response_bytes": 1024 * 1024,
    "max_total_bytes": 10 * 1024 * 1024,
    "max_elapsed_seconds": 300,
    "max_retries": 2,
    "max_backoff_seconds": 8,
}


def validate_limits(values: Mapping[str, int]) -> dict[str, int]:
    limits = {
        key: int(values.get(key, default)) for key, default in DEFAULT_LIMITS.items()
    }
    for key, value in limits.items():
        if value < 0 or value > HARD_LIMITS[key]:
            raise ValueError(f"DISCOVERY_LIMIT_OUT_OF_RANGE:{key}")
    if not limits["max_requests"] or not limits["max_response_bytes"]:
        raise ValueError("DISCOVERY_LIMIT_MUST_BE_POSITIVE")
    return limits


def require_read_method(method: str) -> str:
    method = method.upper()
    if method not in {"GET", "HEAD"}:
        raise ValueError(f"DISCOVERY_UNSAFE_HTTP_METHOD:{method}")
    return method


@dataclass
class DiscoveryResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]


def _urllib_fetch(
    method: str, url: str, headers: Mapping[str, str], timeout: float
) -> DiscoveryResponse:
    request = urllib.request.Request(
        url, headers=dict(headers), method=require_read_method(method)
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
        return DiscoveryResponse(
            int(response.status), response.read(), dict(response.headers)
        )
    except urllib.error.HTTPError as exc:
        return DiscoveryResponse(exc.code, exc.read(), dict(exc.headers or {}))


def _identity_report(
    records: list[dict[str, Any]], evidence_class: str
) -> dict[str, Any]:
    identifiers = ("composite_figi", "share_class_figi", "cik")
    populated = {
        field: sum(bool(row.get(field)) for row in records) for field in identifiers
    }
    ticker_ids: dict[str, set[str]] = {}
    id_tickers: dict[str, set[str]] = {}
    for row in records:
        ticker = str(row.get("ticker") or "")
        stable = str(
            row.get("composite_figi")
            or row.get("share_class_figi")
            or row.get("cik")
            or ""
        )
        if ticker and stable:
            ticker_ids.setdefault(ticker, set()).add(stable)
            id_tickers.setdefault(stable, set()).add(ticker)
    return {
        "schema_version": "alpha-atlas-v4-phase1-identity-coverage.v1",
        "evidence_class": evidence_class,
        "records": len(records),
        "identifier_population": populated,
        "identifier_population_rates": {
            k: round(v / len(records), 6) if records else None
            for k, v in populated.items()
        },
        "reused_tickers": sorted(k for k, v in ticker_ids.items() if len(v) > 1),
        "one_identity_many_tickers": sorted(
            k for k, v in id_tickers.items() if len(v) > 1
        ),
        "conflicting_identity_mappings": sum(len(v) > 1 for v in ticker_ids.values()),
        "deterministic_point_in_time_chain": False,
        "blocker": "effective-dated identity intervals and ticker-event completeness are not verified",
    }


def build_discovery_reports(
    records: list[dict[str, Any]],
    *,
    evidence_class: str,
    metrics: Mapping[str, Any],
    stopped_by: str | None,
) -> dict[str, dict[str, Any]]:
    if evidence_class not in EVIDENCE_CLASSES:
        raise ValueError("DISCOVERY_EVIDENCE_CLASS_INVALID")
    active = sum(row.get("active") is True for row in records)
    inactive = sum(row.get("active") is False for row in records)
    missing_status = sum(row.get("active") is None for row in records)
    dates = sorted(
        str(row.get("date") or row.get("list_date"))
        for row in records
        if row.get("date") or row.get("list_date")
    )
    identity = _identity_report(records, evidence_class)
    sector_fields = ("sector", "industry", "sic_code", "sic_description")
    sector_rows = sum(any(row.get(field) for field in sector_fields) for row in records)
    sector = {
        "schema_version": "alpha-atlas-v4-phase1-sector-coverage.v1",
        "evidence_class": evidence_class,
        "classification_fields": list(sector_fields),
        "records_with_classification": sector_rows,
        "coverage_percentage": (
            round(100 * sector_rows / len(records), 4) if records else None
        ),
        "effective_date_support": False,
        "earliest_supported_date": None,
        "latest_supported_date": None,
        "fallback_recommendation": "disable sector-relative features for unsupported observations under a future versioned policy; do not use current sector metadata historically",
        "status": "INDETERMINATE",
    }
    interval = {
        "schema_version": "alpha-atlas-v4-phase1-common-interval.v1",
        "evidence_class": evidence_class,
        "per_source": {},
        "proposed_start": None,
        "proposed_end": None,
        "confidence": "INSUFFICIENT_EVIDENCE",
        "gaps": [
            "bounded metadata discovery does not prove bars/actions/context completeness"
        ],
    }
    terminal = {
        "schema_version": "alpha-atlas-v4-phase1-terminal-price-discovery.v1",
        "evidence_class": evidence_class,
        "inactive_records_observed": inactive,
        "unresolved_events": inactive,
        "policy_recommendation": [
            "use actual tradable prices when available",
            "never invent zero or use an unverified current/stale quote",
            "distinguish acquisitions, ticker changes, suspensions, and terminal listings",
            "fail closed when a scheduled exit cannot be reconstructed",
        ],
        "final_policy_approved": False,
    }
    estimate = {
        "schema_version": "alpha-atlas-v4-phase1-backfill-estimate.v1",
        "evidence_class": evidence_class,
        "candidate_securities_observed": len(
            {str(row.get("ticker")) for row in records if row.get("ticker")}
        ),
        "security_days": None,
        "object_count": None,
        "listing_requests_observed": int(metrics.get("requests", 0)),
        "compressed_storage_bytes": None,
        "uncompressed_working_bytes": None,
        "temporary_headroom_bytes": None,
        "runtime_range_seconds": None,
        "uncertainties": [
            "enumeration completeness",
            "common interval",
            "rows per security",
            "delivery object layout",
        ],
    }
    discovery = {
        "schema_version": DISCOVERY_VERSION,
        "evidence_class": evidence_class,
        "verdict": "INCOMPLETE_COVERAGE",
        "full_backfill_authorized": False,
        "full_backfill_started": False,
        "records_enumerated": len(records),
        "active_count": active,
        "inactive_count": inactive,
        "earliest_effective_date_observed": dates[0] if dates else None,
        "latest_effective_date_observed": dates[-1] if dates else None,
        "missing_date_count": len(records) - len(dates),
        "missing_status_count": missing_status,
        "pagination_complete": stopped_by is None
        and evidence_class == "COMPLETE_DISCOVERY",
        "stopped_by_limit": stopped_by,
        "metrics": dict(metrics),
        "blockers": [
            "Full inactive/delisted universe completeness remains unverified; representative TWTR access was demonstrated.",
            identity["blocker"],
            "effective-dated sector coverage remains unverified",
            "common historical interval remains unverified",
            "final terminal-price policy remains unapproved",
        ],
    }
    return {
        "coverage_discovery": discovery,
        "identity_coverage": identity,
        "sector_coverage": sector,
        "common_interval": interval,
        "terminal_price_discovery": terminal,
        "backfill_estimate": estimate,
    }


def run_coverage_discovery(
    *,
    live: bool,
    api_key: str = "",
    limits: Mapping[str, int] | None = None,
    fetcher: Callable[
        [str, str, Mapping[str, str], float], DiscoveryResponse
    ] = _urllib_fetch,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, dict[str, Any]]:
    caps = validate_limits(limits or {})
    metrics: dict[str, Any] = {
        "requests": 0,
        "pages": 0,
        "bytes": 0,
        "retries": 0,
        "elapsed_seconds": 0.0,
        "write_operations": 0,
    }
    if not live:
        return build_discovery_reports(
            [], evidence_class="STATIC_ONLY", metrics=metrics, stopped_by=None
        )
    if not api_key:
        raise ValueError("DISCOVERY_CREDENTIAL_REQUIRED")
    start = clock()
    records: list[dict[str, Any]] = []
    pending_urls = [
        "https://api.massive.com/v3/reference/tickers?market=stocks&type=CS&active=true&limit=1000&sort=ticker",
        "https://api.massive.com/v3/reference/tickers?market=stocks&type=CS&active=false&limit=1000&sort=ticker",
    ]
    url: str | None = pending_urls.pop(0)
    stopped_by = None
    while url:
        elapsed = clock() - start
        if metrics["requests"] >= caps["max_requests"]:
            stopped_by = "max_requests"
            break
        if metrics["pages"] >= caps["max_pages"]:
            stopped_by = "max_pages"
            break
        if elapsed >= caps["max_elapsed_seconds"]:
            stopped_by = "max_elapsed_seconds"
            break
        response = None
        for attempt in range(caps["max_retries"] + 1):
            metrics["requests"] += 1
            response = fetcher("GET", url, {"Authorization": f"Bearer {api_key}"}, 15)
            if response.status != 429 and response.status < 500:
                break
            if (
                attempt >= caps["max_retries"]
                or metrics["requests"] >= caps["max_requests"]
            ):
                break
            delay = min(caps["max_backoff_seconds"], 2**attempt)
            metrics["retries"] += 1
            sleeper(delay)
        if response is None or response.status >= 400:
            raise ValueError(
                f"DISCOVERY_HTTP_ERROR:{response.status if response else 'NO_RESPONSE'}"
            )
        if len(response.body) > caps["max_response_bytes"]:
            stopped_by = "max_response_bytes"
            break
        if metrics["bytes"] + len(response.body) > caps["max_total_bytes"]:
            stopped_by = "max_total_bytes"
            break
        metrics["bytes"] += len(response.body)
        payload = json.loads(response.body or b"{}")
        page = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(page, list) or not all(isinstance(row, dict) for row in page):
            raise ValueError("DISCOVERY_MALFORMED_RESPONSE")
        records.extend(sanitize(page))
        metrics["pages"] += 1
        url = payload.get("next_url") or (pending_urls.pop(0) if pending_urls else None)
    metrics["elapsed_seconds"] = round(clock() - start, 6)
    evidence_class = "PARTIAL_LIMIT_REACHED" if stopped_by else "COMPLETE_DISCOVERY"
    return build_discovery_reports(
        records, evidence_class=evidence_class, metrics=metrics, stopped_by=stopped_by
    )
