import json
import sys

import pytest

from moneybot.services.production_servability import (
    certify_candidate,
    validate_certification,
)
from scripts import day14_promote_candidate


def _candidate(
    *, version="challenger", lane="decision", probabilities=(0.41, 0.57, 0.63)
):
    features = ["feature_return_1d", "feature_return_5d", "feature_rsi"]
    declarations = [
        {
            "feature_name": name,
            "required": True,
            "source": "market_history_at_T",
            "available_at_prediction_time": True,
            "training_builder": "market_features.v1",
            "serving_builder": "market_features.v1",
            "transformation": (
                "backward_return_decimal" if "return" in name else "rsi_14"
            ),
            "fill_policy": "training_median",
            "time_semantics": (
                "T-1_to_T"
                if name.endswith("1d")
                else "T-5_to_T" if name.endswith("5d") else "at_T"
            ),
        }
        for name in features
    ]
    return {
        "version": version,
        "model_type": "logistic_regression",
        "feature_columns": list(features),
        "means": [0.0, 0.0, 50.0],
        "stds": [1.0, 1.0, 10.0],
        "weights": [0.5, 0.4, 0.2],
        "bias": 0.0,
        "lineage": {"lineage_id": "recipe-abc", "recipe_hash": "abc123"},
        "production_feature_contract": {
            "feature_contract_version": "moneybot-serving-features.v1",
            "forecast_horizon": "5d",
            "lane": lane,
            "leakage_safe": True,
            "feature_columns": list(features),
            "required_features": list(features),
            "optional_features": [],
            "features": declarations,
            "training_transform": {"units": "declared", "scaling": "artifact"},
            "serving_transform": {"units": "declared", "scaling": "artifact"},
            "training_fill_policy": "training_median",
            "serving_fill_policy": "training_median",
            "fill_values": {
                name: value for name, value in zip(features, [0.0, 0.0, 50.0])
            },
            "representative_dry_runs": [
                {
                    "symbol": symbol,
                    "required_feature_count": 3,
                    "available_feature_count": 3,
                    "imputed_feature_count": 0,
                    "missing_required_features": [],
                    "feature_contract_servable": True,
                    "feature_vector_is_training_mean": False,
                    "raw_probability": probability,
                    "forecast_horizon": "5d",
                }
                for symbol, probability in zip(("SPY", "IWM", "AAPL"), probabilities)
            ],
        },
    }


def _write_candidate(tmp_path, **kwargs):
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(_candidate(**kwargs)), encoding="utf-8")
    return path


def test_healthy_candidate_is_certified_and_bound_to_artifact(tmp_path):
    path = _write_candidate(tmp_path)
    certification = certify_candidate(path)

    assert certification["passed"] is True
    assert validate_certification(path, certification) == []
    assert {row["classification"] for row in certification["feature_audit"]} == {
        "SERVABLE"
    }

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["bias"] = 1.0
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert "candidate_artifact_hash_mismatch" in validate_certification(
        path, certification
    )


@pytest.mark.parametrize(
    ("feature_name", "time_semantics", "expected"),
    [
        ("future_return_5d", "T_to_T+5 future", "NON_SERVABLE_FUTURE"),
        ("feature_rec_buy", "post_event", "NON_SERVABLE_POST_EVENT"),
        ("feature_probability_up", "at_T", "NON_SERVABLE_CIRCULAR"),
    ],
)
def test_time_unsafe_and_circular_features_fail_closed(
    tmp_path, feature_name, time_semantics, expected
):
    candidate = _candidate()
    contract = candidate["production_feature_contract"]
    contract["feature_columns"].append(feature_name)
    contract["required_features"].append(feature_name)
    candidate["feature_columns"].append(feature_name)
    contract["fill_values"][feature_name] = 0.0
    contract["features"].append(
        {
            "feature_name": feature_name,
            "source": "same_event",
            "available_at_prediction_time": True,
            "training_builder": "events.v1",
            "serving_builder": "events.v1",
            "transformation": "identity",
            "fill_policy": "training_median",
            "time_semantics": time_semantics,
        }
    )
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(candidate), encoding="utf-8")

    certification = certify_candidate(path)

    assert certification["passed"] is False
    assert expected in {row["classification"] for row in certification["feature_audit"]}


def test_frozen_upstream_probability_can_be_certified(tmp_path):
    candidate = _candidate()
    contract = candidate["production_feature_contract"]
    name = "feature_probability_up"
    candidate["feature_columns"].append(name)
    contract["feature_columns"].append(name)
    contract["required_features"].append(name)
    contract["fill_values"][name] = 0.5
    contract["features"].append(
        {
            "feature_name": name,
            "source": "frozen-upstream-v1",
            "upstream_model_version": "frozen-upstream-v1",
            "available_at_prediction_time": True,
            "training_builder": "upstream_probability.v1",
            "serving_builder": "upstream_probability.v1",
            "transformation": "identity_probability",
            "fill_policy": "training_median",
            "time_semantics": "at_T",
        }
    )
    for row in contract["representative_dry_runs"]:
        row["required_feature_count"] = 4
        row["available_feature_count"] = 4
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(candidate), encoding="utf-8")
    assert certify_candidate(path)["passed"] is True


@pytest.mark.parametrize(
    "mutation", ["constant", "ranking", "unsupported", "transform", "fill", "order"]
)
def test_structural_contract_failures_are_rejected(tmp_path, mutation):
    candidate = _candidate(
        probabilities=(0.5, 0.5, 0.5) if mutation == "constant" else (0.4, 0.5, 0.6)
    )
    contract = candidate["production_feature_contract"]
    if mutation == "ranking":
        contract["lane"] = "ranking"
    elif mutation == "unsupported":
        candidate["model_type"] = "ranking_model"
    elif mutation == "transform":
        contract["serving_transform"] = {"units": "percent"}
    elif mutation == "fill":
        contract["serving_fill_policy"] = "zero"
    elif mutation == "order":
        contract["feature_columns"] = list(reversed(contract["feature_columns"]))
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(candidate), encoding="utf-8")
    assert certify_candidate(path)["passed"] is False


def test_day14_requires_certification_even_with_force_and_promotes_next_version(
    tmp_path, monkeypatch
):
    comparison = tmp_path / "comparison.json"
    candidate = _write_candidate(tmp_path, version="challenger-v9")
    production = tmp_path / "production.json"
    certification_path = tmp_path / "certification.json"
    comparison.write_text(json.dumps({"candidate_win": False}), encoding="utf-8")
    production.write_text(json.dumps({"version": "alpha-atlas-v3"}), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "day14",
            "--comparison-report",
            str(comparison),
            "--candidate-model",
            str(candidate),
            "--production-model",
            str(production),
            "--force",
        ],
    )
    with pytest.raises(SystemExit, match="certification is missing"):
        day14_promote_candidate.main()

    certification_path.write_text(
        json.dumps(certify_candidate(candidate)), encoding="utf-8"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "day14",
            "--comparison-report",
            str(comparison),
            "--candidate-model",
            str(candidate),
            "--production-model",
            str(production),
            "--servability-certification",
            str(certification_path),
            "--force",
        ],
    )
    day14_promote_candidate.main()

    promoted = json.loads(production.read_text(encoding="utf-8"))
    assert promoted["version"] == "alpha-atlas-v4"
    assert promoted["production_servability_certification"]["passed"] is True
    assert production.with_suffix(".json.bak").exists()
