#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moneybot.services.runtime_paths import resolve_runtime_dir, historical_validation_report_path
from moneybot.services.ops_runtime import run_bounded, atomic_write_text, read_status, ProcessCleanupError


def build_weekly_refresh_commands(
    *,
    python_executable: str,
    project_root: Path,
    input_log: str,
) -> list[list[str]]:
    scripts_dir = project_root / "scripts"
    return [
        [python_executable, str(scripts_dir / "day1_refresh_artifact.py")],
        [python_executable, str(scripts_dir / "run_daily_ops.py"), "--outcomes-rows-limit", "200", "--input-log", input_log, "--skip-day1-refresh"],
        [python_executable, str(scripts_dir / "page6_historical_validation_report.py")],
    ]


def main() -> None:
    base_dir = resolve_runtime_dir()
    parser = argparse.ArgumentParser(description="Run weekly model refresh + daily ops bundle.")
    parser.add_argument("--input-log", default=str(base_dir / "decision_events.jsonl"))
    args = parser.parse_args()

    project_root = PROJECT_ROOT
    commands = build_weekly_refresh_commands(
        python_executable=sys.executable,
        project_root=project_root,
        input_log=args.input_log,
    )
    results = []
    started = time.time()
    for command in commands:
        stage = Path(command[1]).name
        result = {"stage": stage, "status": "skipped", "returncode": None}
        stage_started = time.monotonic()
        stage_started_wall = time.time()
        if not any(r["status"] in {"failure", "degraded", "insufficient_data"} for r in results):
            try:
                completed = run_bounded(command, timeout=660)
                result.update(status="success" if completed.returncode == 0 else "failure", returncode=completed.returncode)
                if stage == "run_daily_ops.py":
                    daily = read_status(base_dir / "daily_ops_status.json")
                    result["daily_stages"] = daily.get("stages", [])
                    result["failed_stage"] = daily.get("failed_stage")
                    if daily.get("started_at", 0) < stage_started_wall or not daily.get("success"):
                        result["status"] = "failure"
                if stage == "page6_historical_validation_report.py":
                    path = historical_validation_report_path()
                    if not path.exists() or path.stat().st_mtime < stage_started_wall:
                        result.update(status="failure", reason="stale_artifact")
                    else:
                        report = read_status(path)
                        gates = report.get("promotion_gates", {})
                        result["artifact_status"] = "fresh"
                        result["promotion_ready"] = gates.get("promotion_ready") is True
                        minimum_gate = next((gate for gate in gates.get("gates", []) if gate.get("name") == "minimum_evaluated_rows"), {})
                        if not minimum_gate.get("passed"):
                            result.update(status="insufficient_data", reason="insufficient_validation_evidence")
                        elif not result["promotion_ready"]:
                            result.update(status="degraded", reason="model_validation_blocked")
            except (subprocess.TimeoutExpired, OSError, ProcessCleanupError) as exc:
                result.update(status="failure", reason="timeout" if isinstance(exc, subprocess.TimeoutExpired) else "execution_error")
                if isinstance(exc, ProcessCleanupError):
                    result.update(reason="timeout" if exc.timed_out else "execution_error", cleanup_status="unconfirmed")
        result["duration_seconds"] = round(time.monotonic() - stage_started, 3)
        results.append(result)
        failed = next((r.get("failed_stage") or r["stage"] for r in results if r["status"] in {"failure", "degraded", "insufficient_data"}), None)
        insufficient = any(r["status"] == "insufficient_data" for r in results)
        payload = {"workflow": "weekly_refresh", "started_at": started, "stages": results,
                   "success": failed is None and not insufficient, "failed_stage": failed,
                   "status": "insufficient_data" if insufficient else ("degraded" if failed else "success")}
        atomic_write_text(base_dir / "weekly_refresh_status.json", json.dumps(payload))
        print(json.dumps(result))
    if not payload["success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
