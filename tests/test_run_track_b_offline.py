from pathlib import Path

from scripts.run_track_b_offline import build_track_b_commands


def test_build_track_b_commands_uses_offline_artifacts_only():
    commands = build_track_b_commands(
        python_executable="python3",
        project_root=Path("/tmp/Moneybot"),
        input_log="data/decision_events.jsonl",
        train_ratio=0.8,
        min_rows=200,
        output_dir=Path("/tmp/Moneybot/data/track_b"),
        production_model="data/track_b/production_model.json",
    )

    assert commands[0][:2] == ["python3", "/tmp/Moneybot/scripts/day8_build_decision_training_dataset.py"]
    assert commands[0][-2:] == ["--limit", "50000"]
    assert commands[1][:2] == ["python3", "/tmp/Moneybot/scripts/day10_train_candidate_model.py"]
    assert commands[2][:2] == ["python3", "/tmp/Moneybot/scripts/day11_compare_candidate_vs_production.py"]

    flat = " ".join(" ".join(cmd) for cmd in commands)
    assert "day14_promote_candidate.py" not in flat
    assert "data/track_b/production_model.json" in flat
    assert "data/day1_baseline_model.json" not in flat
    assert "candidate_model_track_b.json" in flat
    assert "model_comparison_track_b.json" in flat


def test_build_track_b_commands_can_skip_dataset_limit():
    commands = build_track_b_commands(
        python_executable="python3",
        project_root=Path("/tmp/Moneybot"),
        input_log="data/decision_events.jsonl",
        train_ratio=0.8,
        min_rows=200,
        output_dir=Path("/tmp/Moneybot/data/track_b"),
        dataset_limit=None,
    )

    assert "--limit" not in commands[0]
