#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yfinance as yf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moneybot.services.decision_log import read_decision_events
from moneybot.services.outcome_tracking import close_values, evaluate_decision_events, summarize_outcome_rows


def select_visible_rows(rows: list[dict], evaluated_rows: list[dict], rows_limit: int) -> list[dict]:
    limit = max(1, int(rows_limit))
    return evaluated_rows[-limit:] if evaluated_rows else rows[-limit:]


def _future_return(symbol: str, start_ts: int, days: int) -> float | None:
    start_dt = datetime.fromtimestamp(int(start_ts), tz=timezone.utc)
    now_utc = datetime.now(timezone.utc)
    if start_dt >= now_utc:
        return None
    if start_dt + timedelta(days=days) > now_utc:
        return None

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
        return None
    closes = close_values(history)
    if len(closes) <= days:
        return None
    start_price = float(closes[0])
    end_price = float(closes[days])
    if start_price == 0:
        return None
    return round((end_price - start_price) / start_price, 4)


def main() -> None:
    base_dir = os.getenv("MONEYBOT_PERSISTENT_DATA_DIR", "data")
    os.makedirs(base_dir, exist_ok=True)
    parser = argparse.ArgumentParser(description="Materialize decision outcomes to a snapshot JSON file.")
    parser.add_argument("--input", default=os.path.join(base_dir, "decision_events.jsonl"))
    parser.add_argument("--output", default=os.path.join(base_dir, "decision_outcomes_snapshot.json"))
    parser.add_argument("--limit", type=int, default=2000)
    parser.add_argument("--rows-limit", type=int, default=20)
    args = parser.parse_args()

    events = read_decision_events(args.input, limit=max(1, args.limit))
    rows = evaluate_decision_events(events, future_return_lookup=_future_return)
    evaluated_rows = [
        row
        for row in rows
        if isinstance(row.get("return_1d"), (int, float)) or isinstance(row.get("return_5d"), (int, float))
    ]
    visible_rows = select_visible_rows(rows, evaluated_rows, args.rows_limit)

    payload = {
        "computed_at_utc": datetime.now(timezone.utc).isoformat(),
        "data": {
            "rows": visible_rows,
            "summary_1d": summarize_outcome_rows(visible_rows),
            "summary_5d": summarize_outcome_rows([{**row, "return_1d": row.get("return_5d")} for row in visible_rows]),
            "include_skipped": False,
            "rows_scanned": len(rows),
            "evaluated_rows_available": len(evaluated_rows),
            "used_unevaluated_fallback": len(evaluated_rows) == 0 and bool(rows),
            "lookup_cache_hits": 0,
            "lookup_cache_misses": 0,
            "lookup_cache_size": 0,
            "lookup_errors": 0,
        },
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote outcomes snapshot -> {output_path}")


if __name__ == "__main__":
    main()
