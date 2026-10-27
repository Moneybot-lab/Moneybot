#!/usr/bin/env python3
"""Offline evidence build + verification + report RSS benchmark; temporary data only."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
from moneybot.services.prediction_evidence import make_forecast
from moneybot.services.historical_evidence import build_evidence, EvidenceDataset
from moneybot.services.market_data_providers import ExchangeCalendar
from moneybot.services.ops_runtime import peak_rss_mib
from scripts.page6_historical_validation_report import report_from_dataset

NOW = datetime(2026, 10, 26, 23, tzinfo=timezone.utc)
ISSUED = datetime(2026, 10, 1, 15, tzinfo=timezone.utc).timestamp()


class FixtureHistory:
    calls = 0

    def __call__(self, symbol, *, start, end, **kwargs):
        self.calls += 1
        cal = ExchangeCalendar()
        dates = [d for d in pd.date_range(start, end, inclusive="left") if cal.is_trading_day(d.date())]
        frame = pd.DataFrame({"Close": [100 + i for i in range(len(dates))]}, index=dates)
        frame.attrs["provider"] = "offline_fixture"
        return frame


def fixture_event(index):
    forecast = make_forecast(symbol="ABC", artifact={"version": "fixture-v1"}, features=[[index]],
                             quote={"price": 100, "event_timestamp": "2026-10-01T14:59:59+00:00", "source": "fixture", "source_mode": "rest", "is_stale": False},
                             recommendation="BUY", probability_up=.8, raw_probability_up=.8,
                             horizon="5d", calibration={"applied": False}, now=ISSUED)
    return {"ts": int(ISSUED), "symbol": "ABC", "decision_source": "deterministic_model", "endpoint": "quick_ask",
            "event_category": "probabilistic_model_forecast", "forecast": forecast}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, choices=[5000, 20000, 50000], default=50000)
    parser.add_argument("--case", choices=["none", "sparse", "mixed", "all"], default="mixed")
    parser.add_argument("--payload-bytes", type=int, default=8192)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="historical-evidence-benchmark-") as directory:
        log, artifact = Path(directory) / "events.jsonl", Path(directory) / "evidence.sqlite"
        with log.open("w") as fh:
            for i in range(args.events):
                event = {"ts": int(ISSUED), "symbol": "ABC", "endpoint": "user_watchlist", "decision_source": "ai_enhanced", "payload": {"advice": "SELL"}}
                if args.case == "all" or (args.case in {"sparse", "mixed"} and i < 12):
                    event = fixture_event(i)
                elif args.case == "mixed" and i < 24:
                    event = fixture_event(i - 12)
                elif args.case == "mixed" and i < 36:
                    event = fixture_event(i)
                    event["forecast"]["probability_up"] = 80 if i % 2 else float("nan")
                elif args.case == "mixed" and i < 48:
                    event = fixture_event(i)
                    event["forecast"]["model_version"] = None
                elif args.case == "mixed" and i < 60:
                    event = fixture_event(i)
                    event["forecast"]["shadow_only"] = True
                    event["event_category"] = "shadow_prediction"
                event["snapshot"] = {"unused": "x" * args.payload_bytes}
                fh.write(json.dumps(event) + "\n")
        provider = FixtureHistory()
        start = time.monotonic()
        manifest = build_evidence(log, artifact, download=provider, now=NOW)
        ds = EvidenceDataset(artifact)
        try:
            report = report_from_dataset(ds)
        finally:
            ds.close()
        print(json.dumps({"case": args.case, "events": args.events, "jsonl_bytes": log.stat().st_size,
                          "artifact_bytes": artifact.stat().st_size, "peak_rss_mib": peak_rss_mib(),
                          "elapsed_seconds": round(time.monotonic() - start, 3), "history_requests": provider.calls,
                          "complete": manifest["complete"], "counts": manifest["counts"], "exclusions": manifest["exclusions"],
                          "evaluated_rows": report["metrics"]["evaluated_rows"], "brier_score": report["metrics"]["brier_score"],
                          "validation_status": report["validation_status"], "promotion_ready": report["promotion_ready"]}, sort_keys=True))


if __name__ == "__main__":
    main()
