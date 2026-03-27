from moneybot.services.model_metadata import (
    append_artifact_history,
    build_artifact_metadata,
    load_artifact_history,
    load_artifact_metadata,
    save_artifact_metadata,
)


def test_artifact_metadata_round_trip(tmp_path):
    model_path = str(tmp_path / "day1_baseline_model.json")
    metadata = build_artifact_metadata(
        model_path=model_path,
        model_version="day1-logreg-v1",
        input_path="data/day1_training_snapshot.csv",
        train_rows=100,
        test_rows=25,
        metrics={"accuracy": 0.51, "positive_rate": 0.33, "rows": 25},
        train_ratio=0.8,
        horizon_days=5,
        target_return=0.0,
    )

    save_artifact_metadata(model_path, metadata)
    append_artifact_history(model_path, metadata)

    assert load_artifact_metadata(model_path)["model_version"] == "day1-logreg-v1"
    assert load_artifact_history(model_path)[0]["train_rows"] == 100
