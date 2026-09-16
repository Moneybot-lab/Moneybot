"""Bounded, read-only live verification of Massive ended-listing access."""

from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
from datetime import date, timedelta
from typing import Any, Callable

from moneybot.services.alpha_atlas_v4_phase1_discovery import DiscoveryResponse, _urllib_fetch
from moneybot.services.market_data_providers import ExchangeCalendar

SCHEMA_VERSION = "alpha-atlas-v4-delisted-availability-verification.v2"
LIST_URL = "https://api.massive.com/v3/reference/tickers?market=stocks&type=CS&active=false&limit=1000&sort=ticker"
PRIOR_RUN = {
    "workflow_run_id": 35123217191, "run_attempt": 1,
    "head_sha": "fd517cf5b752df85ed981d980ac6f832be64c904",
    "status": "BLOCKED", "inactive_listings": 6607, "pages": 7,
    "sample_retrieved": 10, "sample_selected": 12,
    "affected_tickers": ["AGU", "HLS"],
    "root_cause": "research-start clipping produced a holiday-only 2018-01-01 window",
}
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


def _identifier(row: dict[str, Any]) -> dict[str, str | None]:
    for key in ("share_class_figi", "composite_figi", "cik"):
        if row.get(key):
            return {"type": key, "value": str(row[key])}
    return {"type": None, "value": None}


def _sessions(calendar: ExchangeCalendar, start: date, end: date) -> list[date]:
    if end < start:
        return []
    result, current = [], start
    while current <= end:
        if calendar.is_trading_day(current):
            result.append(current)
        current += timedelta(days=1)
    return result


def _window(row: dict[str, Any], research_start: date, research_end: date,
            calendar: ExchangeCalendar) -> dict[str, Any]:
    delisted = date.fromisoformat(str(row["delisted_utc"])[:10])
    last_possible = calendar.previous_session(delisted)
    listed = date.fromisoformat(str(row["list_date"])[:10]) if row.get("list_date") else None
    in_start = max(research_start, last_possible - timedelta(days=45), listed or research_start)
    in_end = min(research_end, last_possible)
    eligible = _sessions(calendar, in_start, in_end)
    if eligible:
        return {"scope": "WITHIN_RESEARCH_INTERVAL", "from": eligible[0], "to": eligible[-1],
                "eligible_sessions": eligible, "requested_from": in_start, "requested_to": in_end}
    # Availability-only exception: at most ten eligible exchange sessions and 21
    # calendar days before research_start. It never establishes interval coverage.
    pre_end = calendar.previous_session(research_start)
    pre_start = max(pre_end - timedelta(days=21), listed or date.min)
    before = _sessions(calendar, pre_start, min(pre_end, last_possible))[-10:]
    if before:
        return {"scope": "BOUNDED_PRE_RESEARCH_START_AVAILABILITY_ONLY", "from": before[0], "to": before[-1],
                "eligible_sessions": before, "requested_from": in_start, "requested_to": in_end}
    return {"scope": "NO_ELIGIBLE_SESSIONS", "from": None, "to": None,
            "eligible_sessions": [], "requested_from": in_start, "requested_to": in_end}


def _select(records: list[dict[str, Any]], maximum: int,
            identity_candidates: set[int]) -> tuple[list[int], dict[str, Any]]:
    """Reserve most capacity for temporal strata and cap identity investigations."""
    if not records:
        return [], {"planned_strata": [], "unrepresented_strata": []}
    strata_count = min(4, maximum, len(records))
    boundaries = [round(i * len(records) / strata_count) for i in range(strata_count + 1)]
    selected: list[int] = []
    strata = []
    identity_budget = min(maximum // 4, len(identity_candidates))
    identity_used = 0
    for number, (left, right) in enumerate(zip(boundaries, boundaries[1:]), 1):
        candidates = list(range(left, right))
        identity_in_stratum = [index for index in candidates if index in identity_candidates]
        chosen = (
            identity_in_stratum[0]
            if identity_in_stratum and identity_used < identity_budget
            else candidates[len(candidates) // 2] if candidates else None
        )
        if chosen in identity_candidates:
            identity_used += 1
        if chosen is not None:
            selected.append(chosen)
        strata.append({"stratum": number, "from_delisted_date": str(records[left]["delisted_utc"])[:10] if candidates else None,
                       "to_delisted_date": str(records[right - 1]["delisted_utc"])[:10] if candidates else None,
                       "selected_ticker": records[chosen].get("ticker") if chosen is not None else None})
    selected.extend(
        index for index in sorted(identity_candidates)
        if len(selected) < maximum and identity_used < identity_budget
        and index not in selected
    )
    for index in [round(i * (len(records) - 1) / max(1, maximum - 1)) for i in range(maximum)]:
        if len(selected) >= maximum:
            break
        if index not in selected:
            selected.append(index)
    return sorted(selected), {"planned_strata": strata,
                              "unrepresented_strata": [x["stratum"] for x in strata if not x["selected_ticker"]],
                              "identity_candidate_budget": identity_budget}


def _probe(row: dict[str, Any], *, api_key: str, research_start: date,
           research_end: date, fetcher: Callable, provenance: list[dict[str, Any]],
           calendar: ExchangeCalendar, role: str) -> dict[str, Any]:
    ticker = str(row.get("ticker") or "")
    identity = _identifier(row)
    base = {"ticker": ticker, "name": row.get("name"), "delisted_utc": row.get("delisted_utc"),
            "classification": "provider_ended_ticker_listing", "classification_basis": "delisted_utc",
            "probe_role": role, "identity_identifier": identity,
            "issuer_identifier_is_not_security_identity": identity["type"] == "cik"}
    try:
        window = _window(row, research_start, research_end, calendar)
    except (KeyError, ValueError) as exc:
        return {**base, "outcome": "IDENTITY_UNRESOLVED", "reason_code": "INVALID_LISTING_INTERVAL", "reason": str(exc)}
    serialized_window = {key: (value.isoformat() if isinstance(value, date) else len(value) if key == "eligible_sessions" else value)
                         for key, value in window.items()}
    if not window["eligible_sessions"]:
        return {**base, "price_window": serialized_window, "outcome": "NO_ELIGIBLE_SESSIONS",
                "reason_code": "NO_ELIGIBLE_SESSIONS", "reason": "No exchange session exists in the requested or bounded pre-start window."}
    reference_url = f"https://api.massive.com/v3/reference/tickers/{urllib.parse.quote(ticker, safe='')}?date={window['to'].isoformat()}"
    bars_url = f"https://api.massive.com/v2/aggs/ticker/{urllib.parse.quote(ticker, safe='')}/range/1/day/{window['from'].isoformat()}/{window['to'].isoformat()}?adjusted=true&sort=asc&limit=50000"
    try:
        reference = _request(fetcher, api_key, reference_url, provenance).get("results") or {}
        bars = _request(fetcher, api_key, bars_url, provenance).get("results") or []
        same_ticker = isinstance(reference, dict) and str(reference.get("ticker") or "") == ticker
        outcome = "HISTORICAL_DATA_RETRIEVED" if same_ticker and bars else "IDENTITY_MISMATCH" if not same_ticker else "NO_PRICE_DATA_RETURNED"
        reason_code = None if outcome == "HISTORICAL_DATA_RETRIEVED" else outcome
        reason = None if outcome == "HISTORICAL_DATA_RETRIEVED" else ("Point-in-time reference ticker differed." if not same_ticker else "Eligible exchange sessions existed but Massive returned zero aggregate bars.")
        return {**base, "price_window": serialized_window, "reference_verified": same_ticker,
                "bars_returned": len(bars), "first_bar_timestamp": bars[0].get("t") if bars else None,
                "last_bar_timestamp": bars[-1].get("t") if bars else None,
                "availability_verified": outcome == "HISTORICAL_DATA_RETRIEVED",
                "in_research_interval_verified": outcome == "HISTORICAL_DATA_RETRIEVED" and window["scope"] == "WITHIN_RESEARCH_INTERVAL",
                "outcome": outcome, "reason_code": reason_code, "reason": reason}
    except ValueError as exc:
        return {**base, "price_window": serialized_window, "outcome": "REQUEST_FAILED",
                "reason_code": "REQUEST_FAILED", "reason": str(exc), "availability_verified": False}


def verify_delisted_availability(*, api_key: str, repository_commit: str,
                                 generated_at: str, research_start: str,
                                 research_end: str, max_pages: int = 20,
                                 max_probes: int = 12, fetcher: Callable = _urllib_fetch,
                                 sleeper: Callable[[float], None] = time.sleep,
                                 prior_run_evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    if not api_key:
        raise ValueError("DELISTED_DISCOVERY_CREDENTIAL_REQUIRED")
    if not (1 <= max_pages <= 50 and 3 <= max_probes <= 25):
        raise ValueError("DELISTED_DISCOVERY_LIMIT_INVALID")
    start_date, end_date = date.fromisoformat(research_start), date.fromisoformat(research_end)
    if end_date < start_date:
        raise ValueError("DELISTED_DISCOVERY_INTERVAL_INVALID")
    provenance: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    url: str | None = LIST_URL
    pages = 0
    while url and pages < max_pages:
        payload = _request(fetcher, api_key, url, provenance)
        page = payload.get("results")
        if not isinstance(page, list) or not all(isinstance(row, dict) for row in page):
            raise ValueError("DELISTED_DISCOVERY_MALFORMED_RESULTS")
        records.extend(page); pages += 1; url = payload.get("next_url")
        if url: sleeper(0.15)
    pagination_complete = not url
    ended = sorted((row for row in records if row.get("active") is False and row.get("delisted_utc")),
                   key=lambda row: (str(row.get("delisted_utc")), str(row.get("ticker"))))
    interval = [row for row in ended if research_start <= str(row.get("delisted_utc"))[:10] <= research_end]
    other_inactive = [row for row in records if row.get("active") is False and not row.get("delisted_utc")]
    candidate_groups: dict[tuple[str, str], set[str]] = {}
    for row in records:
        ident = _identifier(row)
        if ident["value"] and row.get("ticker"):
            candidate_groups.setdefault((str(ident["type"]), str(ident["value"])), set()).add(str(row["ticker"]))
    multi = {key: sorted(value) for key, value in candidate_groups.items() if len(value) > 1}
    identity_indexes = {i for i, row in enumerate(interval) if (_identifier(row)["type"], _identifier(row)["value"]) in multi}
    selected_indexes, selection = _select(interval, max_probes, identity_indexes) if pagination_complete else ([], {"planned_strata": [], "unrepresented_strata": []})
    calendar = ExchangeCalendar()
    probes = [_probe(interval[index], api_key=api_key, research_start=start_date, research_end=end_date,
                     fetcher=fetcher, provenance=provenance, calendar=calendar, role="REPRESENTATIVE_SAMPLE") for index in selected_indexes]
    by_ticker = {str(row.get("ticker")): row for row in ended}
    followups = [_probe(by_ticker[ticker], api_key=api_key, research_start=start_date, research_end=end_date,
                        fetcher=fetcher, provenance=provenance, calendar=calendar, role="PRIOR_RUN_FOLLOW_UP")
                 for ticker in PRIOR_RUN["affected_tickers"] if ticker in by_ticker]
    failed = [row for row in probes if not row.get("availability_verified")]
    followup_failed = [row for row in followups if not row.get("availability_verified")]
    criteria = {"pagination_complete": pagination_complete, "ended_listing_population_nonempty": bool(interval),
                "at_least_three_reproducible_probes": len(probes) >= 3,
                "all_representative_probes_availability_verified": not failed and bool(probes),
                "prior_failed_tickers_followed_up": {row["ticker"] for row in followups} == set(PRIOR_RUN["affected_tickers"]),
                "all_follow_up_probes_availability_verified": not followup_failed and len(followups) == len(PRIOR_RUN["affected_tickers"])}
    reasons = []
    if not pagination_complete: reasons.append({"code": "PAGINATION_INCOMPLETE", "explanation": "Inactive-listing enumeration did not reach the final page."})
    if len(probes) < 3: reasons.append({"code": "INSUFFICIENT_REPRESENTATIVE_PROBES", "explanation": "Fewer than three representative ended listings were probed."})
    for row in [*failed, *followup_failed]:
        reasons.append({"code": row.get("reason_code") or row["outcome"], "ticker": row["ticker"],
                        "explanation": row.get("reason"), "price_window": row.get("price_window"), "outcome": row["outcome"]})
    if not criteria["prior_failed_tickers_followed_up"]:
        reasons.append({"code": "PRIOR_FAILED_TICKER_FOLLOW_UP_MISSING", "explanation": "AGU and HLS were not both present for explicit follow-up."})
    unresolved_findings = []
    if len(records) != 6629:
        unresolved_findings.append({"code": "PRIOR_INACTIVE_COUNT_RECONCILIATION_UNRESOLVED", "explanation": f"Current identical query returned {len(records)} records versus the prior reported 6629; the earlier response snapshot is unavailable for identity-level reconciliation."})
    availability_verified = all(criteria.values())
    all_probes = [*probes, *followups]
    outcome_counts = {
        "historical_data_retrieved": sum(row.get("outcome") == "HISTORICAL_DATA_RETRIEVED" for row in all_probes),
        "no_eligible_sessions": sum(row.get("outcome") == "NO_ELIGIBLE_SESSIONS" for row in all_probes),
        "no_price_data_returned": sum(row.get("outcome") == "NO_PRICE_DATA_RETURNED" for row in all_probes),
        "identity_mismatch_or_unresolved": sum(row.get("outcome") in {"IDENTITY_MISMATCH", "IDENTITY_UNRESOLVED"} for row in all_probes),
        "access_or_entitlement_denied": sum("HTTP_ERROR:403" in str(row.get("reason")) for row in all_probes),
        "request_failure_or_unverified": sum(row.get("outcome") == "REQUEST_FAILED" for row in all_probes),
    }
    return {
        "schema_version": SCHEMA_VERSION, "generated_at_utc": generated_at,
        "repository_commit": repository_commit, "scope": "bounded_live_read_only_ended_listing_historical_access",
        "research_interval": {"start": research_start, "end": research_end}, "provider": "Massive",
        "official_documentation": DOCS,
        "prior_run_evidence": prior_run_evidence or PRIOR_RUN,
        "query": {"market": "stocks", "type": "CS", "active": False, "limit": 1000, "sort": "ticker"},
        "pagination": {"pages": pages, "complete": pagination_complete, "next_url_remaining": _safe_url(url) if url else None, "max_pages": max_pages},
        "population": {"inactive_listings": len(records), "provider_ended_ticker_listings": len(ended),
                       "ended_listings_in_research_interval": len(interval), "other_inactive": len(other_inactive),
                       "previously_reported_inactive_count": 6629, "previous_count_matches": len(records) == 6629,
                       "count_reconciliation": "MATCHED" if len(records) == 6629 else "UNRESOLVED_EARLIER_SNAPSHOT_UNAVAILABLE"},
        "identity_investigation_candidates": [{"identifier_type": key[0], "identifier_value": key[1], "tickers": value,
                                                "verified_ticker_chain": False} for key, value in sorted(multi.items())],
        "probe_selection": {**selection, "method": "four_temporal_strata_plus_capped_identity_investigation_candidates",
                            "population": len(interval), "selected": len(probes), "maximum": max_probes,
                            "selected_delisting_dates": [str(interval[i]["delisted_utc"])[:10] for i in selected_indexes]},
        "probes": probes, "follow_up_probes": followups,
        "outcome_counts": outcome_counts,
        "acceptance_criteria": criteria, "failure_reasons": reasons,
        "unresolved_findings": unresolved_findings,
        "status_explanation": "All required bounded availability checks passed." if availability_verified else "; ".join(f"{x['code']}: {x['explanation']}" for x in reasons) or "Required acceptance criteria were not satisfied.",
        "sanitized_request_provenance": provenance,
        "conclusions": {"historical_access_for_provider_ended_ticker_listings": "VERIFIED" if availability_verified else "NOT_VERIFIED",
                        "confirmed_company_or_security_termination": "NOT_ESTABLISHED_BY_DELISTED_UTC",
                        "complete_historical_universe": "NOT_ESTABLISHED_BY_BOUNDED_PROBES",
                        "effective_dated_identity_and_ticker_history": "NOT_ESTABLISHED",
                        "terminal_price_and_delisting_treatment": "NOT_ESTABLISHED"},
        "status": "VERIFIED" if availability_verified else "BLOCKED",
        "full_backfill_authorized": False, "research_only": True,
        "automatic_promotion": False, "ready_for_live_routing": False,
    }
