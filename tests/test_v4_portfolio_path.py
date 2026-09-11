from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from moneybot.services.market_data_providers import ExchangeCalendar
from moneybot.services.v4_portfolio_path import (
    V4ExecutionPolicy,
    reconstruct_v4_portfolio_path,
)
from scripts.backtest_challenger_suite import backtest_challenger_suite

CALENDAR = ExchangeCalendar()


def _trade(
    identifier,
    symbol,
    entry_session,
    prices,
    *,
    score=0.8,
    entry_price=10.0,
    security_id=None,
):
    sessions = []
    current = entry_session
    for price in prices:
        sessions.append({"session": current.isoformat(), "adjusted_close": price})
        current = CALENDAR.next_session(current)
    exit_session = date.fromisoformat(sessions[-1]["session"])
    return {
        "canonical_observation_id": identifier,
        "symbol": symbol,
        "point_in_time_symbol_id": security_id or f"SEC-{symbol}",
        "decision_at": (
            CALENDAR.session_close(CALENDAR.previous_session(entry_session))
            + timedelta(minutes=1)
        ).isoformat(),
        "entry_at": CALENDAR.session_open(entry_session).isoformat(),
        "exit_at": CALENDAR.session_close(exit_session).isoformat(),
        "adjusted_entry_price": entry_price,
        "adjusted_exit_price": prices[-1],
        "valuation_path": sessions,
        "score": score,
        "prediction": 1,
    }


def _run(rows, **overrides):
    policy = V4ExecutionPolicy(
        starting_capital=100.0,
        max_positions=overrides.pop("max_positions", 2),
        transaction_cost_bps=overrides.pop("transaction_cost_bps", 0),
        slippage_bps=overrides.pop("slippage_bps", 0),
        **overrides,
    )
    return reconstruct_v4_portfolio_path(
        rows,
        candidate_id="frozen-development-winner",
        input_sha256="input-a",
        policy=policy,
    )


def test_daily_equity_has_initial_loss_known_drawdown_and_recovery():
    result = _run([_trade("a", "AAA", date(2026, 1, 5), [8.0, 12.0])], max_positions=1)

    assert result["metrics"]["path_valid"] is True
    assert [row["total_equity"] for row in result["daily_equity"]] == pytest.approx(
        [100, 80, 120]
    )
    assert result["metrics"]["max_drawdown"] == pytest.approx(-0.20)
    assert result["metrics"]["drawdown_peak_session"] == "2026-01-02"
    assert result["metrics"]["drawdown_trough_session"] == "2026-01-05"
    assert result["metrics"]["drawdown_recovered"] is True
    assert result["metrics"]["cumulative_return"] == pytest.approx(0.20)
    # Compounding the row's endpoint return twice would manufacture 44%, not
    # the finite-capital ledger's independently calculated 20%.
    assert (1.2 * 1.2) - 1 != pytest.approx(result["metrics"]["cumulative_return"])


def test_losing_trade_fees_and_slippage_are_charged_once_per_fill():
    result = _run(
        [_trade("loss", "LOS", date(2026, 1, 5), [10.0, 8.0])],
        max_positions=1,
        transaction_cost_bps=100,
        slippage_bps=100,
    )
    entries = [
        row
        for row in result["orders_and_position_events"]
        if row["event"] == "entry_fill"
    ]
    exits = [
        row
        for row in result["orders_and_position_events"]
        if row["event"] == "exit_fill"
    ]
    assert len(entries) == len(exits) == 1
    assert entries[0]["fee"] == pytest.approx(100 / 1.01 * 0.01)
    assert exits[0]["fee"] == pytest.approx(exits[0]["notional"] * 0.01)
    assert result["metrics"]["cumulative_fees"] == pytest.approx(
        entries[0]["fee"] + exits[0]["fee"]
    )
    assert result["metrics"]["cumulative_slippage"] > 0
    assert result["metrics"]["ending_equity"] < 80


def test_overlap_repeats_duplicates_cash_sessions_and_event_order_are_deterministic():
    rows = [
        _trade("a", "AAA", date(2026, 1, 5), [10, 11, 12], score=0.9),
        _trade("b", "BBB", date(2026, 1, 5), [20, 20, 20], score=0.8, entry_price=20),
        _trade("c", "CCC", date(2026, 1, 5), [30, 30], score=0.7, entry_price=30),
        _trade("repeat", "AAA", date(2026, 1, 6), [11, 12], score=1.0),
        _trade(
            "renamed",
            "AAX",
            date(2026, 1, 6),
            [11, 12],
            score=0.95,
            security_id="SEC-AAA",
        ),
    ]
    result = _run(rows + [dict(rows[0])])
    shuffled = _run(list(reversed(rows)) + [dict(rows[0])])

    assert result == shuffled
    assert result["metrics"]["trade_count"] == 2
    assert result["metrics"]["rejected_order_count"] == 3
    assert {
        row["reason"]
        for row in result["orders_and_position_events"]
        if row["event"] == "order_rejected"
    } == {"maximum_positions_reached", "repeated_symbol_while_open"}
    assert any(row["daily_return"] == 0 for row in result["daily_equity"])
    assert max(row["gross_exposure"] for row in result["daily_equity"]) <= 1.0
    assert result["metrics"]["ending_equity"] == pytest.approx(
        100
        + sum(
            row.get("realized_pnl", 0)
            for row in result["orders_and_position_events"]
            if row["event"] == "exit_fill"
        )
    )


def test_same_session_entries_cannot_use_later_close_exit_cash():
    old = _trade("old", "AAA", date(2026, 1, 5), [10, 10], entry_price=10)
    new = _trade("new", "BBB", date(2026, 1, 6), [10], entry_price=10)
    result = _run([old, new], max_positions=1)
    events = [
        row
        for row in result["orders_and_position_events"]
        if row["session"] == "2026-01-06"
    ]
    assert [row["event"] for row in events] == ["order_rejected", "exit_fill"]
    assert events[0]["reason"] == "maximum_positions_reached"


def test_decision_time_precedes_score_and_prior_loss_can_exhaust_cash():
    later = _trade("later", "LATE", date(2026, 1, 6), [10], score=0.99)
    later["decision_at"] = (
        datetime.fromisoformat(later["decision_at"]) + timedelta(minutes=5)
    ).isoformat()
    earlier = _trade("earlier", "EARLY", date(2026, 1, 6), [10], score=0.01)
    abstained = _trade("cash", "CASH", date(2026, 1, 6), [10])
    abstained["prediction"] = 0
    competition = _run([later, abstained, earlier], max_positions=1)
    assert (
        next(
            row
            for row in competition["orders_and_position_events"]
            if row["event"] == "entry_fill"
        )["canonical_observation_id"]
        == "earlier"
    )

    losing = _trade("loss", "LOS", date(2026, 1, 5), [5], entry_price=10)
    result = _run([later, abstained, losing, earlier], max_positions=1)
    rejections = [
        row
        for row in result["orders_and_position_events"]
        if row["event"] == "order_rejected"
    ]
    assert {row["reason"] for row in rejections} == {"insufficient_cash"}
    assert {row["canonical_observation_id"] for row in rejections} == {
        "earlier",
        "later",
    }
    assert all(
        row.get("canonical_observation_id") != "cash"
        for row in result["orders_and_position_events"]
    )


def test_calendar_split_basis_and_missing_evidence_fail_closed():
    # July 3 is the observed holiday; the calendar path progresses July 2 -> 6.
    split_trade = _trade("split", "SPL", date(2026, 7, 2), [50, 52], entry_price=50)
    split_trade["valuation_path"][0]["split_adjustment_factor"] = 0.5
    split_trade["valuation_path"][1]["split_adjustment_factor"] = 1.0
    result = _run([split_trade], max_positions=1)
    assert [row["session"] for row in result["daily_equity"]][1:] == [
        "2026-07-02",
        "2026-07-06",
    ]
    assert (
        result["policy"]["split_policy"]
        == "backward_adjusted_price_basis_no_quantity_readjustment"
    )

    missing = _trade("missing", "BAD", date(2026, 11, 27), [10, 11, 12])
    del missing["valuation_path"][1]
    invalid = _run([missing], max_positions=1)
    assert invalid["metrics"]["path_valid"] is False
    assert any(
        reason.startswith("missing_valuation_sessions:missing:")
        for reason in invalid["metrics"]["invalid_reasons"]
    )
    assert invalid["metrics"]["max_drawdown"] is None

    no_exit = _trade("no-exit", "BAD", date(2026, 11, 27), [10])
    del no_exit["adjusted_exit_price"]
    missing_exit = _run([no_exit], max_positions=1)
    assert missing_exit["metrics"]["path_valid"] is False
    assert (
        "missing_execution_or_valuation_evidence:no-exit"
        in missing_exit["metrics"]["invalid_reasons"]
    )


def test_policy_and_input_lineage_hashes_change():
    rows = [_trade("a", "AAA", date(2026, 1, 5), [10, 11])]
    first = _run(rows, max_positions=1)
    changed_policy = reconstruct_v4_portfolio_path(
        rows,
        candidate_id="frozen-development-winner",
        input_sha256="input-a",
        policy=V4ExecutionPolicy(starting_capital=200, max_positions=1),
    )
    changed_input = reconstruct_v4_portfolio_path(
        rows,
        candidate_id="frozen-development-winner",
        input_sha256="input-b",
        policy=V4ExecutionPolicy(
            starting_capital=100,
            max_positions=1,
            transaction_cost_bps=0,
            slippage_bps=0,
        ),
    )
    assert first["policy_sha256"] != changed_policy["policy_sha256"]
    assert first["input_sha256"] != changed_input["input_sha256"]


def test_abstention_produces_a_valid_cash_only_path_for_every_session():
    abstained = _trade("cash", "CASH", date(2026, 1, 5), [10, 10, 10])
    abstained["prediction"] = 0
    result = _run([abstained])
    assert result["metrics"]["path_valid"] is True
    assert result["metrics"]["trade_count"] == 0
    assert [row["total_equity"] for row in result["daily_equity"]] == [
        100,
        100,
        100,
        100,
    ]
    assert [row["session"] for row in result["daily_equity"]][1:] == [
        "2026-01-05",
        "2026-01-06",
        "2026-01-07",
    ]


def test_backtest_integration_writes_primary_path_and_keeps_routing_disabled(tmp_path):
    row = _trade("a", "AAA", date(2026, 1, 5), [9, 11])
    row.update(
        {
            "event_date": "2026-01-02",
            "feature_close": 10.0,
            "return_5d": 0.1,
            "label_up_5d": 1,
        }
    )
    feature_store = tmp_path / "test.jsonl"
    feature_store.write_text(json.dumps(row) + "\n")
    models = tmp_path / "models"
    models.mkdir()
    artifact = models / "always-up.json"
    artifact.write_text(
        json.dumps(
            {
                "version": "always-up",
                "model_type": "baseline_classifier",
                "training_spec": {},
            }
        )
    )
    suite = {
        "feature_columns": ["feature_close"],
        "feature_fill_values": {"feature_close": 10},
        "ranked_model_versions": ["always-up"],
        "challengers": [
            {
                "model_version": "always-up",
                "model_type": "baseline_classifier",
                "model_path": str(artifact),
            }
        ],
    }
    suite_path = models / "challenger_suite_manifest.json"
    suite_path.write_text(json.dumps(suite))

    report = backtest_challenger_suite(
        suite_manifest_path=suite_path,
        feature_store_path=feature_store,
        output_path=tmp_path / "backtest.json",
        min_rows=1,
        transaction_cost_bps=0,
        slippage_bps=0,
    )
    candidate = report["challengers"][0]
    assert candidate["backtest_metrics"]["max_drawdown_evaluable"] is True
    assert candidate["backtest_metrics"]["max_drawdown"] == pytest.approx(-0.02)
    assert (
        "drawdown_evidence_unavailable"
        not in candidate["promotion_gates"]["failed_gates"]
    )
    assert candidate["routing_allowed"] is False
    assert report["primary_portfolio_candidate"] == "always-up"
    assert set(report["primary_portfolio_artifacts"]) == {
        "execution_policy.json",
        "execution_ledger.json",
        "daily_portfolio_equity.json",
        "valuation_evidence_manifest.json",
        "portfolio_metrics.json",
    }
