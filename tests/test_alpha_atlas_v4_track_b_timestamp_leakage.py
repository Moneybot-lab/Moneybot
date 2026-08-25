"""Adversarial evidence for legacy Track B timestamp defects.

These tests state the V4-required result. Confirmed legacy violations are strict
xfails: an implementation repair therefore becomes an XPASS and fails the suite
until Prompt 3 removes the marker. The legacy row has no entry/label timestamps or
provider-availability timestamps, so tests assert the earliest observable violation
rather than inventing fields. Breadth is likewise deferred because the current row
schema has no breadth feature family.
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from moneybot.services.alpha_atlas_v4_timing_contract import AlphaAtlasV4TimingRecord
from moneybot.services.corporate_actions import canonical_splits
from moneybot.services.market_data_providers import ExchangeCalendar
from moneybot.services.outcome_tracking import event_market_date, normalize_unix_ts
from scripts.build_massive_decision_training_rows import (
    _event_day,
    _market_date,
    _market_date_index,
    _row_before_or_on_indexed,
    build_training_rows_from_raw_market,
    load_market_history,
)

NY = ZoneInfo("America/New_York")


def _utc(local_day: str, local_time: time) -> datetime:
    return datetime.combine(
        date.fromisoformat(local_day), local_time, tzinfo=NY
    ).astimezone(timezone.utc)


def _event(local_day: str, local_time: time, **payload):
    return {
        "ts": int(_utc(local_day, local_time).timestamp()),
        "symbol": "LEAK",
        "endpoint": "quick_ask",
        "decision_source": "deterministic",
        "payload": {"recommendation": "BUY", "sector_etf": "XLK", **payload},
    }


@pytest.fixture
def daily_market():
    """Small weekday-only history with conspicuous decision-session values."""
    days = []
    current = date(2026, 3, 2)
    while len(days) < 25:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)

    market = {}
    for symbol, base, step in (
        ("LEAK", 100.0, 1.0),
        ("SPY", 400.0, 2.0),
        ("XLK", 200.0, 1.5),
    ):
        rows = []
        for index, day in enumerate(days):
            close = base + index * step
            rows.append(
                {
                    "symbol": symbol,
                    "date": day.isoformat(),
                    "open": close - 0.5,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                    "volume": 1_000.0 + index,
                }
            )
        # March 23 is the decision session. Make its unknowable completed values
        # unmistakable in every affected family.
        decision_bar = next(row for row in rows if row["date"] == "2026-03-23")
        decision_bar.update(
            open=900.0, high=999.0, low=1.0, close=950.0, volume=9_999_999.0
        )
        market[symbol] = rows
    return market


def _row(market, local_time: time, *, horizon_days: int = 5, splits=()):
    rows, summary = build_training_rows_from_raw_market(
        [_event("2026-03-23", local_time)],
        market,
        horizon_days=horizon_days,
        split_events=list(splits),
    )
    assert summary["rows_joined"] == 1
    return rows[0]


@pytest.mark.xfail(
    strict=True,
    reason="legacy _event_day/_row_before_or_on_indexed includes the premarket decision session's completed daily bar",
)
def test_premarket_uses_only_previous_completed_daily_session(daily_market):
    assert _row(daily_market, time(8, 0))["market_asof_date"] == "2026-03-20"


@pytest.mark.xfail(
    strict=True,
    reason="legacy build_training_rows_from_raw_market leaks premarket same-session final close and full-day volume into symbol features",
)
def test_premarket_excludes_current_session_final_symbol_ohlcv(daily_market):
    row = _row(daily_market, time(8, 0))
    assert row["feature_close"] != 950.0
    assert row["feature_volume"] != 9_999_999.0
    assert row["feature_drawdown_from_20d_high"] != pytest.approx(950.0 / 999.0 - 1)


@pytest.mark.xfail(
    strict=True,
    reason="legacy SPY/sector/regime joins use the same date-inclusive premarket daily-bar selection",
)
def test_premarket_market_context_uses_same_previous_session_cutoff(daily_market):
    row = _row(daily_market, time(8, 0))
    expected_spy_previous = round(428.0 / 418.0 - 1, 6)
    expected_sector_previous = round(221.0 / 213.5 - 1, 6)
    assert row["feature_spy_return_5d"] == expected_spy_previous
    assert row["feature_sector_relative_return_5d"] == round(
        row["feature_return_5d_lagged"] - expected_sector_previous, 6
    )


@pytest.mark.xfail(
    strict=True,
    reason="legacy forward label starts at the selected same-day close instead of the executable entry open and S0-S4 exit close",
)
def test_premarket_label_is_entry_open_to_fifth_session_close(daily_market):
    row = _row(daily_market, time(8, 0))
    entry_open = 900.0
    s4_close = next(
        bar["close"] for bar in daily_market["LEAK"] if bar["date"] == "2026-03-27"
    )
    assert row["return_5d"] == pytest.approx(s4_close / entry_open - 1)
    assert row["label_asof_date"] == "2026-03-27"


@pytest.mark.xfail(
    strict=True,
    reason="legacy regular-session date-inclusive selection uses final daily OHLCV and daily-derived indicators before the close",
)
def test_regular_session_excludes_current_daily_bar_and_derivatives(daily_market):
    row = _row(daily_market, time(12, 0))
    assert row["market_asof_date"] == "2026-03-20"
    assert row["feature_close"] != 950.0
    assert row["feature_volume"] != 9_999_999.0
    assert row["feature_return_1d_lagged"] == pytest.approx(114.0 / 113.0 - 1)


@pytest.mark.xfail(
    strict=True,
    reason="legacy regular-session SPY/sector/regime joins include completed same-session context aggregates before they are available",
)
def test_regular_session_context_obeys_symbol_feature_cutoff(daily_market):
    regular_context = _row(daily_market, time(12, 0))
    assert regular_context["feature_spy_return_5d"] == round(428.0 / 418.0 - 1, 6)
    expected_sector_previous = round(221.0 / 213.5 - 1, 6)
    assert regular_context["feature_sector_relative_return_5d"] == round(
        regular_context["feature_return_5d_lagged"] - expected_sector_previous, 6
    )


@pytest.mark.xfail(
    strict=True,
    reason="legacy regular-session label is same-close close-to-close and has no next-session executable entry timestamp",
)
def test_regular_session_label_starts_at_next_eligible_open(daily_market):
    row = _row(daily_market, time(12, 0))
    next_open = next(
        bar["open"] for bar in daily_market["LEAK"] if bar["date"] == "2026-03-24"
    )
    s4_close = next(
        bar["close"] for bar in daily_market["LEAK"] if bar["date"] == "2026-03-30"
    )
    assert row["return_5d"] == pytest.approx(s4_close / next_open - 1)
    assert row["label_asof_date"] == "2026-03-30"


@pytest.mark.xfail(
    strict=True,
    reason="legacy after-hours daily rows have calendar dates but no provider availability timestamp and do not fail closed",
)
def test_after_hours_requires_proven_current_bar_availability(daily_market):
    rows, _ = build_training_rows_from_raw_market(
        [_event("2026-03-23", time(18, 0))], daily_market, split_events=[]
    )
    assert rows == []


@pytest.mark.xfail(
    strict=True,
    reason="legacy after-hours forward label starts at the current close rather than the next eligible regular-session open",
)
def test_after_hours_label_starts_at_next_eligible_open(daily_market):
    row = _row(daily_market, time(18, 0))
    next_open = next(
        bar["open"] for bar in daily_market["LEAK"] if bar["date"] == "2026-03-24"
    )
    s4_close = next(
        bar["close"] for bar in daily_market["LEAK"] if bar["date"] == "2026-03-30"
    )
    assert row["return_5d"] == pytest.approx(s4_close / next_open - 1)
    assert row["label_asof_date"] == "2026-03-30"


@pytest.mark.xfail(
    strict=True,
    reason="legacy normalize_unix_ts does not normalize millisecond epochs and treats them as seconds",
)
def test_unix_normalization_preserves_the_decision_instant():
    decision = _utc("2026-03-23", time(8, 0))
    assert normalize_unix_ts(decision.timestamp() * 1_000) == int(decision.timestamp())


@pytest.mark.xfail(
    strict=True,
    reason="legacy event_market_date uses the UTC calendar date instead of the America/New_York exchange-session date",
)
def test_outcome_market_date_uses_new_york_date_when_utc_date_differs():
    instant = datetime(2026, 1, 3, 0, 30, tzinfo=timezone.utc)  # Jan 2, 19:30 ET
    assert event_market_date(int(instant.timestamp())) == date(2026, 1, 2)


@pytest.mark.xfail(
    strict=True,
    reason="legacy _event_day uses UTC date alone across the daylight-saving boundary instead of New York session semantics",
)
def test_builder_event_day_uses_new_york_date_across_dst_boundary():
    instant = datetime(2026, 3, 9, 0, 30, tzinfo=timezone.utc)  # Mar 8, 20:30 EDT
    assert _event_day(int(instant.timestamp())) == "2026-03-08"


def test_market_date_helpers_are_deterministic_but_not_availability_proof():
    assert _market_date("2026-03-23T13:00:00Z") == "2026-03-23"
    index = _market_date_index({"LEAK": [{"date": "2026-03-20"}]})
    assert _row_before_or_on_indexed(index, "LEAK", "2026-03-23") == 0


def test_weekend_and_holiday_do_not_create_fictional_market_bars(daily_market):
    saturday = _event("2026-03-21", time(12, 0))
    holiday_days = []
    candidate = date(2026, 6, 8)
    while len(holiday_days) < 25:
        if candidate.weekday() < 5 and candidate != date(2026, 7, 3):
            holiday_days.append(candidate.isoformat())
        candidate += timedelta(days=1)
    july_holiday_market = {
        key: [{**bar, "date": day} for bar, day in zip(value, holiday_days)]
        for key, value in daily_market.items()
    }
    weekend_rows, _ = build_training_rows_from_raw_market(
        [saturday], daily_market, split_events=[]
    )
    holiday_rows, _ = build_training_rows_from_raw_market(
        [_event("2026-07-03", time(12, 0))], july_holiday_market, split_events=[]
    )
    assert weekend_rows[0]["market_asof_date"] == "2026-03-20"
    assert holiday_rows[0]["market_asof_date"] == "2026-07-02"


@pytest.mark.xfail(
    strict=True,
    reason="legacy ExchangeCalendar hardcodes a 16:00 regular close and does not model official early-close sessions",
)
def test_official_early_close_ends_regular_session_at_1300_eastern():
    early_close = datetime(2026, 11, 27, 14, 0, tzinfo=NY)
    assert ExchangeCalendar().session_at(early_close) != "regular"


@pytest.mark.xfail(
    strict=True,
    reason="legacy Track B rows carry no source availability/staleness timestamp and accept a stale symbol daily bar",
)
def test_stale_symbol_bar_fails_closed(daily_market):
    stale = {symbol: list(rows) for symbol, rows in daily_market.items()}
    stale["LEAK"] = [
        row
        for row in stale["LEAK"]
        if row["date"] <= "2026-03-13" or row["date"] >= "2026-03-24"
    ]
    rows, _ = build_training_rows_from_raw_market(
        [_event("2026-03-23", time(8, 0))], stale, split_events=[]
    )
    assert rows == []


@pytest.mark.xfail(
    strict=True,
    reason="legacy Track B accepts rows when SPY/required regime context is missing instead of rejecting the row",
)
def test_missing_required_spy_and_regime_context_fails_closed(daily_market):
    rows, _ = build_training_rows_from_raw_market(
        [_event("2026-03-23", time(8, 0))],
        {key: value for key, value in daily_market.items() if key != "SPY"},
        split_events=[],
    )
    assert rows == []


@pytest.mark.xfail(
    strict=True,
    reason="legacy Track B has no per-family staleness timestamp and accepts stale SPY/regime bars when symbol data is current",
)
def test_stale_spy_and_regime_context_fails_closed(daily_market):
    stale = {symbol: list(rows) for symbol, rows in daily_market.items()}
    stale["SPY"] = [
        row
        for row in stale["SPY"]
        if row["date"] <= "2026-03-13" or row["date"] >= "2026-03-24"
    ]
    rows, _ = build_training_rows_from_raw_market(
        [_event("2026-03-23", time(8, 0))], stale, split_events=[]
    )
    assert rows == []


@pytest.mark.xfail(
    strict=True,
    reason="legacy Track B labels from closes and accepts a premarket row even when the required executable entry open is missing",
)
def test_missing_required_entry_open_fails_closed(daily_market):
    market = {
        symbol: [dict(row) for row in rows] for symbol, rows in daily_market.items()
    }
    next(row for row in market["LEAK"] if row["date"] == "2026-03-23")["open"] = None
    rows, _ = build_training_rows_from_raw_market(
        [_event("2026-03-23", time(8, 0))], market, split_events=[]
    )
    assert rows == []


def test_contract_rejects_feature_source_after_cutoff():
    with pytest.raises(ValueError, match="source-bar timestamps"):
        AlphaAtlasV4TimingRecord(
            decision_id="d1",
            symbol="LEAK",
            point_in_time_symbol_id="provider:LEAK:2026-03-23",
            exchange="XNAS",
            trading_calendar="XNYS",
            model_feature_contract_version="alpha-atlas-v4-features.v1",
            decision_at=datetime(2026, 3, 23, 12, 0, tzinfo=timezone.utc),
            feature_cutoff_at=datetime(2026, 3, 23, 11, 59, tzinfo=timezone.utc),
            latest_source_bar_at={
                "symbol_daily": datetime(2026, 3, 23, 20, 0, tzinfo=timezone.utc)
            },
            entry_at=datetime(2026, 3, 23, 13, 30, tzinfo=timezone.utc),
            label_start_at=datetime(2026, 3, 23, 13, 30, tzinfo=timezone.utc),
            exit_at=datetime(2026, 3, 27, 20, 0, tzinfo=timezone.utc),
            entry_price_source="official_open",
            exit_price_source="official_close",
            data_provider_id="massive",
            corporate_action_adjustment_ids=(),
            staleness_status="fresh",
            rejection_reason=None,
            code_commit="abc",
            dataset_manifest_hash="sha256:abc",
            transaction_cost_bps=None,
            entry_slippage_bps=None,
            exit_slippage_bps=None,
        )


def test_missing_forward_price_rejects_row(daily_market):
    truncated = {symbol: rows[:16] for symbol, rows in daily_market.items()}
    rows, summary = build_training_rows_from_raw_market(
        [_event("2026-03-23", time(8, 0))], truncated, split_events=[]
    )
    assert rows == []
    assert summary["insufficient_forward_window"] == 1


@pytest.mark.xfail(
    strict=True,
    reason="legacy split adjustment applies a decision-date action without proving its publication was available before the premarket cutoff",
)
def test_premarket_split_requires_point_in_time_action_availability(daily_market):
    split = canonical_splits(
        [
            {
                "ticker": "LEAK",
                "execution_date": "2026-03-23",
                "adjustment_type": "forward_split",
                "split_from": 1,
                "split_to": 2,
                "id": "same-day-split",
            }
        ]
    )
    row = _row(daily_market, time(8, 0), splits=split)
    assert "same-day-split" not in row["feature_split_ids"]


def test_split_in_forward_horizon_is_adjusted_and_provenanced(daily_market):
    split = canonical_splits(
        [
            {
                "ticker": "LEAK",
                "execution_date": "2026-03-25",
                "adjustment_type": "forward_split",
                "split_from": 1,
                "split_to": 2,
                "id": "horizon-split",
            }
        ]
    )
    row = _row(daily_market, time(18, 0), splits=split)
    assert row["label_split_ids"] == ["horizon-split"]
    assert row["label_split_adjustment_factor"] == 0.5


def test_new_listing_with_insufficient_history_fails_closed(daily_market):
    market = {**daily_market, "LEAK": daily_market["LEAK"][-4:]}
    rows, summary = build_training_rows_from_raw_market(
        [_event("2026-03-23", time(8, 0))], market, split_events=[]
    )
    assert rows == []
    assert summary["insufficient_history"] == 1


def test_ticker_change_without_point_in_time_identity_mapping_fails_closed(
    daily_market,
):
    event = _event("2026-03-23", time(8, 0))
    event["symbol"] = "NEW"
    rows, summary = build_training_rows_from_raw_market(
        [event], daily_market, split_events=[]
    )
    assert rows == []
    assert summary["missing_symbol_history"] == 1


def test_delisting_or_missing_terminal_price_fails_closed(daily_market):
    market = {symbol: rows[:16] for symbol, rows in daily_market.items()}
    rows, summary = build_training_rows_from_raw_market(
        [_event("2026-03-23", time(18, 0))], market, split_events=[]
    )
    assert rows == []
    assert summary["insufficient_forward_window"] == 1


def test_market_calendar_safe_basics_cover_premarket_holiday_weekend_and_dst():
    calendar = ExchangeCalendar()
    assert calendar.session_at(_utc("2026-03-23", time(8, 0))) == "pre"
    assert calendar.session_at(_utc("2026-03-21", time(12, 0))) == "closed"
    assert calendar.session_at(_utc("2026-07-03", time(12, 0))) == "closed"
    assert calendar.session_at(_utc("2026-03-09", time(9, 30))) == "regular"


def test_load_market_history_keeps_daily_date_but_does_not_claim_availability(tmp_path):
    root = tmp_path / "raw"
    root.mkdir()
    (root / "bars.csv").write_text(
        "ticker,date,open,high,low,close,volume\nLEAK,2026-03-23,1,2,0.5,1.5,100\n",
        encoding="utf-8",
    )
    row = load_market_history(root)["LEAK"][0]
    assert row["date"] == "2026-03-23"
    assert "available_at" not in row
