#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yfinance as yf

from moneybot.services.decision_log import read_decision_events
from moneybot.services.outcome_tracking import classify_outcome, normalize_action, summarize_outcome_rows


def _close_values(history) -> list[float]:
    if history is None or getattr(history, "empty", False):
        return []
    if "Close" not in history:
        return []

    close_data = history["Close"]
    if hasattr(close_data, "columns"):
        if getattr(close_data, "empty", False):
            return []
        close_data = close_data.iloc[:, 0]

    if not hasattr(close_data, "dropna"):
        return []

    return [float(value) for value in close_data.dropna().tolist()]


def _future_return(symbol: str, start_ts: int, days: int) -> float | None:
    start_dt = datetime.fromtimestamp(int(start_ts), tz=timezone.utc)
    end_dt = start_dt + timedelta(days=max(days + 3, 7))
    history = yf.download(
        symbol,
        start=start_dt.strftime("%Y-%m-%d"),
        end=end_dt.strftime("%Y-%m-%d"),
        interval="1d",
        progress=False,
        auto_adjust=False,
    )
    closes = _close_values(history)
    if len(closes) <= days:
        return None
    start_price = float(closes[0])
    end_price = float(closes[days])
    if start_price == 0:
        return None
    return round((end_price - start_price) / start_price, 4)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate logged recommendations against later price moves.")
    parser.add_argument("--input", default="data/decision_events.jsonl")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    events = read_decision_events(args.input, limit=max(1, args.limit))
    rows = []
    for event in events:
        symbol = str(event.get("symbol") or "").strip().upper()
        action = normalize_action(event)
        ts = event.get("ts")
        if not symbol or action is None or not isinstance(ts, int):
            continue
        row = {
            "symbol": symbol,
            "endpoint": event.get("endpoint"),
            "decision_source": event.get("decision_source"),
            "action": action,
            "ts": ts,
        }
        row["return_1d"] = _future_return(symbol, ts, 1)
        row["return_5d"] = _future_return(symbol, ts, 5)
        row["outcome_1d"] = classify_outcome(action, row["return_1d"])
        row["outcome_5d"] = classify_outcome(action, row["return_5d"])
        rows.append(row)

    output = {
        "summary_1d": summarize_outcome_rows(
            [{**row, "return_1d": row["return_1d"]} for row in rows]
        ),
        "summary_5d": summarize_outcome_rows(
            [{**row, "return_1d": row["return_5d"]} for row in rows]
        ),
        "rows": rows,
    }

    payload = json.dumps(output, indent=2, sort_keys=True)
    print(payload)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")


if __name__ == "__main__":
    main()
