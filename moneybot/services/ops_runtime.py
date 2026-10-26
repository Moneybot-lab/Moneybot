"""Local ops primitives: atomic artifacts, bounded output and process cleanup."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
from collections import deque
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as fh:
            name = fh.name
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def peak_rss_mib():
    try:
        import resource
        import sys
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1048576 if sys.platform == "darwin" else 1024), 2)
    except (ImportError, ValueError):
        return None


class ProcessCleanupError(RuntimeError):
    """Cleanup was not confirmed: abort orchestration, retaining the live fence."""

    def __init__(self, *, timed_out=False):
        super().__init__("subprocess tree cleanup could not be confirmed")
        self.timed_out = timed_out


def run_bounded(command, *, cwd=None, timeout=660, capture_output=True, text=True,
                check=False, pass_fds=(), log_paths=None):
    """Bound output and wait for a subreaper to finish whole-tree cleanup.

    Every stage has a supervisor. The outer supervisor discovers all descendants
    by ancestry, even if a nested stage starts its own session. Only a confirmed
    clean supervisor exit permits the caller to continue with another stage.
    """
    from .ops_supervisor import CLEANUP_WAIT_SECONDS

    env = dict(os.environ)
    inherited_fd = os.environ.get("MONEYBOT_OPS_LOCK_FD")
    if inherited_fd and not pass_fds:
        pass_fds = (int(inherited_fd),)
    if pass_fds:
        env["MONEYBOT_OPS_LOCK_FD"] = str(pass_fds[0])
    buffers = [deque(maxlen=32), deque(maxlen=32)]
    def drain(pipe, chunks):
        try:
            while chunk := pipe.read(8192):
                chunks.append(chunk)
        finally:
            pipe.close()
    with tempfile.TemporaryFile() as receipt:
        launcher = [sys.executable, str(Path(__file__).with_name("ops_supervisor.py")),
                    str(timeout), str(receipt.fileno())]
        proc = subprocess.Popen(launcher + list(command), cwd=cwd, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True,
                                pass_fds=(*pass_fds, receipt.fileno()))
        threads = [threading.Thread(target=drain, args=(pipe, chunks), daemon=True)
                   for pipe, chunks in zip((proc.stdout, proc.stderr), buffers)]
        for thread in threads:
            thread.start()
        timed_out = False
        interrupted = None
        try:
            proc.wait(timeout=timeout)
        except BaseException as exc:
            timed_out = isinstance(exc, subprocess.TimeoutExpired)
            interrupted = None if timed_out else exc
            proc.terminate()  # Ask the supervisor; never kill its cleanup owner.
            try:
                proc.wait(timeout=CLEANUP_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                raise ProcessCleanupError(timed_out=timed_out) from None
        finally:
            # A stuck supervisor retains the lock and keeps draining bounded
            # output. Normal cleanup closes every descendant's pipe handles.
            if proc.poll() is not None:
                for thread in threads:
                    thread.join(timeout=1)
            else:
                # Reap our direct supervisor if quarantine later succeeds,
                # without extending the caller's bounded cleanup wait.
                threading.Thread(target=proc.wait, daemon=True).start()
        receipt.seek(0)
        try:
            result = json.loads(receipt.read(4096))
        except (ValueError, OSError):
            result = {}
        if not result.get("cleanup_confirmed") or any(thread.is_alive() for thread in threads):
            raise ProcessCleanupError(timed_out=timed_out or result.get("timed_out", False))
    output = [b"".join(chunks).decode("utf-8", errors="replace") for chunks in buffers]
    if log_paths:
        for path, value in zip(log_paths, output):
            Path(path).write_text(value, encoding="utf-8")
    if interrupted is not None:
        raise interrupted
    if timed_out or result["timed_out"]:
        raise subprocess.TimeoutExpired(command, timeout, output=output[0], stderr=output[1])
    completed = subprocess.CompletedProcess(command, result["returncode"], *output)
    if check:
        completed.check_returncode()
    return completed


def read_status(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}
