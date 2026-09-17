"""Bounded, read-only follow-up to ended-listing availability verification."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import urllib.parse
from typing import Any, Callable

from moneybot.services.alpha_atlas_v4_delisted_coverage import _request
from moneybot.services.alpha_atlas_v4_phase1_discovery import _urllib_fetch
from moneybot.services.market_data_providers import ExchangeCalendar

GAP_TICKERS = ("GSS", "SWCH", "KAII", "MGI")

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
    for group in (source.get("identity_investigation_candidates") or [])[:8]:
        ticker_evidence = []
        for ticker in (group.get("tickers") or [])[:4]:
            ticker = str(ticker); listing_rows: list[dict[str, Any]] = []
            try:
                results = _request(fetcher, api_key, _listing_query_url(ticker), provenance).get("results") or []
                listing_rows = [r for r in results if isinstance(r, dict) and str(r.get("ticker")) == ticker]
            except ValueError as exc:
                ticker_evidence.append({"ticker": ticker, "status": "LISTING_METADATA_REQUEST_FAILED",
                                        "original_failed_request": {"date": research_end, "http_status": 404,
                                                                    "source_run_id": 35176245513},
                                        "request_failure": _request_failure(exc, provenance)})
                continue
            row = listing_rows[0] if len(listing_rows) == 1 else {}
            as_of, basis = _historical_identity_date(row, research_start, research_end, calendar)
            item = {"ticker": ticker, "listing_metadata": {k: row.get(k) for k in
                    ("ticker", "name", "type", "market", "primary_exchange", "list_date", "delisted_utc",
                     "share_class_figi", "composite_figi", "cik")}, "query_date": as_of,
                    "date_selection_basis": basis,
                    "original_failed_request": {"date": research_end, "http_status": 404,
                                                "source_run_id": 35176245513}}
            if not as_of:
                ticker_evidence.append({**item, "status": "HISTORICAL_IDENTITY_DATE_UNRESOLVED",
                                        "reference_request_issued": False})
                continue
            ref_url = f"https://api.massive.com/v3/reference/tickers/{urllib.parse.quote(ticker, safe='')}?date={as_of}"
            try:
                reference = _request(fetcher, api_key, ref_url, provenance).get("results") or {}
                ticker_evidence.append({**item, "status": "DATED_REFERENCE_RETURNED" if reference else "DATED_REFERENCE_NOT_RETURNED",
                                        "reference_request_issued": True, "reference_returned": bool(reference),
                                        "identifiers": {k: reference.get(k) for k in ("share_class_figi", "composite_figi", "cik")} if isinstance(reference, dict) else {}})
            except ValueError as exc:
                ticker_evidence.append({**item, "status": "DATED_REFERENCE_REQUEST_FAILED", "reference_request_issued": True,
                                        "request_failure": _request_failure(exc, provenance)})
        identity_results.append({"identifier_type": group.get("identifier_type"),
                                 "identifier_value": group.get("identifier_value"), "tickers": group.get("tickers"),
                                 "identifier_matching_is_transition_proof": False,
                                 "dated_reference_evidence": ticker_evidence,
                                 "status": "INVESTIGATED_NOT_VERIFIED", "verified_ticker_chain": False})

    request_failures = [x for x in gap_results if x["status"] == "REQUEST_FAILED"]
    gap_ref_failures = [i for x in gap_results for i in x.get("missing_session_investigations", []) if i.get("request_failure")]
    identity_failures = [i for x in identity_results for i in x["dated_reference_evidence"] if i.get("request_failure")]
    unresolved_codes = {code for item in gap_results for code in item.get("reason_codes", [])}
    unresolved_codes |= {"EFFECTIVE_DATED_IDENTITY_CHAIN_UNVERIFIED", "UNRESOLVED_EARLIER_SNAPSHOT_UNAVAILABLE",
                         "HISTORICAL_UNIVERSE_COMPLETENESS_UNVERIFIED", "TERMINAL_VALUATION_UNVERIFIED"}
    if any(i.get("status") == "HISTORICAL_IDENTITY_DATE_UNRESOLVED" for x in identity_results for i in x["dated_reference_evidence"]):
        unresolved_codes.add("HISTORICAL_IDENTITY_DATE_UNRESOLVED")
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
        "daily_price_gap_diagnostics": gap_results, "identity_diagnostics": identity_results,
        "population_reconciliation": {"current_inactive_count": (source.get("population") or {}).get("inactive_listings"),
                                      "prior_reported_count": 6629, "status": "UNRESOLVED_EARLIER_SNAPSHOT_UNAVAILABLE"},
        "universe_completeness": {"current_query_pagination_complete": (source.get("pagination") or {}).get("complete"),
                                  "status": "NOT_ESTABLISHED_FOR_HISTORICAL_INTERVAL",
                                  "reason": "A complete current active=false query is not a point-in-time universe."},
        "terminal_valuation": {"status": "NOT_ESTABLISHED", "zero_recovery_assumed": False,
                               "forward_fill_used": False, "securities_substituted": False},
        "sanitized_request_provenance": provenance,
        "status": "BLOCKED" if request_failures or gap_ref_failures or identity_failures else "VERIFIED_DIAGNOSTIC_EXECUTION",
        "unresolved_reason_codes": sorted(unresolved_codes),
        "request_failure_count": len(request_failures) + len(gap_ref_failures) + len(identity_failures),
        "research_only": True, "full_backfill_authorized": False, "automatic_promotion": False,
        "ready_for_live_routing": False,
    }
