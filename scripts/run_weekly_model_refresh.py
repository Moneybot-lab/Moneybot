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

from moneybot.services.runtime_paths import resolve_runtime_dir, historical_validation_report_path, historical_validation_evidence_path
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
        [python_executable, str(scripts_dir / "materialize_validation_evidence.py"), "--input", input_log],
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
                if stage == "materialize_validation_evidence.py" and completed.returncode == 0:
                    path = historical_validation_evidence_path()
                    if not path.exists() or path.stat().st_mtime < stage_started_wall:
                        result.update(status="failure", reason="stale_evidence_artifact")
                if stage == "page6_historical_validation_report.py" and completed.returncode == 0:
                    path = historical_validation_report_path()
                    if not path.exists() or path.stat().st_mtime < stage_started_wall:
                        result.update(status="failure", reason="stale_artifact")
                    else:
                        report = read_status(path)
                        gates = report.get("promotion_gates", {})
                        if report.get("execution_status") != "success" or report.get("validation_status") not in {"passed", "insufficient_evidence", "blocked"} or not isinstance(gates.get("gates"), list):
                            raise ValueError("invalid_validation_report")
                        result["execution_status"] = "success"
                        result["validation_status"] = report["validation_status"]
                        result["artifact_status"] = "fresh"
                        result["promotion_ready"] = gates.get("promotion_ready") is True
                        minimum_gate = next((gate for gate in gates.get("gates", []) if gate.get("name") == "minimum_evaluated_rows"), {})
                        if not minimum_gate.get("passed"):
                            result.update(status="insufficient_data", reason="insufficient_validation_evidence")
                        elif not result["promotion_ready"]:
                            result.update(status="degraded", reason="model_validation_blocked")
            except (subprocess.TimeoutExpired, OSError, ValueError, ProcessCleanupError) as exc:
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
        execution_failed = any(r["status"] == "failure" for r in results)
        validation = next((r for r in results if r["stage"] == "page6_historical_validation_report.py"), {})
        payload.update(execution_status="failed" if execution_failed else ("success" if validation.get("execution_status") == "success" else "degraded"),
                       validation_status=validation.get("validation_status", "unavailable"),
                       promotion_ready=validation.get("promotion_ready") is True)
        atomic_write_text(base_dir / "weekly_refresh_status.json", json.dumps(payload))
        print(json.dumps(result))
    if not payload["success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
