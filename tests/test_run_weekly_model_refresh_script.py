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
