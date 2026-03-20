from moneybot.services.outcome_tracking import classify_outcome, normalize_action, summarize_outcome_rows


def test_normalize_action_reads_recommendation_and_advice():
    assert normalize_action({"payload": {"recommendation": "BUY"}}) == "BUY"
    assert normalize_action({"payload": {"advice": "sell"}}) == "SELL"
    assert normalize_action({"payload": {}}) is None


def test_classify_outcome_handles_positive_and_negative_actions():
    assert classify_outcome("BUY", 0.03) == "correct"
    assert classify_outcome("BUY", -0.01) == "incorrect"
    assert classify_outcome("HOLD OFF FOR NOW", -0.02) == "correct"
    assert classify_outcome("SELL", 0.05) == "incorrect"
    assert classify_outcome("HOLD", 0.01) == "neutral"


def test_summarize_outcome_rows_reports_accuracy_and_average_returns():
    summary = summarize_outcome_rows(
        [
            {"action": "BUY", "return_1d": 0.02, "return_5d": 0.04},
            {"action": "BUY", "return_1d": -0.01, "return_5d": -0.03},
            {"action": "HOLD", "return_1d": 0.00, "return_5d": 0.01},
        ]
    )

    assert summary["rows"] == 3
    assert summary["counts"]["correct"] == 1
    assert summary["counts"]["incorrect"] == 1
    assert summary["counts"]["neutral"] == 1
    assert summary["accuracy"] == 0.5
