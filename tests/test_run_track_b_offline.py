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

    assert len(commands) == 3
    assert commands[0][:2] == ["python3", "/tmp/Moneybot/scripts/train_massive_baseline_model.py"]
    assert commands[1][:2] == ["python3", "/tmp/Moneybot/scripts/day10_train_candidate_model.py"]
    assert commands[2][:2] == ["python3", "/tmp/Moneybot/scripts/day11_compare_candidate_vs_production.py"]

    flat = " ".join(" ".join(cmd) for cmd in commands)
    assert "day8_build_decision_training_dataset.py" not in flat
    assert "day14_promote_candidate.py" not in flat
    assert "decision_training_snapshot_massive.jsonl" not in flat
    assert "decision_training_snapshot_track_b.jsonl" not in flat
    assert "data/track_b/production_model.json" in flat
    assert "data/day1_baseline_model.json" not in flat
    assert "candidate_model_track_b.json" in flat
    assert "training_quality/cleaned_train.jsonl" in flat
    assert "training_quality/cleaned_test.jsonl" in flat
    assert "training_quality/cleaned_all.jsonl" in flat
    assert "massive_baseline_model_v1.json" in flat
    assert "candidate_market_no_echo_v1" in flat
    assert "--input-is-holdout" in commands[2]
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
        training_source="legacy",
    )

    assert commands[0][:2] == ["python3", "/tmp/Moneybot/scripts/day8_build_decision_training_dataset.py"]
    assert "--limit" not in commands[0]
    assert "--cleaned-train" not in commands[1]
    assert any(value.endswith("decision_training_snapshot_track_b.jsonl") for value in commands[1])
