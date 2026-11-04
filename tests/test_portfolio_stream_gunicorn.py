"""Local Unix-socket WSGI smoke test; no TCP or paid provider requests."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

from flask import Flask
import pytest


def test_gunicorn_flushes_snapshot_and_keeps_lightweight_requests_responsive(tmp_path):
    gunicorn = Path(sys.executable).parent / "gunicorn"
    # The environment's venv entry point can differ from the resolved interpreter.
    if not gunicorn.exists(): gunicorn = Path(".venv/bin/gunicorn").resolve()
    curl = shutil.which("curl")
    if not gunicorn.exists() or not curl:
        pytest.skip("Gunicorn and curl required for local WSGI smoke")
    root = Path(__file__).resolve().parents[1]
    socket_path = tmp_path / "web.sock"
    attempt_path = tmp_path / "attempts.txt"
    (tmp_path / "smoke_server.py").write_text('''
import tempfile
from pathlib import Path
import requests
from scripts.benchmark_portfolio_watchlist import scenario
_directory = tempfile.TemporaryDirectory()
_fixture = scenario(_directory.name, 5, 2, "success", market_delay=.005, ai_delay=.25)
_state = _fixture.__enter__()
app = _state.app
_original_post = requests.post
def recorded_post(*args, **kwargs):
    with Path(ATTEMPT_PATH).open("a") as handle:
        handle.write("attempt\\n")
    return _original_post(*args, **kwargs)
requests.post = recorded_post
'''.replace("ATTEMPT_PATH", repr(str(attempt_path))))
    app = Flask("synthetic-cookie")
    app.secret_key = "synthetic-only"
    cookie = app.session_interface.get_signing_serializer(app).dumps({"user_id": 1})
    common = [curl, "--silent", "--show-error", "--no-buffer", "--max-time", "10",
              "--unix-socket", str(socket_path), "-H", f"Cookie: session={cookie}"]
    with (tmp_path / "server.log").open("w") as log:
        server = subprocess.Popen([str(gunicorn), "--chdir", str(tmp_path), "--pythonpath", str(root),
                                   "--workers", "2", "--threads", "4", "--bind", f"unix:{socket_path}",
                                   "smoke_server:app"], stdout=log, stderr=log, start_new_session=True)
        stream = None
        try:
            deadline = time.monotonic() + 10
            while not socket_path.exists() and time.monotonic() < deadline:
                assert server.poll() is None
                time.sleep(.02)
            assert socket_path.exists()
            # DB-only readiness check; no market or AI calls.
            ready = subprocess.run(common + ["http://localhost/api/user-watchlist?skip_market_data=1"],
                                   text=True, capture_output=True, check=True, timeout=12)
            assert len(json.loads(ready.stdout)["items"]) == 5
            start = time.monotonic()
            stream = subprocess.Popen(common + ["http://localhost/api/user-watchlist/stream"], stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True)
            first = json.loads(stream.stdout.readline())
            initial = time.monotonic() - start
            assert first["type"] == "snapshot"
            assert all(row["enrichment_state"] == "pending" for row in first["enriched_items"])
            other_start = time.monotonic()
            busy = subprocess.run(common + ["-o", "/dev/null", "-w", "%{http_code}",
                                            "http://localhost/api/user-watchlist/stream"],
                                  text=True, capture_output=True, check=True, timeout=12)
            assert busy.stdout == "429"  # Same-account fence across both workers.
            lightweight = subprocess.run(common + ["http://localhost/api/user-watchlist?skip_market_data=1"],
                                         text=True, capture_output=True, check=True, timeout=12)
            assert len(json.loads(lightweight.stdout)["items"]) == 5
            assert time.monotonic() - other_start < .75
            rest = [json.loads(line) for line in stream.stdout]
            complete = time.monotonic() - start
            assert stream.wait(timeout=2) == 0
            assert [e["type"] for e in rest] == ["advice"] * 5 + ["complete"]
            assert complete - initial >= 1.0  # 5 x .25s provider work after first flush.
            assert len(attempt_path.read_text().splitlines()) == 5

            # Disconnect after the next snapshot. The current provider request
            # may finish before WSGI detects EPIPE; the remaining lots must stop.
            stream = subprocess.Popen(common + ["http://localhost/api/user-watchlist/stream"],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            assert json.loads(stream.stdout.readline())["type"] == "snapshot"
            stream.terminate()
            stream.communicate(timeout=3)
            from moneybot.services.portfolio_enrichment import admit
            deadline = time.monotonic() + 3
            release = None
            while time.monotonic() < deadline:
                release = admit(1)
                if release is not None:
                    release()
                    break
                time.sleep(.02)
            assert release is not None
            after_disconnect = len(attempt_path.read_text().splitlines()) - 5
            assert 0 <= after_disconnect <= 2
            time.sleep(.35)
            assert len(attempt_path.read_text().splitlines()) == 5 + after_disconnect
            print("WSGI_SMOKE", json.dumps(dict(initial_seconds=initial, complete_seconds=complete,
                  disconnect_additional_attempts=after_disconnect, worker_count=2, threads_per_worker=4)))
        finally:
            if stream is not None and stream.poll() is None:
                stream.terminate()
                stream.communicate(timeout=3)
            try:
                os.killpg(server.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(server.pid, signal.SIGKILL)
                server.wait(timeout=3)
