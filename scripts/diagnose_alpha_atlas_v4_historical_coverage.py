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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-report", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(); args.output_dir.mkdir(parents=True, exist_ok=True)
    source = json.loads(args.source_report.read_text())
    source_sha256 = hashlib.sha256(args.source_report.read_bytes()).hexdigest()
    commit = os.getenv("GITHUB_SHA") or subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    try:
        report = diagnose_historical_coverage(source, api_key=os.getenv("MASSIVE_API_KEY", ""), repository_commit=commit,
                                               generated_at=datetime.now(timezone.utc).isoformat())
        report["source_report_sha256"] = source_sha256
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
              "| Identifier type | Ticker | Selected date | Basis/result | Original failure |",
              "|---|---|---|---|---|"]
    for group in report.get("identity_diagnostics", []):
        for finding in group.get("dated_reference_evidence", []):
            lines.append(f"| `{group.get('identifier_type')}` | `{finding.get('ticker')}` | `{finding.get('query_date')}` | `{finding.get('date_selection_basis')}` / `{finding.get('status')}` | `2026-09-15 HTTP 404` |")
    lines += ["", "Identifier types remain separate. CIK, composite FIGI, and share-class FIGI matches are investigation candidates, not proof of same-security continuity; share classes, units, and warrants are not collapsed.", "",
              "## Request failures and remaining blockers", "",
              f"- Request failures in this execution: `{report.get('request_failure_count')}`.",
              f"- Unresolved: `{', '.join(report.get('unresolved_reason_codes') or [report.get('reason_code', '')])}`.",
              "- Exact next evidence: obtain venue trade/quote or halt records for KAII on 2023-01-19, 2023-02-17, and 2023-02-24, then separately validate terminal payment timing before any valuation rule.", ""]
    (args.output_dir / "alpha_atlas_v4_historical_coverage_diagnostics.md").write_text("\n".join(lines))
    print(json.dumps({"status": report["status"], "unresolved": report.get("unresolved_reason_codes") or [report.get("reason_code")]}, sort_keys=True))
    return 0 if report["status"] == "VERIFIED_DIAGNOSTIC_EXECUTION" else 2


if __name__ == "__main__": raise SystemExit(main())
