from __future__ import annotations

import json
from pathlib import Path

from moneybot.services.alpha_atlas_v4_delisted_coverage import (
    DiscoveryResponse,
    verify_delisted_availability,
)


def _response(value, status=200):
    return DiscoveryResponse(status, json.dumps(value).encode(), {})


def test_complete_pagination_classifies_inactive_and_probes_confirmed_delistings():
    calls = []
    first = [
        {"ticker": "OLD1", "active": False, "delisted_utc": "2019-02-01", "composite_figi": "ID1"},
        {"ticker": "INACTIVE", "active": False},
    ]
    second = [
        {"ticker": "OLD2", "active": False, "delisted_utc": "2021-06-01", "composite_figi": "ID2"},
        {"ticker": "OLD3", "active": False, "delisted_utc": "2025-03-01", "composite_figi": "ID3"},
    ]

    def fetch(_method, url, headers, _timeout):
        calls.append((url, headers))
        if "reference/tickers?" in url and "page=2" not in url:
            return _response({"results": first, "next_url": "https://api.massive.com/v3/reference/tickers?page=2&apiKey=secret"})
        if "page=2" in url:
            return _response({"results": second})
        if "/v3/reference/tickers/" in url:
            ticker = url.split("/tickers/")[1].split("?")[0]
            return _response({"results": {"ticker": ticker}})
        return _response({"results": [{"t": 1}, {"t": 2}]})

    report = verify_delisted_availability(
        api_key="secret", repository_commit="a" * 40,
        generated_at="2026-09-16T00:00:00+00:00",
        research_start="2018-01-01", research_end="2026-09-15",
        max_pages=2, max_probes=3, fetcher=fetch, sleeper=lambda _: None,
    )
    assert report["status"] == "VERIFIED"
    assert report["pagination"]["complete"] is True
    assert report["population"] == {
        "inactive_listings": 4, "confirmed_delisted": 3,
        "confirmed_delisted_in_research_interval": 3, "other_inactive": 1,
        "missing_or_unknown_status": 0, "possible_renamed_identity_groups": 0,
        "previously_reported_inactive_count": 6629,
        "previous_count_matches": False,
    }
    assert report["outcome_counts"]["historical_data_retrieved"] == 3
    assert all(item["outcome"] == "HISTORICAL_DATA_RETRIEVED" for item in report["probes"])
    assert report["conclusions"]["complete_historical_universe"] == "NOT_ESTABLISHED_BY_BOUNDED_PROBES"
    assert report["conclusions"]["effective_dated_identity_and_ticker_history"] == "NOT_ESTABLISHED"
    assert report["conclusions"]["terminal_price_and_delisting_treatment"] == "NOT_ESTABLISHED"
    assert "secret" not in json.dumps(report)
    assert all(headers == {"Authorization": "Bearer secret"} for _, headers in calls)


def test_incomplete_pagination_fails_closed_without_misclassifying_inactive():
    def fetch(*_args):
        return _response({"results": [{"ticker": "OLD", "active": False, "delisted_utc": "2020-01-01"}], "next_url": "https://api.massive.com/next"})

    report = verify_delisted_availability(
        api_key="secret", repository_commit="a" * 40, generated_at="now",
        research_start="2018-01-01", research_end="2026-09-15",
        max_pages=1, max_probes=3, fetcher=fetch, sleeper=lambda _: None,
    )
    assert report["status"] == "BLOCKED"
    assert report["pagination"]["complete"] is False
    assert report["acceptance_criteria"]["at_least_three_reproducible_probes"] is False


def test_no_bars_and_entitlement_failures_remain_explicit():
    records = [
        {"ticker": ticker, "active": False, "delisted_utc": f"202{i}-01-01"}
        for i, ticker in enumerate(("A", "B", "C"))
    ]

    def fetch(_method, url, _headers, _timeout):
        if "reference/tickers?" in url: return _response({"results": records})
        if "/v3/reference/tickers/" in url: return _response({"results": {"ticker": url.split("/tickers/")[1].split("?")[0]}})
        if "/B/" in url: return _response({"error": "forbidden"}, status=403)
        return _response({"results": []})

    report = verify_delisted_availability(
        api_key="secret", repository_commit="a" * 40, generated_at="now",
        research_start="2018-01-01", research_end="2026-09-15",
        max_probes=3, fetcher=fetch, sleeper=lambda _: None,
    )
    assert report["status"] == "BLOCKED"
    assert report["outcome_counts"]["no_data_returned"] == 2
    assert report["outcome_counts"]["access_or_entitlement_denied"] == 1


def test_workflow_is_manual_bounded_read_only_and_always_uploads():
    text = Path(".github/workflows/alpha-atlas-v4-delisted-coverage-verification.yml").read_text()
    assert text.startswith("name: Alpha Atlas V4 Delisted Coverage Verification\n")
    assert "workflow_dispatch:" in text and "schedule:" not in text and "push:" not in text
    assert text.count("if: always()") == 2
    assert "MASSIVE_API_KEY: ${{ secrets.MASSIVE_API_KEY }}" in text
    for forbidden in ("train_challenger", "backtest", "promote", "deploy", "ingest_massive"):
        assert forbidden not in text.lower()
