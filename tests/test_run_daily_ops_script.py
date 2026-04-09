import os
import subprocess
import sys
from pathlib import Path

from scripts.run_daily_ops import build_daily_ops_commands


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

    assert commands[0][:2] == ["python3", "/tmp/Moneybot/scripts/day7_decision_log_summary.py"]
    assert "--output" in commands[0]
    assert "data/day13_calibration_report.json" in commands[2]
    assert "data/day13_recalibration_plan.json" in commands[3]
    assert commands[-1][:2] == ["python3", "/tmp/Moneybot/scripts/autofill_daily_report.py"]


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
