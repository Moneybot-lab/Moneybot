import json

from scripts.train_challenger_suite import train_challenger_suite


def test_train_challenger_suite_writes_multiple_offline_models_and_manifest(tmp_path):
    input_path = tmp_path / "train.jsonl"
    rows = []
    for idx in range(30):
        rows.append({
            "ts": idx,
            "symbol": "AAPL",
            "recommendation": "BUY" if idx % 2 else "HOLD",
            "feature_close": 100 + idx,
            "feature_return_1d_lagged": idx / 100,
            "feature_volume": 1000 + idx,
            "label_up_5d": int(idx % 3 != 0),
        })
    input_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    manifest = train_challenger_suite(input_path, tmp_path / "models", min_rows=10)

    assert manifest["schema_version"] == "moneybot-challenger-suite.v1"
    assert manifest["live_routing"] is False
    assert len(manifest["challengers"]) == 3
    assert len(manifest["ranked_model_versions"]) == 3
    for challenger in manifest["challengers"]:
        assert (tmp_path / "models" / f"{challenger['model_version']}.json").exists()
        assert challenger["metrics"]["rows"] > 0
