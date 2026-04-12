#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moneybot.services.runtime_paths import (
    day13_calibration_report_path,
    day13_recalibration_plan_path,
    resolve_runtime_dir,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger(__name__)


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
    base_dir: Path,
    include_day1_refresh: bool = True,
) -> list[list[str]]:
    scripts_dir = project_root / "scripts"
    commands: list[list[str]] = []
    if include_day1_refresh:
        commands.append([python_executable, str(scripts_dir / "day1_refresh_artifact.py")])
    commands.extend([
        [
            python_executable,
            str(scripts_dir / "day7_decision_log_summary.py"),
            "--input",
            input_log,
            "--limit",
            str(summary_limit),
            "--output",
            str(base_dir / "day7_decision_log_summary.json"),
        ],
        [
            python_executable,
            str(scripts_dir / "day12_materialize_outcomes.py"),
            "--input",
            input_log,
            "--output",
            str(base_dir / "decision_outcomes_snapshot.json"),
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
            str(day13_calibration_report_path()),
            "--limit",
            str(calibration_limit),
            "--horizon-days",
            str(horizon_days),
        ],
        [
            python_executable,
            str(scripts_dir / "day13_recalibrate.py"),
            "--report",
            str(day13_calibration_report_path()),
            "--output",
            str(day13_recalibration_plan_path()),
        ],
        [
            python_executable,
            str(scripts_dir / "autofill_daily_report.py"),
            "--summary",
            str(base_dir / "day7_decision_log_summary.json"),
            "--outcomes",
            str(base_dir / "decision_outcomes_snapshot.json"),
            "--calibration",
            str(day13_calibration_report_path()),
            "--plan",
            str(day13_recalibration_plan_path()),
            "--output",
            str(base_dir / "daily_report.md"),
        ],
    ])
    return commands


def _log_file_state(label: str, path: Path) -> None:
    exists = path.exists()
    LOGGER.info("%s path=%s exists=%s", label, path, exists)
    if not exists:
        return
    stat = path.stat()
    modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    LOGGER.info("%s file_size_bytes=%s modified_utc=%s", label, stat.st_size, modified)


def main() -> None:
    base_dir = resolve_runtime_dir()
    parser = argparse.ArgumentParser(description="Run daily Moneybot ops scripts in one command.")
    parser.add_argument("--input-log", default=str(base_dir / "decision_events.jsonl"))
    parser.add_argument("--summary-limit", type=int, default=200)
    parser.add_argument("--outcomes-limit", type=int, default=2000)
    parser.add_argument("--outcomes-rows-limit", type=int, default=20)
    parser.add_argument("--calibration-limit", type=int, default=1000)
    parser.add_argument("--horizon-days", type=int, default=5)
    parser.add_argument(
        "--skip-day1-refresh",
        action="store_true",
        help="Skip day1_refresh_artifact.py (used when already run by another wrapper).",
    )
    args = parser.parse_args()

    project_root = PROJECT_ROOT
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
        include_day1_refresh=not args.skip_day1_refresh,
    )

    calibration_report = day13_calibration_report_path()
    recalibration_plan = day13_recalibration_plan_path()
    LOGGER.info("Resolved daily ops runtime base_dir=%s", base_dir)
    LOGGER.info("Resolved Day 13 calibration_report_path=%s", calibration_report)
    LOGGER.info("Resolved Day 13 recalibration_plan_path=%s", recalibration_plan)

    for command in commands:
        script_name = Path(command[1]).name if len(command) > 1 else "unknown"
        LOGGER.info("Running daily ops script=%s command=%s", script_name, " ".join(command))
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.stdout:
            LOGGER.info("Script stdout (%s): %s", script_name, completed.stdout.strip())
        if completed.returncode != 0:
            if completed.stderr:
                LOGGER.error("Script stderr (%s): %s", script_name, completed.stderr.strip())
            raise subprocess.CalledProcessError(
                returncode=completed.returncode,
                cmd=command,
                output=completed.stdout,
                stderr=completed.stderr,
            )
        if completed.stderr:
            LOGGER.warning("Script stderr (%s): %s", script_name, completed.stderr.strip())

        if script_name == "day13_calibration_report.py":
            _log_file_state("After day13_calibration_report", calibration_report)
        if script_name == "day13_recalibrate.py":
            _log_file_state("After day13_recalibrate calibration_report", calibration_report)
            _log_file_state("After day13_recalibrate recalibration_plan", recalibration_plan)


if __name__ == "__main__":
    main()
