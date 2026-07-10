import json
from datetime import date, datetime, timedelta, timezone

from scripts.build_massive_decision_training_rows import build_training_rows_from_raw_market, load_market_history, write_rows


def _ts(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())


def test_build_training_rows_uses_only_asof_features_and_future_label(tmp_path):
    raw = tmp_path / "raw" / "2026-07-03" / "us_stocks_sip" / "day_aggs_v1"
    raw.mkdir(parents=True)
    csv_rows = ["ticker,date,open,high,low,close,volume"]
    for idx, close in enumerate([10, 11, 12, 13, 14, 15, 16, 18, 21, 20, 22], start=1):
        csv_rows.append(f"AAPL,2026-01-{idx:02d},{close},{close},{close},{close},{1000 + idx}")
    (raw / "aapl.csv").write_text("\n".join(csv_rows) + "\n", encoding="utf-8")
    market = load_market_history(tmp_path / "raw")
    events = [{"ts": _ts("2026-01-06"), "symbol": "AAPL", "endpoint": "quick_ask", "decision_source": "deterministic", "payload": {"recommendation": "BUY"}}]

    rows, summary = build_training_rows_from_raw_market(events, market, horizon_days=3)

    assert summary["rows_joined"] == 1
    row = rows[0]
    assert row["market_asof_date"] == "2026-01-06"
    assert row["label_asof_date"] == "2026-01-09"
    assert row["feature_close"] == 15.0
    assert row["feature_return_1d_lagged"] == round(15 / 14 - 1, 6)
    assert row["return_3d"] == round(21 / 15 - 1, 6)
    assert row["label_up_3d"] == 1
    assert row["leakage_guard"].startswith("features_asof")


def test_build_training_rows_adds_phase_1_technical_features(tmp_path):
    market = {
        "AAPL": [
            {
                "symbol": "AAPL",
                "date": (date(2026, 1, 1) + timedelta(days=idx - 1)).isoformat(),
                "open": float(99 + idx),
                "high": float(101 + idx),
                "low": float(98 + idx),
                "close": float(100 + idx),
                "volume": float(1000 + idx),
            }
            for idx in range(1, 61)
        ]
    }
    events = [{"ts": _ts("2026-02-25"), "symbol": "AAPL", "endpoint": "quick_ask", "payload": {"recommendation": "BUY"}}]

    rows, summary = build_training_rows_from_raw_market(events, market, horizon_days=3)

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
    assert row["feature_return_10d_lagged"] == round(156 / 146 - 1, 6)
    assert row["feature_return_20d_lagged"] == round(156 / 136 - 1, 6)
    assert row["feature_momentum_5d_vs_20d"] == round(
        row["feature_return_5d_lagged"] - row["feature_return_20d_lagged"], 6
    )
    assert row["feature_volume"] == 1056.0
    assert row["feature_volume_ratio_20d"] == round(1056.0 / 1046.5, 6)
    assert row["feature_relative_volume_5d"] == round(1056.0 / 1054.0, 6)
    assert row["feature_volume_zscore_20d"] == 1.647509
    assert row["feature_dollar_volume"] == 156.0 * 1056.0


def test_write_rows_creates_reproducible_join_manifest(tmp_path):
    out = tmp_path / "training.jsonl"
    manifest = write_rows(
        out,
        [{"ts": 1, "symbol": "AAPL", "feature_close": 10.0, "label_up_5d": 1}],
        {"events_scanned": 1, "rows_joined": 1},
        raw_root=tmp_path / "raw",
        decision_log=tmp_path / "decision_events.jsonl",
        horizon_days=5,
    )

    assert out.exists()
    manifest_path = out.with_suffix(out.suffix + ".manifest.json")
    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "massive-decision-training-rows.v1"
    assert saved["leakage_safe"] is True
    assert saved["join_policy"] == "last_market_row_on_or_before_decision_date; labels strictly after that row"


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

    market = load_market_history(tmp_path / "raw", symbols={"AAPL"}, start_date="2026-01-01", end_date="2026-01-31")

    assert list(market) == ["AAPL"]
    assert len(market["AAPL"]) == 1
    assert market["AAPL"][0]["date"] == "2026-01-02"
