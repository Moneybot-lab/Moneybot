#!/usr/bin/env python3
"""Run bounded metadata-only Phase 1 coverage discovery; never performs a backfill."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from moneybot.services.alpha_atlas_v4_phase1_discovery import (  # noqa: E402
    run_coverage_discovery,
)


def _markdown(reports: dict[str, dict]) -> str:
    report = reports["coverage_discovery"]
    return (
        "\n".join(
            [
                "# Alpha Atlas V4 Phase 1 coverage discovery",
                "",
                f"- Evidence: `{report['evidence_class']}`",
                f"- Verdict: `{report['verdict']}`",
                f"- Securities enumerated: `{report['records_enumerated']}`",
                f"- Active: `{report['active_count']}`",
                f"- Inactive: `{report['inactive_count']}`",
                f"- Pagination complete: `{str(report['pagination_complete']).lower()}`",
                f"- Stopped by: `{report['stopped_by_limit']}`",
                f"- Full backfill authorized: `{str(report['full_backfill_authorized']).lower()}`",
                "",
                "Representative metadata discovery does not authorize a historical backfill.",
            ]
        )
        + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--live", action="store_true")
    for name, default in (
        ("max-requests", 40),
        ("max-pages", 10),
        ("max-response-bytes", 1_048_576),
        ("max-total-bytes", 10_485_760),
        ("max-elapsed-seconds", 300),
        ("max-retries", 2),
        ("max-backoff-seconds", 8),
    ):
        parser.add_argument(f"--{name}", type=int, default=default)
    args = parser.parse_args()
    limits = {
        name.replace("-", "_"): getattr(args, name.replace("-", "_"))
        for name in (
            "max-requests",
            "max-pages",
            "max-response-bytes",
            "max-total-bytes",
            "max-elapsed-seconds",
            "max-retries",
            "max-backoff-seconds",
        )
    }
    reports = run_coverage_discovery(
        live=args.live,
        api_key=os.getenv("MASSIVE_API_KEY", ""),
        limits=limits,
    )
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    for name, report in sorted(reports.items()):
        (output / f"alpha_atlas_v4_phase1_{name}.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    discovery_link = {
        "evidence_class": reports["coverage_discovery"]["evidence_class"],
        "verdict": reports["coverage_discovery"]["verdict"],
        "records_enumerated": reports["coverage_discovery"]["records_enumerated"],
        "stopped_by_limit": reports["coverage_discovery"]["stopped_by_limit"],
        "full_backfill_authorized": False,
    }
    for stem in ("readiness", "source_inventory", "backfill_plan"):
        source = ROOT / "docs/reports" / f"alpha_atlas_v4_phase1_{stem}.json"
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["coverage_discovery_evidence"] = discovery_link
        (output / source.name).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    for stem in ("readiness", "source_inventory"):
        source = ROOT / "docs/reports" / f"alpha_atlas_v4_phase1_{stem}.md"
        target = output / source.name
        if source.resolve() != target.resolve():
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    markdown = _markdown(reports)
    (output / "alpha_atlas_v4_phase1_coverage_discovery.md").write_text(
        markdown, encoding="utf-8"
    )
    (
        output / "alpha_atlas_v4_phase1_coverage_discovery_workflow_summary.md"
    ).write_text(markdown, encoding="utf-8")
    print(json.dumps(reports["coverage_discovery"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
