import json

import numpy as np
import pandas as pd
import pytest

from moneybot.services.alpha_atlas_v4_phase0 import fit_feature_fill_policy
from moneybot.services import alpha_atlas_v4_weighting_model_reload as reload_service
from moneybot.services.deterministic_model import BaselineModelArtifact, load_artifact, predict_proba, save_artifact


def _artifact(names):
    return BaselineModelArtifact(version="fixture", feature_columns=names,
        means=[1.0, 2.0], stds=[2.0, 4.0], weights=[0.5, -0.25], bias=0.1,
        decision_threshold=0.6)


def test_explicit_mapping_save_load_and_named_reordering(tmp_path):
    artifact = _artifact(["feature_a", "feature_b"])
    path = tmp_path / "model.json"
    save_artifact(artifact, path)
    loaded = load_artifact(path)
    row = {"feature_b": 10.0, "feature_a": 3.0}
    aligned = np.asarray([[row[name] for name in loaded.feature_columns]])
    assert predict_proba(loaded, aligned) == pytest.approx(predict_proba(artifact, np.asarray([[3.0, 10.0]])))


@pytest.mark.parametrize("names", [["a"], ["a", "a"], []])
def test_loader_rejects_missing_duplicate_or_dimensionally_wrong_mapping(tmp_path, names):
    payload = _artifact(["a", "b"]).to_dict()
    payload["feature_columns"] = names
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="feature"):
        load_artifact(path)


def test_legacy_valid_ten_feature_artifact_still_loads(tmp_path):
    from moneybot.services.deterministic_model import default_baseline_artifact
    path = tmp_path / "legacy.json"
    save_artifact(default_baseline_artifact(), path)
    assert load_artifact(path).feature_columns == default_baseline_artifact().feature_columns


def test_no_fit_replay_uses_exact_captured_fold_fill_policy(tmp_path, monkeypatch):
    features = [f"feature_{index}" for index in range(43)]
    policy_frame = pd.DataFrame([{**{name: float(index + 1) for index, name in enumerate(features)},
        "model_feature_contract_version": "alpha-atlas-v4-features.v2"}])
    policy = fit_feature_fill_policy(policy_frame, features)
    state = BaselineModelArtifact(version="saved", feature_columns=[f"legacy_{i}" for i in range(10)],
        means=[0.0] * 43, stds=[1.0] * 43, weights=[0.0] * 43, bias=0.2,
        decision_threshold=0.6).to_dict()
    expected_score = float(predict_proba(BaselineModelArtifact(**{**state, "feature_columns": features}), np.zeros((1, 43)))[0])
    folds = [{"fold_index": index, "train_ids": [f"train-{index}"], "validation_ids": [f"dev-{index}"]} for index in (1, 2, 3)]
    registration = {"feature_columns": features, "folds": folds, "registration_sha256": "registered"}
    capture = [{"model_version": "challenger-big-loss-avoider-v1", "fold_index": index,
        "train_ids": [f"train-{index}"], "validation_ids": [f"dev-{index}"], "fill_policy": policy,
        "records": [{"id": f"dev-{index}"}]} for index in (1, 2, 3)]
    predictions = {"arms": {arm: [{"fold_index": index, "model_state": state,
        "records": [{"id": f"dev-{index}", "score": expected_score}]} for index in (1, 2, 3)]
        for arm in ("current_weights", "uniform_weights")}}
    rows = [{"canonical_observation_id": f"dev-{index}", "model_feature_contract_version": "alpha-atlas-v4-features.v2",
        **{name: (None if name == features[0] else 0.0) for name in features}} for index in (1, 2, 3)]
    paths = {}
    for name, payload in (("registration", registration), ("capture", capture), ("predictions", predictions),
                          ("paired", {"primary_aggregate_equal_fold_mean": 0.006319421301219023})):
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(payload))
    feature_store = tmp_path / "all.jsonl"
    feature_store.write_text("".join(json.dumps(row) + "\n" for row in rows))
    monkeypatch.setattr(reload_service, "EXPECTED_COUNTS", {1: 1, 2: 1, 3: 1})
    report = reload_service.repair_and_verify(execution_predictions=paths["predictions"],
        paired_comparison=paths["paired"], registration_path=paths["registration"],
        diagnostic_capture=paths["capture"], feature_store=feature_store,
        output_dir=tmp_path / "out", execution_code_sha="execution")
    assert report["all_replays_verified"] is True
    assert {item["replay_status"] for item in report["models"]} == {"VERIFIED"}
    assert all(item["failure_reason"] is None for item in report["models"])
