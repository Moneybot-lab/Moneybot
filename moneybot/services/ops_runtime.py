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


def run_bounded(command, *, cwd=None, timeout=660, capture_output=True, text=True,
                check=False, pass_fds=(), log_paths=None):
    """Drain both pipes continuously, retaining at most 256 KiB per stream.

    Nested ops inherit the top-level process group and lock descriptor. A timeout
    at the API boundary kills the entire group, including nested weekly stages.
    """
    nested = os.environ.get("MONEYBOT_OPS_PROCESS_GROUP") == str(os.getpgrp())
    env = dict(os.environ)
    # Top-level deadlines survive abrupt Gunicorn worker death.
    launcher = [] if nested else [sys.executable, str(Path(__file__).with_name("ops_supervisor.py")), str(timeout + 1)]
    inherited_fd = os.environ.get("MONEYBOT_OPS_LOCK_FD")
    if inherited_fd and not pass_fds:
        pass_fds = (int(inherited_fd),)
    if pass_fds:
        env["MONEYBOT_OPS_LOCK_FD"] = str(pass_fds[0])
    proc = subprocess.Popen(launcher + list(command), cwd=cwd, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=not nested, pass_fds=pass_fds)
    buffers = [deque(maxlen=32), deque(maxlen=32)]
    def drain(pipe, chunks):
        try:
            while chunk := pipe.read(8192):
                chunks.append(chunk)
        finally:
            pipe.close()
    threads = [threading.Thread(target=drain, args=(pipe, chunks), daemon=True)
               for pipe, chunks in zip((proc.stdout, proc.stderr), buffers)]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except BaseException as exc:
        timed_out = isinstance(exc, subprocess.TimeoutExpired)
        # Nested wrappers terminate their own child; the outer deadline is the
        # final group-wide kill. Stage executables do not spawn detached sessions.
        try:
            if nested:
                proc.kill()
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        if not timed_out:
            raise
    finally:
        if not nested:
            # Also reap descendants left behind after an early wrapper exit.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for thread in threads:
            thread.join(timeout=2)
    output = [b"".join(chunks).decode("utf-8", errors="replace") for chunks in buffers]
    if log_paths:
        for path, value in zip(log_paths, output):
            Path(path).write_text(value, encoding="utf-8")
    if timed_out:
        raise subprocess.TimeoutExpired(command, timeout, output=output[0], stderr=output[1])
    result = subprocess.CompletedProcess(command, proc.returncode, *output)
    if check:
        result.check_returncode()
    return result


def read_status(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}
