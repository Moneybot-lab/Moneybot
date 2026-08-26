import json
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from moneybot.services.alpha_atlas_v3_features import (
    ALPHA_ATLAS_V3_FEATURES,
    FORBIDDEN_V3_FEATURE_FRAGMENTS,
    build_alpha_atlas_v3_features,
    ordered_feature_row,
    validate_v3_feature_columns,
)
from moneybot.services.deterministic_advisor import DeterministicQuickAdvisor
from moneybot.services.deterministic_model import BaselineModelArtifact, save_artifact
from moneybot.services.production_servability import certify_candidate
from scripts.build_massive_decision_training_rows import (
    build_training_rows_from_raw_market,
)
from scripts.train_alpha_atlas_v3_candidate import (
    attach_v3_contract,
    build_serving_dry_runs,
)
from scripts.train_massive_baseline_model import _fill_from_fit, _market_feature_columns
from scripts.day14_promote_candidate import _next_alpha_atlas_version


def _bars(symbol, *, base=20.0, slope=0.2, volume_base=1_000_000.0, count=70):
    days = []
    candidate = date(2026, 1, 2)
    while len(days) < count:
        if candidate.weekday() < 5:
            days.append(candidate.isoformat())
        candidate += timedelta(days=1)
    return [
        {
            "symbol": symbol,
            "date": days[index],
            "open": base + index * slope - 0.1,
            "high": base + index * slope + 0.4,
            "low": base + index * slope - 0.4,
            "close": base + index * slope + ((index % 4) * 0.03),
            "volume": volume_base + index * 10_000 + (index % 3) * 50_000,
        }
        for index in range(count)
    ]


def _event(day="2026-02-20", symbol="AAPL"):
    return {
        "ts": int(
            datetime.fromisoformat(day)
            .replace(hour=12, tzinfo=timezone.utc)
            .timestamp()
        ),
        "symbol": symbol,
        "endpoint": "quick_ask",
        "decision_source": "rule_based",
        "payload": {"recommendation": "BUY", "probability_up": 0.99},
    }


def test_exact_backward_market_calculations_and_order():
    symbol = _bars("AAPL")
    spy = _bars("SPY", base=100.0, slope=0.1)
    values = build_alpha_atlas_v3_features(symbol_bars=symbol, spy_bars=spy)

    closes = [row["close"] for row in symbol]
    expected_1d = round(closes[-1] / closes[-2] - 1.0, 6)
    expected_5d = round(closes[-1] / closes[-6] - 1.0, 6)
    expected_20d = round(closes[-1] / closes[-21] - 1.0, 6)
    expected_volume_ratio = round(
        symbol[-1]["volume"] / np.mean([row["volume"] for row in symbol[-20:]]), 6
    )

    assert values["feature_return_1d_lagged"] == expected_1d
    assert values["feature_return_5d_lagged"] == expected_5d
    assert values["feature_return_20d_lagged"] == expected_20d
    assert values["feature_volume_ratio_20d"] == expected_volume_ratio
    assert 0 <= values["feature_rsi_14"] <= 100
    assert values["feature_macd_hist"] is not None
    assert values["feature_price_vs_sma_20"] is not None
    assert values["feature_volatility_20d"] is not None
    assert values["feature_symbol_minus_spy_5d"] == round(
        values["feature_return_5d_lagged"] - values["feature_spy_return_5d"], 6
    )
    assert ordered_feature_row(values, reversed(ALPHA_ATLAS_V3_FEATURES)) == [
        values[name] for name in reversed(ALPHA_ATLAS_V3_FEATURES)
    ]


def test_training_builder_uses_same_shared_values_and_future_return_only_as_label():
    market = {"AAPL": _bars("AAPL"), "SPY": _bars("SPY", base=100, slope=0.1)}
    event = _event(market["AAPL"][50]["date"])
    rows, summary = build_training_rows_from_raw_market(
        [event], market, horizon_days=5, split_events=[]
    )
    assert summary["rows_joined"] == 1
    row = rows[0]
    asof_bars = [
        bar for bar in market["AAPL"] if bar["date"] <= row["market_asof_date"]
    ]
    spy_asof = [bar for bar in market["SPY"] if bar["date"] <= row["market_asof_date"]]
    shared = build_alpha_atlas_v3_features(symbol_bars=asof_bars, spy_bars=spy_asof)
    assert {name: row[name] for name in ALPHA_ATLAS_V3_FEATURES} == shared
    assert row["label_asof_date"] > row["market_asof_date"]
    assert row["return_5d"] == round(
        float(row["raw_exit_price"]) / float(row["raw_entry_price"]) - 1.0, 6
    )
    assert "return_5d" not in ALPHA_ATLAS_V3_FEATURES
    assert "probability_up" not in ALPHA_ATLAS_V3_FEATURES
    assert all(
        "rec_" not in feature and "source_" not in feature
        for feature in ALPHA_ATLAS_V3_FEATURES
    )


def test_allowlist_rejects_every_event_output_or_future_feature():
    forbidden = [
        "feature_probability_up",
        "feature_rec_buy",
        "feature_source_rule_based",
        "feature_future_return_5d",
        "feature_forward_close",
        "feature_label_gain_5d",
    ]
    assert validate_v3_feature_columns(ALPHA_ATLAS_V3_FEATURES) == []
    assert validate_v3_feature_columns(forbidden) == forbidden
    assert set(FORBIDDEN_V3_FEATURE_FRAGMENTS).isdisjoint(ALPHA_ATLAS_V3_FEATURES)


def test_fit_period_only_fill_values_are_reused():
    pd = pytest.importorskip("pandas")
    fit = pd.DataFrame({"feature_rsi_14": [10.0, 20.0, None]})
    later = pd.DataFrame({"feature_rsi_14": [None, 999.0]})
    fit_out, [later_out], fills = _fill_from_fit(fit, [later], ["feature_rsi_14"])
    assert fills == {"feature_rsi_14": 15.0}
    assert fit_out.iloc[-1]["feature_rsi_14"] == 15.0
    assert later_out.iloc[0]["feature_rsi_14"] == 15.0


class _HistoryService:
    def __init__(self, histories):
        self.histories = histories

    def get_price_history_data(self, symbol, days=90):
        return {
            "bars": self.histories[symbol],
            "source": "massive",
            "adjusted_for_splits": True,
        }


def _artifact(path):
    artifact = BaselineModelArtifact(
        version="candidate-alpha-atlas-v3-clean-v1",
        feature_columns=list(ALPHA_ATLAS_V3_FEATURES),
        means=[0.0] * len(ALPHA_ATLAS_V3_FEATURES),
        stds=[1.0] * len(ALPHA_ATLAS_V3_FEATURES),
        weights=[0.4, -0.3, 0.2, 0.01, 0.2, 0.15, 0.3, -0.2, 0.1, 0.2, 0.1],
        bias=-0.1,
        decision_threshold=0.55,
        lineage={"lineage_id": "recipe-v3", "recipe_hash": "v3-hash"},
        forecast_horizon="5d",
    )
    save_artifact(artifact, str(path))
    payload = json.loads(path.read_text())
    payload["model_type"] = "logistic_regression"
    payload["feature_fill_values"] = {name: 0.0 for name in ALPHA_ATLAS_V3_FEATURES}
    path.write_text(json.dumps(payload))


def test_dry_runs_contract_certification_and_live_advisor_are_non_degenerate(tmp_path):
    path = tmp_path / "candidate.json"
    _artifact(path)
    histories = {"SPY": _bars("SPY", base=100, slope=0.08)}
    for index, symbol in enumerate(("AAPL", "APLD", "UMAC", "ASPI", "ACHR")):
        histories[symbol] = _bars(
            symbol,
            base=8 + index * 15,
            slope=(-0.04 + index * 0.08),
            volume_base=500_000 + index * 250_000,
        )
    service = _HistoryService(histories)
    runs = build_serving_dry_runs(
        artifact_path=path,
        market_service=service,
        symbols=("AAPL", "APLD", "UMAC", "ASPI", "ACHR"),
    )
    attach_v3_contract(path, runs)
    certification = certify_candidate(path)

    assert certification["passed"] is True
    assert all(certification["gates"].values())
    assert {item["classification"] for item in certification["feature_audit"]} == {
        "SERVABLE"
    }
    assert len({round(run["raw_probability"], 8) for run in runs}) > 1
    assert all(run["missing_required_features"] == [] for run in runs)
    assert all(run["imputed_feature_count"] == 0 for run in runs)

    advisor = DeterministicQuickAdvisor(
        enabled=True, artifact_path=str(path), rollout_percentage=100
    )
    advisor.set_market_history_service(service)
    first = advisor.predict_quick_decision(
        signal_data={}, quote_data={"price": 10}, symbol="AAPL"
    )
    second = advisor.predict_quick_decision(
        signal_data={}, quote_data={"price": 20}, symbol="ASPI"
    )
    assert first["forecast_horizon"] == "5d"
    assert first["feature_contract_servable"] is True
    assert first["feature_contract_imputed_count"] == 0
    assert first["probability_up"] != second["probability_up"]


def test_missing_history_and_all_fill_vector_fail_safe(tmp_path):
    path = tmp_path / "candidate.json"
    _artifact(path)
    advisor = DeterministicQuickAdvisor(
        enabled=True, artifact_path=str(path), rollout_percentage=100
    )
    advisor.set_market_history_service(_HistoryService({"SPY": [], "AAPL": []}))
    assert (
        advisor.predict_quick_decision(signal_data={}, quote_data={}, symbol="AAPL")
        is None
    )
    assert (
        advisor.last_feature_contract_diagnostics["feature_contract_servable"] is False
    )


def test_market_feature_selector_excludes_forbidden_event_fields():
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame({name: [1.0] for name in ALPHA_ATLAS_V3_FEATURES})
    frame["feature_probability_up"] = 0.9
    frame["feature_rec_buy"] = 1.0
    frame["feature_future_return_5d"] = 0.5
    selected = _market_feature_columns(frame)
    assert set(ALPHA_ATLAS_V3_FEATURES) <= set(selected)
    assert "feature_probability_up" not in selected
    assert "feature_rec_buy" not in selected
    assert "feature_future_return_5d" not in selected


def test_manual_promotion_version_progression_remains_unbounded():
    assert _next_alpha_atlas_version("alpha-atlas-v2") == "alpha-atlas-v3"
    assert _next_alpha_atlas_version("alpha-atlas-v3") == "alpha-atlas-v4"
    assert _next_alpha_atlas_version("alpha-atlas-v19") == "alpha-atlas-v20"
