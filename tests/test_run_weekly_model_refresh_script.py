import os
import subprocess
import sys
from pathlib import Path

from scripts.run_weekly_model_refresh import build_weekly_refresh_commands


def test_build_weekly_refresh_commands_runs_refresh_then_daily_bundle():
    commands = build_weekly_refresh_commands(
        python_executable="python3",
        project_root=Path("/tmp/Moneybot"),
        input_log="data/decision_events.jsonl",
    )

    assert commands[0][:2] == ["python3", "/tmp/Moneybot/scripts/day1_refresh_artifact.py"]
    assert commands[1][:2] == ["python3", "/tmp/Moneybot/scripts/run_daily_ops.py"]
    assert commands[1][-3:] == ["--input-log", "data/decision_events.jsonl", "--skip-day1-refresh"]


def test_run_weekly_model_refresh_bootstraps_project_root_for_imports(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [sys.executable, str(repo_root / "scripts/run_weekly_model_refresh.py"), "--help"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
