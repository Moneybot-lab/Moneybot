#!/usr/bin/env python3
"""Write an early V4 valuation coverage report and fail closed when incomplete."""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from moneybot.services.alpha_atlas_v4_valuation_coverage import (  # noqa: E402
    build_valuation_coverage_report,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--supplement")
    parser.add_argument(
        "--diagnostic-only",
        action="store_true",
        help="Report all-observation gaps without treating them as core-certification failures.",
    )
    args = parser.parse_args()
    rows = [
        json.loads(line)
        for line in Path(args.input).read_text().splitlines()
        if line.strip()
    ]
    supplement = (
        json.loads(Path(args.supplement).read_text())
        if args.supplement and Path(args.supplement).is_file()
        else None
    )
    report = build_valuation_coverage_report(
        rows,
        supplement=supplement,
        run_identity={
            "github_run_id": os.getenv("GITHUB_RUN_ID"),
            "checked_out_sha": os.getenv("GITHUB_SHA"),
        },
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        f"valuation coverage: {report['coverage_status']}; affected={report['affected_observation_count']}"
    )
    return 0 if args.diagnostic_only or report["certification_may_proceed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
