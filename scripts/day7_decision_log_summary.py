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
    args = parser.parse_args()

    summary = summarize_decision_events(args.input, limit=max(1, args.limit))
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
