#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import json
import time
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moneybot.services.ops_runtime import atomic_write_text, run_bounded, read_status, peak_rss_mib
from moneybot.services.runtime_paths import (
    day13_calibration_report_path,
    day13_recalibration_plan_path,
    resolve_runtime_dir,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger(__name__)


def _tail_text(path: Path, *, max_bytes: int = 12000) -> str:
    if not path.exists():
        return ""
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > max_bytes:
            fh.seek(max(0, size - max_bytes))
        data = fh.read(max_bytes)
    text = data.decode("utf-8", errors="replace").strip()
    if size > max_bytes and text:
        return f"... <truncated to last {max_bytes} bytes>\n{text}"
    return text


def _run_daily_ops_command(command: list[str], *, script_name: str, log_dir: Path) -> subprocess.CompletedProcess[str]:
    stdout_path = log_dir / f"{script_name}.stdout.log"
    stderr_path = log_dir / f"{script_name}.stderr.log"
    completed = run_bounded(command, timeout=600, log_paths=(stdout_path, stderr_path))
    if completed.returncode != 0:
        # Never forward report bodies or private decision records to logs/API.
        raise subprocess.CalledProcessError(completed.returncode, command,
                                            output="", stderr="stage subprocess failed")
    if script_name == "day13_calibration_report.py":
        try:
            metrics = json.loads(completed.stdout)
            safe = {key: value for key, value in metrics.items()
                    if isinstance(value, (int, float)) and key in {
                        "rows_scanned", "calibration_eligible_rows", "mature_fitted_rows",
                        "history_requests", "history_cache_size", "history_cache_points",
                        "history_cache_hits", "history_cache_misses", "history_cache_evictions",
                        "duration_seconds", "peak_rss_mib"}}
            LOGGER.info("CALIBRATION_METRICS %s", json.dumps(safe))
        except (ValueError, AttributeError):
            pass
    return completed


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
    parser.add_argument("--outcomes-limit", type=int, default=5000)
    parser.add_argument("--outcomes-rows-limit", type=int, default=20)
    parser.add_argument("--calibration-limit", type=int, default=5000)
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

    log_dir = base_dir / "daily_ops_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    results = []
    dependencies = {
        "day13_recalibrate.py": {"day13_calibration_report.py"},
        "autofill_daily_report.py": {"day7_decision_log_summary.py", "day12_materialize_outcomes.py",
                                    "day13_calibration_report.py", "day13_recalibrate.py"},
    }
    status_path = base_dir / "daily_ops_status.json"
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="daily-ops-", dir=str(log_dir)) as tmp_log_dir:
        for command in commands:
            script_name = Path(command[1]).name
            failed_dependencies = [r["stage"] for r in results if r["status"] not in {"success", "insufficient_data"}
                                   and r["stage"] in dependencies.get(script_name, set())]
            result = {"stage": script_name, "status": "skipped" if failed_dependencies else "success",
                      "artifact_status": "stale_artifact", "returncode": None}
            output = Path(command[command.index("--output") + 1]) if "--output" in command else None
            if output is None:
                result["artifact_status"] = "not_applicable"
            before = output.stat().st_mtime_ns if output and output.exists() else None
            stage_started = time.monotonic()
            if failed_dependencies:
                result["reason"] = "prerequisite_failed"
            else:
                try:
                    _run_daily_ops_command(command, script_name=script_name, log_dir=Path(tmp_log_dir))
                    result["returncode"] = 0
                    if output:
                        after = output.stat().st_mtime_ns if output.exists() else None
                        if after is None or after == before:
                            result["status"] = "stale_artifact"
                        else:
                            result["artifact_status"] = "fresh"
                            result["artifact_modified_at"] = output.stat().st_mtime
                            if script_name == "day13_calibration_report.py" and read_status(output).get("rows", 0) < 30:
                                result["status"] = "insufficient_data"
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
                    result.update(status="failure", returncode=getattr(exc, "returncode", None),
                                  reason="timeout" if isinstance(exc, subprocess.TimeoutExpired) else "subprocess_failed")
                    result["signal"] = -exc.returncode if getattr(exc, "returncode", 0) < 0 else None
                    LOGGER.error("DAILY_OPS_FAILED script=%s returncode=%s", script_name, result["returncode"])
                    LOGGER.error("failed_script=%s failed_command=%s stdout_tail=%s stderr_tail=%s",
                                 script_name, " ".join(command), "suppressed", "timeout" if isinstance(exc, subprocess.TimeoutExpired) else "stage subprocess failed")
            result["duration_seconds"] = round(time.monotonic() - stage_started, 3)
            results.append(result)
            failed = [r["stage"] for r in results if r["status"] in {"failure", "stale_artifact", "skipped"}]
            payload = {"workflow": "daily_ops", "started_at": started, "stages": results,
                       "success": not failed, "failed_stage": failed[0] if failed else None,
                       "status": "degraded" if failed else ("insufficient_data" if any(r["status"] == "insufficient_data" for r in results) else "success"),
                       "duration_seconds": round(time.time() - started, 3), "peak_rss_mib": peak_rss_mib()}
            atomic_write_text(status_path, json.dumps(payload))
            LOGGER.info("OPS_STAGE %s", json.dumps(result))
    if not payload["success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
