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
from moneybot.services.outcome_tracking import close_values, evaluate_decision_events, summarize_outcome_rows


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
    closes = close_values(history)
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
    rows = evaluate_decision_events(events, future_return_lookup=_future_return)

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
