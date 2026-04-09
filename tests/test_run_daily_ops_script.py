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
