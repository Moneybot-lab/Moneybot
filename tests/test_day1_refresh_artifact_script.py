from pathlib import Path

from scripts.day1_refresh_artifact import build_day1_commands


def test_build_day1_commands_uses_absolute_script_paths():
    project_root = Path("/tmp/Moneybot")

    commands = build_day1_commands(
        python_executable="python3",
        project_root=project_root,
        output_snapshot="data/day1_training_snapshot.csv",
        output_model="data/day1_baseline_model.json",
        period="2y",
        interval="1d",
        horizon_days=5,
        target_return=0.0,
        train_ratio=0.8,
        symbols=["AAPL", "MSFT"],
    )

    assert commands[0][:2] == ["python3", str(project_root / "scripts" / "day1_generate_training_data.py")]
    assert commands[1][:2] == ["python3", str(project_root / "scripts" / "day1_train_baseline_model.py")]
    assert "--symbols" in commands[0]
    assert commands[0][-2:] == ["AAPL", "MSFT"]
    assert commands[1][commands[1].index("--input") + 1] == "data/day1_training_snapshot.csv"
    assert commands[1][commands[1].index("--output-model") + 1] == "data/day1_baseline_model.json"


def test_existing_model_version_reads_candidate_version(tmp_path):
    from scripts.day1_refresh_artifact import existing_model_version, is_promoted_model_version

    model = tmp_path / "day1_baseline_model.json"
    model.write_text('{"version":"candidate-logreg-v1-20260714T120000Z"}', encoding="utf-8")

    version = existing_model_version(model)

    assert version == "candidate-logreg-v1-20260714T120000Z"
    assert is_promoted_model_version(version) is True
    assert is_promoted_model_version("alpha-atlas-v1") is False
