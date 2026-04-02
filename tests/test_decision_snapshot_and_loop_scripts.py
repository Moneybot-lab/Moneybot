from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from moneybot.services.decision_log import DecisionLogger, read_decision_events
from moneybot.services.decision_snapshot import build_decision_snapshot
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
        model_version="day1-logreg-v1",
        calibration_version="cal-v1",
        quote={"price": 180.5, "change_percent": 1.2, "source": "finnhub"},
        features={"return_1d": 0.01},
        signals={"trend": "up"},
        explanation={"rationale": "Momentum", "risk_notes": ["volatility"], "next_checks": ["earnings"]},
    )

    assert snapshot["schema_version"] == "decision_snapshot.v1"
    assert snapshot["symbol"] == "AAPL"
    assert snapshot["quote"]["price"] == 180.5
    assert snapshot["features"]["return_1d"] == 0.01
    assert snapshot["explanation"]["risk_notes"] == ["volatility"]


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

    monkeypatch.setattr("scripts.day8_build_decision_training_dataset._future_return", lambda symbol, ts, days: 0.02)
    rows, summary = build_rows(events, horizon_days=5)

    assert summary["rows_scanned"] == 2
    assert summary["mature_rows"] == 1
    assert summary["labeled_rows"] == 1
    assert len(rows) == 1


def test_day8_builder_outputs_labeled_rows_with_snapshot_fields(monkeypatch):
    now = datetime.now(timezone.utc)
    mature_ts = int((now - timedelta(days=10)).timestamp() * 1000)  # milliseconds path
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
        }
    ]
    monkeypatch.setattr("scripts.day8_build_decision_training_dataset._future_return", lambda symbol, ts, days: 0.03 if days == 1 else -0.01)

    rows, _ = build_rows(events, horizon_days=5)
    assert rows[0]["recommendation"] == "BUY"
    assert rows[0]["probability_up"] == 0.61
    assert rows[0]["model_version"] == "snap-v1"
    assert rows[0]["outcome_5d"] in {"correct", "incorrect"}
    assert rows[0]["return_1d"] == 0.03


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


def test_day11_compare_detects_win_and_loss():
    win, _ = day11._decide(
        {"accuracy": 0.60, "brier_score": 0.18, "rows": 250},
        {"accuracy": 0.57, "brier_score": 0.20, "rows": 250},
        min_rows=200,
    )
    loss, _ = day11._decide(
        {"accuracy": 0.58, "brier_score": 0.21, "rows": 250},
        {"accuracy": 0.58, "brier_score": 0.20, "rows": 250},
        min_rows=200,
    )
    assert win is True
    assert loss is False


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
