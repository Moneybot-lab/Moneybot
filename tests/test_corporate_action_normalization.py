import json

import pytest
from pathlib import Path

from moneybot.services.alpha_atlas_v3_features import build_alpha_atlas_v3_features
from moneybot.services.corporate_actions import (
    adjust_bars_to_asof,
    canonical_splits,
    price_factor_between,
    split_adjusted_forward_return,
    split_source_hash,
)
from scripts.fetch_massive_splits import fetch_pages
from scripts.build_massive_decision_training_rows import (
    build_training_rows_from_raw_market,
)
from datetime import datetime, timedelta, timezone


def split(ticker, day, split_from, split_to, identifier="split"):
    kind = "reverse_split" if split_from > split_to else "forward_split"
    return {
        "ticker": ticker,
        "execution_date": day,
        "adjustment_type": kind,
        "split_from": split_from,
        "split_to": split_to,
        "id": identifier,
    }


def bar(day, close, volume=1000):
    return {
        "date": day,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": volume,
    }


def test_ctnt_reverse_split_adjusts_price_volume_and_return():
    events = canonical_splits([split("CTNT", "2026-04-29", 200, 1, "ctnt")])
    raw = [bar("2026-04-28", 0.0235, 200_000), bar("2026-04-29", 3.37, 1_000)]
    adjusted = adjust_bars_to_asof(raw, events, "2026-04-29")
    assert adjusted[0]["close"] == pytest.approx(4.70)
    assert adjusted[0]["volume"] == pytest.approx(1_000)
    assert adjusted[0]["close"] * adjusted[0]["volume"] == pytest.approx(
        raw[0]["close"] * raw[0]["volume"]
    )
    corrected = adjusted[1]["close"] / adjusted[0]["close"] - 1
    assert corrected == pytest.approx(-0.2829787234)
    assert corrected != pytest.approx(142.404255)


def test_forward_split_and_multiple_factors_compose():
    forward = canonical_splits([split("ABC", "2026-02-01", 1, 2, "fwd")])
    adjusted = adjust_bars_to_asof(
        [bar("2026-01-31", 100, 100), bar("2026-02-01", 51, 200)], forward, "2026-02-01"
    )
    assert adjusted[0]["close"] == 50
    assert adjusted[0]["volume"] == 200
    assert adjusted[1]["close"] / adjusted[0]["close"] - 1 == pytest.approx(0.02)
    multiple = canonical_splits(
        [split("ABC", "2026-02-01", 1, 2, "a"), split("ABC", "2026-03-01", 3, 1, "b")]
    )
    assert price_factor_between(multiple, "2026-01-01", "2026-03-01") == pytest.approx(
        1.5
    )


def test_stock_dividend_with_explicit_ratio_uses_same_share_basis_math():
    dividend = canonical_splits(
        [
            {
                "ticker": "ABC",
                "execution_date": "2026-02-01",
                "adjustment_type": "stock_dividend",
                "split_from": 1,
                "split_to": 1.1,
                "id": "dividend",
            }
        ]
    )
    assert len(dividend) == 1
    adjusted = adjust_bars_to_asof(
        [bar("2026-01-31", 110, 100), bar("2026-02-01", 101, 110)],
        dividend,
        "2026-02-01",
    )
    assert adjusted[0]["close"] == pytest.approx(100)
    assert adjusted[0]["volume"] == pytest.approx(110)
    assert adjusted[0]["close"] * adjusted[0]["volume"] == pytest.approx(11_000)


def test_future_split_normalizes_label_but_cannot_change_features():
    events = canonical_splits([split("ABC", "2026-01-08", 1, 2, "future")])
    history = [bar(f"2026-01-{day:02d}", 100 + day) for day in range(1, 8)]
    asof = "2026-01-07"
    adjusted_at_t = adjust_bars_to_asof(history, events, asof)
    assert all(item["_split_adjustment_factor"] == 1.0 for item in adjusted_at_t)
    no_future_features = build_alpha_atlas_v3_features(
        symbol_bars=adjusted_at_t, spy_bars=adjusted_at_t, asof_date=asof
    )
    assert no_future_features["feature_return_1d_lagged"] == pytest.approx(
        107 / 106 - 1, abs=1e-6
    )
    # 107 on T becomes 53.5 on the post 2-for-1 basis; a future close of 54 is a small gain.
    result = split_adjusted_forward_return(107, 54, asof, "2026-01-12", events)
    assert result == pytest.approx(54 / 53.5 - 1)
    assert result > 0


def test_split_on_prediction_date_is_effective_for_feature_history():
    events = canonical_splits([split("ABC", "2026-01-02", 1, 2)])
    adjusted = adjust_bars_to_asof(
        [bar("2026-01-01", 100), bar("2026-01-02", 50)], events, "2026-01-02"
    )
    assert adjusted[0]["close"] == 50
    assert adjusted[1]["close"] == 50


def test_ctnt_boundary_recomputes_canonical_features_before_emission():
    start = datetime(2026, 4, 9, tzinfo=timezone.utc)
    bars = []
    for index in range(26):
        day = (start + timedelta(days=index)).date().isoformat()
        close = (
            (0.022 + index * 0.000075) if index < 20 else (3.37 + (index - 20) * 0.03)
        )
        bars.append(
            {"symbol": "CTNT", **bar(day, close, 200_000 if index < 20 else 1_000)}
        )
    event_day = bars[20]["date"]
    events = [
        {
            "ts": int(
                datetime.fromisoformat(event_day)
                .replace(tzinfo=timezone.utc)
                .timestamp()
            ),
            "symbol": "CTNT",
            "endpoint": "quick_ask",
            "decision_source": "deterministic_model",
        }
    ]
    splits = canonical_splits([split("CTNT", event_day, 200, 1, "ctnt")])
    rows, _ = build_training_rows_from_raw_market(
        events, {"CTNT": bars}, split_events=splits
    )
    row = rows[0]
    assert row["feature_return_1d_lagged"] == pytest.approx(
        3.37 / (bars[19]["close"] * 200) - 1, abs=1e-6
    )
    assert abs(row["feature_return_5d_lagged"]) < 1
    assert abs(row["feature_return_20d_lagged"]) < 1
    assert row["feature_split_ids"] == ["ctnt"]


def test_split_cache_sort_and_hash_are_deterministic():
    first = split("B", "2026-02-01", 1, 2, "2")
    second = split("A", "2026-01-01", 2, 1, "1")
    assert split_source_hash([first, second]) == split_source_hash([second, first])
    assert [item["ticker"] for item in canonical_splits([first, second])] == ["A", "B"]


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return json.dumps(self.payload).encode()


def test_massive_split_fetch_supports_next_url_pagination():
    payloads = iter(
        [
            {
                "results": [split("A", "2026-01-01", 1, 2)],
                "next_url": "https://api.massive.com/stocks/v1/splits?page=2",
            },
            {"results": [split("B", "2026-01-02", 2, 1)], "next_url": None},
        ]
    )
    requested = []

    def opener(request, timeout):
        requested.append(request.full_url)
        return Response(next(payloads))

    records, pages = fetch_pages("secret", "2026-01-01", "2026-02-01", opener=opener)
    assert pages == 2
    assert len(records) == 2
    assert "apiKey=secret" in requested[1]


def test_track_b_materializes_splits_before_canonical_rows():
    workflow = Path(".github/workflows/track-b-offline.yml").read_text()
    assert workflow.index(
        "Materialize point-in-time Massive split metadata"
    ) < workflow.index("Build leakage-safe Massive decision training rows")
    assert (
        "--split-cache data/track_b/corporate_actions/massive_splits.jsonl" in workflow
    )
