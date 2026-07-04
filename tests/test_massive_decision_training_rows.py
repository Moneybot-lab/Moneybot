import json
from datetime import datetime, timezone

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
    events = [{"ts": _ts("2026-01-07"), "symbol": "AAPL", "endpoint": "quick_ask", "decision_source": "deterministic", "payload": {"recommendation": "BUY"}}]

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


def test_build_training_rows_does_not_use_same_day_daily_bar_for_preclose_event(tmp_path):
    raw = tmp_path / "raw" / "2026-07-03" / "us_stocks_sip" / "day_aggs_v1"
    raw.mkdir(parents=True)
    csv_rows = ["ticker,date,open,high,low,close,volume"]
    for idx, close in enumerate([10, 11, 12, 13, 14, 15, 99, 18, 21, 20, 22], start=1):
        csv_rows.append(f"AAPL,2026-01-{idx:02d},{close},{close},{close},{close},{1000 + idx}")
    (raw / "aapl.csv").write_text("\n".join(csv_rows) + "\n", encoding="utf-8")
    market = load_market_history(tmp_path / "raw")
    events = [{"ts": _ts("2026-01-07"), "symbol": "AAPL", "payload": {"recommendation": "BUY"}}]

    rows, summary = build_training_rows_from_raw_market(events, market, horizon_days=3)

    assert summary["rows_joined"] == 1
    row = rows[0]
    assert row["event_date"] == "2026-01-07"
    assert row["market_asof_date"] == "2026-01-06"
    assert row["feature_close"] == 15.0
    assert row["label_asof_date"] == "2026-01-09"
    assert row["return_3d"] == round(21 / 15 - 1, 6)


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
    assert saved["join_policy"] == "last_completed_market_row_before_decision_date; labels strictly after that row"


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
