#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def build_recalibration_plan(
    report: dict,
    *,
    current_slope: float = 1.0,
    current_intercept: float = 0.0,
    max_intercept_step: float = 0.2,
    min_rows: int = 30,
) -> dict:
    rows = int(report.get("rows") or 0)
    recommendation = report.get("recommended") if isinstance(report.get("recommended"), dict) else {}
    intercept_delta = float(recommendation.get("intercept_delta") or 0.0)
    slope_delta = float(recommendation.get("slope_delta") or 0.0)
    bounded_intercept_delta = max(-max_intercept_step, min(max_intercept_step, intercept_delta))
    apply_change = rows >= min_rows
    next_slope = float(current_slope + slope_delta) if apply_change else float(current_slope)
    next_intercept = float(current_intercept + bounded_intercept_delta) if apply_change else float(current_intercept)
    return {
        "schema_version": "calibration_recalibration_plan.v1",
        "computed_at_utc": datetime.now(timezone.utc).isoformat(),
        "rows": rows,
        "apply_change": apply_change,
        "current": {"slope": float(current_slope), "intercept": float(current_intercept)},
        "recommended_delta": {
            "slope_delta": round(slope_delta, 6),
            "intercept_delta": round(intercept_delta, 6),
            "bounded_intercept_delta": round(bounded_intercept_delta, 6),
        },
        "next": {"slope": round(next_slope, 6), "intercept": round(next_intercept, 6)},
    }


def main() -> None:
    base_dir = os.getenv("MONEYBOT_PERSISTENT_DATA_DIR", "data")
    os.makedirs(base_dir, exist_ok=True)
    parser = argparse.ArgumentParser(description="Create Day-13 deterministic recalibration plan from report JSON.")
    parser.add_argument("--report", default=os.path.join(base_dir, "day13_calibration_report.json"))
    parser.add_argument("--output", default=os.path.join(base_dir, "day13_recalibration_plan.json"))
    parser.add_argument("--current-slope", type=float, default=1.0)
    parser.add_argument("--current-intercept", type=float, default=0.0)
    parser.add_argument("--max-intercept-step", type=float, default=0.2)
    parser.add_argument("--min-rows", type=int, default=30)
    args = parser.parse_args()

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    plan = build_recalibration_plan(
        report,
        current_slope=args.current_slope,
        current_intercept=args.current_intercept,
        max_intercept_step=max(0.01, abs(args.max_intercept_step)),
        min_rows=max(1, args.min_rows),
    )
    serialized = json.dumps(plan, indent=2, sort_keys=True)
    print(serialized)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(serialized, encoding="utf-8")


if __name__ == "__main__":
    main()
