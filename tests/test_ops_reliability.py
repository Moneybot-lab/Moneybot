import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pandas as pd
import pytest

from moneybot.services.decision_log import iter_decision_events
from moneybot.services.ops_execution import execute_ops
from moneybot.services.ops_runtime import atomic_write_text, run_bounded
from moneybot.services.outcome_tracking import OutcomeHistoryCache
from scripts import day13_calibration_report as calibration


@pytest.fixture(autouse=True)
def isolated_calibration_globals(monkeypatch):
    monkeypatch.setattr(calibration, "_outcome_history_download", None)
    monkeypatch.setattr(calibration, "_outcome_history_cache", None)
    calibration._future_return.cache_clear()


def event(**changes):
    base = {"symbol": "AAPL", "ts": 1704204000, "endpoint": "quick_ask",
            "payload": {"probability_up": .7, "forecast_horizon": "5d", "model_version": "alpha-atlas-v2", "features": {"rsi": 45}},
            "snapshot": {"unused": "x" * 1024}}
    base.update(changes)
    return base


def test_newest_limit_counts_blank_and_malformed_lines(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"ts":1}\n{bad}\n\n{"ts":2}\n')
    assert list(iter_decision_events(str(path), limit=3)) == [{"ts": 2}]
    assert list(iter_decision_events(str(path), limit=0)) == []
    assert list(iter_decision_events(str(path), limit=4)) == [{"ts": 1}, {"ts": 2}]


@pytest.mark.parametrize("count", [0, 20000, 50000])
def test_large_no_eligible_log_preserves_scan_cap_and_never_downloads(tmp_path, monkeypatch, count):
    path = tmp_path / "events.jsonl"
    with path.open("w") as fh:
        line = json.dumps(event(payload={"probability_up": None, "forecast_horizon": "5d"})) + "\n"
        for _ in range(count):
            fh.write(line)
    class NoDownload:
        massive_requests = 0
        def __call__(self, *args, **kwargs):
            pytest.fail("ineligible records must not fetch history")
        def provider_for_symbol(self, symbol):
            return "test"
        def diagnostics_payload(self):
            return {}
    monkeypatch.setattr(calibration, "MassivePreferredHistoryDownload", NoDownload)
    output = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["calibration", "--input", str(path), "--output", str(output), "--limit", "5000"])
    calibration.main()
    report = json.loads(output.read_text())
    assert report["rows_scanned"] == count
    assert report["scan_limit_used"] == report["scan_cap"] == 50000
    assert report["status"] == "insufficient_data"
    assert report["rows"] == 0
    assert report["brier_score"] is None


def test_projection_preserves_calibration_math_and_all_diagnostics(monkeypatch):
    events = [event(), event(payload={"probability_up": None, "forecast_horizon": "5d"}),
              event(payload={"probability_up": .6, "forecast_horizon": "5d", "provider": "portfolio_quote_only"}),
              event(ts=int(time.time())), event(payload={"probability_up": .2, "forecast_horizon": "1d"})]
    monkeypatch.setattr(calibration, "_future_return", lambda *args: .1)
    projected = [calibration.compact_calibration_event(row) for row in events]
    assert all("snapshot" not in row for row in projected)
    assert calibration.calibration_input_profile(events) == calibration.calibration_input_profile(projected)
    assert calibration.calibration_input_segments(events) == calibration.calibration_input_segments(projected)
    original = calibration.calibration_rows_from_events(events)
    compact = calibration.calibration_rows_from_events(projected)
    assert original == compact
    assert len(compact) == 1
    assert calibration.calibration_summary(original) == calibration.calibration_summary(compact)
    assert calibration.segmented_calibration_summaries(original) == calibration.segmented_calibration_summaries(compact)


def test_provider_failures_preserve_last_good_calibration(tmp_path, monkeypatch):
    class Broken:
        massive_requests = 1
        def __call__(self, *args, **kwargs):
            raise RuntimeError("provider unavailable")
        def provider_for_symbol(self, symbol):
            return "test"
        def diagnostics_payload(self):
            return {}
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps(event()) + "\n")
    output = tmp_path / "report.json"
    output.write_text('{"last":"valid"}')
    monkeypatch.setattr(calibration, "MassivePreferredHistoryDownload", Broken)
    monkeypatch.setattr(sys, "argv", ["calibration", "--input", str(path), "--output", str(output), "--read-cap", "2", "--limit", "1"])
    with pytest.raises(SystemExit, match="1"):
        calibration.main()
    assert output.read_text() == '{"last":"valid"}'
    assert json.loads(output.with_suffix(".failed.json").read_text())["status"] == "degraded"


def test_cache_reuses_history_extends_older_ranges_and_bounds_memory():
    calls = []
    def download(symbol, *, start, end, **kwargs):
        calls.append((symbol, start))
        return pd.DataFrame({"Close": range(100, 110)}, index=pd.date_range(start, periods=10))
    cache = OutcomeHistoryCache(download=download, max_cache_entries=2, max_cache_points=20)
    cache.plan_events([event()])
    assert cache.future_return("AAPL", event()["ts"], 5) == .05
    assert cache.future_return("AAPL", event()["ts"], 5) == .05
    assert len(calls) == 1
    cache.plan_events([event(ts=event()["ts"] - 86400)])
    assert cache.future_return("AAPL", event()["ts"] - 86400, 5) == .05
    assert len(calls) == 2
    for symbol in ("B", "C", "D"):
        cache.future_return(symbol, event()["ts"], 5)
    assert cache.cache_size <= 2
    assert cache.cache_points <= 20
    assert cache.cache_evictions > 0


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, 0])
def test_invalid_prices_are_not_calibration_observations(bad):
    cache = OutcomeHistoryCache(download=lambda *args, **kwargs: pd.DataFrame(
        {"Close": [100, bad, 102, 103, 104, 105, 106]}, index=pd.date_range("2024-01-02", periods=7)))
    assert cache.future_return("AAPL", event()["ts"], 5) is None


def test_atomic_write_failure_preserves_previous_artifact(tmp_path, monkeypatch):
    target = tmp_path / "report.json"
    target.write_text("previous valid report")
    def fail(*args):
        raise OSError("replace failed")
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        atomic_write_text(target, "new report")
    assert target.read_text() == "previous valid report"
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("code", [1, -9])
def test_subprocess_exit_and_signal_diagnostics(code):
    script = "import os,signal; os.kill(os.getpid(), signal.SIGKILL)" if code == -9 else "raise SystemExit(1)"
    assert run_bounded([sys.executable, "-c", script]).returncode == code


def test_subprocess_output_is_bounded():
    result = run_bounded([sys.executable, "-c", "import sys; sys.stdout.write('x'*2000000); sys.stderr.write('y'*2000000)"])
    assert result.returncode == 0
    assert 0 < len(result.stdout) <= 262144
    assert 0 < len(result.stderr) <= 262144


def test_timeout_kills_descendant_and_releases_lock(tmp_path):
    pidfile = tmp_path / "pid"
    script = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); open(sys.argv[1],'w').write(str(p.pid)); time.sleep(60)"
    with pytest.raises(subprocess.TimeoutExpired):
        run_bounded([sys.executable, "-c", script, str(pidfile)], timeout=.5)
    pid = int(pidfile.read_text())
    for _ in range(20):
        stat = Path(f"/proc/{pid}/stat")
        if not stat.exists() or stat.read_text().split()[2] == "Z":
            break
        time.sleep(.01)
    else:
        pytest.fail("descendant still running after timeout")


def run_request(tmp_path, key, runner, workflow="daily_ops"):
    return execute_ops([sys.executable, "fake.py"], workflow=workflow, runtime_dir=tmp_path,
                       cwd=str(tmp_path), timeout=1, idempotency_key=key, runner=runner)


def success_runner(tmp_path, workflow="daily_ops", code=0):
    def run(*args, **kwargs):
        atomic_write_text(tmp_path / f"{workflow}_status.json", json.dumps(
            {"started_at": time.time(), "success": code == 0, "status": "success" if code == 0 else "degraded",
             "failed_stage": "calibration" if code else None, "stages": []}))
        return subprocess.CompletedProcess(args[0], code, "private-output", "private-error")
    return run


def test_shared_lock_blocks_concurrent_daily_and_weekly(tmp_path):
    entered, release = threading.Event(), threading.Event()
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return success_runner(tmp_path)(*args, **kwargs)
    thread = threading.Thread(target=run_request, args=(tmp_path, "one", slow))
    thread.start()
    assert entered.wait(5)
    try:
        data, code = run_request(tmp_path, "two", lambda *a, **k: pytest.fail("must not execute"), "weekly_refresh")
        assert code == 409 and data["status"] == "busy"
    finally:
        release.set()
        thread.join(5)


@pytest.mark.parametrize("first_code", [0, 1, -9])
def test_duplicate_receipts_include_failures_and_new_key_can_recover(tmp_path, first_code):
    data, code = run_request(tmp_path, "original", success_runner(tmp_path, code=first_code))
    replay, replay_code = run_request(tmp_path, "original", lambda *a, **k: pytest.fail("duplicate executed"))
    assert replay_code == code
    assert replay["replayed"] is True
    assert replay["success"] == (first_code == 0)
    assert replay["stdout"] == replay["stderr"] == ""
    recovered, code = run_request(tmp_path, "new", success_runner(tmp_path))
    assert code == 200 and recovered["success"]


def test_missing_fresh_status_cannot_claim_success(tmp_path):
    (tmp_path / "daily_ops_status.json").write_text('{"started_at":1,"success":true}')
    data, code = run_request(tmp_path, "new", lambda *a, **k: subprocess.CompletedProcess([], 0))
    assert code == 500 and not data["success"]
    assert data["failed_stage"] == "missing_fresh_stage_status"


def test_daily_calibration_failure_preserves_independent_results_and_skips_dependents(tmp_path, monkeypatch):
    from scripts import run_daily_ops as daily
    names = ["day13_calibration_report.py", "day7_decision_log_summary.py", "day13_recalibrate.py", "autofill_daily_report.py"]
    outputs = {name: tmp_path / (name + ".json") for name in names}
    for output in outputs.values():
        output.write_text('{"old":true}')
    commands = [[sys.executable, name, "--output", str(outputs[name])] for name in names]
    ran = []
    def run(command, **kwargs):
        name = command[1]
        ran.append(name)
        if name == names[0]:
            raise subprocess.CalledProcessError(-9, command)
        atomic_write_text(outputs[name], '{"new":true}')
    monkeypatch.setattr(daily, "resolve_runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(daily, "build_daily_ops_commands", lambda **k: commands)
    monkeypatch.setattr(daily, "_run_daily_ops_command", run)
    monkeypatch.setattr(sys, "argv", ["daily"])
    with pytest.raises(SystemExit, match="1"):
        daily.main()
    status = json.loads((tmp_path / "daily_ops_status.json").read_text())
    assert not status["success"]
    assert status["status"] == "degraded"
    assert status["failed_stage"] == names[0]
    assert [stage["status"] for stage in status["stages"]] == ["failure", "success", "skipped", "skipped"]
    assert status["stages"][0]["signal"] == 9
    assert ran == names[:2]
    assert outputs[names[0]].read_text() == '{"old":true}'
    assert outputs[names[1]].read_text() == '{"new":true}'
    assert outputs[names[2]].read_text() == '{"old":true}'


def test_weekly_propagates_daily_failed_stage_and_skips_validation(tmp_path, monkeypatch):
    from scripts import run_weekly_model_refresh as weekly
    calls = []
    def run(command, **kwargs):
        calls.append(Path(command[1]).name)
        if calls[-1] == "run_daily_ops.py":
            atomic_write_text(tmp_path / "daily_ops_status.json", json.dumps({
                "started_at": time.time(), "success": False, "failed_stage": "day13_calibration_report.py", "stages": []}))
            return subprocess.CompletedProcess(command, 1)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(weekly, "resolve_runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(weekly, "run_bounded", run)
    monkeypatch.setattr(sys, "argv", ["weekly"])
    with pytest.raises(SystemExit, match="1"):
        weekly.main()
    payload = json.loads((tmp_path / "weekly_refresh_status.json").read_text())
    assert not payload["success"]
    assert payload["failed_stage"] == "day13_calibration_report.py"
    assert payload["stages"][-1]["status"] == "skipped"
    assert calls == ["day1_refresh_artifact.py", "run_daily_ops.py"]


def test_scalar_outcome_cache_survives_scan_expansion(tmp_path, monkeypatch):
    class Empty:
        massive_requests = 0
        def __call__(self, *args, **kwargs):
            self.massive_requests += 1
            return pd.DataFrame()
        def provider_for_symbol(self, symbol):
            return "test"
        def diagnostics_payload(self):
            return {"massive_history_requests": self.massive_requests}
    path = tmp_path / "events.jsonl"
    path.write_text((json.dumps(event()) + "\n") * 20)
    output = tmp_path / "report.json"
    monkeypatch.setattr(calibration, "MassivePreferredHistoryDownload", Empty)
    monkeypatch.setattr(sys, "argv", ["calibration", "--input", str(path), "--output", str(output), "--limit", "5", "--read-cap", "20"])
    calibration.main()
    report = json.loads(output.read_text())
    assert report["rows_scanned"] == 20
    assert report["massive_history_requests"] == 1
    assert report["eligibility_diagnostics"]["rows_excluded_outcome_unavailable"] == 20


def test_interrupted_receipt_does_not_execute_again(tmp_path):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        run_request(tmp_path, "same", interrupted)
    payload, code = run_request(tmp_path, "same", lambda *a, **k: pytest.fail("interrupted request re-executed"))
    assert code == 409
    assert payload["status"] == "interrupted_or_running"


def test_worker_death_keeps_execution_locked_until_supervisor_deadline(tmp_path):
    marker = tmp_path / "child-started"
    script = '''
import sys
from pathlib import Path
from moneybot.services.ops_execution import execute_ops
command = [sys.executable, '-c', 'import pathlib,sys,time; pathlib.Path(sys.argv[1]).touch(); time.sleep(60)', sys.argv[2]]
execute_ops(command, workflow='daily_ops', runtime_dir=Path(sys.argv[1]), cwd=None, timeout=1, idempotency_key='worker-dies')
'''
    worker = subprocess.Popen([sys.executable, "-c", script, str(tmp_path), str(marker)])
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert marker.exists()
        worker.kill()
        worker.wait(timeout=2)
        data, code = run_request(tmp_path, "concurrent", lambda *a, **k: pytest.fail("orphan lock lost"), "weekly_refresh")
        assert code == 409 and data["status"] == "busy"
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            data, code = run_request(tmp_path, "recovered", success_runner(tmp_path))
            if code == 200:
                break
            time.sleep(.05)
        assert code == 200 and data["success"]
        data, code = run_request(tmp_path, "worker-dies", lambda *a, **k: pytest.fail("interrupted receipt repeated"))
        assert code == 409 and data["replayed"]
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait()


@pytest.mark.parametrize("gate_passed,minimum_passed,expected", [(True, True, "success"), (False, True, "degraded"), (False, False, "insufficient_data")])
def test_weekly_validation_gate_conclusion(tmp_path, monkeypatch, gate_passed, minimum_passed, expected):
    from scripts import run_weekly_model_refresh as weekly
    output = tmp_path / "validation.json"
    def run(command, **kwargs):
        name = Path(command[1]).name
        if name == "run_daily_ops.py":
            atomic_write_text(tmp_path / "daily_ops_status.json", json.dumps({"started_at": time.time(), "success": True, "stages": []}))
        if name == "page6_historical_validation_report.py":
            atomic_write_text(output, json.dumps({"promotion_gates": {"promotion_ready": gate_passed, "gates": [{"name": "minimum_evaluated_rows", "passed": minimum_passed}]}}))
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(weekly, "resolve_runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(weekly, "historical_validation_report_path", lambda: output)
    monkeypatch.setattr(weekly, "run_bounded", run)
    monkeypatch.setattr(sys, "argv", ["weekly"])
    if gate_passed:
        weekly.main()
    else:
        with pytest.raises(SystemExit, match="1"):
            weekly.main()
    status = json.loads((tmp_path / "weekly_refresh_status.json").read_text())
    assert status["success"] == gate_passed
    assert status["stages"][-1]["status"] == expected
    assert status["stages"][-1]["artifact_status"] == "fresh"
    assert status["stages"][-1]["promotion_ready"] == gate_passed


def test_abrupt_exit_after_partial_success_status_is_not_success(tmp_path):
    def run(*args, **kwargs):
        success_runner(tmp_path)(*args, **kwargs)
        return subprocess.CompletedProcess([], -9)
    payload, code = run_request(tmp_path, "killed", run)
    assert code == 500
    assert payload["success"] is False
    assert payload["status"] == "degraded"
    assert payload["failed_stage"] == "execution"
    assert payload["signal"] == 9


def test_evicted_preloaded_history_keeps_original_full_range():
    calls = []
    def download(symbol, *, start, end, **kwargs):
        calls.append((symbol, start, end))
        return pd.DataFrame({"Close": range(100, 200)}, index=pd.date_range(start, periods=100))
    cache = OutcomeHistoryCache(download=download, max_cache_entries=1)
    older = event()
    later = event(ts=event()["ts"] + 86400)
    cache.preload_events([older, later, event(symbol="OTHER")])
    first_range = next(call for call in calls if call[0] == "AAPL")
    # Preloading OTHER and SPY evicts AAPL. Its later event must still use the
    # same full range, never silently change to a short event-specific window.
    assert "AAPL" not in cache._symbol_cache
    assert cache.future_return("AAPL", later["ts"], 5) == round(5 / 101, 4)
    assert calls[-1] == first_range
    assert cache.cache_size == 1
