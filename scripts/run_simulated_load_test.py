#!/usr/bin/env python3
"""Run a simulated API load test against Moneybot.

The default scenario is the first launch-readiness load test: 200 concurrent
virtual users making API requests. It intentionally uses the standard library
plus the project's existing requests dependency so it can run anywhere the app
runs without installing a separate load-testing binary.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import random
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import requests

DEFAULT_ENDPOINTS = (
    "/api/model-health",
    "/api/quote?symbol=AAPL",
    "/api/signal?symbol=MSFT",
    "/api/quick-ask?symbol=NVDA",
)


@dataclass(frozen=True)
class RequestResult:
    endpoint: str
    status_code: int | None
    elapsed_ms: float
    ok: bool
    error: str | None = None


def _request_once(base_url: str, endpoint: str, timeout: float, session: requests.Session) -> RequestResult:
    url = f"{base_url.rstrip('/')}{endpoint}"
    started = time.perf_counter()
    try:
        response = session.get(url, timeout=timeout)
        elapsed_ms = (time.perf_counter() - started) * 1000
        return RequestResult(
            endpoint=endpoint,
            status_code=response.status_code,
            elapsed_ms=elapsed_ms,
            ok=response.status_code < 500,
            error=None if response.status_code < 500 else f"HTTP {response.status_code}",
        )
    except requests.RequestException as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        return RequestResult(endpoint=endpoint, status_code=None, elapsed_ms=elapsed_ms, ok=False, error=str(exc))


def _virtual_user(
    user_id: int,
    *,
    base_url: str,
    endpoints: tuple[str, ...],
    duration_seconds: float,
    timeout: float,
    think_time_seconds: float,
    stop_at: float,
) -> list[RequestResult]:
    rng = random.Random(user_id)
    results: list[RequestResult] = []
    with requests.Session() as session:
        while time.monotonic() < stop_at:
            endpoint = rng.choice(endpoints)
            results.append(_request_once(base_url, endpoint, timeout, session))
            if think_time_seconds > 0:
                time.sleep(rng.uniform(0, think_time_seconds))
    return results


def summarize(results: Iterable[RequestResult], *, users: int, duration_seconds: float, base_url: str) -> dict:
    rows = list(results)
    latencies = [row.elapsed_ms for row in rows]
    failures = [row for row in rows if not row.ok]
    by_endpoint: dict[str, dict[str, float | int]] = {}
    for row in rows:
        stats = by_endpoint.setdefault(row.endpoint, {"requests": 0, "failures": 0, "avg_ms": 0.0})
        stats["requests"] += 1
        stats["failures"] += 0 if row.ok else 1
        stats["avg_ms"] += row.elapsed_ms
    for stats in by_endpoint.values():
        count = int(stats["requests"])
        stats["avg_ms"] = round(float(stats["avg_ms"]) / count, 2) if count else 0.0

    return {
        "schema_version": "moneybot.load_test.v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "virtual_users": users,
        "duration_seconds": duration_seconds,
        "requests": len(rows),
        "requests_per_second": round(len(rows) / duration_seconds, 2) if duration_seconds else 0.0,
        "failures": len(failures),
        "failure_rate": round(len(failures) / len(rows), 4) if rows else 1.0,
        "latency_ms": {
            "min": round(min(latencies), 2) if latencies else None,
            "avg": round(statistics.fmean(latencies), 2) if latencies else None,
            "p95": round(statistics.quantiles(latencies, n=100)[94], 2) if len(latencies) >= 100 else None,
            "max": round(max(latencies), 2) if latencies else None,
        },
        "by_endpoint": by_endpoint,
        "sample_failures": [asdict(row) for row in failures[:10]],
    }


def run_load_test(
    *,
    base_url: str,
    users: int,
    duration_seconds: float,
    endpoints: tuple[str, ...] = DEFAULT_ENDPOINTS,
    timeout: float = 10.0,
    think_time_seconds: float = 0.25,
) -> dict:
    if users < 1:
        raise ValueError("users must be at least 1")
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be greater than 0")
    if not endpoints:
        raise ValueError("at least one endpoint is required")

    stop_at = time.monotonic() + duration_seconds
    all_results: list[RequestResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=users, thread_name_prefix="moneybot-vu") as executor:
        futures = [
            executor.submit(
                _virtual_user,
                user_id,
                base_url=base_url,
                endpoints=endpoints,
                duration_seconds=duration_seconds,
                timeout=timeout,
                think_time_seconds=think_time_seconds,
                stop_at=stop_at,
            )
            for user_id in range(users)
        ]
        for future in concurrent.futures.as_completed(futures):
            all_results.extend(future.result())
    return summarize(all_results, users=users, duration_seconds=duration_seconds, base_url=base_url)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Moneybot's 200-virtual-user simulated API load test.")
    parser.add_argument("--base-url", default=os.environ.get("MONEYBOT_LOAD_TEST_BASE_URL", "http://127.0.0.1:5000"))
    parser.add_argument("--users", type=int, default=int(os.environ.get("MONEYBOT_LOAD_TEST_USERS", "200")))
    parser.add_argument("--duration-seconds", type=float, default=float(os.environ.get("MONEYBOT_LOAD_TEST_DURATION_SECONDS", "60")))
    parser.add_argument("--timeout-seconds", type=float, default=float(os.environ.get("MONEYBOT_LOAD_TEST_TIMEOUT_SECONDS", "10")))
    parser.add_argument("--think-time-seconds", type=float, default=float(os.environ.get("MONEYBOT_LOAD_TEST_THINK_TIME_SECONDS", "0.25")))
    parser.add_argument("--endpoint", action="append", dest="endpoints", help="Endpoint path to include; repeat to override defaults.")
    parser.add_argument("--output", default=os.environ.get("MONEYBOT_LOAD_TEST_OUTPUT", "data/load_test_200_vu_report.json"))
    parser.add_argument("--max-failure-rate", type=float, default=float(os.environ.get("MONEYBOT_LOAD_TEST_MAX_FAILURE_RATE", "0.05")))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_load_test(
        base_url=args.base_url,
        users=args.users,
        duration_seconds=args.duration_seconds,
        endpoints=tuple(args.endpoints or DEFAULT_ENDPOINTS),
        timeout=args.timeout_seconds,
        think_time_seconds=args.think_time_seconds,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["failure_rate"] <= args.max_failure_rate else 1


if __name__ == "__main__":
    raise SystemExit(main())
