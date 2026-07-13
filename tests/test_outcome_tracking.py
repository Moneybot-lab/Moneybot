import pandas as pd

from moneybot.services.outcome_tracking import (
    classify_outcome,
    close_values,
    evaluate_decision_events,
    normalize_action,
    select_recent_unique_rows,
    summarize_outcome_rows,
)


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
        {"symbol": "AAPL", "endpoint": "quick_ask", "decision_source": "deterministic_model", "ts": 1, "payload": {"recommendation": "BUY", "model_version": "alpha-atlas-v1", "probability_up": 0.72}, "snapshot": {"quote": {"source_mode": "websocket", "is_stale": False}, "market_data": {"schema_version": "market-data.v1"}, "personalization": {"base_action": "BUY", "action": "HOLD"}}},
        {"symbol": "TSLA", "endpoint": "user_watchlist", "decision_source": "rule_based", "ts": 2, "payload": {"advice": "SELL"}},
    ]

    rows = evaluate_decision_events(
        events,
        future_return_lookup=lambda symbol, ts, days: {("AAPL", 1): 0.02, ("AAPL", 5): 0.04, ("TSLA", 1): -0.03, ("TSLA", 5): -0.05}[(symbol, days)],
    )

    assert rows[0]["outcome_1d"] == "correct"
    assert rows[0]["model_version"] == "alpha-atlas-v1"
    assert rows[0]["probability_up"] == 0.72
    assert rows[0]["source_mode"] == "websocket"
    assert rows[0]["is_stale"] is False
    assert rows[0]["personalization"]["action"] == "HOLD"
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


def test_rows_with_horizon_return_keeps_horizons_separate():
    from moneybot.services.outcome_tracking import rows_with_horizon_return

    rows = [
        {"symbol": "AAPL", "return_1d": 0.01, "return_5d": None},
        {"symbol": "MSFT", "return_1d": 0.02, "return_5d": 0.05},
    ]

    assert [row["symbol"] for row in rows_with_horizon_return(rows, "1d")] == ["AAPL", "MSFT"]
    assert [row["symbol"] for row in rows_with_horizon_return(rows, "5d")] == ["MSFT"]


def test_evaluate_decision_events_tracks_live_paper_pnl_fields():
    events = [
        {"symbol": "AAPL", "endpoint": "quick_ask", "decision_source": "deterministic_model", "ts": 1, "payload": {"recommendation": "BUY"}},
        {"symbol": "TSLA", "endpoint": "quick_ask", "decision_source": "deterministic_model", "ts": 2, "payload": {"recommendation": "SELL"}},
    ]

    returns = {
        ("AAPL", 1): 0.01,
        ("AAPL", 5): 0.05,
        ("AAPL", 10): 0.08,
        ("AAPL", 20): 0.10,
        ("TSLA", 1): -0.02,
        ("TSLA", 5): -0.06,
        ("TSLA", 10): -0.09,
        ("TSLA", 20): -0.12,
    }

    rows = evaluate_decision_events(
        events,
        future_return_lookup=lambda symbol, ts, days: returns[(symbol, days)],
        price_path_lookup=lambda symbol, ts, days: [100, 95, 102, 110] if symbol == "AAPL" else [100, 108, 92, 88],
        benchmark_return_lookup=lambda ts, days: 0.04,
    )

    assert rows[0]["return_10d"] == 0.08
    assert rows[0]["return_20d"] == 0.10
    assert rows[0]["paper_return_20d"] == 0.10
    assert rows[0]["max_drawdown_to_date"] == -0.05
    assert rows[0]["max_favorable_excursion_to_date"] == 0.10
    assert rows[0]["max_drawdown"] is None
    assert rows[0]["max_favorable_excursion"] is None
    assert rows[0]["benchmark_relative_return_20d"] == 0.06
    assert rows[1]["paper_return_20d"] == 0.12
    assert rows[1]["max_drawdown_to_date"] == -0.08
    assert rows[1]["max_favorable_excursion_to_date"] == 0.12



def test_select_recent_unique_rows_collapses_same_day_duplicate_visible_decisions():
    rows = [
        {
            "ts": 1700000000 + idx,
            "symbol": "NVDA",
            "endpoint": "quick_ask",
            "decision_source": "rule_based",
            "action": "HOLD OFF FOR NOW",
            "model_version": None,
            "return_5d": 0.0263,
            "outcome_5d": "incorrect",
        }
        for idx in range(5)
    ]
    rows.append(
        {
            "ts": 1700086400,
            "symbol": "TSLA",
            "endpoint": "quick_ask",
            "decision_source": "rule_based",
            "action": "SELL",
            "model_version": None,
            "return_5d": -0.04,
            "outcome_5d": "correct",
        }
    )

    selected = select_recent_unique_rows(rows, limit=20, horizon="5d")

    assert [row["symbol"] for row in selected] == ["NVDA", "TSLA"]


def test_summarize_paper_pnl_by_action_groups_recommendations():
    from moneybot.services.outcome_tracking import summarize_paper_pnl_by_action

    rows = [
        {"action": "BUY", "return_1d": 0.01, "paper_return_1d": 0.01, "return_5d": 0.05, "paper_return_5d": 0.05, "return_10d": 0.07, "paper_return_10d": 0.07, "return_20d": 0.10, "paper_return_20d": 0.10, "max_drawdown": -0.02, "max_favorable_excursion": 0.12, "benchmark_return_20d": 0.04, "benchmark_relative_return_20d": 0.06},
        {"action": "SELL", "return_1d": -0.02, "paper_return_1d": 0.02, "return_5d": -0.04, "paper_return_5d": 0.04, "return_10d": -0.06, "paper_return_10d": 0.06, "return_20d": -0.08, "paper_return_20d": 0.08, "max_drawdown": -0.01, "max_favorable_excursion": 0.09, "benchmark_return_20d": 0.04, "benchmark_relative_return_20d": 0.04},
    ]

    summary = summarize_paper_pnl_by_action(rows)

    assert summary["BUY"]["rows"] == 1
    assert summary["BUY"]["evaluated_rows_1d"] == 1
    assert summary["BUY"]["evaluated_rows_5d"] == 1
    assert summary["BUY"]["avg_paper_return_20d"] == 0.10
    assert summary["SELL"]["evaluated_rows_1d"] == 1
    assert summary["SELL"]["evaluated_rows_5d"] == 1
    assert summary["SELL"]["avg_paper_return_20d"] == 0.08
    assert summary["SELL"]["avg_benchmark_relative_return_20d"] == 0.04
    assert summary["HOLD"]["rows"] == 0


def _history_frame(closes, start="2026-01-02"):
    dates = pd.bdate_range(start=start, periods=len(closes))
    return pd.DataFrame({"Close": closes}, index=dates)


def test_history_cache_calculates_5d_from_six_trading_closes_across_holiday():
    from datetime import datetime, timezone

    from moneybot.services.outcome_tracking import OutcomeHistoryCache

    calls = []

    def fake_download(symbol, **kwargs):
        calls.append((symbol, kwargs))
        dates = pd.to_datetime(
            [
                "2026-01-02",
                "2026-01-05",
                "2026-01-06",
                "2026-01-07",
                "2026-01-09",
                "2026-01-12",
            ]
        )
        return pd.DataFrame({"Close": [100, 101, 102, 103, 104, 110]}, index=dates)

    cache = OutcomeHistoryCache(
        download=fake_download,
        now=datetime(2026, 1, 13, tzinfo=timezone.utc),
    )

    assert cache.future_return("AAPL", int(datetime(2026, 1, 2, tzinfo=timezone.utc).timestamp()), 5) == 0.1
    assert len(calls) == 1


def test_history_cache_includes_yahoo_exclusive_end_date_padding():
    from datetime import datetime, timezone

    from moneybot.services.outcome_tracking import OutcomeHistoryCache

    seen = {}

    def fake_download(symbol, **kwargs):
        seen.update(kwargs)
        return _history_frame([100, 101, 102, 103, 104, 105], start="2026-02-02")

    cache = OutcomeHistoryCache(
        download=fake_download,
        now=datetime(2026, 2, 20, tzinfo=timezone.utc),
    )

    assert cache.future_return("AAPL", int(datetime(2026, 2, 2, tzinfo=timezone.utc).timestamp()), 5) == 0.05
    assert seen["end"] > "2026-02-09"


def test_history_cache_returns_none_for_insufficient_closes():
    from datetime import datetime, timezone

    from moneybot.services.outcome_tracking import OutcomeHistoryCache

    cache = OutcomeHistoryCache(
        download=lambda symbol, **kwargs: _history_frame([100, 101, 102], start="2026-03-02"),
        now=datetime(2026, 3, 20, tzinfo=timezone.utc),
    )

    assert cache.future_return("AAPL", int(datetime(2026, 3, 2, tzinfo=timezone.utc).timestamp()), 5) is None
    assert cache.diagnostics.insufficient_history_5d == 1


def test_history_cache_reuses_symbol_date_download_for_all_horizons_and_events():
    from datetime import datetime, timezone

    from moneybot.services.outcome_tracking import OutcomeHistoryCache

    calls = []

    def fake_download(symbol, **kwargs):
        calls.append((symbol, kwargs["start"]))
        return _history_frame([100 + idx for idx in range(30)], start="2026-04-01")

    cache = OutcomeHistoryCache(
        download=fake_download,
        now=datetime(2026, 5, 15, tzinfo=timezone.utc),
    )
    ts1 = int(datetime(2026, 4, 1, 14, tzinfo=timezone.utc).timestamp())
    ts2 = int(datetime(2026, 4, 1, 18, tzinfo=timezone.utc).timestamp())

    assert cache.future_return("AAPL", ts1, 1) == 0.01
    assert cache.future_return("AAPL", ts1, 5) == 0.05
    assert cache.price_path("AAPL", ts2, 20)[0] == 100
    assert len(calls) == 1
    assert cache.diagnostics.history_cache_misses == 1
    assert cache.diagnostics.history_cache_hits == 2


def test_paper_path_extremes_include_zero_baseline_for_long_and_inverse():
    from moneybot.services.outcome_tracking import paper_path_extremes

    long_drawdown, long_favorable = paper_path_extremes("BUY", [100, 95, 90])
    short_drawdown, short_favorable = paper_path_extremes("SELL", [100, 105, 110])

    assert long_drawdown <= 0
    assert long_favorable == 0.0
    assert short_drawdown <= 0
    assert short_favorable == 0.0


def test_symbol_level_preload_downloads_once_per_symbol_and_spy_once():
    from datetime import datetime, timezone

    from moneybot.services.outcome_tracking import OutcomeHistoryCache

    calls = []

    def fake_download(symbol, **kwargs):
        calls.append(symbol)
        return _history_frame([100 + idx for idx in range(30)], start="2026-04-01")

    cache = OutcomeHistoryCache(
        download=fake_download,
        now=datetime(2026, 5, 15, tzinfo=timezone.utc),
    )
    events = [
        {"symbol": "AAPL", "ts": int(datetime(2026, 4, day, tzinfo=timezone.utc).timestamp())}
        for day in [1, 2, 3]
    ]

    cache.preload_events(events)
    assert cache.future_return("AAPL", events[0]["ts"], 5) == 0.05
    assert sorted(calls) == ["AAPL", "SPY"]


def test_same_day_events_do_not_download_history():
    from datetime import datetime, timezone

    from moneybot.services.outcome_tracking import OutcomeHistoryCache

    calls = []
    now = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
    ts = int(now.timestamp())
    cache = OutcomeHistoryCache(
        download=lambda symbol, **kwargs: calls.append(symbol) or _history_frame([100, 101]),
        now=now,
    )

    cache.preload_events([{"symbol": "AAPL", "ts": ts}])
    assert cache.future_return("AAPL", ts, 1) is None
    assert calls == []
    assert cache.diagnostics.insufficient_history_1d == 1
