#!/usr/bin/env python3
"""Execute bounded live delisted-security coverage verification."""

from __future__ import annotations

import argparse
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


def _markdown(report: dict) -> str:
    population, outcomes, conclusions = (report.get(key) or {} for key in ("population", "outcome_counts", "conclusions"))
    return "\n".join((
        "# Alpha Atlas V4 delisted-security availability verification", "",
        f"- Status: `{report.get('status')}`",
        f"- Evidence scope: `{report.get('scope')}`",
        f"- Inactive listings enumerated: `{population.get('inactive_listings')}`",
        f"- Confirmed delisted: `{population.get('confirmed_delisted')}`",
        f"- Other inactive: `{population.get('other_inactive')}`",
        f"- Previous 6,629 count matched: `{population.get('previous_count_matches')}`",
        f"- Pagination complete: `{(report.get('pagination') or {}).get('complete')}`",
        f"- Probes retrieved: `{outcomes.get('historical_data_retrieved')}` / `{(report.get('probe_selection') or {}).get('selected')}`",
        f"- Delisted historical availability: `{conclusions.get('historical_access_for_confirmed_delisted_securities')}`",
        f"- Complete historical universe: `{conclusions.get('complete_historical_universe')}`",
        f"- Effective-dated identity: `{conclusions.get('effective_dated_identity_and_ticker_history')}`",
        f"- Terminal valuation: `{conclusions.get('terminal_price_and_delisting_treatment')}`", "",
        "This bounded read-only verification does not authorize a backfill, establish complete-universe coverage, or approve a terminal-value policy.", "",
    ))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--research-start", default="2018-01-01")
    parser.add_argument("--research-end", default="2026-09-15")
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--max-probes", type=int, default=12)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(timezone.utc).isoformat()
    commit = os.getenv("GITHUB_SHA") or subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    try:
        report = verify_delisted_availability(api_key=os.getenv("MASSIVE_API_KEY", ""), repository_commit=commit, generated_at=generated, research_start=args.research_start, research_end=args.research_end, max_pages=args.max_pages, max_probes=args.max_probes)
    except Exception as exc:
        report = {"schema_version": "alpha-atlas-v4-delisted-availability-verification.v1", "generated_at_utc": generated, "repository_commit": commit, "scope": "bounded_live_read_only_delisted_historical_access", "status": "BLOCKED", "failure": type(exc).__name__, "reason": str(exc), "full_backfill_authorized": False, "research_only": True, "automatic_promotion": False, "ready_for_live_routing": False}
    (args.output_dir / "alpha_atlas_v4_delisted_coverage_verification.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    (args.output_dir / "alpha_atlas_v4_delisted_coverage_verification.md").write_text(_markdown(report))
    print(json.dumps({"status": report["status"], "reason": report.get("reason")}, sort_keys=True))
    return 0 if report["status"] == "VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
