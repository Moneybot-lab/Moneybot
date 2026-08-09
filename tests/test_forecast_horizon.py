from types import SimpleNamespace

import pytest

from moneybot.services.forecast_horizon import resolve_forecast_horizon


def test_explicit_artifact_forecast_horizon_resolves():
    assert (
        resolve_forecast_horizon(artifact=SimpleNamespace(forecast_horizon="5d"))
        == "5d"
    )


def test_artifact_horizon_days_resolves():
    assert resolve_forecast_horizon(artifact={"horizon_days": 5}) == "5d"


def test_true_one_day_artifact_resolves():
    assert resolve_forecast_horizon(artifact={"forecast_horizon": "1 day"}) == "1d"


@pytest.mark.parametrize(
    "version", ["alpha-atlas-v1", "alpha-atlas-v2", "day1-logreg-v1"]
)
def test_proven_legacy_model_contract_resolves_to_five_days(version):
    assert resolve_forecast_horizon(model_version=version) == "5d"


def test_unknown_and_malformed_horizons_fail_safe():
    assert resolve_forecast_horizon(model_version="unproven-model") == "unknown"
    assert (
        resolve_forecast_horizon(
            explicit_horizon="soon", model_version="unproven-model"
        )
        == "unknown"
    )
    assert (
        resolve_forecast_horizon(
            explicit_horizon=float("nan"), model_version="unproven-model"
        )
        == "unknown"
    )
