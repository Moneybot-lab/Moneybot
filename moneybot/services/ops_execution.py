"""Single-instance execution fence shared by all Gunicorn workers and both jobs.

The lock and SQLite receipts live on the existing persistent runtime disk. Receipts
are retained seven days. An interrupted request is never automatically replayed.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

from .ops_runtime import read_status, run_bounded, ProcessCleanupError


def execute_ops(command, *, workflow, runtime_dir, cwd, timeout, idempotency_key, runner=run_bounded):
    runtime_dir = Path(runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(f"{workflow}:{idempotency_key}".encode()).hexdigest()
    with (runtime_dir / "ops_execution.lock").open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"success": False, "status": "busy", "failed_stage": "execution_lock"}, 409
        with sqlite3.connect(runtime_dir / "ops_receipts.sqlite") as db:
            db.execute("CREATE TABLE IF NOT EXISTS receipts (key TEXT PRIMARY KEY, created REAL, response TEXT, code INTEGER)")
            db.execute("DELETE FROM receipts WHERE created < ?", (time.time() - 7 * 86400,))
            existing = db.execute("SELECT response, code FROM receipts WHERE key = ?", (key,)).fetchone()
            if existing:
                response, code = existing
                data = json.loads(response)
                data["replayed"] = True
                return data, code
            pending = {"success": False, "status": "interrupted_or_running", "failed_stage": "execution", "returncode": None}
            db.execute("INSERT INTO receipts VALUES (?, ?, ?, ?)", (key, time.time(), json.dumps(pending), 409))
            db.commit()  # Fence retries even if the request worker is killed.
            started = time.time()
            try:
                completed = runner(command, cwd=cwd, timeout=timeout, pass_fds=(lock.fileno(),))
                data = {"success": completed.returncode == 0, "returncode": completed.returncode,
                        "exit_code": completed.returncode, "stdout": "", "stderr": "",
                        "signal": -completed.returncode if completed.returncode < 0 else None}
                code = 200 if data["success"] else 500
            except ProcessCleanupError as exc:
                data = {"success": False, "returncode": -1, "stdout": "", "stderr": "subprocess tree cleanup unconfirmed", "status": "failure", "failed_stage": "execution_cleanup", "cleanup_status": "unconfirmed", "timed_out": exc.timed_out}
                code = 500
            except subprocess.TimeoutExpired:
                data = {"success": False, "returncode": -1, "stdout": "", "stderr": "server execution timeout", "status": "failure", "failed_stage": "execution_timeout"}
                code = 504
            except Exception:
                data = {"success": False, "returncode": -1, "stdout": "", "stderr": "could not execute ops subprocess", "status": "failure", "failed_stage": "execution"}
                code = 500
            execution_failed = not data["success"]
            execution_failure_stage = data.get("failed_stage")
            status = read_status(runtime_dir / f"{workflow}_status.json")
            if status.get("started_at", 0) >= started:
                data.update({name: status.get(name) for name in ("stages", "failed_stage", "status")})
                if execution_failed and status.get("success"):
                    data.update(status="degraded", failed_stage=execution_failure_stage or "execution")
                if not status.get("success"):
                    data["success"] = False
                    code = 500 if code == 200 else code
            else:
                data.update(status="failure", failed_stage=data.get("failed_stage") or "missing_fresh_stage_status", success=False)
                code = 500 if code == 200 else code
            data["executed_at"] = started
            data["duration_seconds"] = round(time.time() - started, 3)
            # Keep terminal results, including failures: same key must never rerun.
            db.execute("UPDATE receipts SET response=?, code=? WHERE key=?", (json.dumps(data), code, key))
            db.commit()
            return data, code
