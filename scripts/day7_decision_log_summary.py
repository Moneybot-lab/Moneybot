#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moneybot.services.decision_log import summarize_decision_events


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize recent Moneybot decision log activity.")
    parser.add_argument(
        "--input",
        default="data/decision_events.jsonl",
        help="Path to the JSONL decision log file.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Number of most recent events to summarize.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional file path to write the summary JSON.",
    )
    args = parser.parse_args()

    summary = summarize_decision_events(args.input, limit=max(1, args.limit))
    payload = json.dumps(summary, indent=2, sort_keys=True)
    print(payload)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(payload, encoding="utf-8")


if __name__ == "__main__":
    main()
