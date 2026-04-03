from __future__ import annotations

from datetime import datetime, timezone

from scripts.day14_generate_daily_report import build_report_text


def test_build_report_text_prefills_known_metrics_and_keeps_takeaway_placeholders():
    text = build_report_text(
        day1_meta={
            "model_path": "data/day1_baseline_model.json",
            "train_rows": 4047,
            "metrics": {"accuracy": 0.4964, "positive_rate": 0.298, "rows": 963},
        },
        decision_summary={
            "events_considered": 104,
            "endpoint_counts": {"hot_momentum_buys": 35, "quick_ask": 3, "user_watchlist": 66},
            "latest_event": {"symbol": "F", "endpoint": "hot_momentum_buys", "decision_source": "deterministic_model", "ts": 1774752427},
            "top_symbols": [{"symbol": "F", "count": 16}, {"symbol": "NIO", "count": 15}],
        },
        outcomes_snapshot={
            "data": {
                "rows": [{"symbol": "AAPL"}, {"symbol": "MSFT"}],
                "summary_1d": {
                    "rows": 69,
                    "evaluated_rows": 17,
                    "accuracy": 0.7647,
                    "avg_return_1d": 0.0073,
                    "counts": {"correct": 13, "incorrect": 4, "neutral": 51, "skipped": 1},
                },
                "summary_5d": {
                    "rows": 69,
                    "evaluated_rows": 0,
                    "accuracy": None,
                    "avg_return_5d": None,
                    "counts": {"correct": 0, "incorrect": 0, "neutral": 0, "skipped": 69},
                },
            }
        },
        calibration_report={
            "rows": 0,
            "avg_predicted": None,
            "avg_observed": None,
            "brier_score": None,
            "recommended": {"slope_delta": 0.0, "intercept_delta": 0.0},
        },
        recalibration_plan={
            "apply_change": False,
            "current": {"slope": 1.0, "intercept": 0.0},
            "next": {"slope": 1.0, "intercept": 0.0},
        },
        now_utc=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )

    assert "**Date (UTC):** `2026-04-01`" in text
    assert "- **Training rows written:** `4047`" in text
    assert "- Accuracy: `0.4964`" in text
    assert "- **Events considered:** `104`" in text
    assert "- **Top symbols:** `F (16), NIO (15)`" in text
    assert "- **Rows materialized (if reported):** `2`" in text
    assert "- **Apply change:** `False`" in text
    assert "- `____`" in text


def test_build_report_text_handles_missing_data_with_warnings_and_na():
    text = build_report_text(
        day1_meta={},
        decision_summary={},
        outcomes_snapshot={},
        calibration_report={},
        recalibration_plan={},
        now_utc=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )

    assert text.count("⚠️ Warning") >= 5
    assert "`N/A`" in text
