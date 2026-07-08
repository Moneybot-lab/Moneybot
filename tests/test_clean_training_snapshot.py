import json

from scripts.clean_training_snapshot import clean_training_snapshot


def test_clean_training_snapshot_drops_bad_rows_and_writes_quality_outputs(tmp_path):
    input_path = tmp_path / "rows.jsonl"
    good = {
        "ts": 1,
        "symbol": "AAPL",
        "event_date": "2026-01-10",
        "market_asof_date": "2026-01-10",
        "label_up_5d": 1,
        "probability_up": 0.61,
        "feature_close": 100.0,
        "feature_return_1d_lagged": 0.01,
        "feature_return_5d_lagged": 0.03,
        "feature_volume": 1000,
    }
    rows = [
        good,
        dict(good),
        {**good, "ts": 2, "label_up_5d": None},
        {**good, "ts": 3, "feature_volume": None},
        {**good, "ts": 4, "event_date": "2026-01-10", "market_asof_date": "2026-01-01"},
        {**good, "ts": 5, "probability_up": None},
    ]
    input_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    report = clean_training_snapshot(input_path, tmp_path / "quality", train_ratio=0.5, max_market_lag_days=3)

    assert report["raw_rows"] == 6
    assert report["drop_counts"]["duplicates"] == 1
    assert report["drop_counts"]["missing_label"] == 1
    assert report["drop_counts"]["missing_required_features"] == 1
    assert report["drop_counts"]["stale_market_asof_date"] == 1
    assert report["cleaned_rows"] == 2
    assert report["train_rows"] == 1
    assert report["test_rows"] == 1
    assert report["evaluation_rows_with_probability_up"] == 1
    assert report["training_ready"] is True
    assert report["evaluation_ready"] is True
    assert (tmp_path / "quality" / "cleaned_all.jsonl").exists()
    assert (tmp_path / "quality" / "cleaned_train.jsonl").exists()
    assert (tmp_path / "quality" / "cleaned_test.jsonl").exists()
    assert (tmp_path / "quality" / "evaluation_probability_rows.jsonl").exists()
    assert (tmp_path / "quality" / "model_quality_report.json").exists()
