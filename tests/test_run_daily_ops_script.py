import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.run_daily_ops import _run_daily_ops_command, _tail_text, build_daily_ops_commands


def test_daily_ops_workflow_has_layered_bounded_timeouts():
    workflow = Path(".github/workflows/moneybot-daily-ops.yml").read_text(encoding="utf-8")

    assert "timeout-minutes: 15" in workflow
    assert "--connect-timeout 15" in workflow
    assert "--max-time 720" in workflow
    assert "curl -sS" in workflow
    assert '--output "$response_file" --write-out "%{http_code}"' in workflow
    assert '[[ ! "$http_code" =~ ^2[0-9][0-9]$ ]]' in workflow


def _daily_ops_workflow_script() -> str:
    workflow = Path(".github/workflows/moneybot-daily-ops.yml").read_text(encoding="utf-8")
    step = workflow.split("- name: Trigger /api/run-daily-ops", 1)[1]
    block = step.split("        run: |\n", 1)[1]
    lines = []
    for line in block.splitlines():
        if line and not line.startswith("          "):
            break
        lines.append(line[10:] if line else "")
    return "\n".join(lines)


def _run_workflow_script(tmp_path: Path, *, mode: str) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        "output=''\n"
        "while (($#)); do\n"
        "  if [[ $1 == --output ]]; then output=$2; shift 2; else shift; fi\n"
        "done\n"
        "printf '%s' \"$FAKE_BODY\" > \"$output\"\n"
        "if [[ $FAKE_MODE == transport ]]; then exit 7; fi\n"
        "[[ $FAKE_MODE == success ]] && printf 200 || printf 500\n",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    env = dict(os.environ)
    env.update(
        PATH=f"{fake_bin}:{env['PATH']}",
        MONEYBOT_BASE_URL="https://example.invalid",
        DAILY_OPS_TOKEN="never-print-this-secret",
        FAKE_MODE=mode,
        FAKE_BODY='{"data":{"success":false,"returncode":1,"stderr":"child failed"},"request_id":"req-1"}',
    )
    return subprocess.run(
        ["bash", "-c", _daily_ops_workflow_script()],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_daily_ops_workflow_prints_200_body_and_succeeds(tmp_path):
    completed = _run_workflow_script(tmp_path, mode="success")

    assert completed.returncode == 0
    assert "Daily Ops HTTP status: 200" in completed.stdout
    assert '"request_id":"req-1"' in completed.stdout
    assert "never-print-this-secret" not in completed.stdout + completed.stderr


def test_daily_ops_workflow_prints_500_body_and_fails(tmp_path):
    completed = _run_workflow_script(tmp_path, mode="failure")

    assert completed.returncode != 0
    assert "Daily Ops HTTP status: 500" in completed.stdout
    assert '"stderr":"child failed"' in completed.stdout
    assert "non-success HTTP status: 500" in completed.stdout


def test_daily_ops_workflow_transport_failure_remains_failure(tmp_path):
    completed = _run_workflow_script(tmp_path, mode="transport")

    assert completed.returncode == 7
    assert "transport layer; curl exit=7" in completed.stdout
    assert '"request_id":"req-1"' in completed.stdout


def test_build_daily_ops_commands_includes_autofill_and_expected_scripts():
    commands = build_daily_ops_commands(
        python_executable="python3",
        project_root=Path("/tmp/Moneybot"),
        input_log="data/decision_events.jsonl",
        summary_limit=200,
        outcomes_limit=2000,
        outcomes_rows_limit=20,
        calibration_limit=1000,
        horizon_days=5,
        base_dir=Path("data"),
    )

    assert commands[0][:2] == ["python3", "/tmp/Moneybot/scripts/day1_refresh_artifact.py"]
    assert commands[1][:2] == ["python3", "/tmp/Moneybot/scripts/day7_decision_log_summary.py"]
    assert "--output" in commands[1]
    assert "data/day13_calibration_report.json" in commands[3]
    assert "data/day13_recalibration_plan.json" in commands[4]
    assert commands[-1][:2] == ["python3", "/tmp/Moneybot/scripts/autofill_daily_report.py"]


def test_build_daily_ops_commands_can_skip_day1_refresh():
    commands = build_daily_ops_commands(
        python_executable="python3",
        project_root=Path("/tmp/Moneybot"),
        input_log="data/decision_events.jsonl",
        summary_limit=200,
        outcomes_limit=2000,
        outcomes_rows_limit=20,
        calibration_limit=1000,
        horizon_days=5,
        base_dir=Path("data"),
        include_day1_refresh=False,
    )

    assert commands[0][:2] == ["python3", "/tmp/Moneybot/scripts/day7_decision_log_summary.py"]


def test_run_daily_ops_command_streams_large_output_to_disk(tmp_path):
    script = tmp_path / "noisy.py"
    script.write_text(
        "import sys\n"
        "print('x' * 20000)\n"
        "print('e' * 20000, file=sys.stderr)\n",
        encoding="utf-8",
    )

    completed = _run_daily_ops_command([sys.executable, str(script)], script_name="noisy.py", log_dir=tmp_path)

    assert completed.returncode == 0
    assert (tmp_path / "noisy.py.stdout.log").stat().st_size > 12000
    assert _tail_text(tmp_path / "noisy.py.stdout.log").startswith("... <truncated")
    assert _tail_text(tmp_path / "noisy.py.stderr.log").startswith("... <truncated")


def test_run_daily_ops_main_reports_failed_command_diagnostics(monkeypatch, tmp_path, caplog):
    import scripts.run_daily_ops as daily_ops

    command = [sys.executable, "/tmp/day12_materialize_outcomes.py"]
    monkeypatch.setattr(daily_ops, "resolve_runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(daily_ops, "build_daily_ops_commands", lambda **kwargs: [command])

    def fail_command(command, *, script_name, log_dir):
        raise subprocess.CalledProcessError(1, command, output="stdout tail", stderr="stderr tail")

    monkeypatch.setattr(daily_ops, "_run_daily_ops_command", fail_command)
    monkeypatch.setattr(sys, "argv", ["run_daily_ops.py"])

    with pytest.raises(SystemExit, match="1"):
        daily_ops.main()

    diagnostics = caplog.text
    assert "DAILY_OPS_FAILED script=day12_materialize_outcomes.py returncode=1" in diagnostics
    assert "failed_script=day12_materialize_outcomes.py" in diagnostics
    assert f"failed_command={sys.executable} /tmp/day12_materialize_outcomes.py" in diagnostics
    assert "stdout_tail=stdout tail" in diagnostics
    assert "stderr_tail=stderr tail" in diagnostics


def test_day13_scripts_bootstrap_project_root_for_imports(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    for script in [
        "scripts/run_daily_ops.py",
        "scripts/day13_recalibrate.py",
        "scripts/autofill_daily_report.py",
    ]:
        completed = subprocess.run(
            [sys.executable, str(repo_root / script), "--help"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert completed.returncode == 0, f"{script} failed to import with stderr={completed.stderr}"
