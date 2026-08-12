#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moneybot.services.decision_log import read_decision_events
from moneybot.services.outcome_history_provider import MassivePreferredHistoryDownload
from moneybot.services.outcome_tracking import (
    OutcomeHistoryCache,
    evaluate_decision_events,
    merge_recent_rows,
    rows_with_any_horizon_return,
    rows_with_horizon_accuracy_outcome,
    rows_with_horizon_return,
    select_recent_unique_rows,
    summarize_outcome_rows,
    summarize_paper_pnl_by_action,
)
from moneybot.services.runtime_paths import resolve_runtime_dir


def select_visible_rows(
    rows: list[dict],
    evaluated_rows: list[dict],
    rows_limit: int,
    *,
    horizon: str | None = None,
) -> list[dict]:
    source_rows = evaluated_rows if evaluated_rows else rows
    return select_recent_unique_rows(source_rows, limit=rows_limit, horizon=horizon)


def summarize_horizon(rows: list[dict], horizon: str) -> dict:
    if horizon == "5d":
        return summarize_outcome_rows(
            [{**row, "return_1d": row.get("return_5d")} for row in rows]
        )
    return summarize_outcome_rows(rows)


def _price_path(symbol: str, start_ts: int, days: int) -> list[float]:
    start_dt = datetime.fromtimestamp(int(start_ts), tz=timezone.utc)
    now_utc = datetime.now(timezone.utc)
    if start_dt >= now_utc:
        return []
    if start_dt + timedelta(days=days) > now_utc:
        return []
    end_dt = start_dt + timedelta(days=max(days + 3, 7))
    safe_end_dt = min(end_dt, now_utc + timedelta(days=1))
    try:
        history = yf.download(
            symbol,
            start=start_dt.strftime("%Y-%m-%d"),
            end=safe_end_dt.strftime("%Y-%m-%d"),
            interval="1d",
            progress=False,
            auto_adjust=False,
        )
    except Exception:  # noqa: BLE001
        return []
    return close_values(history)


def _benchmark_return(start_ts: int, days: int) -> float | None:
    return _future_return("SPY", start_ts, days)


def _price_path(symbol: str, start_ts: int, days: int) -> list[float]:
    start_dt = datetime.fromtimestamp(int(start_ts), tz=timezone.utc)
    now_utc = datetime.now(timezone.utc)
    if start_dt >= now_utc:
        return []
    if start_dt + timedelta(days=days) > now_utc:
        return []
    end_dt = start_dt + timedelta(days=max(days + 3, 7))
    safe_end_dt = min(end_dt, now_utc + timedelta(days=1))
    try:
        history = yf.download(
            symbol,
            start=start_dt.strftime("%Y-%m-%d"),
            end=safe_end_dt.strftime("%Y-%m-%d"),
            interval="1d",
            progress=False,
            auto_adjust=False,
        )
    except Exception:  # noqa: BLE001
        return []
    return close_values(history)


def _benchmark_return(start_ts: int, days: int) -> float | None:
    return _future_return("SPY", start_ts, days)


def _price_path(symbol: str, start_ts: int, days: int) -> list[float]:
    start_dt = datetime.fromtimestamp(int(start_ts), tz=timezone.utc)
    now_utc = datetime.now(timezone.utc)
    if start_dt >= now_utc:
        return []
    if start_dt + timedelta(days=days) > now_utc:
        return []
    end_dt = start_dt + timedelta(days=max(days + 3, 7))
    safe_end_dt = min(end_dt, now_utc + timedelta(days=1))
    try:
        history = yf.download(
            symbol,
            start=start_dt.strftime("%Y-%m-%d"),
            end=safe_end_dt.strftime("%Y-%m-%d"),
            interval="1d",
            progress=False,
            auto_adjust=False,
        )
    except Exception:  # noqa: BLE001
        return []
    return close_values(history)


def _benchmark_return(start_ts: int, days: int) -> float | None:
    return _future_return("SPY", start_ts, days)


def main() -> None:
    base_dir = resolve_runtime_dir()
    parser = argparse.ArgumentParser(
        description="Materialize decision outcomes to a snapshot JSON file."
    )
    parser.add_argument("--input", default=str(base_dir / "decision_events.jsonl"))
    parser.add_argument(
        "--output", default=str(base_dir / "decision_outcomes_snapshot.json")
    )
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--rows-limit", type=int, default=20)
    parser.add_argument(
        "--prefer-massive",
        action="store_true",
        default=True,
        help="Prefer Massive aggregate bars before yfinance fallback.",
    )
    parser.add_argument(
        "--no-prefer-massive",
        dest="prefer_massive",
        action="store_false",
        help="Use legacy yfinance-only outcome materialization.",
    )
    args = parser.parse_args()

    events = read_decision_events(args.input, limit=max(1, args.limit))
    rows = evaluate_decision_events(
        events,
        future_return_lookup=_future_return,
        price_path_lookup=_price_path,
        benchmark_return_lookup=_benchmark_return,
    )
    evaluated_rows_1d = rows_with_horizon_return(rows, "1d")
    evaluated_rows_5d = rows_with_horizon_return(rows, "5d")
    evaluated_rows = rows_with_any_horizon_return(rows)
    actionable_rows_1d = rows_with_horizon_accuracy_outcome(rows, "1d")
    actionable_rows_5d = rows_with_horizon_accuracy_outcome(rows, "5d")
    visible_rows_1d = (
        select_visible_rows(
            rows, actionable_rows_1d or evaluated_rows_1d, args.rows_limit, horizon="1d"
        )
        if evaluated_rows_1d
        else []
    )
    visible_rows_5d = (
        select_visible_rows(
            rows, actionable_rows_5d or evaluated_rows_5d, args.rows_limit, horizon="5d"
        )
        if evaluated_rows_5d
        else []
    )
    visible_rows = merge_recent_rows(
        visible_rows_1d, visible_rows_5d, limit=args.rows_limit
    )
    if not visible_rows and rows:
        visible_rows = select_visible_rows(rows, [], args.rows_limit)
    visible_pnl_rows = merge_recent_rows(
        visible_rows_1d,
        visible_rows_5d,
        limit=max(1, len(visible_rows_1d) + len(visible_rows_5d)),
    )
    if not visible_pnl_rows:
        visible_pnl_rows = visible_rows

    payload = {
        "computed_at_utc": datetime.now(timezone.utc).isoformat(),
        "data": {
            "rows": visible_rows,
            "rows_1d": visible_rows_1d,
            "rows_5d": visible_rows_5d,
            "summary_1d": summarize_horizon(visible_rows_1d, "1d"),
            "summary_5d": summarize_horizon(visible_rows_5d, "5d"),
            "paper_pnl_by_recommendation": summarize_paper_pnl_by_action(rows),
            "include_skipped": False,
            "events_read": len(events),
            "rows_scanned": len(rows),
            "aggregate_complete": len(events) < max(1, args.limit),
            "aggregate_events_available": None,
            "aggregate_events_scanned": len(events),
            "aggregate_scan_cap_reached": len(events) >= max(1, args.limit),
            "aggregate_oldest_event_ts": (
                min(event_ts_values) if event_ts_values else None
            ),
            "evaluated_rows_available": len(evaluated_rows),
            "evaluated_rows_1d_available": len(evaluated_rows_1d),
            "evaluated_rows_5d_available": len(evaluated_rows_5d),
            "used_unevaluated_fallback": len(evaluated_rows) == 0 and bool(rows),
            "oldest_event_ts_scanned": (
                min(event_ts_values) if event_ts_values else None
            ),
            "newest_event_ts_scanned": (
                max(event_ts_values) if event_ts_values else None
            ),
            "read_cap": max(1, args.limit),
            "scan_cap_reached": len(events) >= max(1, args.limit),
            "all_available_events_read": len(events) < max(1, args.limit),
            **history_cache.diagnostics_payload(),
            **(
                history_download.diagnostics_payload()
                if hasattr(history_download, "diagnostics_payload")
                else {"outcome_history_preferred_provider": "yfinance"}
            ),
            "lookup_cache_hits": history_cache.diagnostics.history_cache_hits,
            "lookup_cache_misses": history_cache.diagnostics.history_cache_misses,
            "lookup_cache_size": history_cache.cache_size,
            "lookup_errors": history_cache.diagnostics.history_download_errors,
        },
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(f"Wrote outcomes snapshot -> {output_path}")


if __name__ == "__main__":
    main()
