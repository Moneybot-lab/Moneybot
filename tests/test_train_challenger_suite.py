import json

import numpy as np
import pandas as pd
import pytest

from moneybot.services.deterministic_model import BaselineModelArtifact, save_artifact
from scripts.train_challenger_suite import _artifact_scored_mistake_rows, _specialized_training_inputs, _write_daily_mistake_slices, train_challenger_suite


def test_mistake_mining_uses_artifact_probabilities_and_threshold(tmp_path):
    model_path = tmp_path / "scoring-model.json"
    save_artifact(
        BaselineModelArtifact(
            version="actual-scoring-artifact-v1",
            feature_columns=["feature_signal"],
            means=[0.0],
            stds=[1.0],
            weights=[10.0],
            bias=0.0,
            decision_threshold=0.55,
        ),
        model_path,
    )
    frame = pd.DataFrame(
        {
            "event_date": ["2026-01-01", "2026-01-02", "2026-01-03"],
            "symbol": ["MISS", "BAD", "SAFE"],
            "feature_signal": [-1.0, 1.0, -1.0],
            "feature_rec_positive": [1.0, 0.0, 1.0],
            "feature_probability_up": [0.99, 0.01, 0.99],
            "return_5d": [0.05, -0.05, -0.05],
            "return_bin_5d": ["big_gain", "big_loss", "big_loss"],
        }
    )

    slices, scoring = _artifact_scored_mistake_rows(frame, model_path, "return_5d")

    assert slices["missed_big_gain_winners"]["symbol"].tolist() == ["MISS"]
    assert slices["bad_buy_big_loss_false_positives"]["symbol"].tolist() == ["BAD"]
    assert slices["missed_big_gain_winners"].iloc[0]["artifact_prediction"] == 0
    assert slices["bad_buy_big_loss_false_positives"].iloc[0]["artifact_prediction"] == 1
    assert scoring == {
        "scoring_method": "artifact_predictions",
        "model_version": "actual-scoring-artifact-v1",
        "model_path": str(model_path),
        "decision_threshold": 0.55,
        "rows_scored": 3,
    }
    manifest = _write_daily_mistake_slices(frame, tmp_path / "suite", "return_5d", model_path)
    bad_buy_file = manifest["slices"]["bad_buy_big_loss_false_positives"]["daily_files"][0]["path"]
    bad_buy_row = json.loads(open(bad_buy_file, encoding="utf-8").readline())
    assert bad_buy_row["symbol"] == "BAD"
    assert bad_buy_row["artifact_model_version"] == "actual-scoring-artifact-v1"
    assert bad_buy_row["artifact_decision_threshold"] == 0.55
    assert bad_buy_row["artifact_prediction"] == 1
    assert bad_buy_row["mistake_type"] == "bad_buy_big_loss_false_positive"

    compatibility_model = tmp_path / "compatibility-suite" / "scoring-model.json"
    compatibility_model.parent.mkdir(parents=True)
    save_artifact(
        BaselineModelArtifact(
            version="compatibility-scoring-artifact-v1",
            feature_columns=["feature_signal"],
            means=[0.0],
            stds=[1.0],
            weights=[10.0],
            bias=0.0,
            decision_threshold=0.55,
        ),
        compatibility_model,
    )
    compatibility_payload = json.loads(compatibility_model.read_text(encoding="utf-8"))
    compatibility_payload["model_type"] = "logistic_regression"
    compatibility_model.write_text(json.dumps(compatibility_payload), encoding="utf-8")

    compatibility_manifest = _write_daily_mistake_slices(frame, compatibility_model.parent, "return_5d")

    assert compatibility_manifest["scoring_method"] == "artifact_predictions"
    assert compatibility_manifest["model_path"] == str(compatibility_model)


def test_mistake_slice_request_before_training_is_non_fatal_and_not_proxy_scored(tmp_path):
    frame = pd.DataFrame(
        {
            "symbol": ["CMI"],
            "event_date": ["2026-07-10"],
            "feature_probability_up": [0.99],
            "return_5d": [-0.05],
            "return_bin_5d": ["big_loss"],
        }
    )

    manifest = _write_daily_mistake_slices(frame, tmp_path / "empty-suite", "return_5d")

    assert manifest["scoring_method"] == "unavailable_no_artifact"
    assert manifest["rows_scored"] == 0
    assert manifest["model_path"] is None
    assert manifest["slices"]["bad_buy_big_loss_false_positives"]["rows"] == 0


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
    assert manifest["lineage_schema_version"] == "moneybot-challenger-lineage.v1"
    assert manifest["live_routing"] is False
    assert manifest["challenger_count"] >= 20
    assert manifest["model_type_counts"]["logistic_regression"] == 16
    assert manifest["model_type_counts"]["calibrated_linear"] == 3
    assert manifest["model_type_counts"]["shallow_decision_tree"] == 2
    assert manifest["phase_2_candidate_families"] == {
        "calibrated_linear": ["full-balanced", "no-raw-price", "momentum"],
        "shallow_decision_tree": {"max_depths": [2, 3], "maximum_allowed_depth": 3},
    }
    assert manifest["phase_2_diversity"]["candidate_count"] == 5
    assert manifest["phase_2_diversity"]["distinct_prediction_clusters"] >= 2
    assert manifest["specialized_challenger_families"] == ["big_loss_avoider", "big_gain_hunter", "recent_window_model", "ranking_top5_model"]
    assert {item.get("specialized_family") for item in manifest["challengers"] if item.get("specialized_family")} == set(manifest["specialized_challenger_families"])
    assert "mistake_mining" in manifest
    assert manifest["mistake_mining"]["scoring_method"] == "artifact_predictions"
    assert manifest["mistake_mining"]["rows_scored"] == manifest["test_rows"]
    assert manifest["mistake_mining"]["model_version"] in manifest["ranked_model_versions"]
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
    assert manifest["temporal_validation_policy"] == {"purged": True, "label_horizon_days": 5, "embargo_days": 1}
    assert manifest["temporal_split"]["purged_train_rows"] > 0
    assert manifest["temporal_split"]["embargoed_test_rows"] > 0
    assert manifest["walk_forward_windows"][0]["temporal_split"]["purged_train_rows"] > 0
    assert "walk-forward" in manifest["ranking_selection_policy"]
    assert "two walk-forward windows" in manifest["promotion_policy"]
    lineage_ids = {challenger["lineage"]["lineage_id"] for challenger in manifest["challengers"]}
    assert len(lineage_ids) == manifest["challenger_count"]
    for challenger in manifest["challengers"]:
        artifact_path = tmp_path / "models" / f"{challenger['model_version']}.json"
        assert artifact_path.exists()
        artifact_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert artifact_payload["lineage"] == challenger["lineage"]
        lineage = challenger["lineage"]
        assert lineage["schema_version"] == "moneybot-challenger-lineage.v1"
        assert len(lineage["recipe_hash"]) == 64
        assert lineage["generation"] == 1
        assert lineage["parent_lineage_ids"] == []
        deployable = lineage["recipe"]["deployable_config"]
        assert set(deployable) == {"model_family", "decision_threshold", "calibration", "feature_subset", "sample_weight_policy", "abstention"}
        assert challenger["metrics"]["rows"] > 0
        assert "top_k_precision" in challenger["metrics"]
        assert "pairwise_ranking_loss" in challenger["metrics"]
        assert "ranking_objective" in challenger["metrics"]
        assert "walk_forward" in challenger["metrics"]
        assert challenger["metrics"]["walk_forward"]["window_count"] >= 2
        assert "zero_big_loss_windows" in challenger["metrics"]["walk_forward"]
        assert 0.0 <= challenger["metrics"]["walk_forward"]["zero_big_loss_window_rate"] <= 1.0
        assert "positive_ranking_windows" in challenger["metrics"]["walk_forward"]
        assert "walk_forward_ranking_objective" in challenger["metrics"]
        assert "walk_forward_passed" in challenger["metrics"]
        assert challenger["metrics"]["walk_forward_recipe_reproduced"] is True

    calibrated = [item for item in manifest["challengers"] if item["model_type"] == "calibrated_linear"]
    assert len({tuple(item["lineage"]["recipe"]["deployable_config"]["feature_subset"]) for item in calibrated}) >= 2
    for challenger in calibrated:
        artifact = json.loads((tmp_path / "models" / f"{challenger['model_version']}.json").read_text(encoding="utf-8"))
        deployable = challenger["lineage"]["recipe"]["deployable_config"]
        assert deployable["calibration"]["method"] == artifact["calibration"]["method"]
        assert deployable["sample_weight_policy"] == challenger["spec"]["sample_weight_policy"]
        assert len(challenger["metrics"]["prediction_fingerprint"]) == 64
        assert challenger["metrics"]["big_loss_predictions"] >= 0

    for challenger in (item for item in manifest["challengers"] if item["model_type"] == "shallow_decision_tree"):
        artifact = json.loads((tmp_path / "models" / f"{challenger['model_version']}.json").read_text(encoding="utf-8"))
        assert artifact["training_spec"]["max_depth"] in {2, 3}
        assert artifact["training_spec"]["max_depth"] <= 3
        assert artifact["lineage"]["recipe"]["deployable_config"]["model_family"] == "shallow_decision_tree"
        assert len(challenger["metrics"]["prediction_fingerprint"]) == 64

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
