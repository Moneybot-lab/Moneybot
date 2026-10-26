"""Linux, standard-library-only owner of a stage's entire descendant tree.

Every boundary is a subreaper, including the outer job boundary. An orphan is
adopted by its nearest living supervisor, even after setsid()/double-fork. Never
signal a process group: an outer deadline discovers nested groups by ancestry.
"""
import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

TERM_GRACE_SECONDS = 1.0
KILL_GRACE_SECONDS = 3.0
CLEANUP_WAIT_SECONDS = TERM_GRACE_SECONDS + KILL_GRACE_SECONDS + 2.0


def _pidfd_open(pid):
    if hasattr(os, "pidfd_open"):
        return os.pidfd_open(pid)
    libc = ctypes.CDLL(None, use_errno=True)
    fd = libc.pidfd_open(pid, 0)
    if fd < 0:
        raise OSError(ctypes.get_errno(), "pidfd_open failed")
    return fd


def _pidfd_signal(fd, signum):
    if hasattr(signal, "pidfd_send_signal"):
        signal.pidfd_send_signal(fd, signum)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.pidfd_send_signal(fd, signum, None, 0) < 0:
        raise OSError(ctypes.get_errno(), "pidfd_send_signal failed")


def _enable_subreaper():
    if not sys.platform.startswith("linux"):
        raise RuntimeError("ops supervision requires Linux subreapers and pidfds")
    fd = _pidfd_open(os.getpid())
    try:
        _pidfd_signal(fd, 0)
    finally:
        os.close(fd)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), "could not enable child subreaper")


def _identity(pid):
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return int(fields[1]), fields[19], fields[0]  # ppid, start time, state


def _descendants():
    processes = {}
    for path in Path("/proc").iterdir():
        if path.name.isdecimal():
            try:
                processes[int(path.name)] = _identity(int(path.name))
            except (FileNotFoundError, ProcessLookupError):
                pass
    owned = {os.getpid()}
    while True:
        added = {pid for pid, (ppid, _, _) in processes.items() if ppid in owned} - owned
        if not added:
            break
        owned.update(added)
    # Zombies cannot execute. waitpid below reaps adopted/direct children; other
    # zombies are reparented to us when their still-living parent terminates.
    return {pid: processes[pid][1] for pid in owned - {os.getpid()}
            if processes[pid][2] != "Z"}


def _signal_identity(pid, start, signum):
    """Pin the PID before signaling; reject reuse between discovery and open."""
    try:
        fd = _pidfd_open(pid)
        try:
            if _identity(pid)[1] == start:
                _pidfd_signal(fd, signum)
        finally:
            os.close(fd)
    except (FileNotFoundError, ProcessLookupError):
        pass


def _reap(child):
    while True:
        try:
            pid, status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return True
        if pid == 0:
            return False
        if pid == child.pid:
            child.returncode = os.waitstatus_to_exitcode(status)


def _cleanup(child):
    """Graceful then forced cleanup; rescan to catch forks and reparenting."""
    started = time.monotonic()
    terminated = set()
    while True:
        _reap(child)
        owned = _descendants()
        if not owned and _reap(child):
            # ECHILD, not a single racy /proc snapshot, proves our adopted and
            # direct children are gone. This supervisor alone creates children.
            return True
        elapsed = time.monotonic() - started
        if elapsed >= TERM_GRACE_SECONDS + KILL_GRACE_SECONDS:
            return False
        for pid, birth in owned.items():
            if elapsed >= TERM_GRACE_SECONDS:
                _signal_identity(pid, birth, signal.SIGSTOP)
                _signal_identity(pid, birth, signal.SIGKILL)
            elif (pid, birth) not in terminated:
                _signal_identity(pid, birth, signal.SIGTERM)
                _signal_identity(pid, birth, signal.SIGCONT)
                terminated.add((pid, birth))
        time.sleep(.01)


def _report(fd, **result):
    value = json.dumps(result).encode()
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    os.write(fd, value)


def main():
    timeout, result_fd = float(sys.argv[1]), int(sys.argv[2])
    stop = False
    def request_stop(signum, frame):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    descriptor = os.environ.get("MONEYBOT_OPS_LOCK_FD")
    try:
        _enable_subreaper()  # Fail before spawning anything if unsupported.
        child = subprocess.Popen(sys.argv[3:], pass_fds=(int(descriptor),) if descriptor else ())
    except Exception:
        _report(result_fd, returncode=125, timed_out=False, cleanup_confirmed=True, setup_failed=True)
        return 125
    deadline = time.monotonic() + timeout
    timed_out = False
    try:
        while child.returncode is None:
            _reap(child)
            if child.returncode is not None:
                break
            if stop or time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(.01)
    finally:
        cleanup_failed = False
        while True:
            try:
                clean = _cleanup(child)
            except Exception:
                clean = False
            if clean:
                break
            cleanup_failed = True
            _report(result_fd, returncode=child.returncode, timed_out=timed_out,
                    cleanup_confirmed=False)
            # Fail closed: the caller reports cleanup failure and must abort its
            # bundle. Keep the inherited flock while retrying unkillable work.
            time.sleep(.1)
    _report(result_fd, returncode=child.returncode, timed_out=timed_out,
            cleanup_confirmed=not cleanup_failed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
