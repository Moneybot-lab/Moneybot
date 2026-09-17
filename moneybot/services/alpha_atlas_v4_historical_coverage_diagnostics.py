"""Targeted read-only diagnostics after ended-listing availability verification."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable
import urllib.parse

from moneybot.services.alpha_atlas_v4_delisted_coverage import _request
from moneybot.services.alpha_atlas_v4_phase1_discovery import _urllib_fetch
from moneybot.services.market_data_providers import ExchangeCalendar

GAP_TICKERS = ("GSS", "SWCH", "KAII", "MGI")


def _date_from_ms(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(float(value) / 1000, timezone.utc).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


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
            gap_results.append({"ticker": ticker, "status": "SOURCE_PROBE_MISSING", "reason_code": "SOURCE_PROBE_MISSING"})
            continue
        window = original.get("price_window") or {}
        start, end = window.get("from"), window.get("to")
        url = f"https://api.massive.com/v2/aggs/ticker/{urllib.parse.quote(ticker, safe='')}/range/1/day/{start}/{end}?adjusted=true&sort=asc&limit=50000"
        bars = _request(fetcher, api_key, url, provenance).get("results") or []
        observed = [_date_from_ms(row.get("t")) for row in bars]
        valid_observed = [value for value in observed if value]
        expected = []
        current = datetime.fromisoformat(start).date()
        finish = datetime.fromisoformat(end).date()
        while current <= finish:
            if calendar.is_trading_day(current): expected.append(current.isoformat())
            current = current.fromordinal(current.toordinal() + 1)
        missing = sorted(set(expected) - set(valid_observed))
        duplicates = sorted({value for value in valid_observed if valid_observed.count(value) > 1})
        outside = sorted(value for value in valid_observed if value not in set(expected))
        investigations = []
        for session in missing:
            ref_url = f"https://api.massive.com/v3/reference/tickers/{urllib.parse.quote(ticker, safe='')}?date={session}"
            reference = _request(fetcher, api_key, ref_url, provenance).get("results") or {}
            present = isinstance(reference, dict) and str(reference.get("ticker") or "") == ticker
            investigations.append({
                "session": session,
                "reference_present": present,
                "classification": "REFERENCE_PRESENT_NO_EVENT_EVIDENCE" if present else "REFERENCE_NOT_RETURNED",
                "terminal_value_inferred": False,
                "evidence_needed": "dated exchange-status/corporate-action evidence explaining the absent bar",
            })
        gap_results.append({
            "ticker": ticker, "requested_window": {"from": start, "to": end},
            "expected_eligible_sessions": len(expected), "bars_returned": len(bars),
            "missing_sessions": missing, "duplicate_sessions": duplicates,
            "out_of_window_sessions": outside, "missing_session_investigations": investigations,
            "status": "COMPLETE" if not missing and not duplicates and not outside else "UNRESOLVED_GAPS",
            "reason_codes": (["MISSING_DAILY_PRICE_EVIDENCE"] if missing else [])
                            + (["DUPLICATE_DAILY_PRICE_EVIDENCE"] if duplicates else [])
                            + (["OUT_OF_WINDOW_PRICE_EVIDENCE"] if outside else []),
        })
    identity_results = []
    for group in (source.get("identity_investigation_candidates") or [])[:8]:
        ticker_evidence = []
        for ticker in (group.get("tickers") or [])[:4]:
            probe = probes.get(str(ticker)) or {}
            as_of = ((probe.get("price_window") or {}).get("to")
                     or (source.get("research_interval") or {}).get("end"))
            ref_url = f"https://api.massive.com/v3/reference/tickers/{urllib.parse.quote(str(ticker), safe='')}?date={as_of}"
            reference = _request(fetcher, api_key, ref_url, provenance).get("results") or {}
            ticker_evidence.append({"ticker": ticker, "as_of": as_of,
                                    "reference_returned": isinstance(reference, dict) and bool(reference),
                                    "share_class_figi": reference.get("share_class_figi") if isinstance(reference, dict) else None,
                                    "composite_figi": reference.get("composite_figi") if isinstance(reference, dict) else None,
                                    "cik": reference.get("cik") if isinstance(reference, dict) else None})
        identity_results.append({**group, "dated_reference_evidence": ticker_evidence,
                                 "status": "AMBIGUOUS_NO_EFFECTIVE_DATED_EVENT_EVIDENCE",
                                 "verified_ticker_chain": False})
    unresolved = [item for item in gap_results if item["status"] != "COMPLETE"]
    return {
        "schema_version": "alpha-atlas-v4-historical-coverage-diagnostics.v1",
        "generated_at_utc": generated_at, "repository_commit": repository_commit,
        "source": {"workflow": "Alpha Atlas V4 Delisted Coverage Verification",
                   "run_id": 35125664186, "run_attempt": 1,
                   "head_sha": "205529d612c1ac2a3497a07f5cb6151d2eef62f4"},
        "daily_price_gap_diagnostics": gap_results,
        "identity_diagnostics": identity_results,
        "population_reconciliation": {"current_inactive_count": (source.get("population") or {}).get("inactive_listings"),
                                      "prior_reported_count": 6629,
                                      "status": "UNRESOLVED_EARLIER_SNAPSHOT_UNAVAILABLE"},
        "universe_completeness": {"current_query_pagination_complete": (source.get("pagination") or {}).get("complete"),
                                  "status": "NOT_ESTABLISHED_FOR_HISTORICAL_INTERVAL",
                                  "reason": "A complete current active=false query is not a point-in-time universe for every date in the research interval."},
        "terminal_valuation": {"status": "NOT_ESTABLISHED", "zero_recovery_assumed": False,
                               "forward_fill_used": False, "securities_substituted": False},
        "sanitized_request_provenance": provenance,
        "status": "VERIFIED_DIAGNOSTIC_EXECUTION" if not any(item["status"] == "SOURCE_PROBE_MISSING" for item in gap_results) else "BLOCKED",
        "unresolved_reason_codes": sorted({code for item in unresolved for code in item.get("reason_codes", [])}
                                          | {"EFFECTIVE_DATED_IDENTITY_CHAIN_UNVERIFIED",
                                             "UNRESOLVED_EARLIER_SNAPSHOT_UNAVAILABLE",
                                             "HISTORICAL_UNIVERSE_COMPLETENESS_UNVERIFIED",
                                             "TERMINAL_VALUATION_UNVERIFIED"}),
        "research_only": True, "full_backfill_authorized": False,
        "automatic_promotion": False, "ready_for_live_routing": False,
    }
