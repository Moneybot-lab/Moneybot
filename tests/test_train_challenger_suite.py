import json

import numpy as np
import pandas as pd
import pytest

from scripts.train_challenger_suite import _specialized_training_inputs, train_challenger_suite


@pytest.mark.parametrize(
    ("family", "expected_rows", "expected_weighted_bucket"),
    [
        ("big_loss_avoider", 12, ("big_loss", 6.0)),
        ("big_gain_hunter", 12, ("big_gain", 6.0)),
        ("recent_window_model", 6, None),
        ("ranking_top5_model", 12, ("big_gain", 4.0)),
    ],
)
def test_specialized_training_inputs_reproduce_complete_recipe(family, expected_rows, expected_weighted_bucket):
    frame = pd.DataFrame(
        {
            "event_date": ["2026-01-01"] * 6 + ["2026-01-02"] * 6,
            "return_5d": np.linspace(-0.06, 0.08, 12),
            "return_bin_5d": ["big_loss", "loss", "flat", "gain", "big_gain", "big_gain"] * 2,
        }
    )
    labels = np.asarray([idx % 2 for idx in range(12)], dtype=float)

    recipe_frame, recipe_labels, sample_weight = _specialized_training_inputs(frame, labels, "return_5d", family)

    assert len(recipe_frame) == expected_rows
    assert len(recipe_labels) == expected_rows
    assert len(sample_weight) == expected_rows
    if family == "recent_window_model":
        assert recipe_frame.index.tolist() == list(range(6, 12))
        assert recipe_labels.tolist() == labels[-6:].tolist()
        assert sample_weight[0] == pytest.approx(0.5)
        assert sample_weight[-1] == pytest.approx(2.5)
    elif family == "ranking_top5_model":
        assert recipe_labels.sum() == 10
    if expected_weighted_bucket:
        bucket, expected_weight = expected_weighted_bucket
        assert set(sample_weight[recipe_frame["return_bin_5d"].to_numpy() == bucket]) == {expected_weight}


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
    assert manifest["model_type_counts"]["logistic_regression"] == 16
    assert manifest["specialized_challenger_families"] == ["big_loss_avoider", "big_gain_hunter", "recent_window_model", "ranking_top5_model"]
    assert {item.get("specialized_family") for item in manifest["challengers"] if item.get("specialized_family")} == set(manifest["specialized_challenger_families"])
    assert "mistake_mining" in manifest
    assert "missed_big_gain_winners" in manifest["mistake_mining"]["slices"]
    assert "bad_buy_big_loss_false_positives" in manifest["mistake_mining"]["slices"]
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
        assert challenger["metrics"]["walk_forward_recipe_reproduced"] is True

    for challenger in manifest["challengers"]:
        family = challenger.get("specialized_family")
        if not family:
            continue
        spec = challenger["spec"]
        expected_threshold = 0.60 if family in {"big_loss_avoider", "ranking_top5_model"} else 0.55
        assert spec["threshold"] == expected_threshold
        assert spec["sample_weight_policy"] == family
        assert spec["target_policy"] == ("daily_top5" if family == "ranking_top5_model" else "configured_target")
        assert spec["training_window_policy"] == ("recent_half" if family == "recent_window_model" else "full_window")


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
