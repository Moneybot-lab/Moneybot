import pandas as pd

from moneybot.services.outcome_tracking import classify_outcome, close_values, evaluate_decision_events, normalize_action, summarize_outcome_rows


def test_normalize_action_reads_recommendation_and_advice():
    assert normalize_action({"payload": {"recommendation": "BUY"}}) == "BUY"
    assert normalize_action({"payload": {"advice": "sell"}}) == "SELL"
    assert normalize_action({"payload": {}}) is None


def test_classify_outcome_handles_positive_and_negative_actions():
    assert classify_outcome("BUY", 0.03) == "correct"
    assert classify_outcome("BUY", -0.01) == "incorrect"
    assert classify_outcome("HOLD OFF FOR NOW", -0.02) == "correct"
    assert classify_outcome("SELL", 0.05) == "incorrect"
    assert classify_outcome("HOLD", 0.001) == "correct"
    assert classify_outcome("HOLD", 0.01) == "incorrect"


def test_summarize_outcome_rows_reports_accuracy_and_average_returns():
    summary = summarize_outcome_rows(
        [
            {"action": "BUY", "return_1d": 0.02, "return_5d": 0.04},
            {"action": "BUY", "return_1d": -0.01, "return_5d": -0.03},
            {"action": "HOLD", "return_1d": 0.00, "return_5d": 0.01},
        ]
    )

    assert summary["rows"] == 3
    assert summary["counts"]["neutral"] == 0
    assert summary["counts"]["correct"] == 2
    assert summary["counts"]["incorrect"] == 1
    assert summary["accuracy"] == 0.6667


def test_close_values_handles_dataframe_close_column():
    history = pd.DataFrame(
        {
            ("Close", "AAPL"): [100.0, 101.0, 102.0],
            ("Volume", "AAPL"): [10, 11, 12],
        }
    )

    assert close_values(history) == [100.0, 101.0, 102.0]


def test_evaluate_decision_events_builds_rows_with_outcomes():
    events = [
        {"symbol": "AAPL", "endpoint": "quick_ask", "decision_source": "deterministic_model", "ts": 1, "payload": {"recommendation": "BUY", "model_version": "day1-logreg-v1"}},
        {"symbol": "TSLA", "endpoint": "user_watchlist", "decision_source": "rule_based", "ts": 2, "payload": {"advice": "SELL"}},
    ]

    rows = evaluate_decision_events(
        events,
        future_return_lookup=lambda symbol, ts, days: {("AAPL", 1): 0.02, ("AAPL", 5): 0.04, ("TSLA", 1): -0.03, ("TSLA", 5): -0.05}[(symbol, days)],
    )

    assert rows[0]["outcome_1d"] == "correct"
    assert rows[0]["model_version"] == "day1-logreg-v1"
    assert rows[1]["outcome_5d"] == "correct"


def test_evaluate_decision_events_handles_lookup_errors_as_skipped():
    events = [
        {"symbol": "AAPL", "endpoint": "quick_ask", "decision_source": "deterministic_model", "ts": 1, "payload": {"recommendation": "BUY"}},
    ]

    def flaky_lookup(symbol, ts, days):
        if days == 1:
            raise ValueError("provider error")
        return 0.02

    rows = evaluate_decision_events(events, future_return_lookup=flaky_lookup)
    assert rows[0]["return_1d"] is None
    assert rows[0]["outcome_1d"] == "skipped"
    assert rows[0]["return_5d"] == 0.02


def test_evaluate_decision_events_normalizes_millisecond_timestamps():
    events = [
        {"symbol": "AAPL", "endpoint": "quick_ask", "decision_source": "deterministic_model", "ts": 1_700_000_000_000, "payload": {"recommendation": "BUY"}},
    ]
    calls: list[int] = []

    def tracking_lookup(symbol, ts, days):
        calls.append(ts)
        return 0.01

    rows = evaluate_decision_events(events, future_return_lookup=tracking_lookup)
    assert rows[0]["ts"] == 1_700_000_000
    assert calls and calls[0] == 1_700_000_000
