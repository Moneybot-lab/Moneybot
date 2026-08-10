import json
import sys
from pathlib import Path

from scripts import run_track_b_offline
from scripts.run_track_b_offline import (
    build_track_b_commands,
    servability_certification_path,
)


def test_servability_certification_path_is_available_to_main_summary(tmp_path):
    assert servability_certification_path(tmp_path) == (
        tmp_path / "production_servability_certification.json"
    )


def test_main_reads_certification_after_commands_without_name_error(
    tmp_path, monkeypatch
):
    output_dir = tmp_path / "track_b"
    output_dir.mkdir()
    (output_dir / "production_servability_certification.json").write_text(
        json.dumps(
            {
                "passed": True,
                "candidate_artifact_sha256": "abc",
                "feature_contract_version": "test-v1",
                "forecast_horizon": "5d",
                "blocking_reasons": [],
            }
        ),
        encoding="utf-8",
    )

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        run_track_b_offline.subprocess, "run", lambda *args, **kwargs: Completed()
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_track_b_offline.py",
            "--output-dir",
            str(output_dir),
            "--training-source",
            "legacy",
        ],
    )

    run_track_b_offline.main()

    summary = json.loads((output_dir / "track_b_summary.json").read_text())
    assert summary["success"] is True
    assert summary["servability_certification"]["passed"] is True


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

    assert len(commands) == 5
    assert commands[0][:2] == [
        "python3",
        "/tmp/Moneybot/scripts/train_massive_baseline_model.py",
    ]
    assert commands[1][:2] == [
        "python3",
        "/tmp/Moneybot/scripts/day10_train_candidate_model.py",
    ]
    assert commands[2][:2] == [
        "python3",
        "/tmp/Moneybot/scripts/day11_compare_candidate_vs_production.py",
    ]
    assert commands[3][:2] == [
        "python3",
        "/tmp/Moneybot/scripts/certify_production_servability.py",
    ]
    assert commands[4][:2] == [
        "python3",
        "/tmp/Moneybot/scripts/generate_next_generation_challengers.py",
    ]

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
    assert "next_generation" in flat


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

    assert commands[0][:2] == [
        "python3",
        "/tmp/Moneybot/scripts/day8_build_decision_training_dataset.py",
    ]
    assert "--limit" not in commands[0]
    assert "--cleaned-train" not in commands[1]
    assert commands[3][:2] == [
        "python3",
        "/tmp/Moneybot/scripts/certify_production_servability.py",
    ]
    assert any(
        value.endswith("decision_training_snapshot_track_b.jsonl")
        for value in commands[1]
    )
