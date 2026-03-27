from pathlib import Path

from moneybot.services.deterministic_advisor import DeterministicQuickAdvisor
from moneybot.services.deterministic_model import BaselineModelArtifact, save_artifact


def _write_artifact(tmp_path: Path) -> Path:
    artifact = BaselineModelArtifact(
        version="day1-logreg-v1",
        feature_columns=["return_1d", "return_5d", "rsi_14", "macd_hist", "vol_ratio_20d"],
        means=[0.0, 0.0, 50.0, 0.0, 1.0],
        stds=[1.0, 1.0, 10.0, 1.0, 1.0],
        weights=[0.3, 0.2, -0.1, 0.5, 0.1],
        bias=0.1,
        decision_threshold=0.55,
    )
    out = tmp_path / "model.json"
    save_artifact(artifact, out)
    return out


def test_predict_quick_decision_returns_none_when_artifact_missing(tmp_path: Path):
    svc = DeterministicQuickAdvisor(enabled=True, artifact_path=str(tmp_path / "missing.json"))
    out = svc.predict_quick_decision(signal_data={}, quote_data={})
    assert out is None


def test_predict_quick_decision_returns_structured_payload(tmp_path: Path):
    artifact_path = _write_artifact(tmp_path)
    svc = DeterministicQuickAdvisor(enabled=True, artifact_path=str(artifact_path))

    signal_data = {
        "technical": {"rsi": 45.0, "macd_histogram": 0.2},
        "volume_ratio": 1.4,
    }
    quote_data = {"price": 101.2, "change_percent": 1.6, "quote_source": "finnhub", "diagnostics": {"provider": "finnhub"}}

    out = svc.predict_quick_decision(signal_data=signal_data, quote_data=quote_data)

    assert out is not None
    assert out["recommendation"] in {"STRONG BUY", "BUY", "HOLD", "HOLD OFF FOR NOW"}
    assert out["decision_source"] == "deterministic_model"
    assert out["model_version"] == "day1-logreg-v1"
    assert 0.0 <= out["probability_up"] <= 1.0
    assert out["quote_source"] == "finnhub"


def test_predict_quick_decision_imputes_missing_features(tmp_path: Path):
    artifact_path = _write_artifact(tmp_path)
    svc = DeterministicQuickAdvisor(enabled=True, artifact_path=str(artifact_path))

    out = svc.predict_quick_decision(
        signal_data={"technical": {}},
        quote_data={"price": 99.0, "change_percent": None, "quote_source": "yfinance", "diagnostics": {}},
    )

    assert out is not None
    assert "return_1d" in out["imputed_features"]
    assert "rsi_14" in out["imputed_features"]


def test_predict_portfolio_position_returns_hold_when_context_missing(tmp_path: Path):
    artifact_path = _write_artifact(tmp_path)
    svc = DeterministicQuickAdvisor(enabled=True, artifact_path=str(artifact_path))

    out = svc.predict_portfolio_position(
        symbol="AAPL",
        entry_price=None,
        current_price=None,
        shares=1,
        signal_data={"technical": {"rsi": 55, "macd_histogram": 0.1}, "volume_ratio": 1.2},
        quote_data={"price": 100.0, "change_percent": 0.4, "quote_source": "finnhub", "diagnostics": {}},
    )

    assert out is not None
    assert out["mode"] == "deterministic_model"
    assert out["advice"] == "HOLD"
    assert out["decision_source"] == "deterministic_model"


def test_predict_portfolio_position_can_return_sell_on_weak_prob_and_profit(tmp_path: Path):
    artifact_path = _write_artifact(tmp_path)
    svc = DeterministicQuickAdvisor(enabled=True, artifact_path=str(artifact_path))

    out = svc.predict_portfolio_position(
        symbol="AAPL",
        entry_price=100.0,
        current_price=112.0,
        shares=2,
        signal_data={"technical": {"rsi": 70, "macd_histogram": -0.2}, "volume_ratio": 0.8},
        quote_data={"price": 112.0, "change_percent": -2.0, "quote_source": "finnhub", "diagnostics": {}},
    )

    assert out is not None
    assert out["advice"] in {"HOLD", "SELL", "BUY"}
    assert out["model_version"] == "day1-logreg-v1"
    assert isinstance(out["confidence"], float)


def test_predict_quick_decision_supports_threshold_overrides(tmp_path: Path):
    artifact_path = _write_artifact(tmp_path)
    svc = DeterministicQuickAdvisor(
        enabled=True,
        artifact_path=str(artifact_path),
        quick_buy_threshold=0.80,
        quick_strong_buy_threshold=0.90,
    )

    out = svc.predict_quick_decision(
        signal_data={"technical": {"rsi": 45.0, "macd_histogram": 0.2}, "volume_ratio": 1.4},
        quote_data={"price": 101.2, "change_percent": 1.6, "quote_source": "finnhub", "diagnostics": {}},
    )

    assert out is not None
    assert out["recommendation"] in {"HOLD", "HOLD OFF FOR NOW"}


def test_predict_quick_decision_can_abstain_to_neutral_hold(tmp_path: Path):
    artifact_path = _write_artifact(tmp_path)
    svc = DeterministicQuickAdvisor(
        enabled=True,
        artifact_path=str(artifact_path),
        quick_buy_threshold=0.55,
        quick_abstain_margin=0.08,
    )

    out = svc.predict_quick_decision(
        signal_data={"technical": {"rsi": 49.0, "macd_histogram": 0.0}, "volume_ratio": 1.0},
        quote_data={"price": 100.0, "change_percent": 0.0, "quote_source": "finnhub", "diagnostics": {}},
    )

    assert out is not None
    assert out["recommendation"] == "HOLD"
    assert out["abstained"] is True
    assert out["decision_band_low"] == 0.47
    assert out["decision_band_high"] == 0.63


def test_predict_portfolio_position_supports_threshold_overrides(tmp_path: Path):
    artifact_path = _write_artifact(tmp_path)
    svc = DeterministicQuickAdvisor(
        enabled=True,
        artifact_path=str(artifact_path),
        portfolio_sell_prob_threshold=0.70,
        portfolio_sell_profit_threshold_pct=5.0,
    )

    out = svc.predict_portfolio_position(
        symbol="AAPL",
        entry_price=100.0,
        current_price=112.0,
        shares=2,
        signal_data={"technical": {"rsi": 70, "macd_histogram": -0.2}, "volume_ratio": 0.8},
        quote_data={"price": 112.0, "change_percent": -2.0, "quote_source": "finnhub", "diagnostics": {}},
    )

    assert out is not None
    assert out["advice"] == "SELL"
