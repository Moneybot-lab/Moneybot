import copy
import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from moneybot.services.alpha_atlas_v4_valuation_coverage import (
    SUPPLEMENT_VERSION,
    SUPPORTED_BASIS,
    build_valuation_coverage_report,
)


def _row(identifier, symbol, missing):
    sessions = ["2026-07-22", "2026-07-23", "2026-07-24", "2026-07-27"]
    return {
        "canonical_observation_id": identifier,
        "symbol": symbol,
        "entry_session_date": sessions[0],
        "exit_session_date": sessions[-1],
        "valuation_path": [{"session": day} for day in sessions if day not in missing],
        "feature_close": identifier,
        "label": identifier % 2,
    }


def _record(symbol, session, close=1.25):
    stamp = int(
        datetime.fromisoformat(session)
        .replace(tzinfo=ZoneInfo("America/New_York"))
        .timestamp()
        * 1000
    )
    content = json.dumps(
        {"ticker": symbol, "results": [{"t": stamp, "c": close}]}, separators=(",", ":")
    )
    return {
        "provider": "massive_rest",
        "symbol": symbol,
        "session": session,
        "original_response_content": content,
        "source_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "raw_close": close,
        "adjustment_basis": SUPPORTED_BASIS,
    }


def test_confirmed_missing_pattern_and_complete_recovery_preserve_rows():
    rows = [
        _row(1, "AUROW", {"2026-07-23", "2026-07-24"}),
        _row(2, "RNWWW", {"2026-07-23"}),
        _row(3, "RNWWW", {"2026-07-23"}),
        _row(4, "RNWWW", {"2026-07-23"}),
    ]
    original = copy.deepcopy(rows)
    missing = build_valuation_coverage_report(rows)
    assert missing["affected_observation_count"] == 4
    assert missing["unique_missing_symbol_sessions"] == [
        {"symbol": "AUROW", "session": "2026-07-23"},
        {"symbol": "AUROW", "session": "2026-07-24"},
        {"symbol": "RNWWW", "session": "2026-07-23"},
    ]
    supplement = {
        "schema_version": SUPPLEMENT_VERSION,
        "records": [
            _record("AUROW", "2026-07-23"),
            _record("AUROW", "2026-07-24"),
            _record("RNWWW", "2026-07-23", 0.0031),
        ],
    }
    complete = build_valuation_coverage_report(rows, supplement=supplement)
    assert complete["coverage_status"] == "COMPLETE_COMPATIBLE_EVIDENCE"
    assert complete["certification_may_proceed"] is True
    assert (
        rows == original
    )  # coverage/recovery never changes model payload or membership


def test_partial_empty_wrong_semantics_and_tampering_fail_closed():
    rows = [_row(1, "AUROW", {"2026-07-23", "2026-07-24"})]
    partial = {
        "schema_version": SUPPLEMENT_VERSION,
        "records": [_record("AUROW", "2026-07-23")],
    }
    assert (
        build_valuation_coverage_report(rows, supplement=partial)[
            "certification_may_proceed"
        ]
        is False
    )
    wrong = _record("AUROW", "2026-07-23")
    wrong["adjustment_basis"] = "adjusted"
    report = build_valuation_coverage_report(
        rows, supplement={"schema_version": SUPPLEMENT_VERSION, "records": [wrong]}
    )
    assert (
        report["recovery_validation_failures"][0]["reason"]
        == "unsupported_or_ambiguous_price_semantics"
    )
    tampered = _record("AUROW", "2026-07-23")
    tampered["raw_close"] = 99
    report = build_valuation_coverage_report(
        rows, supplement={"schema_version": SUPPLEMENT_VERSION, "records": [tampered]}
    )
    assert (
        report["recovery_validation_failures"][0]["reason"]
        == "supplement_response_security_session_price_mismatch"
    )


def test_access_failures_remain_distinguishable():
    rows = [_row(1, "RNWWW", {"2026-07-23"})]
    supplement = {
        "schema_version": SUPPLEMENT_VERSION,
        "records": [],
        "recovery_attempts": [
            {"outcome": "authentication_error"},
            {"outcome": "timeout_or_network_error"},
            {"outcome": "missing_provider_records"},
        ],
    }
    report = build_valuation_coverage_report(rows, supplement=supplement)
    assert [item["outcome"] for item in report["recovery_attempts"]] == [
        "authentication_error",
        "timeout_or_network_error",
        "missing_provider_records",
    ]
    assert report["certification_may_proceed"] is False
