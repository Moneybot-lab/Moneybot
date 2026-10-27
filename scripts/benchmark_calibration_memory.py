#!/usr/bin/env python3
"""Deterministic offline calibration RSS benchmark (writes only to a tempdir).

Run with --events 20000 / 50000. --legacy-reader measures the original raw-line
plus decoded-record retention, using the same imports and synthetic JSONL.
"""
import argparse
import json
import sys
import tempfile
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from moneybot.services.ops_runtime import peak_rss_mib
from scripts import day13_calibration_report as calibration


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=int, default=50000)
    parser.add_argument("--payload-bytes", type=int, default=8192)
    parser.add_argument("--mature-rows", type=int, default=0)
    parser.add_argument("--legacy-reader", action="store_true")
    args = parser.parse_args()
    class NoNetwork:
        massive_requests = 0
        def __call__(self, *a, **k):
            import pandas as pd
            self.massive_requests += 1
            return pd.DataFrame({"Close": range(100, 120)}, index=pd.date_range(k["start"], periods=20))
        def provider_for_symbol(self, symbol):
            return "deterministic_fixture"
        def diagnostics_payload(self):
            return {}
    calibration.MassivePreferredHistoryDownload = NoNetwork
    with tempfile.TemporaryDirectory(prefix="calibration-memory-") as directory:
        path = Path(directory) / "events.jsonl"
        line = json.dumps({"symbol": "AAPL", "ts": 1704204000,
                           "payload": {"probability_up": None, "forecast_horizon": "5d"},
                           "snapshot": {"signals": {"unused": "x" * args.payload_bytes}}}) + "\n"
        with path.open("w") as fh:
            for i in range(args.events):
                fh.write(line.replace('"probability_up": null', '"probability_up": 0.7') if i < args.mature_rows else line)
        started = time.monotonic()
        if args.legacy_reader:
            events = []
            limit = 5000
            while True:
                # Original reader keeps all raw tail lines while decoding; the
                # caller retains the previous batch during the next assignment.
                def legacy_read():
                    with path.open() as fh:
                        lines = list(deque(fh, maxlen=limit))
                    return [json.loads(line) for line in lines]
                events = legacy_read()
                if limit >= 50000:
                    break
                limit = min(limit * 2, 50000)
            assert len(events) == args.events
        else:
            sys.argv = ["calibration", "--input", str(path), "--output", str(Path(directory) / "report.json"), "--limit", "5000"]
            calibration.main()
        print(json.dumps({"implementation": "legacy_reader" if args.legacy_reader else "repaired_full_stage",
                          "events": args.events, "mature_rows": args.mature_rows, "unused_payload_bytes": args.payload_bytes,
                          "jsonl_bytes": path.stat().st_size, "peak_rss_mib": peak_rss_mib(),
                          "elapsed_seconds": round(time.monotonic() - started, 3)}))


if __name__ == "__main__":
    main()
