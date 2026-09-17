"""Bounded, read-only follow-up to ended-listing availability verification."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import urllib.parse
from typing import Any, Callable

from moneybot.services.alpha_atlas_v4_delisted_coverage import _request
from moneybot.services.alpha_atlas_v4_phase1_discovery import _urllib_fetch
from moneybot.services.market_data_providers import ExchangeCalendar

GAP_TICKERS = ("GSS", "SWCH", "KAII", "MGI")
IDENTITY_LOOKBACK_SESSIONS = 5
TARGET_IDENTITY_TICKERS = (
    "BWINA", "BWINB", "PTVCA", "PTVCB", "KHD", "MFCB", "MIL", "TRY", "TRY.B",
    "FITBM", "FITBO", "HUB.A", "HUB.B", "ANDV", "TSO", "TSOw", "FRM", "XNR",
    "KV.A", "KV.B",
)
MAX_TARGET_IDENTITY_RECORDS = len(TARGET_IDENTITY_TICKERS)
MAX_LISTING_METADATA_PAGES_PER_TICKER = 2
MAX_LISTING_METADATA_RECORDS_PER_TICKER = 20

TRANSITION_CASES = (
    {"old_ticker": "BWINA", "new_ticker": "PTVCA", "security_class": "Class A common stock",
     "exchange": "Nasdaq", "old_date": "2018-07-31", "new_date": "2018-08-01",
     "event_effective_date": "2018-08-01", "source_publication_date": "2018-08-01",
     "source_url": "https://www.sec.gov/Archives/edgar/data/9346/000000934618000071/form8k.htm",
     "primary_evidence": "The issuer's Form 8-K states that Class A common stock ceased BWINA and began PTVCA on August 1, 2018."},
    {"old_ticker": "BWINB", "new_ticker": "PTVCB", "security_class": "Class B common stock",
     "exchange": "Nasdaq", "old_date": "2018-07-31", "new_date": "2018-08-01",
     "event_effective_date": "2018-08-01", "source_publication_date": "2018-08-01",
     "source_url": "https://www.sec.gov/Archives/edgar/data/9346/000000934618000071/form8k.htm",
     "primary_evidence": "The issuer's Form 8-K states that Class B common stock ceased BWINB and began PTVCB on August 1, 2018."},
    {"old_ticker": "KAII", "new_ticker": "QDRO", "security_class": "Class A ordinary shares",
     "exchange": "Nasdaq", "old_date": "2023-02-24", "new_date": "2023-02-27",
     "event_effective_date": "2023-02-27", "source_publication_date": "2023-02-24",
     "source_url": "https://www.sec.gov/Archives/edgar/data/1825962/000121390023014342/ea174191-8k_quadroacq1.htm",
     "primary_evidence": "The issuer's Form 8-K maps Class A ordinary shares from KAII to QDRO at the February 27, 2023 market open."},
    {"old_ticker": "KAIIU", "new_ticker": "QDROU", "security_class": "units",
     "exchange": "Nasdaq", "old_date": "2023-02-24", "new_date": "2023-02-27",
     "event_effective_date": "2023-02-27", "source_publication_date": "2023-02-24",
     "source_url": "https://www.sec.gov/Archives/edgar/data/1825962/000121390023014342/ea174191-8k_quadroacq1.htm",
     "primary_evidence": "The issuer's Form 8-K separately maps units from KAIIU to QDROU at the February 27, 2023 market open."},
    {"old_ticker": "KAIIW", "new_ticker": "QDROW", "security_class": "redeemable warrants",
     "exchange": "Nasdaq", "old_date": "2023-02-24", "new_date": "2023-02-27",
     "event_effective_date": "2023-02-27", "source_publication_date": "2023-02-24",
     "source_url": "https://www.sec.gov/Archives/edgar/data/1825962/000121390023014342/ea174191-8k_quadroacq1.htm",
     "primary_evidence": "The issuer's Form 8-K separately maps warrants from KAIIW to QDROW at the February 27, 2023 market open."},
)

# Primary-source research is deliberately checked in rather than inferred from a
# missing aggregate.  publication_date is also retained so later users cannot
# accidentally turn retrospective knowledge into point-in-time knowledge.
GAP_EVIDENCE: dict[tuple[str, str], dict[str, Any]] = {
    ("GSS", "2022-01-28"): {
        "classification": "EXPLAINED_NONTRADING_AFTER_ACQUISITION",
        "source_url": "https://gse.com.gh/wp-content/uploads/2022/01/PR-014-GSR-Chifeng-Jilong-Gold-Completes-the-Acquisition-of-Golden-Star-Resources.pdf",
        "source_type": "issuer_release_hosted_by_exchange", "publication_date": "2022-01-28",
        "event_effective_date": "2022-01-28",
        "evidence": "Golden Star announced completion of the plan of arrangement on January 28; each share was acquired for US$3.91 and delisting was to follow.",
        "available_at_original_decision_time": False,
        "conclusion": "The effective acquisition explains why GSS was not trading on this otherwise eligible session; it does not supply a January 28 price or independently settle payment timing.",
    },
    ("SWCH", "2022-12-06"): {
        "classification": "EXPLAINED_NONTRADING_MERGER_OPEN_HALT",
        "source_url": "https://www.sec.gov/Archives/edgar/data/1710583/000119312522298966/d356989d8k.htm",
        "source_type": "sec_form_8_k", "publication_date": "2022-12-06", "event_effective_date": "2022-12-06",
        "evidence": "The filed 8-K says the merger completed and NYSE trading was requested halted before the December 6 open; shares converted to the right to receive $34.25 cash.",
        "available_at_original_decision_time": False,
        "conclusion": "The pre-open halt explains the absent daily bar. The contractual merger consideration is not treated here as a retrieved price or as verified portfolio proceeds timing.",
    },
    ("MGI", "2023-06-01"): {
        "classification": "EXPLAINED_NONTRADING_MERGER_OPEN_HALT",
        "source_url": "https://www.sec.gov/Archives/edgar/data/1273931/000119312523158474/d493619d8k.htm",
        "source_type": "sec_form_8_k", "publication_date": "2023-06-01", "event_effective_date": "2023-06-01",
        "evidence": "MoneyGram reported merger completion and requested Nasdaq halt trading before the June 1 open and suspend trading effective at the close.",
        "available_at_original_decision_time": False,
        "conclusion": "The pre-open halt explains the absent daily bar, but neither creates a June 1 market price nor establishes terminal valuation cash timing.",
    },
}

KAII_EVIDENCE = {
    "classification": "TRADING_ELIGIBLE_GAP_UNRESOLVED",
    "source_url": "https://www.sec.gov/Archives/edgar/data/1825962/000121390023014342/ea174191-8k_quadroacq1.htm",
    "source_type": "sec_form_8_k", "publication_date": "2023-02-24", "event_effective_date": "2023-02-27",
    "evidence": "The filing keeps Class A shares, units, and warrants distinct and says KAII, KAIIU, and KAIIW changed to QDRO, QDROU, and QDROW at the February 27 market open.",
    "conclusion": "KAII remained the Class A share ticker on the cited session. No primary event evidence found in this bounded review explains the absent aggregate; no-trade, halt, and provider omission remain unproven alternatives.",
}


def _request_failure(exc: ValueError, provenance: list[dict[str, Any]]) -> dict[str, Any]:
    last = provenance[-1] if provenance else {}
    return {"reason_code": str(exc).split(":", 1)[0], "reason": str(exc),
            "http_status": last.get("status"), "request_url": last.get("url"),
            "response_sha256": last.get("response_sha256"), "response_bytes": last.get("response_bytes")}


def _date_from_ms(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(float(value) / 1000, timezone.utc).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def _listing_query_url(ticker: str) -> str:
    return "https://api.massive.com/v3/reference/tickers?" + urllib.parse.urlencode(
        {"ticker": ticker, "market": "stocks", "active": "false", "limit": 10})


def _fetch_listing_metadata(ticker: str, *, api_key: str, fetcher: Callable,
                            provenance: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch only one exact ticker with independent page and record budgets."""
    initial_url = _listing_query_url(ticker)
    url: str | None = initial_url
    pages = 0
    records: list[dict[str, Any]] = []
    duplicate_records = 0
    seen: set[str] = set()
    while url and pages < MAX_LISTING_METADATA_PAGES_PER_TICKER:
        payload = _request(fetcher, api_key, url, provenance)
        page = payload.get("results") or []
        if not isinstance(page, list) or not all(isinstance(row, dict) for row in page):
            raise ValueError("LISTING_METADATA_MALFORMED_RESULTS")
        pages += 1
        for row in page:
            if str(row.get("ticker") or "") != ticker:
                continue
            fingerprint = repr(sorted(row.items()))
            if fingerprint in seen:
                duplicate_records += 1
                continue
            seen.add(fingerprint)
            records.append(row)
            if len(records) > MAX_LISTING_METADATA_RECORDS_PER_TICKER:
                return records, {"status": "LIMIT_EXHAUSTED", "limit_name": "listing_metadata_records_per_ticker",
                                 "configured_limit": MAX_LISTING_METADATA_RECORDS_PER_TICKER,
                                 "observed_count": len(records), "pages_requested": pages,
                                 "pagination_remaining": bool(payload.get("next_url")),
                                 "sanitized_query": initial_url, "affected_ticker": ticker,
                                 "duplicate_records_ignored": duplicate_records}
        url = payload.get("next_url")
    status = "LIMIT_EXHAUSTED" if url else "COMPLETE"
    diagnostics = {"status": status,
                   "limit_name": "listing_metadata_pages_per_ticker" if url else None,
                   "configured_limit": MAX_LISTING_METADATA_PAGES_PER_TICKER if url else None,
                   "observed_count": len(records), "pages_requested": pages,
                   "pagination_remaining": bool(url), "sanitized_query": initial_url,
                   "affected_ticker": ticker, "duplicate_records_ignored": duplicate_records}
    return records, diagnostics


def _historical_identity_date(row: dict[str, Any], research_start: str, research_end: str,
                              calendar: ExchangeCalendar) -> tuple[str | None, str]:
    """Choose an actual session inside this ticker listing, never research_end by default."""
    try:
        listed = date.fromisoformat(str(row["list_date"])[:10])
        delisted = date.fromisoformat(str(row["delisted_utc"])[:10])
        start, end = date.fromisoformat(research_start), date.fromisoformat(research_end)
    except (KeyError, TypeError, ValueError):
        return None, "HISTORICAL_IDENTITY_DATE_UNRESOLVED"
    first, last = max(listed, start), min(delisted - timedelta(days=1), end)
    sessions = []
    while first <= last:
        if calendar.is_trading_day(first):
            sessions.append(first)
        first += timedelta(days=1)
    if not sessions:
        return None, "HISTORICAL_IDENTITY_DATE_UNRESOLVED"
    # The last supported session best brackets a transition without querying an
    # ended symbol years later. It is selection-by-metadata, not by HTTP success.
    return sessions[-1].isoformat(), "LAST_EXCHANGE_SESSION_BEFORE_PROVIDER_LISTING_END"


def _ended_date(value: Any) -> date | None:
    """Normalize an ended-listing timestamp to its UTC calendar date."""
    if not value:
        return None
    text = str(value).strip()
    try:
        if len(text) == 10:
            return date.fromisoformat(text)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).date()
    except ValueError:
        return None


def _identity_candidate_dates(row: dict[str, Any], research_start: str, research_end: str,
                              calendar: ExchangeCalendar) -> tuple[list[str], str]:
    """Return a fixed lookback ending before the UTC ended-listing date."""
    ended = _ended_date(row.get("delisted_utc"))
    if ended is None:
        return [], "HISTORICAL_IDENTITY_END_DATE_UNRESOLVED"
    research_first, research_last = date.fromisoformat(research_start), date.fromisoformat(research_end)
    cursor = min(ended - timedelta(days=1), research_last)
    listed = row.get("list_date")
    lower = research_first
    if listed:
        try:
            lower = max(lower, date.fromisoformat(str(listed)[:10]))
        except ValueError:
            return [], "HISTORICAL_IDENTITY_LIST_DATE_INVALID"
    sessions: list[str] = []
    while cursor >= lower and len(sessions) < IDENTITY_LOOKBACK_SESSIONS:
        if calendar.is_trading_day(cursor):
            sessions.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    if not sessions:
        return [], "HISTORICAL_IDENTITY_DATE_UNRESOLVED"
    basis = ("ENDED_TIMESTAMP_BOUNDED_PRECEDING_SESSIONS_WITHOUT_LIST_DATE"
             if not listed else "LISTING_INTERVAL_BOUNDED_PRECEDING_SESSIONS")
    return sessions, basis


def _reference_attempt(ticker: str, as_of: str, *, api_key: str, fetcher: Callable,
                       provenance: list[dict[str, Any]]) -> dict[str, Any]:
    url = f"https://api.massive.com/v3/reference/tickers/{urllib.parse.quote(ticker, safe='')}?date={as_of}"
    try:
        reference = _request(fetcher, api_key, url, provenance).get("results") or {}
        returned = isinstance(reference, dict) and str(reference.get("ticker") or "") == ticker
        return {"date": as_of, "request_succeeded": True, "exact_ticker_returned": returned,
                "response_metadata": {key: reference.get(key) for key in
                    ("ticker", "name", "type", "market", "primary_exchange", "share_class_figi", "composite_figi", "cik")}
                    if isinstance(reference, dict) else {},
                "request_provenance": dict(provenance[-1])}
    except ValueError as exc:
        return {"date": as_of, "request_succeeded": False, "exact_ticker_returned": False,
                "request_failure": _request_failure(exc, provenance),
                "request_provenance": dict(provenance[-1]) if provenance else {}}


def _security_type_conflict(row: dict[str, Any]) -> dict[str, Any] | None:
    description = " ".join(str(row.get(key) or "") for key in ("name", "description")).lower()
    if str(row.get("type") or "").upper() == "CS" and any(term in description for term in ("preferred", "depositary")):
        return {"status": "CONFLICTING_SECURITY_TYPE_METADATA", "provider_type": row.get("type"),
                "description": row.get("name") or row.get("description"),
                "conclusion": "Neither the CS code nor the preferred/depositary description is accepted as conclusive identity evidence."}
    return None


def _gap_evidence(ticker: str, session: str) -> dict[str, Any]:
    evidence = GAP_EVIDENCE.get((ticker, session))
    if evidence:
        return {**evidence, "price_retrieved": False, "trading_eligibility": "NOT_TRADING_DUE_TO_DOCUMENTED_EVENT",
                "terminal_value_inferred": False}
    if ticker == "KAII" and session in {"2023-01-19", "2023-02-17", "2023-02-24"}:
        return {**KAII_EVIDENCE, "available_at_original_decision_time": False,
                "price_retrieved": False, "trading_eligibility": "ELIGIBLE_LISTED_SESSION",
                "terminal_value_inferred": False}
    return {"classification": "REFERENCE_PRESENT_NO_EVENT_EVIDENCE", "price_retrieved": False,
            "trading_eligibility": "UNRESOLVED", "terminal_value_inferred": False,
            "evidence_needed": "dated exchange-status or corporate-action evidence explaining the absent bar"}


def diagnose_historical_coverage(source: dict[str, Any], *, api_key: str,
                                 repository_commit: str, generated_at: str,
                                 fetcher: Callable = _urllib_fetch) -> dict[str, Any]:
    if source.get("status") != "VERIFIED":
        raise ValueError("SOURCE_AVAILABILITY_NOT_VERIFIED")
    if not api_key:
        raise ValueError("HISTORICAL_COVERAGE_CREDENTIAL_REQUIRED")
    provenance: list[dict[str, Any]] = []
    calendar = ExchangeCalendar()
    probes = {str(row.get("ticker")): row for row in source.get("probes") or []}
    gap_results = []
    for ticker in GAP_TICKERS:
        original = probes.get(ticker)
        if not original:
            gap_results.append({"ticker": ticker, "status": "SOURCE_PROBE_MISSING", "reason_codes": ["SOURCE_PROBE_MISSING"]})
            continue
        window = original.get("price_window") or {}; start, end = window.get("from"), window.get("to")
        url = f"https://api.massive.com/v2/aggs/ticker/{urllib.parse.quote(ticker, safe='')}/range/1/day/{start}/{end}?adjusted=true&sort=asc&limit=50000"
        try:
            bars = _request(fetcher, api_key, url, provenance).get("results") or []
        except ValueError as exc:
            gap_results.append({"ticker": ticker, "requested_window": {"from": start, "to": end},
                                "status": "REQUEST_FAILED", "reason_codes": ["DAILY_PRICE_REQUEST_FAILED"],
                                "request_failure": _request_failure(exc, provenance), "missing_sessions": [],
                                "duplicate_sessions": [], "out_of_window_sessions": [], "missing_session_investigations": []})
            continue
        observed = [_date_from_ms(row.get("t")) for row in bars]; valid = [value for value in observed if value]
        current, finish, expected = date.fromisoformat(start), date.fromisoformat(end), []
        while current <= finish:
            if calendar.is_trading_day(current): expected.append(current.isoformat())
            current += timedelta(days=1)
        missing = sorted(set(expected) - set(valid)); duplicates = sorted({v for v in valid if valid.count(v) > 1})
        outside = sorted(v for v in valid if v not in set(expected)); investigations = []
        for session in missing:
            ref_url = f"https://api.massive.com/v3/reference/tickers/{urllib.parse.quote(ticker, safe='')}?date={session}"
            try:
                reference = _request(fetcher, api_key, ref_url, provenance).get("results") or {}
                present = isinstance(reference, dict) and str(reference.get("ticker") or "") == ticker
                investigations.append({"session": session, "reference_present": present,
                                       **(_gap_evidence(ticker, session) if present else {
                                           "classification": "REFERENCE_NOT_RETURNED", "price_retrieved": False,
                                           "trading_eligibility": "UNRESOLVED", "terminal_value_inferred": False})})
            except ValueError as exc:
                investigations.append({"session": session, "reference_present": False,
                                       "classification": "REFERENCE_REQUEST_FAILED",
                                       "request_failure": _request_failure(exc, provenance), "price_retrieved": False,
                                       "trading_eligibility": "UNRESOLVED", "terminal_value_inferred": False})
        gap_results.append({"ticker": ticker, "requested_window": {"from": start, "to": end},
                            "expected_eligible_sessions": len(expected), "bars_returned": len(bars),
                            "missing_sessions": missing, "duplicate_sessions": duplicates,
                            "out_of_window_sessions": outside, "missing_session_investigations": investigations,
                            "status": "COMPLETE" if not missing and not duplicates and not outside else "INVESTIGATED_GAPS",
                            "reason_codes": (["MISSING_DAILY_PRICE_EVIDENCE"] if missing else [])
                            + (["DUPLICATE_DAILY_PRICE_EVIDENCE"] if duplicates else [])
                            + (["OUT_OF_WINDOW_PRICE_EVIDENCE"] if outside else [])})

    research = source.get("research_interval") or {}; research_start = research.get("start", "2018-01-01")
    research_end = research.get("end", "2026-09-15"); identity_results = []
    source_groups = source.get("identity_investigation_candidates") or []
    target_set = set(TARGET_IDENTITY_TICKERS)
    source_occurrences = [(group, str(ticker)) for group in source_groups for ticker in (group.get("tickers") or [])]
    targeted_occurrences = [(group, ticker) for group, ticker in source_occurrences if ticker in target_set]
    duplicate_target_occurrences = len(targeted_occurrences) - len({ticker for _, ticker in targeted_occurrences})
    selected_groups = []
    selected_tickers: set[str] = set()
    for group in source_groups:
        selected = []
        for ticker_value in group.get("tickers") or []:
            ticker = str(ticker_value)
            if ticker in target_set and ticker not in selected_tickers:
                selected.append(ticker); selected_tickers.add(ticker)
        if selected:
            selected_groups.append({**group, "tickers": selected})
    planned_records = [(group, ticker) for group in selected_groups for ticker in group["tickers"]]
    limit_exhausted = len(planned_records) > MAX_TARGET_IDENTITY_RECORDS
    process_records = planned_records[:MAX_TARGET_IDENTITY_RECORDS]
    remaining_records = [ticker for _, ticker in planned_records[MAX_TARGET_IDENTITY_RECORDS:]]
    missing_target_tickers = sorted(target_set - selected_tickers)
    by_group: dict[tuple[str, str], list[str]] = {}
    group_values: dict[tuple[str, str], dict[str, Any]] = {}
    for group, ticker in process_records:
        key = (str(group.get("identifier_type")), str(group.get("identifier_value")))
        by_group.setdefault(key, []).append(ticker); group_values[key] = group
    for key, tickers in by_group.items():
        group = group_values[key]
        ticker_evidence = []
        for ticker in tickers:
            listing_rows: list[dict[str, Any]] = []
            try:
                listing_rows, listing_diagnostics = _fetch_listing_metadata(
                    ticker, api_key=api_key, fetcher=fetcher, provenance=provenance)
            except ValueError as exc:
                ticker_evidence.append({"ticker": ticker, "status": "LISTING_METADATA_REQUEST_FAILED",
                                        "original_failed_request": {"date": research_end, "http_status": 404,
                                                                    "source_run_id": 35176245513},
                                        "request_failure": _request_failure(exc, provenance)})
                continue
            if listing_diagnostics["status"] == "LIMIT_EXHAUSTED":
                ticker_evidence.append({"ticker": ticker, "status": "LISTING_METADATA_LIMIT_EXHAUSTED",
                                        "limit_diagnostics": listing_diagnostics,
                                        "completed_listing_records": listing_rows,
                                        "reference_request_issued": False})
                continue
            if len(listing_rows) != 1:
                ticker_evidence.append({"ticker": ticker, "status": "LISTING_METADATA_MISSING_OR_AMBIGUOUS",
                                        "listing_diagnostics": listing_diagnostics,
                                        "completed_listing_records": listing_rows,
                                        "reference_request_issued": False})
                continue
            row = listing_rows[0]
            candidate_dates, basis = _identity_candidate_dates(row, research_start, research_end, calendar)
            ended = _ended_date(row.get("delisted_utc"))
            relevance = ("ENDED_WITHIN_RESEARCH_INTERVAL" if ended and research_start <= ended.isoformat() <= research_end
                         else "PRE_RESEARCH_PREDECESSOR_CANDIDATE" if ended and ended.isoformat() < research_start
                         else "ENDED_DATE_RELEVANCE_UNRESOLVED")
            predecessor_need = ("DIRECT_RESEARCH_INTERVAL_CASE" if relevance == "ENDED_WITHIN_RESEARCH_INTERVAL" else
                                "NOT_REQUIRED_UNLESS_CHAIN_CROSSES_RESEARCH_BOUNDARY" if relevance == "PRE_RESEARCH_PREDECESSOR_CANDIDATE" else
                                "UNRESOLVED")
            item = {"ticker": ticker, "listing_metadata": {k: row.get(k) for k in
                    ("ticker", "name", "description", "type", "market", "primary_exchange", "list_date", "delisted_utc",
                     "share_class_figi", "composite_figi", "cik")},
                    "candidate_dates": candidate_dates, "query_date": candidate_dates[0] if candidate_dates else None,
                    "date_selection_basis": basis, "research_relevance": relevance,
                    "predecessor_link_necessity": predecessor_need,
                    "security_type_investigation": _security_type_conflict(row),
                    "original_failed_request": {"date": research_end, "http_status": 404,
                                                "source_run_id": 35176245513}}
            if not candidate_dates:
                ticker_evidence.append({**item, "status": basis,
                                        "reference_request_issued": False})
                continue
            attempts = []
            for candidate in candidate_dates:
                attempt = _reference_attempt(ticker, candidate, api_key=api_key, fetcher=fetcher, provenance=provenance)
                attempts.append(attempt)
                if attempt["exact_ticker_returned"]:
                    break
            success = next((attempt for attempt in attempts if attempt["exact_ticker_returned"]), None)
            ticker_evidence.append({**item, "status": "HISTORICAL_REFERENCE_CANDIDATE_FOUND" if success else
                                    "HISTORICAL_IDENTITY_REFERENCE_UNRESOLVED",
                                    "reference_request_issued": True, "reference_attempts": attempts,
                                    "historical_reference_date": success.get("date") if success else None,
                                    "identifiers": (success.get("response_metadata") or {}) if success else {},
                                    "candidate_is_trading_proof": False})
        identity_results.append({"identifier_type": group.get("identifier_type"),
                                 "identifier_value": group.get("identifier_value"), "tickers": tickers,
                                 "identifier_matching_is_transition_proof": False,
                                 "dated_reference_evidence": ticker_evidence,
                                 "status": "INVESTIGATED_NOT_VERIFIED", "verified_ticker_chain": False})

    identity_scope = {
        "source_query": {"market": "stocks", "type": "CS", "active": False},
        "source_pagination": source.get("pagination"),
        "source_candidate_groups": len(source_groups),
        "source_ticker_occurrences": len(source_occurrences),
        "target_tickers_configured": len(TARGET_IDENTITY_TICKERS),
        "target_occurrences_received": len(targeted_occurrences),
        "target_unique_received": len(selected_tickers),
        "duplicate_target_occurrences_ignored": duplicate_target_occurrences,
        "unrelated_ticker_occurrences_excluded": len(source_occurrences) - len(targeted_occurrences),
        "missing_target_tickers": missing_target_tickers,
        "processing_limit": {"name": "target_identity_records", "configured": MAX_TARGET_IDENTITY_RECORDS,
                             "observed": len(planned_records), "exhausted": limit_exhausted},
        "completed_target_records": sum(len(group["dated_reference_evidence"]) for group in identity_results),
        "remaining_unprocessed_tickers": remaining_records,
        "listing_request_budget": {"maximum_requests": MAX_TARGET_IDENTITY_RECORDS * MAX_LISTING_METADATA_PAGES_PER_TICKER,
                                   "pages_per_ticker": MAX_LISTING_METADATA_PAGES_PER_TICKER,
                                   "records_per_ticker": MAX_LISTING_METADATA_RECORDS_PER_TICKER},
        "historical_reference_attempt_budget": MAX_TARGET_IDENTITY_RECORDS * IDENTITY_LOOKBACK_SESSIONS,
        "status": "BLOCKED_INCOMPLETE" if limit_exhausted or missing_target_tickers else "COMPLETE_BOUNDED_SCOPE",
    }

    transition_results = []
    for case in TRANSITION_CASES:
        old = _reference_attempt(case["old_ticker"], case["old_date"], api_key=api_key,
                                 fetcher=fetcher, provenance=provenance)
        new = _reference_attempt(case["new_ticker"], case["new_date"], api_key=api_key,
                                 fetcher=fetcher, provenance=provenance)
        references_confirmed = old["exact_ticker_returned"] and new["exact_ticker_returned"]
        result = "VERIFIED" if references_confirmed else "UNRESOLVED_REFERENCE_EVIDENCE"
        transition_results.append({**case, "old_reference": old, "new_reference": new,
                                   "result": result, "same_security_continuity": result == "VERIFIED",
                                   "verification_basis": "explicit class-specific primary filing plus both dated provider references",
                                   "shared_cik_alone_used": False, "retrospective_reconstruction": True,
                                   "available_at_original_decision_time": case["source_publication_date"] < case["event_effective_date"]})

    request_failures = [x for x in gap_results if x["status"] == "REQUEST_FAILED"]
    gap_ref_failures = [i for x in gap_results for i in x.get("missing_session_investigations", []) if i.get("request_failure")]
    identity_failures = ([item for x in identity_results for item in x["dated_reference_evidence"]
                          if item.get("request_failure")]
                         + [attempt for x in identity_results for item in x["dated_reference_evidence"]
                            for attempt in item.get("reference_attempts", []) if not attempt["request_succeeded"]])
    transition_request_failures = [attempt for item in transition_results for attempt in
                                   (item["old_reference"], item["new_reference"]) if not attempt["request_succeeded"]]
    identity_limit_failures = [item for group in identity_results for item in group["dated_reference_evidence"]
                               if item.get("status") == "LISTING_METADATA_LIMIT_EXHAUSTED"]
    unresolved_codes = {code for item in gap_results for code in item.get("reason_codes", [])}
    unresolved_codes |= {"EFFECTIVE_DATED_IDENTITY_CHAIN_UNVERIFIED", "UNRESOLVED_EARLIER_SNAPSHOT_UNAVAILABLE",
                         "HISTORICAL_UNIVERSE_COMPLETENESS_UNVERIFIED", "TERMINAL_VALUATION_UNVERIFIED"}
    if any(i.get("status") in {"HISTORICAL_IDENTITY_DATE_UNRESOLVED", "HISTORICAL_IDENTITY_END_DATE_UNRESOLVED",
                               "HISTORICAL_IDENTITY_LIST_DATE_INVALID"}
           for x in identity_results for i in x["dated_reference_evidence"]):
        unresolved_codes.add("HISTORICAL_IDENTITY_DATE_UNRESOLVED")
    if any(item["result"] != "VERIFIED" for item in transition_results):
        unresolved_codes.add("EFFECTIVE_DATED_IDENTITY_TRANSITION_REFERENCE_UNRESOLVED")
    if limit_exhausted:
        unresolved_codes.add("TARGET_IDENTITY_RECORD_LIMIT_EXHAUSTED")
    if missing_target_tickers:
        unresolved_codes.add("TARGET_IDENTITY_CASES_MISSING_FROM_SOURCE")
    if identity_limit_failures:
        unresolved_codes.add("LISTING_METADATA_LIMIT_EXHAUSTED")
    return {
        "schema_version": "alpha-atlas-v4-historical-coverage-diagnostics.v2", "generated_at_utc": generated_at,
        "repository_commit": repository_commit,
        "source": {"workflow": "Alpha Atlas V4 Delisted Coverage Verification", "run_id": 35125664186,
                   "run_attempt": 1, "head_sha": "205529d612c1ac2a3497a07f5cb6151d2eef62f4",
                   "report_sha256": "896a61604ae6fc8ba16821c0bf4d609b0e48efe275bd066318acc243351d0d7d",
                   "bounded_availability": {"representative": "12/12", "follow_ups": "2/2", "distinct_tickers": 12,
                                            "status": "VERIFIED_DO_NOT_REOPEN"}},
        "failed_diagnostic_source": {"run_id": 35176245513, "run_attempt": 1,
                                     "head_sha": "79b51d02e454dac6dae1c3660d7ddd8761bbab79",
                                     "preserved_unsupported_identity_requests": 20, "date": "2026-09-15", "http_status": 404},
        "prior_diagnostic_source": {"run_id": 35178703375, "run_attempt": 1,
                                    "head_sha": "0dc495973f7b2bb2892c6e78ae0d933d475fe80d",
                                    "report_sha256": "78ee7d3afe8434ff952b46b2899d529c37d4864cfe3f30ada61467bd48fb7081",
                                    "identity_records": 20, "historical_reference_requests": 0,
                                    "finding": "HISTORICAL_IDENTITY_DATE_UNRESOLVED"},
        "daily_price_gap_diagnostics": gap_results, "identity_diagnostics": identity_results,
        "identity_investigation_scope": identity_scope,
        "transition_diagnostics": transition_results,
        "identity_request_summary": {
            "generic_historical_reference_requests": sum(len(item.get("reference_attempts", [])) for group in identity_results for item in group["dated_reference_evidence"]),
            "transition_historical_reference_requests": len(transition_results) * 2,
            "transition_cases_verified": sum(item["result"] == "VERIFIED" for item in transition_results),
            "transition_cases_total": len(transition_results),
        },
        "population_reconciliation": {"current_inactive_count": (source.get("population") or {}).get("inactive_listings"),
                                      "prior_reported_count": 6629, "status": "UNRESOLVED_EARLIER_SNAPSHOT_UNAVAILABLE"},
        "universe_completeness": {"current_query_pagination_complete": (source.get("pagination") or {}).get("complete"),
                                  "status": "NOT_ESTABLISHED_FOR_HISTORICAL_INTERVAL",
                                  "reason": "A complete current active=false query is not a point-in-time universe."},
        "terminal_valuation": {"status": "NOT_ESTABLISHED", "zero_recovery_assumed": False,
                               "forward_fill_used": False, "securities_substituted": False},
        "sanitized_request_provenance": provenance,
        "status": "BLOCKED" if request_failures or gap_ref_failures or identity_failures or transition_request_failures or identity_limit_failures or limit_exhausted or missing_target_tickers else "VERIFIED_DIAGNOSTIC_EXECUTION",
        "unresolved_reason_codes": sorted(unresolved_codes),
        "request_failure_count": len(request_failures) + len(gap_ref_failures) + len(identity_failures) + len(transition_request_failures),
        "research_only": True, "full_backfill_authorized": False, "automatic_promotion": False,
        "ready_for_live_routing": False,
    }
