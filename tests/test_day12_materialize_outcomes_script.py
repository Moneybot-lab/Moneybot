import json
from datetime import datetime, timezone

import pandas as pd

from scripts import day12_materialize_outcomes as materializer
from scripts.day12_materialize_outcomes import select_visible_rows


def test_select_visible_rows_prefers_evaluated_rows_and_returns_list_slice():
    rows = [{"symbol": "RAW1"}, {"symbol": "RAW2"}]
    evaluated_rows = [{"symbol": "E1"}, {"symbol": "E2"}, {"symbol": "E3"}]

    visible = select_visible_rows(rows, evaluated_rows, 2)

    assert visible == [{"symbol": "E2"}, {"symbol": "E3"}]
    assert isinstance(visible, list)


def test_select_visible_rows_falls_back_to_recent_raw_rows():
    rows = [{"symbol": "RAW1"}, {"symbol": "RAW2"}, {"symbol": "RAW3"}]

    visible = select_visible_rows(rows, [], 2)

    assert visible == [{"symbol": "RAW2"}, {"symbol": "RAW3"}]



def test_day12_materializer_uses_history_cache_for_repeated_symbol_date(tmp_path, monkeypatch):
    input_path = tmp_path / "decision_events.jsonl"
    output_path = tmp_path / "decision_outcomes_snapshot.json"
    event = {
        "ts": int(datetime(2026, 1, 2, tzinfo=timezone.utc).timestamp()),
        "endpoint": "quick_ask",
        "symbol": "AAPL",
        "decision_source": "deterministic_model",
        "payload": {"recommendation": "BUY"},
    }
    input_path.write_text("\n".join(json.dumps(event) for _ in range(3)) + "\n", encoding="utf-8")
    calls = []

    def fake_download(symbol, **kwargs):
        calls.append((symbol, kwargs["start"]))
        dates = pd.bdate_range(start="2026-01-02", periods=30)
        return pd.DataFrame({"Close": [100 + idx for idx in range(30)]}, index=dates)

    monkeypatch.setattr(materializer.yf, "download", fake_download)
    monkeypatch.setattr(
        materializer.sys,
        "argv",
        [
            "day12_materialize_outcomes.py",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--limit",
            "10",
            "--rows-limit",
            "2",
        ],
    )

    materializer.main()

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["data"]["events_read"] == 3
    assert payload["data"]["evaluated_rows_5d_available"] == 3
    assert payload["data"]["history_cache_misses"] == 2  # AAPL + SPY
    assert len(calls) == 2
