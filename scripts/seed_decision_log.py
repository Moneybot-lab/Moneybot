#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

SYMBOLS = ["AAPL", "MSFT", "NVDA", "TSLA", "SOFI", "PLUG", "LCID", "AMD", "AMZN", "META"]
ENDPOINTS = ["quick_ask", "hot_momentum_buys", "user_watchlist"]
ACTIONS = ["BUY", "STRONG BUY", "HOLD OFF FOR NOW", "HOLD", "SELL"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed a workspace decision_events.jsonl file for Track B/offline testing.")
    parser.add_argument("--output", default="data/decision_events.jsonl")
    parser.add_argument("--rows", type=int, default=300)
    parser.add_argument("--days", type=int, default=45, help="Spread synthetic events over last N days.")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if args.overwrite else "a"

    now = int(time.time())
    horizon = max(1, int(args.days)) * 86400
    rng = random.Random(42)

    with out.open(mode, encoding="utf-8") as fh:
        for _ in range(max(1, int(args.rows))):
            ts = now - rng.randint(3 * 86400, horizon)
            symbol = rng.choice(SYMBOLS)
            endpoint = rng.choice(ENDPOINTS)
            decision_source = "deterministic_model" if rng.random() < 0.8 else "rule_based"
            prob = round(rng.uniform(0.35, 0.92), 4)
            action = rng.choice(ACTIONS)
            payload = {
                "recommendation": action,
                "model_version": "alpha-atlas-v1" if decision_source == "deterministic_model" else None,
                "probability_up": prob,
                "confidence": round(max(prob, 1.0 - prob) * 100.0, 1),
            }
            record = {
                "ts": ts,
                "endpoint": endpoint,
                "symbol": symbol,
                "decision_source": decision_source,
                "payload": payload,
            }
            fh.write(json.dumps(record) + "\n")

    print(json.dumps({"output": str(out), "rows_written": int(args.rows), "mode": mode}, indent=2))


if __name__ == "__main__":
    main()
