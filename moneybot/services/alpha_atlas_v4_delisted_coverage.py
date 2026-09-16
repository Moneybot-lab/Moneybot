"""Bounded, read-only live verification of Massive delisted-security access."""

from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
from datetime import date, timedelta
from typing import Any, Callable, Mapping

from moneybot.services.alpha_atlas_v4_phase1_discovery import (
    DiscoveryResponse,
    _urllib_fetch,
)

SCHEMA_VERSION = "alpha-atlas-v4-delisted-availability-verification.v1"
LIST_URL = "https://api.massive.com/v3/reference/tickers?market=stocks&type=CS&active=false&limit=1000&sort=ticker"
DOCS = {
    "ticker_enumeration": "https://massive.com/docs/rest/stocks/tickers/all-tickers",
    "point_in_time_reference": "https://massive.com/docs/rest/stocks/tickers/ticker-overview",
    "historical_aggregates": "https://massive.com/docs/rest/stocks/aggregates/custom-bars",
}


def _safe_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    safe = [(key, value) for key, value in query if key.lower() not in {"apikey", "api_key"}]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(safe), ""))


def _request(fetcher, api_key: str, url: str, provenance: list[dict[str, Any]]) -> dict[str, Any]:
    response: DiscoveryResponse = fetcher("GET", url, {"Authorization": f"Bearer {api_key}"}, 30)
    provenance.append({"method": "GET", "url": _safe_url(url), "status": response.status,
                       "response_bytes": len(response.body), "response_sha256": hashlib.sha256(response.body).hexdigest()})
    if response.status >= 400:
        raise ValueError(f"DELISTED_DISCOVERY_HTTP_ERROR:{response.status}")
    value = json.loads(response.body or b"{}")
    if not isinstance(value, dict):
        raise ValueError("DELISTED_DISCOVERY_MALFORMED_RESPONSE")
    return value


def _probe_indexes(count: int, maximum: int) -> list[int]:
    if count <= maximum:
        return list(range(count))
    return sorted({round(index * (count - 1) / (maximum - 1)) for index in range(maximum)})


def verify_delisted_availability(*, api_key: str, repository_commit: str,
                                 generated_at: str, research_start: str,
                                 research_end: str, max_pages: int = 20,
                                 max_probes: int = 12,
                                 fetcher: Callable = _urllib_fetch,
                                 sleeper: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    if not api_key:
        raise ValueError("DELISTED_DISCOVERY_CREDENTIAL_REQUIRED")
    if not (1 <= max_pages <= 50 and 3 <= max_probes <= 25):
        raise ValueError("DELISTED_DISCOVERY_LIMIT_INVALID")
    provenance: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    url: str | None = LIST_URL
    pages = 0
    while url and pages < max_pages:
        payload = _request(fetcher, api_key, url, provenance)
        page = payload.get("results")
        if not isinstance(page, list) or not all(isinstance(row, dict) for row in page):
            raise ValueError("DELISTED_DISCOVERY_MALFORMED_RESULTS")
        records.extend(page)
        pages += 1
        url = payload.get("next_url")
        if url:
            sleeper(0.15)
    pagination_complete = not url
    confirmed = sorted(
        (row for row in records if row.get("active") is False and row.get("delisted_utc")),
        key=lambda row: (str(row.get("delisted_utc")), str(row.get("ticker"))),
    )
    interval_confirmed = [
        row for row in confirmed
        if research_start <= str(row.get("delisted_utc"))[:10] <= research_end
    ]
    other_inactive = [row for row in records if row.get("active") is False and not row.get("delisted_utc")]
    identity_tickers: dict[str, set[str]] = {}
    for row in records:
        stable = str(row.get("composite_figi") or row.get("share_class_figi") or row.get("cik") or "")
        if stable and row.get("ticker"):
            identity_tickers.setdefault(stable, set()).add(str(row["ticker"]))
    renamed_identities = {key: sorted(value) for key, value in identity_tickers.items() if len(value) > 1}
    selected_indexes = _probe_indexes(len(interval_confirmed), max_probes) if pagination_complete else []
    rename_indexes = [index for index, row in enumerate(interval_confirmed) if str(row.get("composite_figi") or row.get("share_class_figi") or row.get("cik") or "") in renamed_identities]
    selected_indexes = sorted(dict.fromkeys([*rename_indexes, *selected_indexes]))[:max_probes]
    probes = []
    for index in selected_indexes:
        row = interval_confirmed[index]
        ticker, delisted = str(row.get("ticker")), str(row.get("delisted_utc"))[:10]
        try:
            end = date.fromisoformat(delisted) - timedelta(days=1)
        except ValueError:
            probes.append({"ticker": ticker, "classification": "confirmed_delisted", "outcome": "IDENTITY_UNRESOLVED", "reason": "invalid_delisted_utc"})
            continue
        start = max(date.fromisoformat(research_start), end - timedelta(days=45))
        reference_url = f"https://api.massive.com/v3/reference/tickers/{urllib.parse.quote(ticker, safe='')}?date={end.isoformat()}"
        bars_url = f"https://api.massive.com/v2/aggs/ticker/{urllib.parse.quote(ticker, safe='')}/range/1/day/{start.isoformat()}/{end.isoformat()}?adjusted=true&sort=asc&limit=50000"
        try:
            reference = _request(fetcher, api_key, reference_url, provenance).get("results") or {}
            bars_payload = _request(fetcher, api_key, bars_url, provenance)
            bars = bars_payload.get("results") or []
            same_ticker = str(reference.get("ticker") or "") == ticker
            outcome = "HISTORICAL_DATA_RETRIEVED" if same_ticker and bars else ("IDENTITY_MISMATCH" if not same_ticker else "NO_PRICE_DATA_RETURNED")
            probes.append({
                "ticker": ticker, "name": row.get("name"), "delisted_utc": row.get("delisted_utc"),
                "classification": "confirmed_delisted", "point_in_time_security_id": row.get("composite_figi") or row.get("share_class_figi") or row.get("cik"),
                "same_identity_tickers_in_population": renamed_identities.get(str(row.get("composite_figi") or row.get("share_class_figi") or row.get("cik") or ""), []),
                "reference_as_of": end.isoformat(), "price_window": {"from": start.isoformat(), "to": end.isoformat()},
                "reference_verified": same_ticker, "bars_returned": len(bars), "first_bar_timestamp": bars[0].get("t") if bars else None,
                "last_bar_timestamp": bars[-1].get("t") if bars else None, "outcome": outcome,
            })
        except ValueError as exc:
            probes.append({"ticker": ticker, "delisted_utc": row.get("delisted_utc"), "classification": "confirmed_delisted", "outcome": "REQUEST_FAILED", "reason": str(exc)})
    retrieved = sum(row["outcome"] == "HISTORICAL_DATA_RETRIEVED" for row in probes)
    unresolved = [row for row in probes if row["outcome"] != "HISTORICAL_DATA_RETRIEVED"]
    availability_verified = pagination_complete and len(interval_confirmed) > 0 and len(probes) >= 3 and retrieved == len(probes)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "repository_commit": repository_commit,
        "scope": "bounded_live_read_only_delisted_historical_access",
        "research_interval": {"start": research_start, "end": research_end},
        "provider": "Massive",
        "official_documentation": DOCS,
        "query": {"market": "stocks", "type": "CS", "active": False, "limit": 1000, "sort": "ticker"},
        "pagination": {"pages": pages, "complete": pagination_complete, "next_url_remaining": _safe_url(url) if url else None, "max_pages": max_pages},
        "population": {"inactive_listings": len(records), "confirmed_delisted": len(confirmed), "confirmed_delisted_in_research_interval": len(interval_confirmed), "other_inactive": len(other_inactive), "missing_or_unknown_status": sum(row.get("active") is not False for row in records), "possible_renamed_identity_groups": len(renamed_identities), "previously_reported_inactive_count": 6629, "previous_count_matches": len(records) == 6629},
        "possible_renamed_identities": [{"point_in_time_security_id": key, "tickers": value} for key, value in sorted(renamed_identities.items())],
        "probe_selection": {"method": "identity-change-candidates_then_evenly_spaced_by_delisted_date_and_ticker", "population": len(interval_confirmed), "selected": len(probes), "maximum": max_probes},
        "probes": probes,
        "outcome_counts": {"historical_data_retrieved": retrieved, "access_or_entitlement_denied": sum("403" in str(row.get("reason")) for row in unresolved), "identity_mismatch_or_unresolved": sum(row["outcome"] in {"IDENTITY_MISMATCH", "IDENTITY_UNRESOLVED"} for row in unresolved), "no_data_returned": sum(row["outcome"] == "NO_PRICE_DATA_RETURNED" for row in unresolved), "request_failure_or_unverified": sum(row["outcome"] == "REQUEST_FAILED" for row in unresolved)},
        "sanitized_request_provenance": provenance,
        "acceptance_criteria": {"pagination_complete": pagination_complete, "confirmed_delisted_population_nonempty": bool(interval_confirmed), "at_least_three_reproducible_probes": len(probes) >= 3, "all_probes_reference_and_price_verified": retrieved == len(probes)},
        "conclusions": {"historical_access_for_confirmed_delisted_securities": "VERIFIED" if availability_verified else "NOT_VERIFIED", "inactive_population_enumeration": "COMPLETE_FOR_QUERY" if pagination_complete else "INCOMPLETE", "complete_historical_universe": "NOT_ESTABLISHED_BY_BOUNDED_PROBES", "effective_dated_identity_and_ticker_history": "NOT_ESTABLISHED", "terminal_price_and_delisting_treatment": "NOT_ESTABLISHED"},
        "status": "VERIFIED" if availability_verified else "BLOCKED",
        "full_backfill_authorized": False,
        "research_only": True,
        "automatic_promotion": False,
        "ready_for_live_routing": False,
    }
