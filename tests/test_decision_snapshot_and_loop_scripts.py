from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from moneybot.services.decision_log import DecisionLogger, read_decision_events
from moneybot.services.decision_snapshot import build_decision_snapshot
from scripts import day8_build_decision_training_dataset as day8
from scripts import day10_train_candidate_model as day10
from scripts import day11_compare_candidate_vs_production as day11
from scripts import day14_promote_candidate as day14_promote
from scripts.day8_build_decision_training_dataset import build_rows


def test_decision_snapshot_structure_valid():
    snapshot = build_decision_snapshot(
        symbol="aapl",
        endpoint="quick_ask",
        decision_source="deterministic_model",
        recommendation="buy",
        probability_up=0.63,
        model_version="alpha-atlas-v1",
        calibration_version="cal-v1",
        quote={"price": 180.5, "change_percent": 1.2, "source": "finnhub"},
        features={"return_1d": 0.01},
        signals={"trend": "up"},
        explanation={"rationale": "Momentum", "risk_notes": ["volatility"], "next_checks": ["earnings"]},
        personalization={"profile_version": 3, "decision": {"changed": True}},
        market_data={"quote_source": "massive", "history_source": "massive", "mixed_sources": False},
    )

    assert snapshot["schema_version"] == "decision_snapshot.v1"
    assert snapshot["symbol"] == "AAPL"
    assert snapshot["quote"]["price"] == 180.5
    assert snapshot["features"]["return_1d"] == 0.01
    assert snapshot["explanation"]["risk_notes"] == ["volatility"]
    assert snapshot["personalization"]["profile_version"] == 3
    assert snapshot["personalization"]["decision"]["changed"] is True
    assert snapshot["market_data"]["mixed_sources"] is False
    assert snapshot["quote"]["source_mode"] is None


def test_decision_logger_writes_snapshot(tmp_path):
    path = tmp_path / "events.jsonl"
    logger = DecisionLogger(enabled=True, output_path=str(path))
    logger.log(
        endpoint="quick_ask",
        symbol="AAPL",
        decision_source="deterministic_model",
        payload={"recommendation": "BUY"},
        snapshot={"schema_version": "decision_snapshot.v1", "recommendation": "BUY"},
    )

    events = read_decision_events(str(path), limit=10)
    assert len(events) == 1
    assert events[0]["snapshot"]["schema_version"] == "decision_snapshot.v1"


def test_day8_builder_skips_immature_rows(monkeypatch):
    now = datetime.now(timezone.utc)
    mature_ts = int((now - timedelta(days=10)).timestamp())
    fresh_ts = int((now - timedelta(days=1)).timestamp())

    events = [
        {"ts": mature_ts, "symbol": "AAPL", "endpoint": "user_watchlist", "decision_source": "ai_enhanced", "payload": {"recommendation": "BUY"}},
        {"ts": fresh_ts, "symbol": "MSFT", "endpoint": "user_watchlist", "decision_source": "ai_enhanced", "payload": {"recommendation": "BUY"}},
    ]

    monkeypatch.setattr("scripts.day8_build_decision_training_dataset._future_return", lambda symbol, ts, days, bad_symbol_cache=None: 0.02)
    rows, summary = build_rows(events, horizon_days=5)

    assert summary["rows_scanned"] == 2
    assert summary["mature_rows"] == 1
    assert summary["labeled_rows"] == 1
    assert len(rows) == 1


def test_day8_builder_outputs_labeled_rows_with_snapshot_fields(monkeypatch):
    now = datetime.now(timezone.utc)
    mature_ts = int((now - timedelta(days=10)).timestamp())
    events = [
        {
            "ts": mature_ts,
            "symbol": "AAPL",
            "endpoint": "quick_ask",
            "decision_source": "ai_enhanced",
            "payload": {"recommendation": "SELL", "model_version": "fallback-v1"},
            "snapshot": {
                "recommendation": "BUY",
                "probability_up": 0.61,
                "model_version": "snap-v1",
                "features": {"return_1d": 0.01, "rsi_14": 55.0},
            },
            "experiment": {"experiment_id": "exp-a", "cohort_id": "treatment", "rollout_dry_run": True},
        }
    ]
    monkeypatch.setattr("scripts.day8_build_decision_training_dataset._future_return", lambda symbol, ts, days, bad_symbol_cache=None: 0.03 if days == 1 else -0.01)

    rows, _ = build_rows(events, horizon_days=5)
    assert rows[0]["recommendation"] == "BUY"
    assert rows[0]["probability_up"] == 0.61
    assert rows[0]["model_version"] == "snap-v1"
    assert rows[0]["outcome_5d"] in {"correct", "incorrect"}
    assert rows[0]["return_1d"] == 0.03
    assert "feature_return_1d" in rows[0]
    assert "feature_rsi_14" in rows[0] or "feature_rsi" in rows[0]
    assert rows[0]["label_up_5d"] in {0, 1}
    assert rows[0]["experiment_id"] == "exp-a"
    assert rows[0]["cohort_id"] == "treatment"
    assert rows[0]["rollout_dry_run"] is True
    assert rows[0]["has_snapshot"] == 1
    assert rows[0]["has_feature_map"] == 1
    assert rows[0]["return_bin_5d"] in {"big_loss", "loss", "flat", "gain", "big_gain"}


def test_day8_builder_backward_compatible_without_snapshot(monkeypatch):
    now = datetime.now(timezone.utc)
    mature_ts = int((now - timedelta(days=10)).timestamp())
    events = [
        {
            "ts": mature_ts,
            "symbol": "TSLA",
            "endpoint": "quick_ask",
            "decision_source": "rule_based",
            "payload": {"recommendation": "BUY", "probability_up": 0.55},
        }
    ]
    monkeypatch.setattr("scripts.day8_build_decision_training_dataset._future_return", lambda symbol, ts, days, bad_symbol_cache=None: 0.02 if days == 1 else 0.01)
    rows, _ = build_rows(events, horizon_days=5)
    assert rows[0]["recommendation"] == "BUY"
    assert rows[0]["feature_probability_up"] == 0.55
    assert rows[0]["feature_return_1d"] == 0.02
    assert rows[0]["label_up_5d"] == 1
    assert rows[0]["has_snapshot"] == 0
    assert rows[0]["experiment_id"] == "default"
    assert rows[0]["cohort_id"] == "unknown"


def test_day8_symbol_quality_filter_normalizes_and_rejects(monkeypatch):
    mature_ts = int((datetime.now(timezone.utc) - timedelta(days=12)).timestamp())
    events = [
        {"ts": mature_ts, "symbol": "NVDIA", "endpoint": "quick_ask", "payload": {"recommendation": "BUY"}},
        {"ts": mature_ts, "symbol": "MAD.TO", "endpoint": "quick_ask", "payload": {"recommendation": "BUY"}},
        {"ts": mature_ts, "symbol": "FDRXX", "endpoint": "quick_ask", "payload": {"recommendation": "BUY"}},
    ]
    seen_symbols = []

    def fake_return(symbol, ts, days, bad_symbol_cache=None):
        seen_symbols.append(symbol)
        return 0.02

    monkeypatch.setattr("scripts.day8_build_decision_training_dataset._future_return", fake_return)

    rows, summary = build_rows(events, horizon_days=5, bad_symbol_cache={"symbols": {}})

    assert [row["symbol"] for row in rows] == ["NVDA"]
    assert seen_symbols == ["NVDA", "NVDA"]
    assert summary["symbols_normalized"] == 1
    assert summary["symbols_rejected"] == 2


def test_day8_symbol_quality_filter_uses_bad_symbol_cache(monkeypatch):
    mature_ts = int((datetime.now(timezone.utc) - timedelta(days=12)).timestamp())
    events = [
        {"ts": mature_ts, "symbol": "ADLX", "endpoint": "quick_ask", "payload": {"recommendation": "BUY"}},
        {"ts": mature_ts, "symbol": "AAPL", "endpoint": "quick_ask", "payload": {"recommendation": "BUY"}},
    ]
    cache = {"symbols": {"AAPL": {"failures": 2, "reason": "no_price_data"}}}

    monkeypatch.setattr("scripts.day8_build_decision_training_dataset._future_return", lambda symbol, ts, days, bad_symbol_cache=None: 0.02)

    rows, summary = build_rows(events, horizon_days=5, bad_symbol_cache=cache)

    assert rows == []
    assert summary["symbols_rejected"] == 2


def test_day8_records_yfinance_failures_in_bad_symbol_cache(monkeypatch):
    cache = {"symbols": {}}

    monkeypatch.setattr("scripts.day8_build_decision_training_dataset.yf.download", lambda *args, **kwargs: [])

    assert day8._future_return("OLFS", int(datetime.now(timezone.utc).timestamp()) - 864000, 1, cache) is None

    assert cache["symbols"]["OLFS"]["failures"] == 1
    assert cache["symbols"]["OLFS"]["reason"] == "no_price_data"


def test_day10_uses_return_buckets_for_gain_target():
    import pandas as pd

    df = pd.DataFrame(
        [
            {"return_5d": -0.08},
            {"return_5d": -0.01},
            {"return_5d": 0.001},
            {"return_5d": 0.02},
            {"return_5d": 0.12},
        ]
    )

    labeled = day10._ensure_return_bucket_labels(df)

    assert labeled["return_bin_5d"].tolist() == ["big_loss", "loss", "flat", "gain", "big_gain"]
    assert labeled["label_gain_5d"].tolist() == [0.0, 0.0, 0.0, 1.0, 1.0]


def test_day11_return_bins_drive_gain_evaluation():
    import pandas as pd

    df = pd.DataFrame({"return_5d": [-0.08, -0.01, 0.001, 0.02, 0.12]})

    binned = day11._ensure_return_bins(df)

    assert binned["return_bin_5d"].tolist() == ["big_loss", "loss", "flat", "gain", "big_gain"]
    y = binned["return_bin_5d"].fillna("").astype(str).isin(day11.TARGET_GAIN_BUCKETS).astype(int).tolist()
    assert y == [0, 0, 0, 1, 1]


def test_day10_bucket_sample_weights_prioritize_tail_outcomes():
    import pandas as pd

    df = pd.DataFrame({"return_bin_5d": ["big_loss", "loss", "flat", "gain", "big_gain", "unknown"]})

    weights = day10._bucket_sample_weights(df)

    assert weights.tolist() == [2.0, 1.25, 0.75, 1.0, 2.0, 1.0]


def test_day11_bucket_signal_rates_track_big_loss_and_big_gain():
    import numpy as np
    import pandas as pd

    df = pd.DataFrame({"return_bin_5d": ["big_loss", "big_loss", "big_gain", "big_gain", "gain"]})
    rates = day11._bucket_signal_rates(df, np.array([1, 0, 1, 0, 1]))

    assert rates["big_loss_prediction_rate"] == 0.5
    assert rates["big_gain_capture_rate"] == 0.5
    assert rates["big_loss_predictions"] == 1
    assert rates["big_gain_predictions"] == 1


def test_day10_candidate_trainer_fails_if_rows_below_min(tmp_path, monkeypatch):
    input_path = tmp_path / "train.jsonl"
    input_path.write_text(json.dumps({"ts": 1, "return_5d": 0.01, "return_1d": 0.01, "x1": 1.0}) + "\n", encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        [
            "day10_train_candidate_model.py",
            "--input",
            str(input_path),
            "--output-model",
            str(tmp_path / "candidate.json"),
            "--min-rows",
            "200",
        ],
    )
    with pytest.raises(SystemExit):
        day10.main()


def test_day10_prepares_fallback_numeric_features_when_snapshot_features_missing():
    # smoke-check helper path through frame prep/select without requiring custom feature dicts
    import pandas as pd

    df = pd.DataFrame(
        [
            {"ts": 1, "recommendation": "BUY", "probability_up": None, "feature_return_1d": 0.02, "return_5d": 0.03},
            {"ts": 2, "recommendation": "SELL", "probability_up": 0.3, "feature_return_1d": -0.01, "return_5d": -0.02},
        ]
    )
    prepared = day10._prepare_frame(df)
    cols = day10._select_feature_columns(prepared)
    assert "feature_return_1d" in cols


def test_day10_trains_when_feature_columns_exist(tmp_path, monkeypatch):
    rows = [
        {"ts": 1, "feature_return_1d": 0.01, "feature_price": 100.0, "label_up_5d": 1, "return_1d": 0.01, "return_5d": 0.02},
        {"ts": 2, "feature_return_1d": -0.02, "feature_price": 99.0, "label_up_5d": 0, "return_1d": -0.02, "return_5d": -0.03},
        {"ts": 3, "feature_return_1d": 0.03, "feature_price": 101.0, "label_up_5d": 1, "return_1d": 0.03, "return_5d": 0.04},
        {"ts": 4, "feature_return_1d": -0.01, "feature_price": 98.0, "label_up_5d": 0, "return_1d": -0.01, "return_5d": -0.01},
        {"ts": 5, "feature_return_1d": 0.02, "feature_price": 102.0, "label_up_5d": 1, "return_1d": 0.02, "return_5d": 0.03},
    ]
    input_path = tmp_path / "decision_training_snapshot.jsonl"
    input_path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    output_model = tmp_path / "candidate_model.json"

    monkeypatch.setattr(
        "sys.argv",
        [
            "day10_train_candidate_model.py",
            "--input",
            str(input_path),
            "--output-model",
            str(output_model),
            "--train-ratio",
            "0.8",
            "--min-rows",
            "4",
        ],
    )
    day10.main()
    assert output_model.exists()


def test_day10_trains_with_sparse_feature_columns_no_complete_raw_rows(tmp_path, monkeypatch):
    rows = [
        {"ts": 1, "feature_alpha": 0.10, "label_up_5d": 1, "return_1d": 0.01, "return_5d": 0.02},
        {"ts": 2, "feature_alpha": 0.20, "label_up_5d": 0, "return_1d": -0.01, "return_5d": -0.02},
        {"ts": 3, "feature_alpha": 0.30, "label_up_5d": 1, "return_1d": 0.02, "return_5d": 0.03},
        {"ts": 4, "feature_beta": 1.10, "label_up_5d": 0, "return_1d": -0.02, "return_5d": -0.03},
        {"ts": 5, "feature_beta": 1.20, "label_up_5d": 1, "return_1d": 0.03, "return_5d": 0.04},
        {"ts": 6, "feature_beta": 1.30, "label_up_5d": 0, "return_1d": -0.03, "return_5d": -0.04},
    ]
    input_path = tmp_path / "sparse_decision_training_snapshot.jsonl"
    input_path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    output_model = tmp_path / "candidate_model.json"

    monkeypatch.setattr(
        "sys.argv",
        [
            "day10_train_candidate_model.py",
            "--input",
            str(input_path),
            "--output-model",
            str(output_model),
            "--train-ratio",
            "0.8",
            "--min-rows",
            "6",
        ],
    )

    day10.main()

    assert output_model.exists()
    metrics = day11._evaluate(str(output_model), day11._load_jsonl(str(input_path)))
    assert metrics["rows"] == 6


def test_day11_compare_detects_profit_aware_win_and_loss():
    win, win_reasons = day11._decide(
        {"accuracy": 0.60, "brier_score": 0.18, "avg_return": 0.015, "downside_risk": 0.02, "big_loss_prediction_rate": 0.10, "big_gain_capture_rate": 0.80, "rows": 250},
        {"accuracy": 0.57, "brier_score": 0.20, "avg_return": 0.010, "downside_risk": 0.03, "big_loss_prediction_rate": 0.20, "big_gain_capture_rate": 0.60, "rows": 250},
        min_rows=200,
    )
    worse_return_loss, loss_reasons = day11._decide(
        {"accuracy": 0.60, "brier_score": 0.18, "avg_return": -0.015, "downside_risk": 0.04, "big_loss_prediction_rate": 0.10, "big_gain_capture_rate": 0.80, "rows": 250},
        {"accuracy": 0.57, "brier_score": 0.20, "avg_return": -0.010, "downside_risk": 0.03, "big_loss_prediction_rate": 0.20, "big_gain_capture_rate": 0.60, "rows": 250},
        min_rows=200,
    )
    lower_downside_win, _ = day11._decide(
        {"accuracy": 0.60, "brier_score": 0.18, "avg_return": -0.015, "downside_risk": 0.02, "big_loss_prediction_rate": 0.10, "big_gain_capture_rate": 0.80, "rows": 250},
        {"accuracy": 0.57, "brier_score": 0.20, "avg_return": -0.010, "downside_risk": 0.03, "big_loss_prediction_rate": 0.20, "big_gain_capture_rate": 0.60, "rows": 250},
        min_rows=200,
    )

    assert win is True
    assert "candidate improves profit utility with acceptable brier, return/downside, big-loss avoidance, and minimum big-gain capture" in win_reasons
    assert worse_return_loss is False
    assert "candidate avg_return is lower and downside_risk is higher than production" in loss_reasons
    assert lower_downside_win is True


def test_day11_compare_blocks_worse_tail_bucket_behavior():
    worse_big_loss, big_loss_reasons = day11._decide(
        {"accuracy": 0.62, "brier_score": 0.17, "avg_return": 0.02, "downside_risk": 0.02, "big_loss_prediction_rate": 0.30, "big_gain_capture_rate": 0.80, "rows": 250},
        {"accuracy": 0.57, "brier_score": 0.20, "avg_return": 0.01, "downside_risk": 0.03, "big_loss_prediction_rate": 0.20, "big_gain_capture_rate": 0.60, "rows": 250},
        min_rows=200,
    )
    too_little_big_gain, big_gain_reasons = day11._decide(
        {"accuracy": 0.62, "brier_score": 0.17, "avg_return": 0.02, "downside_risk": 0.02, "big_loss_prediction_rate": 0.10, "big_gain_capture_rate": 0.04, "rows": 250},
        {"accuracy": 0.57, "brier_score": 0.20, "avg_return": 0.01, "downside_risk": 0.03, "big_loss_prediction_rate": 0.20, "big_gain_capture_rate": 0.60, "rows": 250},
        min_rows=200,
    )

    assert worse_big_loss is False
    assert "candidate signals too many big-loss rows versus production" in big_loss_reasons
    assert too_little_big_gain is False
    assert "candidate big-gain capture is below minimum (0.0400 < 0.1000)" in big_gain_reasons


def test_day11_profit_utility_promotes_high_precision_candidate():
    candidate = {
        "accuracy": 0.784,
        "avg_return": 0.1044,
        "big_gain_capture_rate": 0.1452,
        "big_loss_prediction_rate": 0.0,
        "brier_score": 0.084,
        "downside_risk": 0.0,
        "rows": 537,
    }
    production = {
        "accuracy": 0.9038,
        "avg_return": 0.0457,
        "big_gain_capture_rate": 0.8226,
        "big_loss_prediction_rate": 0.0474,
        "brier_score": 0.2453,
        "downside_risk": 0.075,
        "rows": 572,
    }

    win, reasons = day11._decide(candidate, production, min_rows=200)

    assert win is True
    assert "candidate accuracy is below production, but accuracy is informational when profit utility improves" in reasons
    assert "candidate improves profit utility with acceptable brier, return/downside, big-loss avoidance, and minimum big-gain capture" in reasons


def test_day11_compare_handles_missing_model_file_gracefully(tmp_path):
    import pandas as pd

    test_df = pd.DataFrame([{"return_5d": 0.02, "return_1d": 0.01, "x1": 1.0}])
    metrics = day11._evaluate(str(tmp_path / "missing.json"), test_df)
    assert metrics["rows"] == 0
    assert metrics["accuracy"] is None


def test_day14_promotion_only_runs_when_allowed(tmp_path, monkeypatch):
    comparison_path = tmp_path / "comparison.json"
    candidate_path = tmp_path / "candidate.json"
    production_path = tmp_path / "production.json"

    comparison_path.write_text(json.dumps({"candidate_win": False, "candidate_metrics": {"rows": 10}, "production_metrics": {"rows": 10}}), encoding="utf-8")
    candidate_path.write_text(json.dumps({"version": "candidate"}), encoding="utf-8")
    production_path.write_text(json.dumps({"version": "production"}), encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        [
            "day14_promote_candidate.py",
            "--comparison-report",
            str(comparison_path),
            "--candidate-model",
            str(candidate_path),
            "--production-model",
            str(production_path),
        ],
    )
    day14_promote.main()
    assert json.loads(production_path.read_text(encoding="utf-8"))["version"] == "production"

    monkeypatch.setattr(
        "sys.argv",
        [
            "day14_promote_candidate.py",
            "--comparison-report",
            str(comparison_path),
            "--candidate-model",
            str(candidate_path),
            "--production-model",
            str(production_path),
            "--force",
        ],
    )
    day14_promote.main()
    assert json.loads(production_path.read_text(encoding="utf-8"))["version"] == "candidate"
