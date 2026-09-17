from __future__ import annotations

import json
from pathlib import Path

from moneybot.services.alpha_atlas_v4_phase1_discovery import DiscoveryResponse
from scripts.evaluate_kaii_trade_conditions import derive, evaluate_dimension, evaluate_trade


def _definition(code, name, consolidated, market_center):
    return {"id": code, "name": name, "sip_mapping": {"UTP": str(code)},
            "update_rules": {"consolidated": consolidated, "market_center": market_center}}


def _rules(open_close, high_low, volume):
    return {"updates_open_close": open_close, "updates_high_low": high_low, "updates_volume": volume}


def test_mixed_conditions_use_restrictive_no_and_keep_scopes_separate():
    definitions = {
        17: _definition(17, "permissive", _rules(True, True, True), _rules(True, True, True)),
        37: _definition(37, "consolidated restrictive", _rules(False, False, True), _rules(True, True, True)),
        41: _definition(41, "volume restrictive", _rules(True, True, False), _rules(True, True, True)),
    }
    row = evaluate_trade({"id": "trade-1", "conditions": [17, 37, 41], "price": 10.14, "size": 1}, definitions)
    consolidated = row["eligibility"]["consolidated"]
    market = row["eligibility"]["market_center"]
    assert consolidated["open_close"]["status"] == "INELIGIBLE"
    assert consolidated["high_low"]["status"] == "INELIGIBLE"
    assert consolidated["volume"]["status"] == "INELIGIBLE"
    assert all(value["status"] == "ELIGIBLE" for value in market.values())
    assert "NO takes precedence" in consolidated["open_close"]["combined_condition_reasoning"]


def test_unknown_condition_fails_closed_without_overriding_known_rule():
    definitions = {17: _definition(17, "known", _rules(True, True, True), _rules(True, True, True))}
    result = evaluate_dimension([17, 999], definitions, "consolidated", "updates_open_close")
    assert result["status"] == "UNVERIFIED/UNKNOWN"
    assert result["historical_2023_applicability"] == "UNVERIFIED/UNKNOWN"


def _source_fixture():
    definitions = [
        _definition(16, "Form T", _rules(False, False, True), _rules(False, False, True)),
        _definition(17, "Automatic Execution", _rules(True, True, True), _rules(True, True, True)),
        _definition(37, "Odd Lot", _rules(False, False, False), _rules(False, False, True)),
        _definition(41, "Trade Through Exempt", _rules(True, True, True), _rules(True, True, True)),
    ]
    def investigation(day, trades, quotes=True):
        return {"session": day, "session_open_at": f"{day}T14:30:00Z", "session_close_at": f"{day}T21:00:00Z",
                "trades": {"status": "DATA_RETURNED" if trades else "EMPTY_RESPONSE", "records": trades,
                           "pagination_complete": True, "request_provenance": [{"url": "https://api.massive.com/v3/trades/KAII", "response_sha256": "a" * 64}]},
                "quotes": {"status": "DATA_RETURNED" if quotes else "EMPTY_RESPONSE"},
                "daily": {"target_status": "EMPTY_RESPONSE", "control_records": [{"t": 1}, {"t": 2}]},
                "intraday": {"status": "EMPTY_RESPONSE"}}
    january_trades = [
        {"id": "one", "conditions": [17, 37, 41], "price": 10.14, "size": 1, "tape": 3, "exchange": 11,
         "sip_timestamp": 1, "participant_timestamp": 2},
        {"id": "two", "conditions": [16], "price": 10.14, "size": 1, "tape": 3, "exchange": 11,
         "sip_timestamp": 3, "participant_timestamp": 4},
    ]
    return {"kaii_gap_investigations": [investigation("2023-01-19", january_trades),
                                         investigation("2023-02-17", []), investigation("2023-02-24", [])],
            "trade_condition_reference": {"records": definitions}}


def test_saved_january_scope_explains_ohlc_only_under_current_rules():
    report = derive(_source_fixture(), source_sha256="b" * 64)
    january = report["january_19"]
    assert january["absent_aggregate_explanation"] == "NO_OHLC_ELIGIBLE_RECORDS_IN_RETRIEVED_REGULAR_SESSION_EVIDENCE_UNDER_CURRENT_RULES"
    assert january["price_availability"] == "NOT_RETRIEVED"
    assert january["valuation_readiness"] == "UNVERIFIED"
    assert all(row["eligibility"]["consolidated"]["open_close"]["status"] == "INELIGIBLE"
               for row in january["trade_records"])


def test_february_full_day_query_is_narrow_and_does_not_repeat_regular_session():
    urls = []
    def fetch(_method, url, _headers, _timeout):
        urls.append(url)
        return DiscoveryResponse(200, b'{"results":[]}', {})
    report = derive(_source_fixture(), source_sha256="b" * 64, api_key="secret", fetcher=fetch)
    assert len(urls) == 2
    assert all("/v3/trades/KAII" in url and "timestamp.lt" in url for url in urls)
    assert all(item["additional_query_result"]["status"] == "EMPTY_RESPONSE" for item in report["february_assessments"])
    assert all(item["provider_clarification_if_still_empty"]["needed"] for item in report["february_assessments"])


def test_manual_workflow_is_pinned_and_does_not_rerun_identity_diagnostics():
    text = Path(".github/workflows/evaluate-kaii-trade-conditions.yml").read_text()
    assert text.startswith("name: Evaluate KAII Trade Conditions\n")
    assert "workflow_dispatch:" in text and "schedule:" not in text and "push:" not in text
    assert "35233428008" in text and "10502208555" in text
    assert "MASSIVE_API_KEY" in text and "diagnose_alpha_atlas_v4_historical_coverage.py" not in text
    assert "actions: read" in text and "contents: read" in text
    assert "pip install -r requirements.txt" in text
    assert "evaluator.stderr.log" in text and "Evaluation completed" in text
    assert "unknown/not established because evaluation did not complete" in text
