import json

import numpy as np
import pytest

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
