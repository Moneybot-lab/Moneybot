#!/usr/bin/env python3
"""Derive KAII condition findings from pinned evidence; optionally fill only missing scope."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from moneybot.services.alpha_atlas_v4_historical_coverage_diagnostics import _bounded_collection
from moneybot.services.alpha_atlas_v4_phase1_discovery import _urllib_fetch

SOURCE_RUN_ID = 35233428008
SOURCE_ATTEMPT = 1
SOURCE_HEAD_SHA = "bf2776e0943b3c0cd392015da4005391d30b15da"
SOURCE_ARTIFACT = "alpha-atlas-v4-historical-coverage-diagnostics-35233428008-1"
CONDITION_RULE_URL = "https://massive.com/blog/understanding-trade-eligibility"
FULL_DAY_PAGE_LIMIT = 2
FULL_DAY_RECORD_LIMIT = 5_000
REPORT_SAMPLE_LIMIT = 100


def _conditions(trade: dict) -> list[int]:
    return [int(value) for value in (trade.get("conditions") or trade.get("c") or [])]


def evaluate_dimension(condition_ids: list[int], definitions: dict[int, dict],
                       scope: str, dimension: str) -> dict:
    rules = []
    for condition_id in condition_ids:
        definition = definitions.get(condition_id)
        value = (((definition or {}).get("update_rules") or {}).get(scope) or {}).get(dimension)
        rules.append({"condition_id": condition_id, "value": value})
    if not condition_ids or any(item["value"] is None for item in rules):
        result = "UNVERIFIED/UNKNOWN"
        reasoning = "At least one observed condition lacks a saved rule for this scope and dimension."
    elif any(item["value"] is False for item in rules):
        result = "INELIGIBLE"
        reasoning = "At least one condition says NO; current Massive documentation says NO takes precedence."
    else:
        result = "ELIGIBLE"
        reasoning = "Every observed condition says YES; no permissive condition overrides a restrictive one."
    return {"status": result, "rules": rules, "combined_condition_reasoning": reasoning,
            "rule_source": CONDITION_RULE_URL, "historical_2023_applicability": "UNVERIFIED/UNKNOWN"}


def evaluate_trade(trade: dict, definitions: dict[int, dict]) -> dict:
    condition_ids = _conditions(trade)
    meanings = []
    for condition_id in condition_ids:
        definition = definitions.get(condition_id) or {}
        meanings.append({"id": condition_id, "name": definition.get("name"),
                         "sip_mapping": definition.get("sip_mapping"),
                         "definition_present": bool(definition)})
    eligibility = {}
    for scope in ("consolidated", "market_center"):
        eligibility[scope] = {
            "open_close": evaluate_dimension(condition_ids, definitions, scope, "updates_open_close"),
            "high_low": evaluate_dimension(condition_ids, definitions, scope, "updates_high_low"),
            "volume": evaluate_dimension(condition_ids, definitions, scope, "updates_volume"),
        }
    return {"record_identifier": trade.get("id") or trade.get("sequence_number"),
            "participant_timestamp": trade.get("participant_timestamp"),
            "sip_timestamp": trade.get("sip_timestamp"), "tape": trade.get("tape"),
            "exchange": trade.get("exchange"), "price": trade.get("price"), "size": trade.get("size"),
            "conditions": condition_ids, "condition_meanings": meanings, "eligibility": eligibility,
            "correction": trade.get("correction"), "trf_id": trade.get("trf_id"),
            "correction_cancellation_assessment": (
                "NO_NONZERO_CORRECTION_INDICATOR_IN_SAVED_RECORD" if trade.get("correction") in {None, 0}
                else "UNVERIFIED_NONZERO_CORRECTION_INDICATOR")}


def _full_day_bounds(day_text: str) -> tuple[str, str]:
    eastern = ZoneInfo("America/New_York")
    day = date.fromisoformat(day_text)
    start = datetime.combine(day, datetime.min.time(), tzinfo=eastern)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=eastern)
    return (start.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
            end.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"))


def _source_investigations(source: dict) -> dict[str, dict]:
    values = {str(item.get("session")): item for item in source.get("kaii_gap_investigations") or []}
    if set(values) != {"2023-01-19", "2023-02-17", "2023-02-24"}:
        raise ValueError("PINNED_KAII_DATES_MISMATCH")
    return values


def derive(source: dict, *, source_sha256: str, api_key: str = "", fetcher=_urllib_fetch) -> dict:
    investigations = _source_investigations(source)
    condition_rows = (source.get("trade_condition_reference") or {}).get("records") or []
    definitions = {int(row["id"]): row for row in condition_rows if isinstance(row, dict) and row.get("id") is not None}
    january = investigations["2023-01-19"]
    trades = (january.get("trades") or {}).get("records") or []
    if len(trades) != 2 or sorted(_conditions(item) for item in trades) != [[16], [17, 37, 41]]:
        raise ValueError("PINNED_JANUARY_TRADE_EVIDENCE_MISMATCH")
    evaluated = [evaluate_trade(trade, definitions) for trade in trades]
    consolidated_ohlc_ineligible = all(
        row["eligibility"]["consolidated"][dimension]["status"] == "INELIGIBLE"
        for row in evaluated for dimension in ("open_close", "high_low"))
    january_complete = (january["trades"].get("pagination_complete") is True
                         and january["trades"].get("status") == "DATA_RETURNED")
    january_result = {
        "date": "2023-01-19", "trade_records": evaluated,
        "trade_condition_interpretation": "CURRENT_RULES_EVALUATED_HISTORICAL_APPLICABILITY_UNKNOWN",
        "absent_aggregate_explanation": (
            "NO_OHLC_ELIGIBLE_RECORDS_IN_RETRIEVED_REGULAR_SESSION_EVIDENCE_UNDER_CURRENT_RULES"
            if consolidated_ohlc_ineligible and january_complete else "UNRESOLVED"),
        "scope": "Only the complete saved Massive regular-session trade response; not venue-wide or full-day proof.",
        "price_availability": "NOT_RETRIEVED", "valuation_readiness": "UNVERIFIED",
    }

    february = []
    provenance: list[dict] = []
    for day in ("2023-02-17", "2023-02-24"):
        saved = investigations[day]
        start, end = _full_day_bounds(day)
        additional = {"status": "NOT_EXECUTED_NO_CREDENTIAL", "request_provenance": []}
        if api_key:
            from urllib.parse import urlencode
            query = urlencode({"timestamp.gte": start, "timestamp.lt": end, "sort": "timestamp",
                               "order": "asc", "limit": FULL_DAY_RECORD_LIMIT})
            additional = _bounded_collection(
                f"https://api.massive.com/v3/trades/KAII?{query}", api_key=api_key, fetcher=fetcher,
                provenance=provenance, page_limit=FULL_DAY_PAGE_LIMIT, record_limit=FULL_DAY_RECORD_LIMIT)
        additional_evaluated = [evaluate_trade(trade, definitions) for trade in additional.get("records", [])]
        february.append({
            "date": day,
            "saved_evidence": {"regular_session_trade_status": saved["trades"]["status"],
                "regular_session_pagination_complete": saved["trades"].get("pagination_complete"),
                "regular_session_bounds": {"open": saved.get("session_open_at"), "close": saved.get("session_close_at")},
                "quotes_status": saved["quotes"]["status"], "daily_target_status": saved["daily"].get("target_status"),
                "minute_status": saved["intraday"]["status"], "adjacent_control_count": len(saved["daily"].get("control_records") or [])},
            "proves": "Massive returned no regular-session trades in the bounded request; quotes and adjacent daily controls were present.",
            "does_not_prove": "No full-day/SIP trades, no venue-wide trading, no eligible late or extended-hours reports, and no provider coverage omission.",
            "smallest_additional_query": {"endpoint": "/v3/trades/KAII", "timestamp_gte": start,
                "timestamp_lt": end, "page_limit": FULL_DAY_PAGE_LIMIT, "record_limit": FULL_DAY_RECORD_LIMIT,
                "purpose": "Cover the same calendar date outside the already-complete regular-session interval without substituting another security."},
            "additional_query_result": additional,
            "additional_trade_condition_evaluation": additional_evaluated,
            "provider_clarification_if_still_empty": {
                "needed": additional.get("status") in {"DATA_RETURNED", "EMPTY_RESPONSE", "NOT_EXECUTED_NO_CREDENTIAL"},
                "technical_question": "For the saved request hashes, does the historical trades entitlement provide complete SIP coverage for KAII over the full 2023 calendar date, including late reports, and were any trades excluded from this endpoint or daily aggregates by condition/correction processing?",
                "sanitized_saved_request_provenance": saved["trades"].get("request_provenance")},
            "price_availability": "NOT_RETRIEVED", "valuation_readiness": "UNVERIFIED",
        })
    return {"schema_version": "alpha-atlas-v4-kaii-condition-evaluation.v1",
            "source": {"run_id": SOURCE_RUN_ID, "run_attempt": SOURCE_ATTEMPT,
                       "head_sha": SOURCE_HEAD_SHA, "artifact": SOURCE_ARTIFACT,
                       "diagnostic_json_sha256": source_sha256},
            "diagnostic_execution": "COMPLETE" if api_key else "LOCAL_DERIVATION_COMPLETE_HOSTED_QUERY_PENDING",
            "january_19": january_result, "february_assessments": february,
            "new_request_provenance": provenance,
            "historical_availability_closure_preserved": True,
            "terminal_valuation": "OPEN", "historical_universe_completeness": "OPEN",
            "population_reconciliation": "OPEN", "broader_identity_coverage": "OPEN",
            "research_only": True, "automatic_promotion": False, "ready_for_live_routing": False}


def _markdown(report: dict) -> str:
    lines = ["# KAII trade-condition and missing-scope evaluation", "",
             f"- Source: run `{SOURCE_RUN_ID}-1`, JSON SHA-256 `{report['source']['diagnostic_json_sha256']}`.",
             f"- Execution: `{report['diagnostic_execution']}`.", "",
             "## 2023-01-19 trades", "",
             "| Record | SIP / participant timestamp | Tape / exchange | Price × size | Conditions | Consolidated O/C · H/L · volume | Market-center O/C · H/L · volume |",
             "|---|---|---|---|---|---|---|"]
    for row in report["january_19"]["trade_records"]:
        c, m = row["eligibility"]["consolidated"], row["eligibility"]["market_center"]
        meanings = "; ".join(f"{item['id']} {item.get('name')} {item.get('sip_mapping')}" for item in row["condition_meanings"])
        lines.append(f"| `{row['record_identifier']}` | `{row['sip_timestamp']}` / `{row['participant_timestamp']}` | `{row['tape']}` / `{row['exchange']}` | `{row['price']}` × `{row['size']}` | {meanings} | `{c['open_close']['status']}` · `{c['high_low']['status']}` · `{c['volume']['status']}` | `{m['open_close']['status']}` · `{m['high_low']['status']}` · `{m['volume']['status']}` |")
        lines.append(f"| ↳ combined rule | | | | | {c['open_close']['combined_condition_reasoning']} Historical 2023 applicability: `{c['open_close']['historical_2023_applicability']}`. | Market-center rules are reported separately and do not determine a consolidated bar. |")
    lines += ["", f"- Aggregate explanation: `{report['january_19']['absent_aggregate_explanation']}`.",
              f"- Scope: {report['january_19']['scope']}", "", "## February missing scope", "",
              "| Date | Saved proof | Full-day query | Result | Remaining evidence |", "|---|---|---|---|---|"]
    for item in report["february_assessments"]:
        q = item["smallest_additional_query"]
        result = item["additional_query_result"]
        records = result.get("records") or []
        evaluations = item.get("additional_trade_condition_evaluation") or []
        if result.get("status") == "DATA_RETURNED":
            prices = ", ".join(f"{row.get('size')} @ ${row.get('price')} conditions {row.get('conditions')}" for row in records)
            current = "; ".join(
                f"record {row.get('record_identifier')}: consolidated O/C {row['eligibility']['consolidated']['open_close']['status']}, H/L {row['eligibility']['consolidated']['high_low']['status']}, volume {row['eligibility']['consolidated']['volume']['status']}"
                for row in evaluations)
            outcome = f"`DATA_RETURNED` ({len(records)} records: {prices}). Current-rule assessment: {current}."
            remaining = ("Confirm that the saved current condition rules and NO-precedence behavior govern Massive's "
                         "historical aggregate reconstruction for 2023; returned records do not make a price available.")
        elif result.get("status") == "EMPTY_RESPONSE" and result.get("pagination_complete") is True:
            outcome = "`EMPTY_RESPONSE`; pagination complete and zero records returned."
            remaining = ("Massive must confirm whether this is complete supported SIP coverage or reflects retention, "
                         "entitlement, correction, ticker-mapping, or other coverage limitations.")
        else:
            outcome = f"`{result.get('status')}`; the full-day evidence is incomplete or was not executed."
            remaining = item["does_not_prove"]
        lines.append(f"| `{item['date']}` | {item['proves']} | Completed bounds: `{q['timestamp_gte']}` to `{q['timestamp_lt']}`, {q['page_limit']} pages/{q['record_limit']} records | {outcome} Historical 2023 rule applicability: `UNVERIFIED/UNKNOWN`. | {remaining} |")
    lines += ["", "An explained absent aggregate is not a retrieved price and does not establish valuation readiness. No replacement data is produced.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(); args.output_dir.mkdir(parents=True, exist_ok=True)
    raw = args.source_report.read_bytes(); source = json.loads(raw)
    report = derive(source, source_sha256=hashlib.sha256(raw).hexdigest(), api_key=os.getenv("MASSIVE_API_KEY", ""))
    json_path = args.output_dir / "kaii_trade_condition_evaluation.json"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    (args.output_dir / "kaii_trade_condition_evaluation.md").write_text(_markdown(report))
    print(json.dumps({"status": report["diagnostic_execution"], "source_sha256": report["source"]["diagnostic_json_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
