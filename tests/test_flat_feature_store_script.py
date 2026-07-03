import json
from pathlib import Path

from scripts.day15_materialize_flat_feature_store import materialize_flat_feature_store


def _write_rows(path: Path) -> None:
    rows = [
        {
            "ts": 1700000000,
            "symbol": "MSFT",
            "recommendation": "BUY",
            "return_5d": 0.02,
            "label_up_5d": 1,
            "feature_price": 100.0,
            "feature_return_1d": 0.01,
        },
        {
            "ts": 1700100000,
            "symbol": "AAPL",
            "recommendation": "HOLD",
            "return_5d": -0.01,
            "label_up_5d": 0,
            "feature_price": 200.0,
            "feature_return_5d": -0.01,
        },
        {
            "ts": 1700200000,
            "symbol": "MSFT",
            "recommendation": "SELL",
            "return_5d": 0.03,
            "label_up_5d": 1,
            "feature_price": 101.0,
            "feature_return_1d": 0.02,
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_materialize_flat_feature_store_writes_manifest_splits_and_partitions(tmp_path):
    input_path = tmp_path / "dataset.jsonl"
    output_dir = tmp_path / "flat"
    _write_rows(input_path)

    manifest = materialize_flat_feature_store(input_path, output_dir, train_ratio=0.67)

    assert manifest["schema_version"] == "flat-feature-store.v1"
    assert manifest["rows"] == 3
    assert manifest["train_rows"] == 2
    assert manifest["test_rows"] == 1
    assert manifest["live_routing"] is False
    assert "feature_price" in manifest["feature_columns"]
    assert "feature_return_1d" not in manifest["feature_columns"]
    assert "feature_return_5d" not in manifest["feature_columns"]
    assert "feature_return_1d" in manifest["label_columns"]
    assert "feature_return_5d" in manifest["label_columns"]
    assert (output_dir / "manifest.json").exists()
    assert (output_dir / "train.jsonl").exists()
    assert (output_dir / "test.csv").exists()
    assert (output_dir / "partitions" / "symbol=MSFT" / "year=2023" / "data.jsonl").exists()
