from pathlib import Path

from moneybot.services.decision_log import DecisionLogger


def test_decision_logger_tracks_counts_and_writes_file(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    logger = DecisionLogger(enabled=True, output_path=str(path))

    logger.log(endpoint="quick_ask", symbol="AAPL", decision_source="deterministic_model", payload={"recommendation": "BUY"})
    logger.log(endpoint="quick_ask", symbol="TSLA", decision_source="rule_based", payload={"recommendation": "HOLD"})

    health = logger.health()
    assert health["enabled"] is True
    assert health["source_counts"]["deterministic_model"] == 1
    assert health["source_counts"]["rule_based"] == 1
    assert health["endpoint_counts"]["quick_ask"] == 2
    assert path.exists()
    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 2


def test_decision_logger_disabled_does_not_write(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    logger = DecisionLogger(enabled=False, output_path=str(path))
    logger.log(endpoint="quick_ask", symbol="AAPL", decision_source="deterministic_model", payload={})

    health = logger.health()
    assert health["enabled"] is False
    assert health["source_counts"] == {}
    assert not path.exists()
