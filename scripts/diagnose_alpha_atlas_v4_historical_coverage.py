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
    lines = ["# Alpha Atlas V4 historical coverage diagnostics", "", f"- Status: `{report['status']}`",
             f"- Source report SHA-256: `{report.get('source_report_sha256')}`",
             f"- Unresolved: `{', '.join(report.get('unresolved_reason_codes') or [report.get('reason_code', '')])}`", ""]
    (args.output_dir / "alpha_atlas_v4_historical_coverage_diagnostics.md").write_text("\n".join(lines))
    print(json.dumps({"status": report["status"], "unresolved": report.get("unresolved_reason_codes") or [report.get("reason_code")]}, sort_keys=True))
    return 0 if report["status"] == "VERIFIED_DIAGNOSTIC_EXECUTION" else 2


if __name__ == "__main__": raise SystemExit(main())
