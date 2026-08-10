import json

import numpy as np
import pandas as pd

from moneybot.services.alpha_atlas_v31_quantitative import (
    calibration_is_stable,
    extreme_return_audit,
    feature_distribution_audit,
)
from moneybot.services.decision_target import target_metadata
from moneybot.services.deterministic_model import (
    BaselineModelArtifact,
    load_artifact,
    predict_proba,
    save_artifact,
    train_logistic_baseline,
)
from scripts.train_massive_baseline_model import _select_threshold


def test_weighted_standard_uses_model_sample_weights():
    X = np.asarray([[0.0], [10.0], [10.0]])
    y = np.asarray([0.0, 1.0, 1.0])
    model = train_logistic_baseline(
        X,
        y,
        sample_weight=np.asarray([1.0, 0.1, 0.1]),
        scaler_type="weighted_standard",
        epochs=1,
    )
    expected_mean = (0.0 + 1.0 + 1.0) / 1.2
    expected_variance = (
        1.0 * (0.0 - expected_mean) ** 2
        + 0.1 * (10.0 - expected_mean) ** 2
        + 0.1 * (10.0 - expected_mean) ** 2
    ) / 1.2
    assert model.means == [pytest.approx(expected_mean)]
    assert model.stds == [pytest.approx(expected_variance**0.5)]
    assert model.scaler_version == "moneybot-weighted-standard.v1"


def test_zero_variance_and_winsorized_serving_are_safe():
    X = np.asarray([[1.0, -100.0], [1.0, 0.0], [1.0, 100.0]])
    model = train_logistic_baseline(
        X,
        np.asarray([0.0, 0.0, 1.0]),
        scaler_type="weighted_standard",
        winsor_quantiles=(0.1, 0.9),
        epochs=2,
    )
    assert model.stds[0] == 1.0
    outside = predict_proba(model, np.asarray([[1.0, 1000.0]]))
    clipped = predict_proba(model, np.asarray([[1.0, model.clip_upper[1]]]))
    assert np.allclose(outside, clipped)


def test_legacy_artifact_loading_keeps_legacy_scaler(tmp_path):
    path = tmp_path / "legacy.json"
    payload = BaselineModelArtifact(
        version="day1-logreg-v1",
        feature_columns=["x"],
        means=[0.0],
        stds=[1.0],
        weights=[1.0],
        bias=0.0,
        decision_threshold=0.5,
    ).to_dict()
    payload.pop("scaler_type")
    payload.pop("scaler_version")
    payload.pop("clip_lower")
    payload.pop("clip_upper")
    path.write_text(json.dumps(payload))
    loaded = load_artifact(path)
    assert loaded.scaler_type == "legacy_standard"
    assert loaded.scaler_version == "moneybot-legacy-standard.v1"


def test_target_metadata_separates_binary_label_from_evaluation_buckets():
    metadata = target_metadata()
    assert metadata["target_name"] == "label_up_5d"
    assert metadata["binary_positive_class"] == {
        "return_column": "return_5d",
        "operator": ">",
        "value": 0.0,
    }
    assert "flat" in metadata["evaluation_return_buckets"]["names"]


def test_threshold_report_discloses_economic_support_requirement():
    rows = 12
    frame = pd.DataFrame(
        {
            "label_up_5d": [1.0] * rows,
            "return_5d": [-0.01] * rows,
            "symbol": [f"S{i}" for i in range(rows)],
            "event_date": [f"2025-01-{i + 1:02d}" for i in range(rows)],
            "endpoint": ["quick_ask"] * rows,
            "decision_source": ["deterministic_model"] * rows,
        }
    )
    report = _select_threshold(frame, np.full(rows, 0.9))
    assert report["support_requirements"]["minimum_avg_selected_return"] == 0.0
    assert not any(item["support_passed"] for item in report["search"])


def test_distribution_and_extreme_audits_preserve_rows_and_provenance():
    frame = pd.DataFrame(
        {
            "symbol": ["A", "A", "B"],
            "event_date": ["2025-01-01", "2025-01-01", "2025-01-02"],
            "endpoint": ["quick_ask"] * 3,
            "decision_source": ["deterministic_model"] * 3,
            "feature_return_1d_lagged": [0.01, 0.01, 9.0],
        }
    )
    distribution = feature_distribution_audit(
        frame, features=["feature_return_1d_lagged"]
    )
    outliers = extreme_return_audit(
        frame, features=["feature_return_1d_lagged"], tail_fraction=0.34
    )
    assert distribution["rows"] == 3
    assert distribution["rows_deleted"] == 0
    assert distribution["features"]["feature_return_1d_lagged"]["max"] == 9.0
    rows = outliers["features"]["feature_return_1d_lagged"]
    assert any(row["symbol"] == "B" and row["raw_feature_value"] == 9.0 for row in rows)
    assert any(row["duplicate_decision_rows"] == 2 for row in rows)


def test_calibration_stability_rejects_material_fold_regression():
    assert calibration_is_stable(-0.01, [-0.002, 0.001])
    assert not calibration_is_stable(-0.01, [-0.002, 0.01])
    assert not calibration_is_stable(0.0, [-0.002, -0.001])


# Imported last so the test module remains explicit about its numerical dependency.
import pytest
