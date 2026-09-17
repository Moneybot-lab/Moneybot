#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from moneybot.services.alpha_atlas_v4_historical_coverage_diagnostics import diagnose_historical_coverage  # noqa: E402


def _validated_transition_source(path: Path | None) -> tuple[dict | None, str | None]:
    if path is None:
        return None, None
    raw = path.read_bytes(); report = json.loads(raw)
    expected = {
        ("BWINA", "PTVCA"): ("Class A common stock", "2018-08-01"),
        ("BWINB", "PTVCB"): ("Class B common stock", "2018-08-01"),
        ("KAII", "QDRO"): ("Class A ordinary shares", "2023-02-27"),
        ("KAIIU", "QDROU"): ("units", "2023-02-27"),
        ("KAIIW", "QDROW"): ("redeemable warrants", "2023-02-27"),
    }
    transitions = report.get("transition_diagnostics") or []
    if len(transitions) != 5:
        raise ValueError("PINNED_TRANSITION_COUNT_MISMATCH")
    for item in transitions:
        key = (item.get("old_ticker"), item.get("new_ticker"))
        if key not in expected or (item.get("security_class"), item.get("event_effective_date")) != expected[key]:
            raise ValueError("PINNED_TRANSITION_IDENTITY_MISMATCH")
        if item.get("result") != "VERIFIED":
            raise ValueError("PINNED_TRANSITION_NOT_VERIFIED")
        if not (item.get("old_reference") or {}).get("exact_ticker_returned") or not (item.get("new_reference") or {}).get("exact_ticker_returned"):
            raise ValueError("PINNED_TRANSITION_REFERENCE_MISSING")
        for reference in (item["old_reference"], item["new_reference"]):
            provenance = reference.get("request_provenance") or {}
            if provenance.get("status") != 200 or not provenance.get("response_sha256"):
                raise ValueError("PINNED_TRANSITION_PROVENANCE_INVALID")
        if not str(item.get("source_url") or "").startswith("https://www.sec.gov/Archives/edgar/data/"):
            raise ValueError("PINNED_TRANSITION_PRIMARY_SOURCE_INVALID")
    return report, hashlib.sha256(raw).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-report", required=True, type=Path)
    parser.add_argument("--verified-transition-report", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(); args.output_dir.mkdir(parents=True, exist_ok=True)
    source = json.loads(args.source_report.read_text())
    source_sha256 = hashlib.sha256(args.source_report.read_bytes()).hexdigest()
    commit = os.getenv("GITHUB_SHA") or subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    try:
        transition_source, transition_source_sha256 = _validated_transition_source(args.verified_transition_report)
        report = diagnose_historical_coverage(source, api_key=os.getenv("MASSIVE_API_KEY", ""), repository_commit=commit,
                                               generated_at=datetime.now(timezone.utc).isoformat(),
                                               verified_transition_source=transition_source,
                                               investigate_kaii_gaps=True)
        report["source_report_sha256"] = source_sha256
        if transition_source_sha256:
            report["verified_transition_source"] = {"workflow_run_id": 35183625727, "run_attempt": 1,
                "head_sha": "5dcd7d8c99cbbc652f637dbb6b35dabcd62c66d9",
                "report_sha256": transition_source_sha256,
                "artifact": "alpha-atlas-v4-historical-coverage-diagnostics-35183625727-1"}
    except Exception as exc:
        reason = str(exc) or type(exc).__name__
        report = {"schema_version": "alpha-atlas-v4-historical-coverage-diagnostics.v1", "status": "BLOCKED",
                  "reason_code": reason.split(":", 1)[0], "reason": reason, "repository_commit": commit,
                  "source_report_sha256": source_sha256,
                  "research_only": True, "full_backfill_authorized": False,
                  "automatic_promotion": False, "ready_for_live_routing": False}
    target = args.output_dir / "alpha_atlas_v4_historical_coverage_diagnostics.json"
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    lines = ["# Alpha Atlas V4 historical coverage diagnostics", "", f"- Diagnostic execution: `{report['status']}`",
             f"- Source report SHA-256: `{report.get('source_report_sha256')}`",
             f"- Verified transition source: `{report.get('verified_transition_source')}`",
             "- Historical availability: `VERIFIED` in bounded run `35125664186-1` (12/12 representative, 2/2 follow-ups, 12 distinct tickers); not reopened.",
             "- Historical-universe completeness: `NOT_ESTABLISHED_FOR_HISTORICAL_INTERVAL`.",
             "- Population reconciliation: `UNRESOLVED_EARLIER_SNAPSHOT_UNAVAILABLE` (6,607 current versus 6,629 reported; prior snapshot unavailable).",
             "- Terminal valuation: `NOT_ESTABLISHED`; no zero recovery, forward fill, substitution, or proceeds timing inferred.", "",
             "## Missing-session investigations", "",
             "| Ticker | Date | Reference | Trading/evidence finding | Price available | Next evidence |",
             "|---|---|---:|---|---:|---|"]
    for item in report.get("daily_price_gap_diagnostics", []):
        for finding in item.get("missing_session_investigations", []):
            evidence = finding.get("conclusion") or finding.get("evidence_needed") or finding.get("classification")
            next_evidence = ("None for nontrading explanation; terminal cash timing remains separate."
                             if str(finding.get("classification", "")).startswith("EXPLAINED_") else
                             "Venue trade/quote or halt record distinguishing no trades from provider omission.")
            lines.append(f"| `{item['ticker']}` | `{finding.get('session')}` | `{finding.get('reference_present')}` | {evidence} | `False` | {next_evidence} |")
            if finding.get("source_url"):
                lines.append(f"| ↳ source | published `{finding.get('publication_date')}` | effective `{finding.get('event_effective_date')}` | {finding['source_url']} | decision-time available `{finding.get('available_at_original_decision_time')}` | |")
    lines += ["", "## Historical identity investigations", "",
              "The failed run's 20 date=`2026-09-15` HTTP 404 requests are preserved in its original artifact and summarized below; a 2026 response is not availability evidence for an ended ticker.", "",
              "| Identifier type | Ticker | Candidate / returned date | Attempts | Relevance / result | Original failure |",
              "|---|---|---|---:|---|---|"]
    for group in report.get("identity_diagnostics", []):
        for finding in group.get("dated_reference_evidence", []):
            lines.append(f"| `{group.get('identifier_type')}` | `{finding.get('ticker')}` | `{finding.get('query_date')}` / `{finding.get('historical_reference_date')}` | {len(finding.get('reference_attempts', []))} | `{finding.get('research_relevance')}` / `{finding.get('status')}` | `2026-09-15 HTTP 404` |")
    scope = report.get("identity_investigation_scope") or {}
    limit = scope.get("processing_limit") or {}
    lines += ["", "### Identity scope and budgets", "",
              f"- Source filter: `{scope.get('source_query')}`; pagination: `{scope.get('source_pagination')}`.",
              f"- Source groups / ticker occurrences: `{scope.get('source_candidate_groups')}` / `{scope.get('source_ticker_occurrences')}`.",
              f"- Target occurrences / unique / duplicates ignored: `{scope.get('target_occurrences_received')}` / `{scope.get('target_unique_received')}` / `{scope.get('duplicate_target_occurrences_ignored')}`.",
              f"- Unrelated occurrences excluded by the explicit case list: `{scope.get('unrelated_ticker_occurrences_excluded')}`.",
              f"- Record limit `{limit.get('name')}`: configured `{limit.get('configured')}`, observed `{limit.get('observed')}`, exhausted `{limit.get('exhausted')}`.",
              f"- Completed / remaining: `{scope.get('completed_target_records')}` / `{scope.get('remaining_unprocessed_tickers')}`.",
              f"- Listing request budget: `{scope.get('listing_request_budget')}`; historical-date attempt budget: `{scope.get('historical_reference_attempt_budget')}`.",
              f"- Scope status: `{scope.get('status')}`."]
    lines += ["", "Identifier types remain separate. CIK, composite FIGI, and share-class FIGI matches are investigation candidates, not proof of same-security continuity; share classes, units, and warrants are not collapsed.", "",
              "## Dated transition investigations", "",
              "| Old → new | Security class | Exchange | Reference dates | Effective / published | Result | Evidence |",
              "|---|---|---|---|---|---|---|"]
    for transition in report.get("transition_diagnostics", []):
        lines.append(
            f"| `{transition['old_ticker']}` → `{transition['new_ticker']}` | {transition['security_class']} | "
            f"{transition['exchange']} | `{transition['old_date']}` / `{transition['new_date']}` | "
            f"`{transition['event_effective_date']}` / `{transition['source_publication_date']}` | "
            f"`{transition['result']}` | {transition['primary_evidence']} "
            f"[primary source]({transition['source_url']}) |")
        timing = transition.get("decision_time_availability") or {}
        public = transition.get("public_knowability") or {}
        lines.append(f"| ↳ timing | SEC accepted `{public.get('source_acceptance_at')}` | event `{transition.get('event_effective_at')}` | public vs event `{public.get('status')}` | decision-time `{timing.get('status')}` | decision/cutoff `{timing.get('decision_at')}` / `{timing.get('feature_cutoff_at')}` |")
    lines += ["", "## KAII exact-gap bounded investigations", "",
              "| Date | Session UTC | Daily target/control | Minute | Trades | Quotes | Classification | Valuation |",
              "|---|---|---|---|---|---|---|---|"]
    for item in report.get("kaii_gap_investigations", []):
        lines.append(f"| `{item['session']}` | `{item['session_open_at']}`–`{item['session_close_at']}` | `{item['daily'].get('target_status')}` / `{len(item['daily'].get('control_records', []))}` | `{item['intraday']['status']}` | `{item['trades']['status']}` | `{item['quotes']['status']}` | `{item['classification']}` | `{item['valuation_status']}` |")
        lines.append(f"| ↳ completeness | pages daily/minute/trades/quotes `{item['daily']['pages_requested']}`/`{item['intraday']['pages_requested']}`/`{item['trades']['pages_requested']}`/`{item['quotes']['pages_requested']}` | budgets `{item['budgets']}` | halt `{item['halt_event_evidence']['status']}` | trade conditions `{item['trade_condition_reference']['status']}` | replacement authorized `False` | | |")
    lines += ["", "A transition is verified only when the class-specific primary filing and both exact dated provider references agree. An ended listing is not company or security termination.", "",
              "## Request failures and remaining blockers", "",
              f"- Request failures in this execution: `{report.get('request_failure_count')}`.",
              f"- Unresolved: `{', '.join(report.get('unresolved_reason_codes') or [report.get('reason_code', '')])}`.",
              "- Exact next evidence: obtain venue trade/quote or halt records for KAII on 2023-01-19, 2023-02-17, and 2023-02-24, then separately validate terminal payment timing before any valuation rule.", ""]
    (args.output_dir / "alpha_atlas_v4_historical_coverage_diagnostics.md").write_text("\n".join(lines))
    report_text = target.read_text()
    run = maximum = 0
    for character in report_text:
        run = run + 1 if character == "`" else 0
        maximum = max(maximum, run)
    fence = "`" * max(3, maximum + 1)
    literal = f"# Historical coverage diagnostic JSON (literal text)\n\n{fence}json\n{report_text}{fence}\n"
    (args.output_dir / "alpha_atlas_v4_historical_coverage_diagnostics_json_summary.md").write_text(literal)
    print(json.dumps({"status": report["status"], "unresolved": report.get("unresolved_reason_codes") or [report.get("reason_code")]}, sort_keys=True))
    return 0 if report["status"] == "VERIFIED_DIAGNOSTIC_EXECUTION" else 2


if __name__ == "__main__": raise SystemExit(main())
