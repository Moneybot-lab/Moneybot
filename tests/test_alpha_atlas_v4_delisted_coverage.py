from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from moneybot.services.alpha_atlas_v4_delisted_coverage import DiscoveryResponse, verify_delisted_availability


def _response(value, status=200):
    return DiscoveryResponse(status, json.dumps(value).encode(), {})


def _fetcher(records, *, missing=(), forbidden=()):
    calls = []
    def fetch(_method, url, headers, _timeout):
        calls.append((url, headers))
        if "reference/tickers?" in url:
            return _response({"results": records})
        if "/v3/reference/tickers/" in url:
            ticker = url.split("/tickers/")[1].split("?")[0]
            return _response({"results": {"ticker": ticker}})
        ticker = url.split("/ticker/")[1].split("/")[0]
        if ticker in forbidden: return _response({"error": "forbidden"}, 403)
        return _response({"results": [] if ticker in missing else [{"t": 1}, {"t": 2}]})
    return fetch, calls


def _run(records, **kwargs):
    fetch, calls = _fetcher(records, missing=kwargs.pop("missing", ()), forbidden=kwargs.pop("forbidden", ()))
    report = verify_delisted_availability(
        api_key="secret", repository_commit="a" * 40, generated_at="2026-09-16T00:00:00Z",
        research_start="2018-01-01", research_end="2026-09-15",
        max_pages=2, max_probes=kwargs.pop("max_probes", 4), fetcher=fetch,
        sleeper=lambda _: None, **kwargs,
    )
    return report, calls


def _records():
    return [
        {"ticker": "AGU", "active": False, "delisted_utc": "2018-01-02T05:00:00Z", "list_date": "2002-01-01", "cik": "10"},
        {"ticker": "HLS", "active": False, "delisted_utc": "2018-01-02T05:00:00Z", "list_date": "1988-01-01", "cik": "11"},
        {"ticker": "MID", "active": False, "delisted_utc": "2021-06-01", "share_class_figi": "SC1"},
        {"ticker": "LATE", "active": False, "delisted_utc": "2025-03-01", "composite_figi": "CF1"},
        {"ticker": "INACTIVE", "active": False},
    ]


def test_agu_hls_boundary_uses_bounded_prior_sessions_and_labels_scope():
    report, calls = _run(_records())
    followups = {row["ticker"]: row for row in report["follow_up_probes"]}
    assert set(followups) == {"AGU", "HLS"}
    for ticker in ("AGU", "HLS"):
        row = followups[ticker]
        assert row["price_window"]["scope"] == "BOUNDED_PRE_RESEARCH_START_AVAILABILITY_ONLY"
        assert row["price_window"]["eligible_sessions"] > 0
        assert row["price_window"]["to"] == "2017-12-29"
        assert row["availability_verified"] is True
        assert row["in_research_interval_verified"] is False
    assert not any("2018-01-01/2018-01-01" in url for url, _ in calls)


def test_no_eligible_sessions_is_distinct_from_missing_provider_data():
    records = [{"ticker": "NEW", "active": False, "delisted_utc": "2018-01-02", "list_date": "2018-01-01"}, *_records()[2:]]
    report, _ = _run(records, max_probes=3)
    new = next(row for row in report["probes"] if row["ticker"] == "NEW")
    assert new["outcome"] == "NO_ELIGIBLE_SESSIONS"
    assert new["reason_code"] == "NO_ELIGIBLE_SESSIONS"
    assert new["price_window"]["eligible_sessions"] == 0


def test_genuine_missing_data_on_eligible_sessions_remains_failure():
    report, _ = _run(_records(), missing={"MID"})
    mid = next(row for row in report["probes"] if row["ticker"] == "MID")
    assert mid["price_window"]["eligible_sessions"] > 0
    assert mid["outcome"] == "NO_PRICE_DATA_RETURNED"
    assert report["status"] == "BLOCKED"
    assert any(reason["ticker"] == "MID" for reason in report["failure_reasons"])


def test_temporal_strata_survive_excess_identity_candidates():
    records = []
    for year in range(2018, 2026):
        records.append({"ticker": f"T{year}", "active": False, "delisted_utc": f"{year}-06-15", "cik": "SAME_ISSUER"})
    # CIK reuse is only an investigation candidate and may not consume the budget.
    report, _ = _run(records, max_probes=4)
    years = {date[:4] for date in report["probe_selection"]["selected_delisting_dates"]}
    assert len(years) == 4
    assert report["probe_selection"]["identity_candidate_budget"] == 1
    assert report["probe_selection"]["unrepresented_strata"] == []


def test_identity_types_are_preserved_and_cik_is_not_security_chain_proof():
    records = [
        {"ticker": "COMMON", "active": False, "delisted_utc": "2020-01-02", "cik": "ISSUER"},
        {"ticker": "WARRANT", "active": False, "delisted_utc": "2022-01-02", "cik": "ISSUER"},
        {"ticker": "UNIT", "active": False, "delisted_utc": "2024-01-02", "cik": "ISSUER"},
    ]
    report, _ = _run(records, max_probes=3)
    candidate = report["identity_investigation_candidates"][0]
    assert candidate["identifier_type"] == "cik"
    assert candidate["verified_ticker_chain"] is False
    assert all(row["identity_identifier"]["type"] == "cik" for row in report["probes"])
    assert report["conclusions"]["confirmed_company_or_security_termination"] == "NOT_ESTABLISHED_BY_DELISTED_UTC"


def test_blocked_result_has_actionable_non_null_reasons_and_count_discrepancy():
    report, _ = _run(_records(), missing={"MID"})
    assert report["status"] == "BLOCKED"
    assert report["status_explanation"]
    assert all(reason.get("code") and reason.get("explanation") for reason in report["failure_reasons"])
    assert report["population"]["inactive_listings"] == 5
    assert report["population"]["count_reconciliation"] == "UNRESOLVED_EARLIER_SNAPSHOT_UNAVAILABLE"
    assert any(reason["code"] == "PRIOR_INACTIVE_COUNT_RECONCILIATION_UNRESOLVED" for reason in report["unresolved_findings"])
    assert report["prior_run_evidence"]["workflow_run_id"] == 35123217191


def test_exact_prior_report_can_be_preserved_without_reclassification():
    prior = {"status": "BLOCKED", "probes": [{"ticker": f"P{i}"} for i in range(12)]}
    fetch, _ = _fetcher(_records())
    report = verify_delisted_availability(
        api_key="secret", repository_commit="a" * 40, generated_at="now",
        research_start="2018-01-01", research_end="2026-09-15",
        max_pages=2, max_probes=4, fetcher=fetch, sleeper=lambda _: None,
        prior_run_evidence=prior,
    )
    assert report["prior_run_evidence"] == prior


def test_incomplete_pagination_fails_closed_without_running_probes():
    def fetch(*_args):
        return _response({"results": _records(), "next_url": "https://api.massive.com/next"})
    report = verify_delisted_availability(
        api_key="secret", repository_commit="a" * 40, generated_at="now",
        research_start="2018-01-01", research_end="2026-09-15",
        max_pages=1, max_probes=3, fetcher=fetch, sleeper=lambda _: None,
    )
    assert report["status"] == "BLOCKED"
    assert report["probes"] == []
    assert report["failure_reasons"][0]["code"] == "PAGINATION_INCOMPLETE"


def test_workflow_is_manual_bounded_read_only_and_always_uploads():
    text = Path(".github/workflows/alpha-atlas-v4-delisted-coverage-verification.yml").read_text()
    assert text.startswith("name: Alpha Atlas V4 Delisted Coverage Verification\n")
    assert "workflow_dispatch:" in text and "schedule:" not in text and "push:" not in text
    assert text.count("if: always()") == 2
    assert "MASSIVE_API_KEY: ${{ secrets.MASSIVE_API_KEY }}" in text
    assert "35123217191" in text
    assert "fd517cf5b752df85ed981d980ac6f832be64c904" in text
    assert "--prior-report" in text
    assert "continue-on-error" not in text
    for forbidden in ("train_challenger", "backtest", "promote", "deploy", "ingest_massive"):
        assert forbidden not in text.lower()


def test_cli_blocked_output_has_reason_code_and_nonzero_exit(tmp_path):
    env = dict(os.environ)
    env.pop("MASSIVE_API_KEY", None)
    completed = subprocess.run(
        [sys.executable, "scripts/verify_alpha_atlas_v4_delisted_coverage.py", "--output-dir", str(tmp_path)],
        text=True, capture_output=True, env=env,
    )
    assert completed.returncode == 2
    console = json.loads(completed.stdout)
    report = json.loads((tmp_path / "alpha_atlas_v4_delisted_coverage_verification.json").read_text())
    assert console["reason_codes"] == ["DELISTED_DISCOVERY_CREDENTIAL_REQUIRED"]
    assert report["status_explanation"] == "DELISTED_DISCOVERY_CREDENTIAL_REQUIRED"
