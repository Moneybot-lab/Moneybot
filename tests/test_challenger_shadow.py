import json

from moneybot.services.challenger_shadow import log_challenger_shadow_decisions, promising_shadow_candidates
from moneybot.services.decision_log import DecisionLogger, read_decision_events


def test_promising_shadow_candidates_require_gates_and_no_routing():
    report = {
        "ranked_model_versions": ["good", "bad", "routed"],
        "challengers": [
            {"model_version": "bad", "promotion_gates": {"promotion_ready": False}, "routing_allowed": False},
            {"model_version": "good", "promotion_gates": {"promotion_ready": True}, "routing_allowed": False},
            {"model_version": "routed", "promotion_gates": {"promotion_ready": True}, "routing_allowed": True},
        ],
    }

    assert [item["model_version"] for item in promising_shadow_candidates(report)] == ["good"]


def test_log_challenger_shadow_decisions_never_enables_routing(tmp_path):
    logger = DecisionLogger(output_path=str(tmp_path / "events.jsonl"))

    logged = log_challenger_shadow_decisions(
        decision_logger=logger,
        endpoint="quick_ask",
        symbol="AAPL",
        production_payload={"recommendation": "BUY"},
        challenger_predictions=[{"model_version": "challenger-a", "recommendation": "HOLD", "probability_up": 0.42}],
    )

    assert logged == 1
    event = read_decision_events(str(tmp_path / "events.jsonl"))[0]
    assert event["endpoint"] == "quick_ask_challenger_shadow"
    assert event["payload"]["shadow_only"] is True
    assert event["payload"]["routing_allowed"] is False
    assert event["experiment"]["promotion_required_before_routing"] is True
