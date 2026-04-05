#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def build_daily_ops_commands(
    *,
    python_executable: str,
    project_root: Path,
    input_log: str,
    summary_limit: int,
    outcomes_limit: int,
    outcomes_rows_limit: int,
    calibration_limit: int,
    horizon_days: int,
    base_dir: str,
) -> list[list[str]]:
    scripts_dir = project_root / "scripts"
    return [
        [
            python_executable,
            str(scripts_dir / "day7_decision_log_summary.py"),
            "--input",
            input_log,
            "--limit",
            str(summary_limit),
            "--output",
            os.path.join(base_dir, "day7_decision_log_summary.json"),
        ],
        [
            python_executable,
            str(scripts_dir / "day12_materialize_outcomes.py"),
            "--input",
            input_log,
            "--output",
            os.path.join(base_dir, "decision_outcomes_snapshot.json"),
            "--limit",
            str(outcomes_limit),
            "--rows-limit",
            str(outcomes_rows_limit),
        ],
        [
            python_executable,
            str(scripts_dir / "day13_calibration_report.py"),
            "--input",
            input_log,
            "--output",
            os.path.join(base_dir, "day13_calibration_report.json"),
            "--limit",
            str(calibration_limit),
            "--horizon-days",
            str(horizon_days),
        ],
        [
            python_executable,
            str(scripts_dir / "day13_recalibrate.py"),
            "--report",
            os.path.join(base_dir, "day13_calibration_report.json"),
            "--output",
            os.path.join(base_dir, "day13_recalibration_plan.json"),
        ],
        [
            python_executable,
            str(scripts_dir / "autofill_daily_report.py"),
            "--summary",
            os.path.join(base_dir, "day7_decision_log_summary.json"),
            "--outcomes",
            os.path.join(base_dir, "decision_outcomes_snapshot.json"),
            "--calibration",
            os.path.join(base_dir, "day13_calibration_report.json"),
            "--plan",
            os.path.join(base_dir, "day13_recalibration_plan.json"),
            "--output",
            os.path.join(base_dir, "daily_report.md"),
        ],
    ]


def main() -> None:
    base_dir = os.getenv("MONEYBOT_PERSISTENT_DATA_DIR", "data")
    os.makedirs(base_dir, exist_ok=True)
    parser = argparse.ArgumentParser(description="Run daily Moneybot ops scripts in one command.")
    parser.add_argument("--input-log", default=os.path.join(base_dir, "decision_events.jsonl"))
    parser.add_argument("--summary-limit", type=int, default=200)
    parser.add_argument("--outcomes-limit", type=int, default=2000)
    parser.add_argument("--outcomes-rows-limit", type=int, default=20)
    parser.add_argument("--calibration-limit", type=int, default=1000)
    parser.add_argument("--horizon-days", type=int, default=5)
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    commands = build_daily_ops_commands(
        python_executable=sys.executable,
        project_root=project_root,
        input_log=args.input_log,
        summary_limit=max(1, args.summary_limit),
        outcomes_limit=max(1, args.outcomes_limit),
        outcomes_rows_limit=max(1, args.outcomes_rows_limit),
        calibration_limit=max(1, args.calibration_limit),
        horizon_days=max(1, args.horizon_days),
        base_dir=base_dir,
    )

    Path(base_dir).mkdir(parents=True, exist_ok=True)
    for command in commands:
        print("Running:", " ".join(command))
        subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
