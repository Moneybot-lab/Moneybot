"""Early, fail-closed coverage checks for V4 daily valuation evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from moneybot.services.market_data_providers import ExchangeCalendar

COVERAGE_REPORT_VERSION = "alpha-atlas-v4-valuation-coverage.v1"
SUPPLEMENT_VERSION = "alpha-atlas-v4-valuation-supplement.v1"
SUPPORTED_BASIS = "unadjusted_official_daily_aggregate_close"


def _sessions(first: str, last: str) -> list[str]:
    calendar = ExchangeCalendar()
    current = date.fromisoformat(first)
    end = date.fromisoformat(last)
    if not calendar.is_trading_day(current) or not calendar.is_trading_day(end):
        raise ValueError("valuation_boundary_is_not_exchange_session")
    result = [current.isoformat()]
    while current < end:
        current = calendar.next_session(current)
        result.append(current.isoformat())
    if current != end:
        raise ValueError("valuation_boundary_order_invalid")
    return result


def supplement_records(
    payload: Mapping[str, Any] | None,
) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]]]:
    """Verify immutable supplemental response hashes and compatible bar semantics."""
    if payload is None:
        return {}, []
    failures: list[dict[str, Any]] = []
    if payload.get("schema_version") != SUPPLEMENT_VERSION:
        return {}, [{"reason": "unsupported_supplement_schema"}]
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for item in payload.get("records") or []:
        symbol, session = str(item.get("symbol") or ""), str(item.get("session") or "")
        reason = None
        content = item.get("original_response_content")
        if not isinstance(content, str) or hashlib.sha256(
            content.encode()
        ).hexdigest() != item.get("source_sha256"):
            reason = "supplement_source_hash_mismatch"
        elif item.get("provider") != "massive_rest":
            reason = "unsupported_supplement_provider"
        elif item.get("adjustment_basis") != SUPPORTED_BASIS:
            reason = "unsupported_or_ambiguous_price_semantics"
        elif (symbol, session) in result:
            reason = "duplicate_supplement_security_session"
        else:
            try:
                decoded = json.loads(content)
                bars = decoded.get("results") or []

                def bar_session(bar: Mapping[str, Any]) -> str:
                    if bar.get("session"):
                        return str(bar["session"])
                    return (
                        datetime.fromtimestamp(float(bar["t"]) / 1000, timezone.utc)
                        .astimezone(ZoneInfo("America/New_York"))
                        .date()
                        .isoformat()
                    )

                matching = [bar for bar in bars if bar_session(bar) == session]
                close = float(item["raw_close"])
                if len(matching) != 1 or float(matching[0]["c"]) != close or close <= 0:
                    reason = "supplement_response_security_session_price_mismatch"
                elif str(decoded.get("ticker")) != symbol:
                    reason = "supplement_response_security_session_price_mismatch"
            except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                reason = "malformed_supplement_response"
        if reason:
            failures.append({"symbol": symbol, "session": session, "reason": reason})
        else:
            result[(symbol, session)] = dict(item)
    return result, failures


def build_valuation_coverage_report(
    rows: Iterable[Mapping[str, Any]],
    *,
    supplement: Mapping[str, Any] | None = None,
    run_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rows = list(rows)
    recovered, recovery_failures = supplement_records(supplement)
    affected: list[dict[str, Any]] = []
    original_count = supplemental_count = 0
    for row in rows:
        try:
            expected = _sessions(
                str(row["entry_session_date"]), str(row["exit_session_date"])
            )
        except (KeyError, TypeError, ValueError) as exc:
            affected.append(
                {
                    "canonical_observation_id": row.get("canonical_observation_id"),
                    "symbol": row.get("symbol"),
                    "blocking_reason": str(exc),
                }
            )
            continue
        available = {
            str(mark.get("session")) for mark in row.get("valuation_path") or []
        }
        original_count += len(available & set(expected))
        missing_original = [session for session in expected if session not in available]
        supplied = [
            session
            for session in missing_original
            if (str(row.get("symbol")), session) in recovered
        ]
        supplemental_count += len(supplied)
        missing = [session for session in missing_original if session not in supplied]
        if missing:
            affected.append(
                {
                    "canonical_observation_id": row.get("canonical_observation_id")
                    or row.get("decision_id"),
                    "symbol": row.get("symbol"),
                    "entry_session": expected[0],
                    "exit_session": expected[-1],
                    "expected_sessions": expected,
                    "available_sessions": sorted(available),
                    "supplemental_sessions": supplied,
                    "missing_sessions": missing,
                    "blocking_reason": "missing_provider_records",
                }
            )
    unique_missing = sorted(
        {
            (item.get("symbol"), session)
            for item in affected
            for session in item.get("missing_sessions", [])
        }
    )
    complete = not affected and not recovery_failures
    return {
        "schema_version": COVERAGE_REPORT_VERSION,
        "run_identity": dict(run_identity or {}),
        "total_observations": len(rows),
        "observations_requiring_valuation": len(rows),
        "coverage_status": "COMPLETE_COMPATIBLE_EVIDENCE"
        if complete
        else "INCOMPLETE_VALUATION_EVIDENCE",
        "original_evidence_marks": original_count,
        "supplemental_evidence_marks": supplemental_count,
        "affected_observation_count": len(affected),
        "affected_observations": affected,
        "unique_missing_symbol_sessions": [
            {"symbol": symbol, "session": session} for symbol, session in unique_missing
        ],
        "recovery_attempts": list((supplement or {}).get("recovery_attempts") or []),
        "recovery_validation_failures": recovery_failures,
        "certification_may_proceed": complete,
    }
