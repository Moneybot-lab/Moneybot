"""Standard-library-only deadline owner that survives a killed HTTP worker.

Invoked by ops_runtime in a new process group. All descendants inherit that group
and the flock descriptor. Do not import application or market-data modules here.
"""
import os
import signal
import subprocess
import sys


def main():
    timeout = float(sys.argv[1])
    os.environ["MONEYBOT_OPS_PROCESS_GROUP"] = str(os.getpgrp())
    descriptor = os.environ.get("MONEYBOT_OPS_LOCK_FD")
    child = subprocess.Popen(sys.argv[2:], pass_fds=(int(descriptor),) if descriptor else ())
    try:
        code = child.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgrp(), signal.SIGKILL)
        return 124
    if code < 0:
        os.kill(os.getpid(), -code)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
