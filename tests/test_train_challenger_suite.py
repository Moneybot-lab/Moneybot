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
            "return_5d": (idx % 5 - 2) / 100,
            "label_up_5d": int(idx % 3 != 0),
        })
    input_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    manifest = train_challenger_suite(input_path, tmp_path / "models", min_rows=10)

    assert manifest["schema_version"] == "moneybot-challenger-suite.v2"
    assert manifest["live_routing"] is False
    assert manifest["challenger_count"] >= 20
    assert manifest["model_type_counts"]["logistic_regression"] == 12
    assert manifest["model_type_counts"]["decision_stump"] >= 3
    assert manifest["model_type_counts"]["baseline_classifier"] == 3
    assert len(manifest["ranked_model_versions"]) == manifest["challenger_count"]
    assert "top_k_avg_return" in manifest["ranking_metric_names"]
    assert "walk_forward_ranking_objective" in manifest["ranking_metric_names"]
    assert "walk_forward_passed" in manifest["ranking_metric_names"]
    assert len(manifest["walk_forward_windows"]) >= 2
    assert manifest["walk_forward_windows"][0]["train_end_row"] == manifest["walk_forward_windows"][0]["test_start_row"]
    assert "walk-forward" in manifest["ranking_selection_policy"]
    assert "two walk-forward windows" in manifest["promotion_policy"]
    for challenger in manifest["challengers"]:
        assert (tmp_path / "models" / f"{challenger['model_version']}.json").exists()
        assert challenger["metrics"]["rows"] > 0
        assert "top_k_precision" in challenger["metrics"]
        assert "pairwise_ranking_loss" in challenger["metrics"]
        assert "ranking_objective" in challenger["metrics"]
        assert "walk_forward" in challenger["metrics"]
        assert challenger["metrics"]["walk_forward"]["window_count"] >= 2
        assert "positive_ranking_windows" in challenger["metrics"]["walk_forward"]
        assert "walk_forward_ranking_objective" in challenger["metrics"]
        assert "walk_forward_passed" in challenger["metrics"]


def test_train_challenger_suite_excludes_unpersisted_derived_app_signal_features(tmp_path):
    input_path = tmp_path / "train.jsonl"
    rows = []
    for idx in range(30):
        rows.append({
            "ts": idx,
            "symbol": "AAPL",
            "endpoint": "quick_ask",
            "decision_source": "deterministic_model",
            "recommendation": "BUY" if idx % 2 else "HOLD",
            "probability_up": 0.6,
            "feature_close": 100 + idx,
            "label_up_5d": int(idx % 3 != 0),
        })
    input_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    manifest = train_challenger_suite(input_path, tmp_path / "models", min_rows=10)

    assert "feature_close" in manifest["feature_columns"]
    assert "feature_rec_buy" not in manifest["feature_columns"]
    assert "feature_endpoint_quick_ask" not in manifest["feature_columns"]
    assert "feature_source_deterministic_model" not in manifest["feature_columns"]
    assert "feature_probability_up" not in manifest["feature_columns"]
