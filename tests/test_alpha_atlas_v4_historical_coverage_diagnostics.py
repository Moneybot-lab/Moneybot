from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from moneybot.services.alpha_atlas_v4_historical_coverage_diagnostics import (
    IDENTITY_LOOKBACK_SESSIONS, _historical_identity_date, _identity_candidate_dates,
    diagnose_historical_coverage,
)
from moneybot.services.market_data_providers import ExchangeCalendar
from moneybot.services.alpha_atlas_v4_phase1_discovery import DiscoveryResponse
from scripts.verify_alpha_atlas_v4_delisted_coverage import derived_probe_summary, _markdown


def _ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000)


def test_corrected_summary_separates_representative_followups_and_distinct_tickers():
    report = {
        "probes": [{"ticker": ticker, "availability_verified": True} for ticker in ("AGU", "HLS", "GSS", "SWCH")],
        "follow_up_probes": [{"ticker": "AGU", "availability_verified": True}, {"ticker": "HLS", "availability_verified": True}],
    }
    summary = derived_probe_summary(report)
    assert summary["representative_probes"] == {"verified": 4, "total": 4}
    assert summary["prior_run_follow_ups"] == {"verified": 2, "total": 2}
    assert summary["distinct_tickers"] == 4
    markdown = _markdown(report)
    assert "Representative probes: `4` / `4` verified" in markdown
    assert "Prior-run follow-ups: `2` / `2` verified" in markdown
    assert "Distinct tickers: `4`" in markdown


def test_corrected_summary_handles_failures_and_variable_counts():
    summary = derived_probe_summary({"probes": [{"ticker": "A", "availability_verified": False}], "follow_up_probes": []})
    assert summary["representative_probes"] == {"verified": 0, "total": 1}
    assert summary["prior_run_follow_ups"] == {"verified": 0, "total": 0}
    assert summary["distinct_tickers"] == 1


def test_gap_diagnostic_reports_exact_missing_duplicate_and_out_of_window_dates():
    probes = [{"ticker": ticker, "price_window": {"from": "2026-01-02", "to": "2026-01-08"}} for ticker in ("GSS", "SWCH", "KAII", "MGI")]
    source = {"status": "VERIFIED", "probes": probes, "identity_investigation_candidates": [],
              "population": {"inactive_listings": 6607}, "pagination": {"complete": True},
              "research_interval": {"end": "2026-09-15"}}
    def fetch(_method, url, _headers, _timeout):
        if "/v2/aggs/" in url:
            ticker = url.split("/ticker/")[1].split("/")[0]
            dates = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
            if ticker == "GSS": dates.remove("2026-01-06")
            if ticker == "SWCH": dates.append("2026-01-05")
            if ticker == "KAII": dates.append("2026-01-09")
            return DiscoveryResponse(200, json.dumps({"results": [{"t": _ms(day)} for day in dates]}).encode(), {})
        ticker = url.split("/tickers/")[1].split("?")[0]
        return DiscoveryResponse(200, json.dumps({"results": {"ticker": ticker}}).encode(), {})
    report = diagnose_historical_coverage(source, api_key="secret", repository_commit="a" * 40,
                                           generated_at="now", fetcher=fetch)
    by_ticker = {item["ticker"]: item for item in report["daily_price_gap_diagnostics"]}
    assert by_ticker["GSS"]["missing_sessions"] == ["2026-01-06"]
    assert by_ticker["GSS"]["missing_session_investigations"][0]["classification"] == "REFERENCE_PRESENT_NO_EVENT_EVIDENCE"
    assert by_ticker["SWCH"]["duplicate_sessions"] == ["2026-01-05"]
    assert by_ticker["KAII"]["out_of_window_sessions"] == ["2026-01-09"]
    assert by_ticker["MGI"]["status"] == "COMPLETE"
    assert report["terminal_valuation"]["zero_recovery_assumed"] is False
    assert report["population_reconciliation"]["status"] == "UNRESOLVED_EARLIER_SNAPSHOT_UNAVAILABLE"


def test_identity_diagnostic_retains_typed_identifier_without_claiming_chain():
    source = {"status": "VERIFIED", "probes": [{"ticker": ticker, "price_window": {"from": "2026-01-02", "to": "2026-01-08"}} for ticker in ("GSS", "SWCH", "KAII", "MGI")],
              "identity_investigation_candidates": [{"identifier_type": "cik", "identifier_value": "1", "tickers": ["OLD", "NEW"], "verified_ticker_chain": False}],
              "population": {"inactive_listings": 6607}, "pagination": {"complete": True}, "research_interval": {"end": "2026-09-15"}}
    def fetch(_method, url, _headers, _timeout):
        if "/v2/aggs/" in url:
            return DiscoveryResponse(200, json.dumps({"results": [{"t": _ms(day)} for day in ("2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08")]}).encode(), {})
        if "/reference/tickers?" in url:
            ticker = url.split("ticker=")[1].split("&")[0]
            return DiscoveryResponse(200, json.dumps({"results": [{"ticker": ticker, "cik": "1", "share_class_figi": f"CLASS-{ticker}", "type": "CS", "list_date": "2020-01-02", "delisted_utc": "2025-01-04"}]}).encode(), {})
        ticker = url.split("/tickers/")[1].split("?")[0]
        return DiscoveryResponse(200, json.dumps({"results": {"ticker": ticker, "cik": "1"}}).encode(), {})
    report = diagnose_historical_coverage(source, api_key="secret", repository_commit="a" * 40, generated_at="now", fetcher=fetch)
    identity = report["identity_diagnostics"][0]
    assert identity["identifier_type"] == "cik"
    assert identity["identifier_matching_is_transition_proof"] is False
    assert identity["verified_ticker_chain"] is False
    assert identity["status"] == "INVESTIGATED_NOT_VERIFIED"
    assert all(row["query_date"] == "2025-01-03" for row in identity["dated_reference_evidence"])
    assert all(row["query_date"] != "2026-09-15" for row in identity["dated_reference_evidence"])
    assert {row["listing_metadata"]["share_class_figi"] for row in identity["dated_reference_evidence"]} == {"CLASS-OLD", "CLASS-NEW"}


def test_historical_identity_date_uses_listing_interval_and_unresolved_metadata():
    calendar = ExchangeCalendar()
    selected, basis = _historical_identity_date(
        {"list_date": "2019-01-01", "delisted_utc": "2022-01-29"},
        "2018-01-01", "2026-09-15", calendar,
    )
    assert selected == "2022-01-28"
    assert basis == "LAST_EXCHANGE_SESSION_BEFORE_PROVIDER_LISTING_END"
    assert _historical_identity_date(
        {"ticker": "OLD"}, "2018-01-01", "2026-09-15", calendar,
    ) == (None, "HISTORICAL_IDENTITY_DATE_UNRESOLVED")


def test_identity_without_dates_does_not_issue_unsupported_reference_request():
    source = {"status": "VERIFIED", "probes": [],
              "identity_investigation_candidates": [{"identifier_type": "cik", "identifier_value": "1", "tickers": ["OLD"]}],
              "population": {"inactive_listings": 6607}, "pagination": {"complete": True},
              "research_interval": {"start": "2018-01-01", "end": "2026-09-15"}}
    urls = []
    def fetch(_method, url, _headers, _timeout):
        urls.append(url)
        return DiscoveryResponse(200, json.dumps({"results": [{"ticker": "OLD"}]}).encode(), {})
    report = diagnose_historical_coverage(source, api_key="secret", repository_commit="a" * 40,
                                           generated_at="now", fetcher=fetch)
    finding = report["identity_diagnostics"][0]["dated_reference_evidence"][0]
    assert finding["status"] == "HISTORICAL_IDENTITY_END_DATE_UNRESOLVED"
    assert finding["reference_request_issued"] is False
    assert not any("/reference/tickers/OLD?date=" in url for url in urls)


def test_missing_list_date_uses_utc_ended_timestamp_and_bounded_sessions():
    dates, basis = _identity_candidate_dates(
        {"list_date": None, "delisted_utc": "2018-08-01T01:00:00+02:00"},
        "2018-01-01", "2026-09-15", ExchangeCalendar(),
    )
    # The timestamp is still July 31 in UTC, so the first preceding Nasdaq/NYSE
    # session is July 30 rather than July 31.
    assert dates[0] == "2018-07-30"
    assert len(dates) == IDENTITY_LOOKBACK_SESSIONS
    assert basis == "ENDED_TIMESTAMP_BOUNDED_PRECEDING_SESSIONS_WITHOUT_LIST_DATE"


def test_missing_list_date_executes_reference_and_preserves_bounded_failures():
    source = {"status": "VERIFIED", "probes": [],
              "identity_investigation_candidates": [{"identifier_type": "cik", "identifier_value": "1", "tickers": ["OLD"]}],
              "population": {"inactive_listings": 6607}, "pagination": {"complete": True},
              "research_interval": {"start": "2018-01-01", "end": "2026-09-15"}}
    def fetch(_method, url, _headers, _timeout):
        if "/reference/tickers?" in url:
            return DiscoveryResponse(200, json.dumps({"results": [{"ticker": "OLD", "list_date": None,
                "delisted_utc": "2020-01-10T00:00:00Z"}]}).encode(), {})
        ticker = url.split("/tickers/")[1].split("?")[0]
        if ticker == "OLD":
            return DiscoveryResponse(200, b'{"results":{}}', {})
        return DiscoveryResponse(200, json.dumps({"results": {"ticker": ticker}}).encode(), {})
    report = diagnose_historical_coverage(source, api_key="secret", repository_commit="a" * 40,
                                           generated_at="now", fetcher=fetch)
    finding = report["identity_diagnostics"][0]["dated_reference_evidence"][0]
    assert finding["reference_request_issued"] is True
    assert finding["status"] == "HISTORICAL_IDENTITY_REFERENCE_UNRESOLVED"
    assert len(finding["reference_attempts"]) == IDENTITY_LOOKBACK_SESSIONS
    assert all(attempt["request_succeeded"] and not attempt["exact_ticker_returned"]
               for attempt in finding["reference_attempts"])


def test_class_specific_transitions_and_conflicting_type_metadata_stay_separate():
    source = {"status": "VERIFIED", "probes": [],
              "identity_investigation_candidates": [{"identifier_type": "cik", "identifier_value": "2",
                                                       "tickers": ["FITBM", "FITBO"]}],
              "population": {"inactive_listings": 6607}, "pagination": {"complete": True},
              "research_interval": {"start": "2018-01-01", "end": "2026-09-15"}}
    def fetch(_method, url, _headers, _timeout):
        if "/reference/tickers?" in url:
            ticker = url.split("ticker=")[1].split("&")[0]
            return DiscoveryResponse(200, json.dumps({"results": [{"ticker": ticker, "type": "CS",
                "name": "Depositary shares representing preferred stock", "list_date": None,
                "delisted_utc": "2020-01-10T00:00:00Z"}]}).encode(), {})
        ticker = url.split("/tickers/")[1].split("?")[0]
        return DiscoveryResponse(200, json.dumps({"results": {"ticker": ticker, "type": "CS",
            "share_class_figi": f"CLASS-{ticker}", "cik": "shared"}}).encode(), {})
    report = diagnose_historical_coverage(source, api_key="secret", repository_commit="a" * 40,
                                           generated_at="now", fetcher=fetch)
    records = report["identity_diagnostics"][0]["dated_reference_evidence"]
    assert {row["ticker"] for row in records} == {"FITBM", "FITBO"}
    assert all(row["security_type_investigation"]["status"] == "CONFLICTING_SECURITY_TYPE_METADATA" for row in records)
    transitions = {(row["old_ticker"], row["new_ticker"]): row for row in report["transition_diagnostics"]}
    assert transitions[("BWINA", "PTVCA")]["security_class"] == "Class A common stock"
    assert transitions[("BWINB", "PTVCB")]["security_class"] == "Class B common stock"
    assert transitions[("KAII", "QDRO")]["security_class"] == "Class A ordinary shares"
    assert transitions[("KAIIU", "QDROU")]["security_class"] == "units"
    assert transitions[("KAIIW", "QDROW")]["security_class"] == "redeemable warrants"
    assert all(row["result"] == "VERIFIED" and not row["shared_cik_alone_used"] for row in transitions.values())


def test_exact_gap_evidence_keeps_price_trading_and_valuation_separate():
    windows = {"GSS": ("2022-01-27", "2022-01-28"), "SWCH": ("2022-12-05", "2022-12-06"),
               "KAII": ("2023-01-19", "2023-02-24"), "MGI": ("2023-05-31", "2023-06-01")}
    probes = [{"ticker": ticker, "price_window": {"from": start, "to": end}} for ticker, (start, end) in windows.items()]
    source = {"status": "VERIFIED", "probes": probes, "identity_investigation_candidates": [],
              "population": {"inactive_listings": 6607}, "pagination": {"complete": True},
              "research_interval": {"start": "2018-01-01", "end": "2026-09-15"}}
    wanted = {"GSS": {"2022-01-28"}, "SWCH": {"2022-12-06"},
              "KAII": {"2023-01-19", "2023-02-17", "2023-02-24"}, "MGI": {"2023-06-01"}}
    def fetch(_method, url, _headers, _timeout):
        if "/v2/aggs/" in url:
            ticker = url.split("/ticker/")[1].split("/")[0]
            start, end = windows[ticker]; current = datetime.fromisoformat(start).date(); finish = datetime.fromisoformat(end).date()
            dates = []
            while current <= finish:
                day = current.isoformat()
                if ExchangeCalendar().is_trading_day(current) and day not in wanted[ticker]: dates.append(day)
                current = current.fromordinal(current.toordinal() + 1)
            return DiscoveryResponse(200, json.dumps({"results": [{"t": _ms(day)} for day in dates]}).encode(), {})
        ticker = url.split("/tickers/")[1].split("?")[0]
        return DiscoveryResponse(200, json.dumps({"results": {"ticker": ticker}}).encode(), {})
    report = diagnose_historical_coverage(source, api_key="secret", repository_commit="a" * 40, generated_at="now", fetcher=fetch)
    findings = {(row["ticker"], x["session"]): x for row in report["daily_price_gap_diagnostics"] for x in row["missing_session_investigations"]}
    assert set(findings) == {(ticker, day) for ticker, days in wanted.items() for day in days}
    assert findings[("SWCH", "2022-12-06")]["classification"] == "EXPLAINED_NONTRADING_MERGER_OPEN_HALT"
    assert findings[("KAII", "2023-02-17")]["classification"] == "TRADING_ELIGIBLE_GAP_UNRESOLVED"
    assert all(item["price_retrieved"] is False and item["terminal_value_inferred"] is False for item in findings.values())


def test_targeted_404_is_preserved_with_url_and_does_not_erase_other_cases():
    source = {"status": "VERIFIED", "probes": [{"ticker": ticker, "price_window": {"from": "2026-01-02", "to": "2026-01-08"}} for ticker in ("GSS", "SWCH", "KAII", "MGI")],
              "identity_investigation_candidates": [], "population": {"inactive_listings": 6607},
              "pagination": {"complete": True}, "research_interval": {"end": "2026-09-15"}}
    def fetch(_method, url, _headers, _timeout):
        if "/ticker/GSS/" in url:
            return DiscoveryResponse(404, b'{"error":"not found"}', {})
        return DiscoveryResponse(200, json.dumps({"results": [{"t": _ms(day)} for day in ("2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08")]}).encode(), {})
    report = diagnose_historical_coverage(source, api_key="secret", repository_commit="a" * 40, generated_at="now", fetcher=fetch)
    by_ticker = {item["ticker"]: item for item in report["daily_price_gap_diagnostics"]}
    assert report["status"] == "BLOCKED"
    assert report["request_failure_count"] == 1
    assert by_ticker["GSS"]["status"] == "REQUEST_FAILED"
    failure = by_ticker["GSS"]["request_failure"]
    assert failure["http_status"] == 404
    assert failure["request_url"].startswith("https://api.massive.com/v2/aggs/ticker/GSS/")
    assert failure["response_sha256"]
    assert by_ticker["SWCH"]["status"] == "COMPLETE"
    assert len(report["sanitized_request_provenance"]) == 14


def test_diagnostic_workflow_is_exact_bounded_manual_and_always_uploads():
    text = Path(".github/workflows/alpha-atlas-v4-historical-coverage-diagnostics.yml").read_text()
    assert text.startswith("name: Alpha Atlas V4 Historical Coverage Diagnostics\n")
    assert "workflow_dispatch:" in text and "schedule:" not in text and "push:" not in text
    assert "35125664186" in text and "205529d612c1ac2a3497a07f5cb6151d2eef62f4" in text
    assert "35174216390" in text and "b736f2cb387525175eb57141f0170e2c919cb6c6" in text
    assert "--derive-summary-only" in text
    assert text.count("if: always()") == 7
    assert "Publish diagnostic JSON as literal text" in text
    assert "35178703375" in text and "0dc495973f7b2bb2892c6e78ae0d933d475fe80d" in Path("scripts/inspect_historical_coverage_evidence.py").read_text()
    assert "78ee7d3afe8434ff952b46b2899d529c37d4864cfe3f30ada61467bd48fb7081" in text
    assert "35176245513" in text and "79b51d02e454dac6dae1c3660d7ddd8761bbab79" in text
    assert "896a61604ae6fc8ba16821c0bf4d609b0e48efe275bd066318acc243351d0d7d" in text
    for forbidden in ("train_challenger", "backtest", "promote", "deploy", "ingest_massive"):
        assert forbidden not in text.lower()
