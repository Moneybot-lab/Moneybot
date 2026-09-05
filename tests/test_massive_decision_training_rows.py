import json
from datetime import date, datetime, timedelta, timezone

import pytest

from scripts.build_massive_decision_training_rows import (
    _aligned_relative_returns,
    _date_aligned_beta,
    _feature_safe_splits,
    _lagged_return,
    build_training_rows_from_raw_market,
    emit_phase0_evidence_bundle,
    load_market_history,
    write_rows,
)
import scripts.build_massive_decision_training_rows as builder
from moneybot.services.market_data_providers import ExchangeCalendar


def _ts(day: str) -> int:
    return int(
        datetime.fromisoformat(day).replace(hour=12, tzinfo=timezone.utc).timestamp()
    )


def _trading_days(start: date, count: int) -> list[date]:
    days = []
    candidate = start
    while len(days) < count:
        if candidate.weekday() < 5:
            days.append(candidate)
        candidate += timedelta(days=1)
    return days


def test_build_training_rows_uses_only_asof_features_and_future_label(tmp_path):
    raw = tmp_path / "raw" / "2026-07-03" / "us_stocks_sip" / "day_aggs_v1"
    raw.mkdir(parents=True)
    csv_rows = ["ticker,date,open,high,low,close,volume"]
    days = _trading_days(date(2026, 1, 2), 11)
    for day, close in zip(days, [10, 11, 12, 13, 14, 15, 16, 18, 21, 20, 22]):
        csv_rows.append(f"AAPL,{day.isoformat()},{close},{close},{close},{close},1000")
    (raw / "aapl.csv").write_text("\n".join(csv_rows) + "\n", encoding="utf-8")
    market = load_market_history(tmp_path / "raw")
    market["SPY"] = [{**row, "symbol": "SPY"} for row in market["AAPL"]]
    events = [
        {
            "ts": _ts(days[6].isoformat()),
            "symbol": "AAPL",
            "endpoint": "quick_ask",
            "decision_source": "deterministic",
            "payload": {"recommendation": "BUY"},
        }
    ]

    rows, summary = build_training_rows_from_raw_market(
        events, market, horizon_days=3, split_events=[]
    )

    assert summary["rows_joined"] == 1
    row = rows[0]
    assert row["market_asof_date"] == days[5].isoformat()
    assert row["label_asof_date"] == days[8].isoformat()
    assert row["feature_close"] == 15.0
    assert row["feature_return_1d_lagged"] == round(15 / 14 - 1, 6)
    assert row["return_3d"] == round(21 / 16 - 1, 6)
    assert row["label_up_3d"] == 1
    assert row["leakage_guard"].startswith("v4_features_at_or_before_cutoff")


def test_market_loader_hashes_each_source_file_once_and_reuses_row_identity(
    tmp_path, monkeypatch
):
    raw = tmp_path / "raw"
    raw.mkdir()
    source = raw / "daily.csv"
    source.write_text(
        "ticker,date,open,high,low,close,volume\n"
        "AAPL,2026-01-02,1,2,1,2,100\n"
        "SPY,2026-01-02,3,4,3,4,200\n"
    )
    calls = []
    real = builder._sha256_file
    monkeypatch.setattr(
        builder, "_sha256_file", lambda path: calls.append(path) or real(path)
    )
    market = load_market_history(raw)
    assert calls == [source]
    assert (
        market["AAPL"][0]["_source_object_id"] == market["SPY"][0]["_source_object_id"]
    )
    assert (
        market["AAPL"][0]["_source_raw_row_sha256"]
        != market["SPY"][0]["_source_raw_row_sha256"]
    )


def test_duplicate_requests_reuse_selected_rows_and_canonical_lineage(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    calendar = ExchangeCalendar()
    days = []
    candidate = date(2026, 1, 2)
    while len(days) < 70:
        try:
            calendar.session_close(candidate)
        except ValueError:
            pass
        else:
            days.append(candidate)
        candidate += timedelta(days=1)
    for symbol, base in (("AAPL", 100), ("SPY", 400), ("XLK", 200)):
        lines = ["ticker,date,open,high,low,close,volume"]
        for index, day in enumerate(days):
            close = base + index
            lines.append(
                f"{symbol},{day.isoformat()},{close},{close + 1},{close - 1},{close},1000"
            )
        (raw / f"{symbol}.csv").write_text("\n".join(lines) + "\n")
    market = load_market_history(raw)
    event = {
        "ts": _ts(days[55].isoformat()),
        "symbol": "AAPL",
        "endpoint": "quick_ask",
        "payload": {"recommendation": "BUY", "sector_etf": "XLK"},
    }
    rows, _ = build_training_rows_from_raw_market(
        [event, {**event, "endpoint": "watchlist"}],
        market,
        horizon_days=5,
        split_events=[],
    )
    split_cache = tmp_path / "splits.jsonl"
    split_cache.write_text("")
    manifest = emit_phase0_evidence_bundle(
        rows=rows,
        market=market,
        split_events=[],
        split_cache_path=split_cache,
        raw_root=raw,
        evidence_dir=tmp_path / "evidence",
        max_selected_rows=10_000,
        max_bundle_bytes=10_000_000,
    )
    assert len(rows) == 2
    assert rows[0]["reconstruction_lineage"] == rows[1]["reconstruction_lineage"]
    assert manifest["metrics"]["raw_request_observations"] == 2
    assert manifest["metrics"]["canonical_economic_observations"] == 1
    assert (
        sum(item["records"] for item in manifest["sections"]["observation_lineage"])
        == 1
    )
    assert (
        sum(
            item["records"]
            for item in manifest["sections"]["security_identity_evidence"]
        )
        == 1
    )

    too_small = tmp_path / "too-small-evidence"
    with pytest.raises(ValueError, match="PHASE0_EVIDENCE_PRIMARY_MANIFEST_TOO_LARGE"):
        emit_phase0_evidence_bundle(
            rows=rows,
            market=market,
            split_events=[],
            split_cache_path=split_cache,
            raw_root=raw,
            evidence_dir=too_small,
            max_selected_rows=10_000,
            max_bundle_bytes=100,
        )
    diagnostics = json.loads(
        (too_small / "evidence_bundle_diagnostics.json").read_text()
    )
    assert diagnostics["configured_primary_manifest_byte_limit"] == 100
    assert diagnostics["primary_manifest_bytes"] > 100
    assert diagnostics["selected_row_count"] > 0
    assert diagnostics["largest_sections"][0]["compressed_bytes"] > 0


def test_build_training_rows_adds_phase_1_technical_features(tmp_path):
    trading_days = _trading_days(date(2026, 1, 2), 61)
    market = {
        "AAPL": [
            {
                "symbol": "AAPL",
                "date": trading_days[idx - 1].isoformat(),
                "open": float(99 + idx),
                "high": float(101 + idx),
                "low": float(98 + idx),
                "close": float(100 + idx),
                "volume": float(1000 + idx),
            }
            for idx in range(1, 61)
        ],
        "SPY": [
            {
                "symbol": "SPY",
                "date": trading_days[idx - 1].isoformat(),
                "open": float(199 + (idx * 0.5)),
                "high": float(201 + (idx * 0.5)),
                "low": float(198 + (idx * 0.5)),
                "close": float(200 + (idx * 0.5)),
                "volume": float(2000 + idx),
            }
            for idx in range(1, 61)
        ],
        "XLK": [
            {
                "symbol": "XLK",
                "date": trading_days[idx - 1].isoformat(),
                "open": float(299 + (idx * 0.75)),
                "high": float(301 + (idx * 0.75)),
                "low": float(298 + (idx * 0.75)),
                "close": float(300 + (idx * 0.75)),
                "volume": float(3000 + idx),
            }
            for idx in range(1, 61)
        ],
    }
    events = [
        {
            "ts": _ts(trading_days[56].isoformat()),
            "symbol": "AAPL",
            "endpoint": "quick_ask",
            "payload": {"recommendation": "BUY", "sector_etf": "XLK"},
        }
    ]

    rows, summary = build_training_rows_from_raw_market(
        events, market, horizon_days=3, split_events=[]
    )

    assert summary["rows_joined"] == 1
    row = rows[0]
    assert row["feature_sma_10"] == 151.5
    assert row["feature_sma_20"] == 146.5
    assert row["feature_sma_50"] == 131.5
    assert row["feature_sma_10_over_20"] == round(151.5 / 146.5, 6)
    assert row["feature_sma_20_over_50"] == round(146.5 / 131.5, 6)
    assert row["feature_trend_slope_10d"] == round(1.0 / 147.0, 6)
    assert row["feature_trend_slope_20d"] == round(1.0 / 137.0, 6)
    assert row["feature_volatility_5d"] is not None
    assert row["feature_volatility_20d"] is not None
    assert row["feature_drawdown_from_20d_high"] == round(156 / 157 - 1, 6)
    assert row["feature_distance_from_20d_low"] == round(156 / 135 - 1, 6)
    assert row["feature_gap_percent"] == 0.0
    assert row["feature_ema_10"] is not None
    assert row["feature_ema_20"] is not None
    assert row["feature_price_vs_sma_20"] == round(156 / 146.5 - 1, 6)
    assert row["feature_price_vs_sma_50"] == round(156 / 131.5 - 1, 6)
    assert row["feature_rsi_14"] == 100.0
    assert row["feature_macd"] is not None
    assert row["feature_macd_signal"] is not None
    assert row["feature_macd_hist"] is not None
    assert row["feature_atr_14"] == 3.0
    expected_spy_return_5d = round(228.0 / 225.5 - 1, 6)
    assert row["feature_spy_return_1d"] == round(228.0 / 227.5 - 1, 6)
    assert row["feature_spy_return_5d"] == expected_spy_return_5d
    assert row["feature_symbol_minus_spy_5d"] == round(
        row["feature_return_5d_lagged"] - expected_spy_return_5d, 6
    )
    assert row["feature_symbol_beta_20d"] is not None
    expected_sector_return_5d = round(342.0 / 338.25 - 1, 6)
    assert row["feature_sector_relative_return_5d"] == round(
        row["feature_return_5d_lagged"] - expected_sector_return_5d, 6
    )
    assert row["feature_market_regime_risk_on"] == 1
    assert row["feature_market_volatility_proxy"] is not None
    assert row["feature_return_10d_lagged"] == round(156 / 146 - 1, 6)
    assert row["feature_return_20d_lagged"] == round(156 / 136 - 1, 6)
    assert row["feature_momentum_5d_vs_20d"] == round(
        row["feature_return_5d_lagged"] - row["feature_return_20d_lagged"], 6
    )
    assert row["feature_volume"] == 1056.0
    assert row["feature_volume_ratio_20d"] == round(1056.0 / 1046.5, 6)
    assert row["feature_relative_volume_5d"] == round(1056.0 / 1054.0, 6)
    assert row["feature_volume_zscore_20d"] == 1.647509
    expected_vwap = round(
        sum(float(100 + idx) * float(1000 + idx) for idx in range(37, 57))
        / sum(float(1000 + idx) for idx in range(37, 57)),
        6,
    )
    assert row["feature_vwap"] == expected_vwap
    assert row["feature_price_vs_vwap"] == round(156.0 / expected_vwap - 1, 6)
    assert row["feature_vwap_slope"] is not None
    assert row["feature_above_vwap"] == 1
    assert row["feature_dollar_volume"] == 156.0 * 1056.0


def test_build_training_rows_adds_symbol_signal_history_counts():
    trading_days = _trading_days(date(2026, 1, 2), 61)
    market = {
        "AAPL": [
            {
                "symbol": "AAPL",
                "date": trading_days[idx - 1].isoformat(),
                "open": float(99 + idx),
                "high": float(101 + idx),
                "low": float(98 + idx),
                "close": float(100 + idx),
                "volume": float(1000 + idx),
            }
            for idx in range(1, 61)
        ]
    }
    market["SPY"] = [{**row, "symbol": "SPY"} for row in market["AAPL"]]
    events = [
        {
            "ts": _ts("2026-02-25"),
            "symbol": "AAPL",
            "endpoint": "quick_ask",
            "payload": {"recommendation": "BUY", "probability_up": 0.70},
        },
        {
            "ts": _ts("2026-02-24"),
            "symbol": "AAPL",
            "endpoint": "quick_ask",
            "payload": {"recommendation": "BUY", "probability_up": 0.60},
        },
        {
            "ts": _ts("2026-02-20"),
            "symbol": "AAPL",
            "endpoint": "quick_ask",
            "payload": {"recommendation": "SELL"},
        },
        {
            "ts": _ts("2026-02-10"),
            "symbol": "AAPL",
            "endpoint": "quick_ask",
            "payload": {"recommendation": "BUY"},
        },
        {
            "ts": _ts("2026-02-24"),
            "symbol": "MSFT",
            "endpoint": "quick_ask",
            "payload": {"recommendation": "BUY"},
        },
    ]

    rows, summary = build_training_rows_from_raw_market(
        events, market, horizon_days=3, split_events=[]
    )

    assert summary["rows_joined"] == 4
    row = next(item for item in rows if item["event_date"] == "2026-02-25")
    state = row["request_prior_state"]
    assert state["symbol_signal_count_7d"] == 2
    assert state["symbol_buy_count_7d"] == 1
    assert state["symbol_sell_count_7d"] == 1
    assert state["days_since_last_signal"] == 1.0
    assert state["previous_recommendation_buy"] == 1
    assert state["recommendation_changed"] == 0
    assert state["probability_up_delta_from_last_signal"] == 0.1
    assert state["prior_signal_at"].endswith("+00:00")
    assert not {
        "feature_probability_up_delta_from_last_signal",
        "feature_previous_recommendation_buy",
        "feature_recommendation_changed",
        "feature_symbol_signal_count_7d",
        "feature_symbol_buy_count_7d",
        "feature_symbol_sell_count_7d",
        "feature_days_since_last_signal",
    }.intersection(row)


def test_write_rows_creates_reproducible_join_manifest(tmp_path):
    out = tmp_path / "training.jsonl"
    manifest = write_rows(
        out,
        [{"ts": 1, "symbol": "AAPL", "feature_close": 10.0, "label_up_5d": 1}],
        {"events_scanned": 1, "rows_joined": 1},
        raw_root=tmp_path / "raw",
        decision_log=tmp_path / "decision_events.jsonl",
        horizon_days=5,
        split_metadata_hash="test-hash",
    )

    assert out.exists()
    manifest_path = out.with_suffix(out.suffix + ".manifest.json")
    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "massive-decision-training-rows.v4"
    assert "leakage_safe" not in saved
    assert saved["temporal_safety"]["status"] == "NOT_EVALUATED"
    assert (
        saved["join_policy"]
        == "point-in-time completed daily bars; executable open entry; S0-based official close exit"
    )


def test_load_market_history_normalizes_massive_nanosecond_window_start(tmp_path):
    raw = tmp_path / "raw" / "2026-07-03" / "us_stocks_sip" / "day_aggs_v1"
    raw.mkdir(parents=True)
    # 2026-01-06T00:00:00Z in nanoseconds, matching Massive-style window_start values.
    (raw / "aapl.csv").write_text(
        "ticker,window_start,open,high,low,close,volume\n"
        "AAPL,1767657600000000000,10,11,9,10.5,1000\n",
        encoding="utf-8",
    )

    market = load_market_history(tmp_path / "raw")

    assert market["AAPL"][0]["date"] == "2026-01-06"


def test_load_market_history_filters_to_decision_symbols_and_date_window(tmp_path):
    raw = tmp_path / "raw" / "2026-07-03" / "us_stocks_sip" / "day_aggs_v1"
    raw.mkdir(parents=True)
    (raw / "aggs.csv").write_text(
        "ticker,date,open,high,low,close,volume\n"
        "AAPL,2026-01-02,10,10,10,10,100\n"
        "MSFT,2026-01-02,20,20,20,20,200\n"
        "AAPL,2026-02-01,30,30,30,30,300\n",
        encoding="utf-8",
    )

    market = load_market_history(
        tmp_path / "raw",
        symbols={"AAPL"},
        start_date="2026-01-01",
        end_date="2026-01-31",
    )

    assert list(market) == ["AAPL"]
    assert len(market["AAPL"]) == 1
    assert market["AAPL"][0]["date"] == "2026-01-02"


def test_load_market_history_skips_out_of_window_dated_paths(tmp_path):
    raw = tmp_path / "raw" / "2026-07-03" / "us_stocks_sip" / "day_aggs_v1"
    old_dir = raw / "2025" / "12"
    wanted_dir = raw / "2026" / "01"
    old_dir.mkdir(parents=True)
    wanted_dir.mkdir(parents=True)
    (old_dir / "2025-12-31.csv").write_text(
        "ticker,date,open,high,low,close,volume\nAAPL,2025-12-31,9,9,9,9,90\n",
        encoding="utf-8",
    )
    (wanted_dir / "2026-01-02.csv").write_text(
        "ticker,date,open,high,low,close,volume\nAAPL,2026-01-02,10,10,10,10,100\n",
        encoding="utf-8",
    )

    market = load_market_history(
        tmp_path / "raw",
        symbols={"AAPL"},
        start_date="2026-01-01",
        end_date="2026-01-31",
    )

    assert [row["date"] for row in market["AAPL"]] == ["2026-01-02"]
