#!/usr/bin/env python3
"""Execute bounded live delisted-security coverage verification."""

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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from moneybot.services.alpha_atlas_v4_delisted_coverage import (  # noqa: E402
    verify_delisted_availability,
)


def derived_probe_summary(report: dict) -> dict:
    representative = report.get("probes") or []
    followups = report.get("follow_up_probes") or []
    verified = lambda rows: sum(row.get("availability_verified") is True for row in rows)
    tickers = {
        str(row.get("ticker"))
        for row in [*representative, *followups]
        if row.get("ticker")
    }
    return {
        "schema_version": "alpha-atlas-v4-delisted-availability-derived-summary.v1",
        "representative_probes": {"verified": verified(representative), "total": len(representative)},
        "prior_run_follow_ups": {"verified": verified(followups), "total": len(followups)},
        "distinct_tickers": len(tickers),
    }


def _markdown(report: dict) -> str:
    population, outcomes, conclusions = (report.get(key) or {} for key in ("population", "outcome_counts", "conclusions"))
    summary = derived_probe_summary(report)
    return "\n".join((
        "# Alpha Atlas V4 delisted-security availability verification", "",
        f"- Status: `{report.get('status')}`",
        f"- Evidence scope: `{report.get('scope')}`",
        f"- Inactive listings enumerated: `{population.get('inactive_listings')}`",
        f"- Provider-ended ticker listings: `{population.get('provider_ended_ticker_listings')}`",
        f"- Other inactive: `{population.get('other_inactive')}`",
        f"- Previous 6,629 count matched: `{population.get('previous_count_matches')}`",
        f"- Count reconciliation: `{population.get('count_reconciliation')}`",
        f"- Pagination complete: `{(report.get('pagination') or {}).get('complete')}`",
        f"- Representative probes: `{summary['representative_probes']['verified']}` / `{summary['representative_probes']['total']}` verified",
        f"- Prior-run follow-ups: `{summary['prior_run_follow_ups']['verified']}` / `{summary['prior_run_follow_ups']['total']}` verified",
        f"- Distinct tickers: `{summary['distinct_tickers']}`",
        f"- Ended-listing historical availability: `{conclusions.get('historical_access_for_provider_ended_ticker_listings')}`",
        f"- Company/security termination established: `{conclusions.get('confirmed_company_or_security_termination')}`",
        f"- Complete historical universe: `{conclusions.get('complete_historical_universe')}`",
        f"- Effective-dated identity: `{conclusions.get('effective_dated_identity_and_ticker_history')}`",
        f"- Terminal valuation: `{conclusions.get('terminal_price_and_delisting_treatment')}`",
        f"- Explanation: {report.get('status_explanation') or report.get('reason')}", "",
        "This bounded read-only verification does not authorize a backfill, establish complete-universe coverage, or approve a terminal-value policy.", "",
    ))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--research-start", default="2018-01-01")
    parser.add_argument("--research-end", default="2026-09-15")
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--max-probes", type=int, default=12)
    parser.add_argument("--prior-report", type=Path)
    parser.add_argument("--derive-summary-only", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(timezone.utc).isoformat()
    commit = os.getenv("GITHUB_SHA") or subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    prior = json.loads(args.prior_report.read_text()) if args.prior_report else None
    if args.derive_summary_only:
        if prior is None:
            parser.error("--derive-summary-only requires --prior-report")
        summary = derived_probe_summary(prior)
        summary.update({
            "source_report_sha256": hashlib.sha256(args.prior_report.read_bytes()).hexdigest(),
            "source_workflow": "Alpha Atlas V4 Delisted Coverage Verification",
            "source_run_id": 35125664186,
            "source_run_attempt": 1,
            "source_commit": "205529d612c1ac2a3497a07f5cb6151d2eef62f4",
            "derived_at_utc": generated,
            "live_requests_performed": False,
        })
        (args.output_dir / "alpha_atlas_v4_delisted_coverage_corrected_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        print(json.dumps(summary, sort_keys=True))
        return 0
    try:
        report = verify_delisted_availability(api_key=os.getenv("MASSIVE_API_KEY", ""), repository_commit=commit, generated_at=generated, research_start=args.research_start, research_end=args.research_end, max_pages=args.max_pages, max_probes=args.max_probes, prior_run_evidence=prior)
        if args.prior_report:
            report["prior_run_report_sha256"] = hashlib.sha256(args.prior_report.read_bytes()).hexdigest()
    except Exception as exc:
        reason = str(exc) or type(exc).__name__
        report = {"schema_version": "alpha-atlas-v4-delisted-availability-verification.v2", "generated_at_utc": generated, "repository_commit": commit, "scope": "bounded_live_read_only_ended_listing_historical_access", "status": "BLOCKED", "failure": type(exc).__name__, "reason": reason, "status_explanation": reason, "failure_reasons": [{"code": reason.split(":", 1)[0], "explanation": reason}], "full_backfill_authorized": False, "research_only": True, "automatic_promotion": False, "ready_for_live_routing": False}
    (args.output_dir / "alpha_atlas_v4_delisted_coverage_verification.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    (args.output_dir / "alpha_atlas_v4_delisted_coverage_verification.md").write_text(_markdown(report))
    print(json.dumps({"status": report["status"],
                      "reason_codes": [item.get("code") for item in report.get("failure_reasons", [])],
                      "explanation": report.get("status_explanation") or report.get("reason")}, sort_keys=True))
    return 0 if report["status"] == "VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
