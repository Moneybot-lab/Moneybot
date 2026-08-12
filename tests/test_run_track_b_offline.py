import json
import sys
from pathlib import Path

from scripts import run_track_b_offline
from scripts.run_track_b_offline import (
    _is_expected_no_candidate_result,
    build_track_b_commands,
    servability_certification_path,
)


def test_track_b_workflow_defines_required_shell_environment_and_aws_compatibility():
    workflow = Path(".github/workflows/track-b-offline.yml").read_text(encoding="utf-8")
    required = {
        "PYTHONPATH": "${{ github.workspace }}",
        "MASSIVE_FLATFILES_PREFIX": "${{ inputs.massive_prefix || 'us_stocks_sip/day_aggs_v1' }}",
        "MASSIVE_INGEST_DATE": "${{ inputs.dataset_date || '' }}",
        "TRACK_B_MIN_ROWS": "${{ inputs.min_rows || '200' }}",
        "AWS_REQUEST_CHECKSUM_CALCULATION": "when_required",
        "AWS_RESPONSE_CHECKSUM_VALIDATION": "when_required",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_EC2_METADATA_DISABLED": '"true"',
    }
    for name, value in required.items():
        assert f"{name}: {value}" in workflow
    assert "set -euo pipefail" in workflow


def test_manual_promotion_workflow_requires_servability_certification():
    workflow = Path(".github/workflows/promote-track-b-candidate.yml").read_text(
        encoding="utf-8"
    )
    assert "production_servability_certification.json" in workflow
    assert "SERVABILITY_CERTIFICATION_PATH" in workflow
    assert 'servability_certification=@$SERVABILITY_CERTIFICATION_PATH' in workflow


def test_expected_no_candidate_marker_is_narrow():
    assert _is_expected_no_candidate_result(
        "candidate_v1 is a no-op clone; generation aborted before publishing reports"
    )
    assert not _is_expected_no_candidate_result("KeyError: feature contract mismatch")


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


def test_main_records_expected_no_candidate_as_successful_no_promotion(
    tmp_path, monkeypatch
):
    output_dir = tmp_path / "track_b"
    quality_dir = output_dir / "training_quality"
    quality_dir.mkdir(parents=True)
    for name in ("cleaned_train.jsonl", "cleaned_test.jsonl", "cleaned_all.jsonl"):
        (quality_dir / name).write_text("{}\n", encoding="utf-8")

    class Completed:
        stdout = ""
        stderr = ""
        returncode = 0

    calls = {"count": 0}

    def fake_run(*args, **kwargs):
        calls["count"] += 1
        result = Completed()
        if calls["count"] == 5:
            result.returncode = 1
            result.stderr = (
                "candidate_threshold_sweep_v1 is a no-op clone; "
                "generation aborted before publishing reports"
            )
        return result

    monkeypatch.setattr(run_track_b_offline.subprocess, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_track_b_offline.py", "--output-dir", str(output_dir)],
    )

    run_track_b_offline.main()

    summary = json.loads((output_dir / "track_b_summary.json").read_text())
    assert summary["success"] is True
    assert summary["outcome"] == "no_candidate_generated"
    assert summary["steps"][-1]["outcome"] == "no_candidate_generated"
    assert summary["primary_candidate_generated"] is False
    assert summary["next_generation_candidate_generated"] is False
    assert summary["promotion_automatic"] is False


def test_main_preserves_primary_artifacts_when_next_generation_is_no_op(
    tmp_path, monkeypatch
):
    output_dir = tmp_path / "track_b"
    quality_dir = output_dir / "training_quality"
    quality_dir.mkdir(parents=True)
    for name in ("cleaned_train.jsonl", "cleaned_test.jsonl", "cleaned_all.jsonl"):
        (quality_dir / name).write_text("{}\n", encoding="utf-8")
    primary_contents = {
        "candidate_model_track_b.json": '{"version":"candidate"}\n',
        "model_comparison_track_b.json": '{"candidate_win":false}\n',
        "production_servability_certification.json": '{"passed":true}\n',
    }
    for name, content in primary_contents.items():
        (output_dir / name).write_text(content, encoding="utf-8")

    class Completed:
        stdout = ""
        stderr = ""
        returncode = 0

    calls = {"count": 0}

    def fake_run(*args, **kwargs):
        calls["count"] += 1
        result = Completed()
        if calls["count"] == 5:
            result.returncode = 1
            result.stderr = (
                "candidate_threshold_sweep_v1 is a no-op clone; "
                "generation aborted before publishing reports"
            )
        return result

    monkeypatch.setattr(run_track_b_offline.subprocess, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_track_b_offline.py", "--output-dir", str(output_dir)],
    )

    run_track_b_offline.main()

    summary = json.loads((output_dir / "track_b_summary.json").read_text())
    assert summary["success"] is True
    assert summary["outcome"] == "next_generation_no_material_candidate"
    assert summary["primary_candidate_generated"] is True
    assert summary["next_generation_candidate_generated"] is False
    assert summary["promotion_automatic"] is False
    for name, content in primary_contents.items():
        assert (output_dir / name).read_text(encoding="utf-8") == content


def test_main_does_not_hide_unrelated_command_failure(tmp_path, monkeypatch):
    output_dir = tmp_path / "track_b"
    quality_dir = output_dir / "training_quality"
    quality_dir.mkdir(parents=True)
    for name in ("cleaned_train.jsonl", "cleaned_test.jsonl", "cleaned_all.jsonl"):
        (quality_dir / name).write_text("{}\n", encoding="utf-8")

    class Completed:
        returncode = 1
        stdout = ""
        stderr = "unexpected training failure"

    monkeypatch.setattr(
        run_track_b_offline.subprocess, "run", lambda *args, **kwargs: Completed()
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_track_b_offline.py", "--output-dir", str(output_dir)],
    )

    try:
        run_track_b_offline.main()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("unrelated Track B failures must remain fatal")

    summary = json.loads((output_dir / "track_b_summary.json").read_text())
    assert summary["success"] is False


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
