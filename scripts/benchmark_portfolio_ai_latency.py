"""Offline baseline/repair comparison with identical synthetic provider schedules.

Run this script with --repo pointing at an unchanged baseline checkout and then
the repair checkout. The imported routes/services always come from that repo.
No provider access, secrets, paid requests, or production traffic are used.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import nullcontext
import hashlib
import json
import math
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--mode", choices=("baseline", "repair"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--realistic", action="store_true", help="28 lots/10 symbols; 0.209s market and 2.627s AI sleeps")
    args = parser.parse_args()
    if not 1 <= args.samples <= 30:
        parser.error("samples must be 1..30")
    root = args.repo.resolve()
    sys.path.insert(0, str(root))
    import requests
    from scripts.benchmark_portfolio_watchlist import scenario

    valid = {"advice": "HOLD", "advice_reason": "HOLD: synthetic response.",
             "risk_notes": ["risk one", "risk two"], "next_checks": ["check one", "check two"]}
    financial = ("id", "symbol", "shares", "entry_price", "current_price", "history30", "history30_bars",
                 "history30_source", "quote_source", "quote_diagnostics", "performance_percent",
                 "performance_amount", "today_change_percent", "today_change_amount", "position_weight_percent",
                 "sector_weight_percent", "weight_basis")
    cases = []
    workloads = [(28, 10)] if args.realistic else [(5, 5), (10, 10), (20, 20), (28, 28), (28, 10)]
    failures = ["profile_sensitive"] if args.realistic else ["success", "profile_sensitive", "first_malformed", "timeout", "server_error", "unavailable", "always_malformed"]
    market_delay, ai_delay = (.209, 2.627) if args.realistic else (.001, .002)
    for failure in failures:
        for positions, unique in workloads:
            observations = []
            for sample in range(args.samples):
                with tempfile.TemporaryDirectory(prefix="portfolio-ai-benchmark-") as directory, scenario(directory, positions, unique, failure, market_delay, ai_delay) as s:
                    attempts = Counter()
                    metrics = Counter()
                    if failure == "unavailable": s.ai.enabled = False

                    def provider(*a, **kw):
                        payload = kw["json"]
                        # Keep synthetic per-lot context only in memory; export aggregates.
                        key = hashlib.sha256(payload["input"].encode()).hexdigest()
                        attempts[key] += 1
                        metrics["attempts"] += 1
                        metrics["allocated_output_tokens"] += payload["max_output_tokens"]
                        with s.m.measure("provider.ai.synthetic_openai", provider=True):
                            time.sleep(ai_delay)
                            if failure == "timeout":
                                metrics["timeouts"] += 1
                                raise requests.ReadTimeout("synthetic only")
                            if failure == "server_error":
                                metrics["http_5xx"] += 1
                                raise requests.HTTPError(response=SimpleNamespace(status_code=503))
                            invalid = (failure == "profile_sensitive" and payload["max_output_tokens"] == 220
                                       or failure == "first_malformed" and attempts[key] == 1 or failure == "always_malformed")
                            metrics["invalid" if invalid else "valid"] += 1
                            if not invalid and attempts[key] == 1: metrics["first_valid"] += 1
                            output_tokens, reasoning_tokens = (220, 200) if invalid else (180, 20)
                            metrics["reported_input_tokens"] += 400
                            metrics["reported_output_tokens"] += output_tokens
                            metrics["reported_reasoning_tokens"] += reasoning_tokens
                            data = {"status": "incomplete" if invalid else "completed",
                                    "output_text": '{"advice":' if invalid else json.dumps(valid),
                                    "usage": {"input_tokens": 400, "output_tokens": output_tokens,
                                              "output_tokens_details": {"reasoning_tokens": reasoning_tokens}}}
                            if invalid: data["incomplete_details"] = {"reason": "max_output_tokens"}
                            return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: data)

                    # Each isolated synthetic app owns its own lock directory.
                    # Do not contend with unrelated local tests using user 1.
                    fence = (patch("moneybot.services.portfolio_enrichment.tempfile", SimpleNamespace(gettempdir=lambda: directory))
                             if args.mode == "repair" else nullcontext())
                    with patch("requests.post", provider), fence:
                        start, cpu = time.perf_counter(), time.process_time()
                        response = s.client.get("/api/user-watchlist/stream" if args.mode == "repair" else "/api/user-watchlist", buffered=False)
                        assert response.status_code == 200
                        if args.mode == "repair":
                            iterator = iter(response.response)
                            snapshot = json.loads(next(iterator))
                            assert snapshot["type"] == "snapshot"
                            usable = time.perf_counter() - start
                            assert metrics["attempts"] == 0
                            final = {row["id"]: dict(row) for row in snapshot["enriched_items"]}
                            terminal = None
                            for chunk in iterator:
                                event = json.loads(chunk)
                                terminal = event["type"]
                                if terminal == "advice": final[event["item"]["id"]].update(event["item"])
                            assert terminal == "complete", terminal
                            rows = list(final.values())
                        else:
                            snapshot = response.get_json()
                            usable = time.perf_counter() - start
                            rows = snapshot["enriched_items"]
                        complete = time.perf_counter() - start
                        cpu = time.process_time() - cpu
                        response.close()
                    assert len(rows) == positions
                    records = [json.loads(line) for line in s.log_path.read_text().splitlines()]
                    assert all(record.get("forecast") is None for record in records)
                    assert all(record["decision_source"] == "rule_based" for record in records)
                    projections = [{**{key: row[key] for key in financial},
                                    "quality_flags": row["live_market"]["quality_flags"]} for row in rows]
                    financial_digest = hashlib.sha256(json.dumps(projections, sort_keys=True).encode()).hexdigest()
                    advice_digest = hashlib.sha256(json.dumps([{k: r[k] for k in ("id", "advice", "advice_reason", "base_advice", "suitability")} for r in rows], sort_keys=True).encode()).hexdigest()
                    observations.append(dict(usable_seconds=usable, complete_seconds=complete, cpu_seconds=cpu,
                        ai=dict(metrics), first_valid_percent=100*metrics["first_valid"]/max(1,len(attempts)),
                        invalid_percent=100*metrics["invalid"]/max(1,metrics["attempts"]), timeout_percent=100*metrics["timeouts"]/max(1,metrics["attempts"]),
                        providers={k: v["calls"] for k, v in s.m.stages.items() if k.startswith("provider.")},
                        peak_provider_concurrency=s.m.peak, decision_events=len(records),
                        completed_ai=sum(r.get("ai_portfolio", {}).get("mode") == "ai_enhanced" for r in rows),
                        financial_sha256=financial_digest, final_advice_sha256=advice_digest))
            summary = {}
            for key in ("usable_seconds", "complete_seconds", "cpu_seconds"):
                values = sorted(row[key] for row in observations)
                summary[key] = dict(median=statistics.median(values), p95=values[math.ceil(.95*len(values))-1] if len(values) >= 20 else None, maximum=max(values))
            cases.append(dict(positions=positions, unique_symbols=unique, failure=failure, summary=summary, observations=observations))
            print(f"{args.mode} {failure} {positions}/{unique}: initial {summary['usable_seconds']['median']:.3f}s, complete {summary['complete_seconds']['median']:.3f}s", flush=True)
    files = ["moneybot/api.py", "moneybot/app_factory.py", "moneybot/services/ai_advisor.py", "moneybot/services/request_timing.py", "moneybot/services/portfolio_enrichment.py"]
    result = dict(schema="portfolio-ai-benchmark.v1", mode=args.mode,
                  parent_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
                  source_sha256={name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in files if (root/name).exists()},
                  peak_process_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
                  fixture=dict(samples=args.samples, market_delay_seconds=market_delay, ai_delay_seconds=ai_delay,
                    network="blocked; synthetic sleeps only", p95="nearest rank, minimum 20 observations",
                    token_usage="Synthetic reported usage, NOT measured billable usage. Reasoning tokens are a subset of output tokens.",
                    profile_sensitive="Conditional reproduction: 220/low is incomplete, 420/minimal is valid. This does not prove why production failed."), cases=cases)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+"\n")


if __name__ == "__main__":
    main()
