from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from moneybot.services.alpha_atlas_v4_historical_coverage_diagnostics import diagnose_historical_coverage
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
        ticker = url.split("/tickers/")[1].split("?")[0]
        return DiscoveryResponse(200, json.dumps({"results": {"ticker": ticker, "cik": "1"}}).encode(), {})
    report = diagnose_historical_coverage(source, api_key="secret", repository_commit="a" * 40, generated_at="now", fetcher=fetch)
    identity = report["identity_diagnostics"][0]
    assert identity["identifier_type"] == "cik"
    assert identity["verified_ticker_chain"] is False
    assert identity["status"] == "AMBIGUOUS_NO_EFFECTIVE_DATED_EVENT_EVIDENCE"


def test_diagnostic_workflow_is_exact_bounded_manual_and_always_uploads():
    text = Path(".github/workflows/alpha-atlas-v4-historical-coverage-diagnostics.yml").read_text()
    assert text.startswith("name: Alpha Atlas V4 Historical Coverage Diagnostics\n")
    assert "workflow_dispatch:" in text and "schedule:" not in text and "push:" not in text
    assert "35125664186" in text and "205529d612c1ac2a3497a07f5cb6151d2eef62f4" in text
    assert "--derive-summary-only" in text
    assert text.count("if: always()") == 2
    for forbidden in ("train_challenger", "backtest", "promote", "deploy", "ingest_massive"):
        assert forbidden not in text.lower()
