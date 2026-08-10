from pathlib import Path

import numpy as np
import pytest

from moneybot.services.alpha_atlas_feature_contract import (
    ALPHA_ATLAS_V2_FEATURES,
    NON_SERVABLE_V2_FEATURES,
    build_alpha_atlas_event_features,
)
from moneybot.services.deterministic_advisor import DeterministicQuickAdvisor
from moneybot.services.deterministic_model import (
    BaselineModelArtifact,
    predict_proba,
    save_artifact,
)


def _v2_artifact() -> BaselineModelArtifact:
    return BaselineModelArtifact(
        version="alpha-atlas-v2",
        feature_columns=list(ALPHA_ATLAS_V2_FEATURES),
        means=[10.0] * len(ALPHA_ATLAS_V2_FEATURES),
        stds=[1.0] * len(ALPHA_ATLAS_V2_FEATURES),
        weights=[0.001 * (idx + 1) for idx in range(len(ALPHA_ATLAS_V2_FEATURES))],
        bias=-0.6375505152781077,
        decision_threshold=0.55,
        forecast_horizon="5d",
    )


def _advisor(tmp_path: Path) -> DeterministicQuickAdvisor:
    path = tmp_path / "v2.json"
    save_artifact(_v2_artifact(), path)
    return DeterministicQuickAdvisor(enabled=True, artifact_path=str(path))


def test_mean_vector_reproduces_confirmed_intercept_probability():
    artifact = _v2_artifact()
    probability = float(predict_proba(artifact, np.asarray(artifact.means))[0])
    assert probability == pytest.approx(0.34580046, abs=1e-7)


def test_contract_handles_every_v2_feature_and_exact_encodings():
    values = build_alpha_atlas_event_features(
        endpoint="quick_ask",
        decision_source="rule_based",
        recommendation="BUY",
        quote={"price": 20.0, "change_percent": 4.5},
        signals={
            "features": {"return_1d": 0.01, "return_5d": 0.08},
            "technical": {"rsi": 61.0, "macd_histogram": 0.4},
            "volume_ratio": 2.2,
        },
        prior_probability=0.62,
    )
    assert set(values) == set(ALPHA_ATLAS_V2_FEATURES)
    assert values["feature_change_percent"] == 4.5
    assert values["feature_return_1d"] == 0.01
    assert values["feature_return_5d"] == 0.08
    assert values["feature_endpoint_quick_ask"] == 1.0
    assert values["feature_rec_buy"] == 1.0
    assert values["feature_rec_positive"] == 1.0
    assert values["feature_source_rule_based"] == 1.0
    assert values["feature_probability_up"] == 0.62


@pytest.mark.parametrize(
    "endpoint", ["quick_ask", "user_watchlist", "hot_momentum_buys"]
)
def test_endpoint_one_hot_matches_training_contract(endpoint):
    values = build_alpha_atlas_event_features(
        endpoint=endpoint,
        decision_source="rule_based",
        recommendation="HOLD",
        quote={},
        signals={},
    )
    assert values[f"feature_endpoint_{endpoint}"] == 1.0
    assert (
        sum(
            values[f"feature_endpoint_{name}"]
            for name in ("quick_ask", "user_watchlist", "hot_momentum_buys")
        )
        == 1.0
    )


def test_v2_rows_follow_artifact_order_and_vary_by_market_input(tmp_path):
    advisor = _advisor(tmp_path)
    signal_a = {
        "action": "BUY",
        "technical": {"rsi": 40.0, "macd_histogram": -0.2},
        "volume_ratio": 1.1,
        "features": {"return_1d": -0.01, "return_5d": -0.04},
    }
    signal_b = {
        "action": "HOLD",
        "technical": {"rsi": 70.0, "macd_histogram": 0.8},
        "volume_ratio": 4.0,
        "features": {"return_1d": 0.03, "return_5d": 0.12},
    }
    row_a, _, diag_a = advisor._build_feature_row_with_diagnostics(
        signal_a, {"price": 5.0, "change_percent": -2.0}
    )
    row_b, _, diag_b = advisor._build_feature_row_with_diagnostics(
        signal_b, {"price": 50.0, "change_percent": 8.0}
    )
    expected = build_alpha_atlas_event_features(
        endpoint="quick_ask",
        decision_source="rule_based",
        recommendation="BUY",
        quote={"price": 5.0, "change_percent": -2.0},
        signals=signal_a,
    )

    for idx, name in enumerate(advisor.artifact.feature_columns):
        assert row_a[idx] == pytest.approx(
            expected[name]
            if expected[name] is not None
            else advisor.artifact.means[idx]
        )
    assert not np.array_equal(row_a, row_b)
    assert float(predict_proba(advisor.artifact, row_a)[0]) != float(
        predict_proba(advisor.artifact, row_b)[0]
    )
    assert diag_a["feature_contract_available_count"] > 0
    assert diag_b["feature_contract_imputed_count"] < len(ALPHA_ATLAS_V2_FEATURES)


def test_probability_is_prior_only_and_current_v2_output_is_never_reused():
    missing = build_alpha_atlas_event_features(
        endpoint="quick_ask",
        decision_source="rule_based",
        recommendation="BUY",
        quote={},
        signals={},
    )
    supplied = build_alpha_atlas_event_features(
        endpoint="quick_ask",
        decision_source="rule_based",
        recommendation="BUY",
        quote={},
        signals={},
        prior_probability=0.73,
    )
    assert missing["feature_probability_up"] is None
    assert supplied["feature_probability_up"] == 0.73


def test_nonservable_v2_contract_fails_safe_instead_of_returning_intercept(tmp_path):
    advisor = _advisor(tmp_path)
    result = advisor.predict_quick_decision(
        signal_data={
            "action": "BUY",
            "technical": {"rsi": 55.0, "macd_histogram": 0.2},
            "volume_ratio": 1.5,
        },
        quote_data={"price": 12.0, "change_percent": 2.0},
        symbol="APLD",
    )
    assert result is None
    diagnostics = advisor.last_feature_contract_diagnostics
    assert diagnostics["feature_contract_servable"] is False
    assert (
        set(diagnostics["feature_contract_blocking_features"])
        == NON_SERVABLE_V2_FEATURES
    )
    assert "circular" in diagnostics["feature_contract_reason"]


def test_five_materially_different_scenarios_do_not_build_constant_rows(tmp_path):
    advisor = _advisor(tmp_path)
    rows = []
    raw_probabilities = []
    for idx in range(5):
        signal = {
            "action": ["BUY", "HOLD", "SELL", "STRONG BUY", "HOLD OFF FOR NOW"][idx],
            "technical": {"rsi": 30.0 + idx * 10.0, "macd_histogram": -0.4 + idx * 0.2},
            "volume_ratio": 1.0 + idx,
            "features": {
                "return_1d": -0.02 + idx * 0.01,
                "return_5d": -0.08 + idx * 0.04,
            },
        }
        row, _, _ = advisor._build_feature_row_with_diagnostics(
            signal, {"price": 5.0 + idx * 15.0, "change_percent": -4.0 + idx * 3.0}
        )
        rows.append(tuple(row.tolist()))
        raw_probabilities.append(
            round(float(predict_proba(advisor.artifact, row)[0]), 8)
        )
    assert len(set(rows)) == 5
    assert len(set(raw_probabilities)) == 5
