"""Measure diagnostic overhead with synthetic complete portfolios; no network."""
import argparse
import hashlib
import json
import logging
from pathlib import Path
import resource
import statistics
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.benchmark_portfolio_watchlist import scenario, stable_payload
from moneybot.extensions import db


def run(mode, samples):
    try:
        from moneybot.services.request_timing import install_request_timing
    except ModuleNotFoundError:
        install_request_timing = None
    times, cpu, hashes, sizes = [], [], set(), []
    with tempfile.TemporaryDirectory(prefix="portfolio-timing-") as directory:
        with scenario(directory, 20, 5, "success", market_delay=0, ai_delay=0) as s:
            if mode != "baseline":
                assert install_request_timing is not None
                s.app.config.update(PORTFOLIO_TIMING_SAMPLE_RATE=1 if mode == "sampled" else 0)
                install_request_timing(s.app, db)
            # Force each *local synthetic* request to be measured for overhead.
            # Production sampling budgets are covered by separate regression tests.
            sampler = s.app.extensions.get("portfolio_timing_sampler")
            sink = logging.getLogger("moneybot.portfolio_timing")

            def record(message, encoded):
                sizes.append(len(encoded.encode()))

            with patch.object(sink, "info", record):
                for i in range(samples+5):
                    if sampler is not None:
                        sampler.used = 0
                    start, cpu_start = time.perf_counter(), time.thread_time()
                    response = s.client.get("/api/user-watchlist")
                    elapsed, cpu_elapsed = time.perf_counter()-start, time.thread_time()-cpu_start
                    assert response.status_code == 200
                    if i >= 5:
                        times.append(elapsed*1000)
                        cpu.append(cpu_elapsed*1000)
                        hashes.add(hashlib.sha256(json.dumps(stable_payload(response.get_json()), sort_keys=True).encode()).hexdigest())
            assert len(hashes) == 1
    return dict(mode=mode, samples=samples, median_ms=statistics.median(times),
                p95_ms=sorted(times)[int(.95*samples+.999)-1], max_ms=max(times),
                median_request_thread_cpu_ms=statistics.median(cpu),
                timing_records=len(sizes), max_log_bytes=max(sizes, default=0),
                semantic_sha256=list(hashes), wall_ms=times,
                note="20 lots / 5 symbols, successful synthetic AI, no provider delay; five warmups; sampled mode resets only local budget; log sink excludes real output transport")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["baseline", "disabled", "sampled"], required=True)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 20 <= args.samples <= 1000:
        parser.error("samples must be 20..1000")
    result = run(args.mode, args.samples)
    result["peak_rss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
    args.output.write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps({key: value for key, value in result.items() if key not in {"wall_ms", "semantic_sha256"}}))


if __name__ == "__main__":
    main()
