"""Bounded, read-only follow-up to ended-listing availability verification."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import urllib.parse
from typing import Any, Callable

from moneybot.services.alpha_atlas_v4_delisted_coverage import _request
from moneybot.services.alpha_atlas_v4_phase1_discovery import _urllib_fetch
from moneybot.services.market_data_providers import ExchangeCalendar

GAP_TICKERS = ("GSS", "SWCH", "KAII", "MGI")
KAII_GAP_DATES = ("2023-01-19", "2023-02-17", "2023-02-24")
KAII_ENDPOINT_PAGE_LIMIT = 2
KAII_ENDPOINT_RECORD_LIMIT = 5_000
KAII_PRESERVED_RECORD_SAMPLE_LIMIT = 100
MASSIVE_ENDPOINT_DOCUMENTATION = {
    "aggregates": "https://massive.com/docs/rest/stocks/aggregates/custom-bars",
    "trades": "https://massive.com/docs/rest/stocks/trades-quotes/trades",
    "quotes": "https://massive.com/docs/rest/stocks/trades-quotes/quotes",
    "conditions": "https://massive.com/docs/rest/stocks/market-operations/condition-codes",
}
MASSIVE_DOCUMENTATION_REVIEW = {
    "reviewed_at_utc": "2026-09-17T00:00:00Z",
    "timestamp_semantics": "Session bounds are sent as timezone-aware RFC 3339 UTC instants; aggregate t values are interpreted as Unix milliseconds.",
    "pagination_semantics": "Only provider next_url is followed, within the explicit per-endpoint page and record budgets.",
    "sale_condition_rule": "Trade condition metadata must be reviewed before aggregate eligibility is inferred; returned trades are not silently converted into a daily bar.",
    "quote_rule": "A quote response is evidence of quoting only and does not establish an eligible trade.",
}
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
     "event_effective_at": "2018-08-01T09:30:00-04:00", "source_acceptance_at": "2018-08-01T16:14:57-04:00",
     "source_url": "https://www.sec.gov/Archives/edgar/data/9346/000000934618000071/form8k.htm",
     "primary_evidence": "The issuer's Form 8-K states that Class A common stock ceased BWINA and began PTVCA on August 1, 2018."},
    {"old_ticker": "BWINB", "new_ticker": "PTVCB", "security_class": "Class B common stock",
     "exchange": "Nasdaq", "old_date": "2018-07-31", "new_date": "2018-08-01",
     "event_effective_date": "2018-08-01", "source_publication_date": "2018-08-01",
     "event_effective_at": "2018-08-01T09:30:00-04:00", "source_acceptance_at": "2018-08-01T16:14:57-04:00",
     "source_url": "https://www.sec.gov/Archives/edgar/data/9346/000000934618000071/form8k.htm",
     "primary_evidence": "The issuer's Form 8-K states that Class B common stock ceased BWINB and began PTVCB on August 1, 2018."},
    {"old_ticker": "KAII", "new_ticker": "QDRO", "security_class": "Class A ordinary shares",
     "exchange": "Nasdaq", "old_date": "2023-02-24", "new_date": "2023-02-27",
     "event_effective_date": "2023-02-27", "source_publication_date": "2023-02-24",
     "event_effective_at": "2023-02-27T09:30:00-05:00", "source_acceptance_at": "2023-02-24T16:15:38-05:00",
     "source_url": "https://www.sec.gov/Archives/edgar/data/1825962/000121390023014342/ea174191-8k_quadroacq1.htm",
     "primary_evidence": "The issuer's Form 8-K maps Class A ordinary shares from KAII to QDRO at the February 27, 2023 market open."},
    {"old_ticker": "KAIIU", "new_ticker": "QDROU", "security_class": "units",
     "exchange": "Nasdaq", "old_date": "2023-02-24", "new_date": "2023-02-27",
     "event_effective_date": "2023-02-27", "source_publication_date": "2023-02-24",
     "event_effective_at": "2023-02-27T09:30:00-05:00", "source_acceptance_at": "2023-02-24T16:15:38-05:00",
     "source_url": "https://www.sec.gov/Archives/edgar/data/1825962/000121390023014342/ea174191-8k_quadroacq1.htm",
     "primary_evidence": "The issuer's Form 8-K separately maps units from KAIIU to QDROU at the February 27, 2023 market open."},
    {"old_ticker": "KAIIW", "new_ticker": "QDROW", "security_class": "redeemable warrants",
     "exchange": "Nasdaq", "old_date": "2023-02-24", "new_date": "2023-02-27",
     "event_effective_date": "2023-02-27", "source_publication_date": "2023-02-24",
     "event_effective_at": "2023-02-27T09:30:00-05:00", "source_acceptance_at": "2023-02-24T16:15:38-05:00",
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


def _bounded_collection(url: str, *, api_key: str, fetcher: Callable,
                        provenance: list[dict[str, Any]], page_limit: int = KAII_ENDPOINT_PAGE_LIMIT,
                        record_limit: int = KAII_ENDPOINT_RECORD_LIMIT) -> dict[str, Any]:
    initial_url = url; pages = 0; records: list[dict[str, Any]] = []; provenance_start = len(provenance)
    while url and pages < page_limit and len(records) < record_limit:
        try:
            payload = _request(fetcher, api_key, url, provenance)
        except ValueError as exc:
            failure = _request_failure(exc, provenance)
            status = "ENTITLEMENT_DENIED" if failure.get("http_status") in {401, 403} else "REQUEST_FAILED"
            return {"status": status, "records": records, "pages_requested": pages + 1,
                    "pagination_complete": False, "request_failure": failure,
                    "initial_request_url": initial_url, "page_limit": page_limit, "record_limit": record_limit,
                    "request_provenance": provenance[provenance_start:]}
        page = payload.get("results") or []
        if not isinstance(page, list):
            return {"status": "MALFORMED_RESPONSE", "records": records, "pages_requested": pages + 1,
                    "pagination_complete": False, "initial_request_url": initial_url,
                    "page_limit": page_limit, "record_limit": record_limit,
                    "request_provenance": provenance[provenance_start:]}
        pages += 1
        remaining = record_limit - len(records)
        records.extend(row for row in page[:remaining] if isinstance(row, dict))
        url = payload.get("next_url")
    exhausted = bool(url) or len(records) >= record_limit
    preserved = records if len(records) <= KAII_PRESERVED_RECORD_SAMPLE_LIMIT else records[:50] + records[-50:]
    return {"status": "BUDGET_EXHAUSTED" if exhausted else "DATA_RETURNED" if records else "EMPTY_RESPONSE",
            "records": preserved, "records_observed": len(records),
            "records_sampled": len(preserved), "records_truncated_in_report": len(records) > len(preserved),
            "pages_requested": pages, "pagination_complete": not url,
            "pagination_remaining": bool(url), "initial_request_url": initial_url,
            "page_limit": page_limit, "record_limit": record_limit,
            "request_provenance": provenance[provenance_start:]}


def classify_kaii_gap_evidence(*, daily: dict[str, Any], intraday: dict[str, Any],
                               trades: dict[str, Any], quotes: dict[str, Any]) -> str:
    if daily.get("target_status", daily["status"]) == "DATA_RETURNED":
        return "DAILY_BAR_RECOVERED_DIAGNOSTIC_ONLY"
    if intraday["status"] == "DATA_RETURNED":
        return "INTRADAY_DATA_PRESENT_DAILY_AGGREGATE_MISSING"
    if trades["status"] == "DATA_RETURNED":
        return "TRADES_PRESENT_AGGREGATE_CONSTRUCTION_UNVERIFIED"
    if any(item["status"] == "BUDGET_EXHAUSTED" for item in (daily, intraday, trades, quotes)):
        return "INVESTIGATION_BUDGET_EXHAUSTED"
    if any(item["status"] == "REQUEST_FAILED" for item in (daily, intraday, trades, quotes)):
        return "PROVIDER_REQUEST_FAILURE_UNRESOLVED"
    if any(item["status"] == "ENTITLEMENT_DENIED" for item in (daily, intraday, trades, quotes)):
        return "PROVIDER_ENTITLEMENT_LIMITED_UNRESOLVED"
    if quotes["status"] == "DATA_RETURNED":
        return "QUOTES_PRESENT_NO_TRADE_OR_AGGREGATE_EVIDENCE"
    return "PROVIDER_EMPTY_RESPONSES_VENUE_TRADING_UNVERIFIED"


def _investigate_kaii_gap(session: str, *, api_key: str, fetcher: Callable,
                          provenance: list[dict[str, Any]], calendar: ExchangeCalendar) -> dict[str, Any]:
    day = date.fromisoformat(session)
    previous = calendar.previous_session(day)
    following = day + timedelta(days=1)
    while not calendar.is_trading_day(following):
        following += timedelta(days=1)
    control_from, control_to = previous.isoformat(), following.isoformat()
    encoded = urllib.parse.quote("KAII", safe="")
    daily_url = f"https://api.massive.com/v2/aggs/ticker/{encoded}/range/1/day/{control_from}/{control_to}?adjusted=true&sort=asc&limit={KAII_ENDPOINT_RECORD_LIMIT}"
    minute_url = f"https://api.massive.com/v2/aggs/ticker/{encoded}/range/1/minute/{session}/{session}?adjusted=true&sort=asc&limit={KAII_ENDPOINT_RECORD_LIMIT}"
    open_at = calendar.session_open(day).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    close_at = calendar.session_close(day).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    time_query = urllib.parse.urlencode({"timestamp.gte": open_at, "timestamp.lte": close_at,
                                        "sort": "timestamp", "order": "asc", "limit": KAII_ENDPOINT_RECORD_LIMIT})
    daily = _bounded_collection(daily_url, api_key=api_key, fetcher=fetcher, provenance=provenance, page_limit=1)
    daily["target_records"] = [row for row in daily.get("records", []) if _date_from_ms(row.get("t")) == session]
    daily["control_records"] = [row for row in daily.get("records", []) if _date_from_ms(row.get("t")) != session]
    daily["target_status"] = "DATA_RETURNED" if daily["target_records"] else (
        daily["status"] if daily["status"] not in {"DATA_RETURNED", "EMPTY_RESPONSE"} else "EMPTY_RESPONSE")
    intraday = _bounded_collection(minute_url, api_key=api_key, fetcher=fetcher, provenance=provenance, page_limit=1)
    trades = _bounded_collection(f"https://api.massive.com/v3/trades/KAII?{time_query}", api_key=api_key,
                                 fetcher=fetcher, provenance=provenance)
    quotes = _bounded_collection(f"https://api.massive.com/v3/quotes/KAII?{time_query}", api_key=api_key,
                                 fetcher=fetcher, provenance=provenance)
    classification = classify_kaii_gap_evidence(daily=daily, intraday=intraday, trades=trades, quotes=quotes)
    return {"session": session, "security": "KAII Class A ordinary shares", "exchange": "Nasdaq",
            "session_open_at": open_at, "session_close_at": close_at,
            "adjacent_control_window": {"from": control_from, "to": control_to},
            "daily": daily, "intraday": intraday, "trades": trades, "quotes": quotes,
            "classification": classification, "quote_evidence_proves_trade": False,
            "empty_trade_response_proves_venue_wide_no_trading": False,
            "aggregate_sale_condition_assessment": "UNVERIFIED" if trades.get("records") else "NOT_APPLICABLE_WITHOUT_RETURNED_TRADES",
            "halt_event_evidence": {"status": "NO_PRIMARY_HALT_EVIDENCE_FOUND_IN_BOUNDED_REVIEW",
                                    "source_url": TRANSITION_CASES[2]["source_url"],
                                    "explanation": "The February 24 filing documents the February 27 name change, not a halt on an earlier gap date."},
            "price_availability": "DIAGNOSTIC_CANDIDATE_ONLY" if classification == "DAILY_BAR_RECOVERED_DIAGNOSTIC_ONLY" else "NOT_RETRIEVED",
            "valuation_status": "UNVERIFIED_NO_VALUE_INFERRED", "replacement_authorized": False,
            "budgets": {"endpoint_queries_per_date": 4, "maximum_http_requests_per_date": 6,
                        "aggregate_pages": 1, "trade_pages": KAII_ENDPOINT_PAGE_LIMIT,
                        "quote_pages": KAII_ENDPOINT_PAGE_LIMIT, "records_per_endpoint": KAII_ENDPOINT_RECORD_LIMIT},
            "documentation": MASSIVE_ENDPOINT_DOCUMENTATION,
            "documentation_review": MASSIVE_DOCUMENTATION_REVIEW}


def _security_type_conflict(row: dict[str, Any]) -> dict[str, Any] | None:
    description = " ".join(str(row.get(key) or "") for key in ("name", "description")).lower()
    if str(row.get("type") or "").upper() == "CS" and any(term in description for term in ("preferred", "depositary")):
        return {"status": "CONFLICTING_SECURITY_TYPE_METADATA", "provider_type": row.get("type"),
                "description": row.get("name") or row.get("description"),
                "conclusion": "Neither the CS code nor the preferred/depositary description is accepted as conclusive identity evidence."}
    return None


def classify_decision_time_availability(*, source_acceptance_at: str,
                                        decision_at: str | None,
                                        feature_cutoff_at: str | None) -> dict[str, Any]:
    """Fail closed unless precise, timezone-aware decision and cutoff instants exist."""
    source_at = datetime.fromisoformat(source_acceptance_at)
    if source_at.tzinfo is None:
        raise ValueError("SOURCE_ACCEPTANCE_TIMEZONE_REQUIRED")
    base = {"source_acceptance_at": source_at.isoformat(), "decision_at": decision_at,
            "feature_cutoff_at": feature_cutoff_at}
    if not decision_at or not feature_cutoff_at:
        return {**base, "status": "UNVERIFIED/UNKNOWN", "available_at_original_decision_time": None,
                "feature_use_authorized": False,
                "reason": "No exact decision_at and feature_cutoff_at were present in the transition evidence."}
    try:
        decision = datetime.fromisoformat(decision_at)
        cutoff = datetime.fromisoformat(feature_cutoff_at)
    except ValueError as exc:
        raise ValueError("DECISION_TIME_INVALID") from exc
    if decision.tzinfo is None or cutoff.tzinfo is None:
        raise ValueError("DECISION_TIME_TIMEZONE_REQUIRED")
    available = source_at <= cutoff <= decision
    return {**base, "status": "VERIFIED_AVAILABLE" if available else "VERIFIED_UNAVAILABLE",
            "available_at_original_decision_time": available,
            "feature_use_authorized": available,
            "reason": "SEC acceptance instant compared with the exact feature cutoff and decision instants."}


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
                                 verified_transition_source: dict[str, Any] | None = None,
                                 investigate_kaii_gaps: bool = False,
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

    kaii_gap_investigations = [
        _investigate_kaii_gap(session, api_key=api_key, fetcher=fetcher,
                              provenance=provenance, calendar=calendar)
        for session in KAII_GAP_DATES
    ] if investigate_kaii_gaps else []
    trade_conditions = _bounded_collection(
        "https://api.massive.com/v3/reference/conditions?asset_class=stocks&data_type=trade&limit=1000",
        api_key=api_key, fetcher=fetcher, provenance=provenance, page_limit=2, record_limit=2000) if investigate_kaii_gaps else {
            "status": "NOT_REQUESTED", "records": [], "pages_requested": 0, "pagination_complete": False}
    documented_condition_ids = {str(row.get("id")) for row in trade_conditions.get("records", []) if row.get("id") is not None}
    for investigation in kaii_gap_investigations:
        used = {str(value) for trade in investigation["trades"].get("records", [])
                for value in (trade.get("conditions") or trade.get("c") or [])}
        investigation["trade_condition_reference"] = {
            "status": trade_conditions["status"], "condition_ids_observed": sorted(used),
            "all_observed_conditions_documented": bool(used) and used <= documented_condition_ids,
            "interpretation": "Documentation retrieval does not by itself prove aggregate eligibility; no reconstructed bar is authorized."}

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
        prior = next((item for item in (verified_transition_source or {}).get("transition_diagnostics", [])
                      if item.get("old_ticker") == case["old_ticker"] and item.get("new_ticker") == case["new_ticker"]), None)
        if prior and prior.get("result") == "VERIFIED":
            old, new, result = prior.get("old_reference") or {}, prior.get("new_reference") or {}, "VERIFIED"
            evidence_origin = "PINNED_RUN_35183625727"
        else:
            old = _reference_attempt(case["old_ticker"], case["old_date"], api_key=api_key,
                                     fetcher=fetcher, provenance=provenance)
            new = _reference_attempt(case["new_ticker"], case["new_date"], api_key=api_key,
                                     fetcher=fetcher, provenance=provenance)
            result = "VERIFIED" if old["exact_ticker_returned"] and new["exact_ticker_returned"] else "UNRESOLVED_REFERENCE_EVIDENCE"
            evidence_origin = "CURRENT_BOUNDED_EXECUTION"
        public_before_effective = datetime.fromisoformat(case["source_acceptance_at"]) <= datetime.fromisoformat(case["event_effective_at"])
        decision_availability = classify_decision_time_availability(
            source_acceptance_at=case["source_acceptance_at"], decision_at=None, feature_cutoff_at=None)
        transition_results.append({**case, "old_reference": old, "new_reference": new,
                                   "result": result, "same_security_continuity": result == "VERIFIED",
                                   "verification_basis": "explicit class-specific primary filing plus both dated provider references",
                                   "shared_cik_alone_used": False, "retrospective_reconstruction": True,
                                   "evidence_origin": evidence_origin,
                                   "public_knowability": {"status": "VERIFIED_AVAILABLE" if public_before_effective else "VERIFIED_UNAVAILABLE",
                                                          "comparison_instant": case["event_effective_at"],
                                                          "source_acceptance_at": case["source_acceptance_at"]},
                                   "decision_time_availability": decision_availability,
                                   "available_at_original_decision_time": decision_availability["available_at_original_decision_time"]})

    request_failures = [x for x in gap_results if x["status"] == "REQUEST_FAILED"]
    gap_ref_failures = [i for x in gap_results for i in x.get("missing_session_investigations", []) if i.get("request_failure")]
    identity_failures = ([item for x in identity_results for item in x["dated_reference_evidence"]
                          if item.get("request_failure")]
                         + [attempt for x in identity_results for item in x["dated_reference_evidence"]
                            for attempt in item.get("reference_attempts", []) if not attempt["request_succeeded"]])
    transition_request_failures = [attempt for item in transition_results for attempt in
                                   (item["old_reference"], item["new_reference"])
                                   if attempt and attempt.get("request_succeeded") is False]
    identity_limit_failures = [item for group in identity_results for item in group["dated_reference_evidence"]
                               if item.get("status") == "LISTING_METADATA_LIMIT_EXHAUSTED"]
    kaii_request_failures = [endpoint for item in kaii_gap_investigations
                             for endpoint in (item["daily"], item["intraday"], item["trades"], item["quotes"])
                             if endpoint["status"] == "REQUEST_FAILED"]
    if trade_conditions["status"] == "REQUEST_FAILED":
        kaii_request_failures.append(trade_conditions)
    kaii_budget_failures = [item for item in kaii_gap_investigations
                            if item["classification"] == "INVESTIGATION_BUDGET_EXHAUSTED"]
    if trade_conditions["status"] == "BUDGET_EXHAUSTED":
        kaii_budget_failures.append({"classification": "TRADE_CONDITION_BUDGET_EXHAUSTED"})
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
    if any(item["classification"] not in {"DAILY_BAR_RECOVERED_DIAGNOSTIC_ONLY"} for item in kaii_gap_investigations):
        unresolved_codes.add("KAII_MISSING_DAILY_BARS_UNRESOLVED")
    if kaii_budget_failures:
        unresolved_codes.add("KAII_INVESTIGATION_BUDGET_EXHAUSTED")
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
        "kaii_gap_investigations": kaii_gap_investigations,
        "trade_condition_reference": trade_conditions,
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
        "status": "BLOCKED" if request_failures or gap_ref_failures or identity_failures or transition_request_failures or identity_limit_failures or limit_exhausted or missing_target_tickers or kaii_request_failures or kaii_budget_failures else "VERIFIED_DIAGNOSTIC_EXECUTION",
        "unresolved_reason_codes": sorted(unresolved_codes),
        "request_failure_count": len(request_failures) + len(gap_ref_failures) + len(identity_failures) + len(transition_request_failures) + len(kaii_request_failures),
        "research_only": True, "full_backfill_authorized": False, "automatic_promotion": False,
        "ready_for_live_routing": False,
    }
