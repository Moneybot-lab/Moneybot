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
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from uuid import uuid4
from pathlib import Path
from typing import Iterable

import requests

DEFAULT_ENDPOINTS = (
    "/api/model-health",
    "/api/quote?symbol=AAPL",
    "/api/signal?symbol=MSFT",
    "/api/quick-ask?symbol=NVDA",
)

DATABASE_STEADY_STATE_ENDPOINTS = (
    "/api/user-watchlist?skip_market_data=1",
    "/api/portfolio-summary?skip_market_data=1",
)

DATABASE_SETUP_MODES = {"inline", "setup-first"}


def _p95(latencies: list[float]) -> float | None:
    return round(statistics.quantiles(latencies, n=100)[94], 2) if len(latencies) >= 100 else None


@dataclass(frozen=True)
class RequestResult:
    endpoint: str
    status_code: int | None
    elapsed_ms: float
    ok: bool
    method: str = "GET"
    error: str | None = None
    request_id: str | None = None
    response_excerpt: str | None = None


def _request_once(
    base_url: str,
    endpoint: str,
    timeout: float,
    session: requests.Session,
    *,
    method: str = "GET",
    json_payload: dict | None = None,
    expected_statuses: set[int] | None = None,
    headers: dict[str, str] | None = None,
) -> RequestResult:
    url = f"{base_url.rstrip('/')}{endpoint}"
    started = time.perf_counter()
    method = method.upper()
    try:
        response = session.request(method, url, timeout=timeout, json=json_payload, headers=headers)
        elapsed_ms = (time.perf_counter() - started) * 1000
        ok = response.status_code in expected_statuses if expected_statuses is not None else response.status_code < 500
        response_excerpt = None
        if not ok:
            response_excerpt = response.text[:500] if response.text else None
        return RequestResult(
            endpoint=endpoint,
            status_code=response.status_code,
            elapsed_ms=elapsed_ms,
            ok=ok,
            method=method,
            error=None if ok else f"HTTP {response.status_code}",
            request_id=response.headers.get("X-Request-ID"),
            response_excerpt=response_excerpt,
        )
    except requests.RequestException as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        return RequestResult(endpoint=endpoint, status_code=None, elapsed_ms=elapsed_ms, ok=False, method=method, error=str(exc))


def _database_probe_requests(user_id: int, run_id: str) -> list[tuple[str, str, dict | None, set[int], bool]]:
    suffix = f"{run_id}-{user_id}".replace("_", "-").lower()
    email = f"loadtest-{suffix}@example.invalid"
    username = f"loadtest_{run_id}_{user_id}"[:80].lower().replace("-", "_")
    password = f"LoadTest-{run_id}-{user_id}!"
    return [
        (
            "POST",
            "/api/auth/signup",
            {
                "name": f"Load Test {user_id}",
                "username": username,
                "email": email,
                "password": password,
                "password_confirmation": password,
            },
            {201, 409},
            True,
        ),
        ("POST", "/api/auth/login", {"email": email, "password": password, "trusted_device": True}, {200}, True),
        (
            "POST",
            "/api/user-watchlist",
            {"symbol": "AAPL", "company": "Apple", "buy_price": "100", "shares": "1"},
            {201, 409},
            False,
        ),
        ("GET", "/api/user-watchlist?skip_market_data=1", None, {200}, False),
        ("GET", "/api/portfolio-summary?skip_market_data=1", None, {200}, False),
    ]


def _run_database_probe(
    user_id: int,
    *,
    base_url: str,
    database_timeout: float,
    run_id: str,
    headers: dict[str, str] | None,
    session: requests.Session,
) -> list[RequestResult]:
    results: list[RequestResult] = []
    for method, endpoint, payload, expected_statuses, stop_on_failure in _database_probe_requests(user_id, run_id):
        result = _request_once(
            base_url,
            endpoint,
            database_timeout,
            session,
            method=method,
            json_payload=payload,
            expected_statuses=expected_statuses,
            headers=headers,
        )
        results.append(result)
        if stop_on_failure and not result.ok:
            break
    return results


def _prepare_database_user(
    user_id: int,
    *,
    base_url: str,
    database_timeout: float,
    run_id: str,
    headers: dict[str, str] | None,
) -> tuple[int, requests.Session | None, list[RequestResult]]:
    session = requests.Session()
    results = _run_database_probe(
        user_id,
        base_url=base_url,
        database_timeout=database_timeout,
        run_id=run_id,
        headers=headers,
        session=session,
    )
    if any(not result.ok for result in results):
        close = getattr(session, "close", None)
        if callable(close):
            close()
        return user_id, None, results
    return user_id, session, results


def _virtual_user(
    user_id: int,
    *,
    base_url: str,
    endpoints: tuple[str, ...],
    duration_seconds: float,
    timeout: float,
    database_timeout: float,
    think_time_seconds: float,
    stop_at: float,
    include_database_flow: bool,
    run_id: str,
    ramp_up_seconds: float,
    headers: dict[str, str] | None,
    prepared_session: requests.Session | None = None,
) -> list[RequestResult]:
    rng = random.Random(user_id)
    results: list[RequestResult] = []
    if ramp_up_seconds > 0:
        time.sleep(rng.uniform(0, ramp_up_seconds))
    owns_session = prepared_session is None
    session = prepared_session or requests.Session()
    try:
        if include_database_flow:
            results.extend(
                _run_database_probe(
                    user_id,
                    base_url=base_url,
                    database_timeout=database_timeout,
                    run_id=run_id,
                    headers=headers,
                    session=session,
                ),
            )

        while time.monotonic() < stop_at:
            endpoint = rng.choice(endpoints)
            results.append(_request_once(base_url, endpoint, timeout, session, headers=headers))
            if think_time_seconds > 0:
                time.sleep(rng.uniform(0, think_time_seconds))
    finally:
        if owns_session:
            close = getattr(session, "close", None)
            if callable(close):
                close()
    return results


def summarize(
    results: Iterable[RequestResult],
    *,
    users: int,
    duration_seconds: float,
    base_url: str,
    started_at_utc: str | None = None,
    ended_at_utc: str | None = None,
    include_database_flow: bool = False,
) -> dict:
    rows = list(results)
    latencies = [row.elapsed_ms for row in rows]
    failures = [row for row in rows if not row.ok]
    throttled = [row for row in rows if row.status_code == 429]
    latencies_by_endpoint: dict[str, list[float]] = {}
    by_endpoint: dict[str, dict[str, float | int | dict[str, int] | None]] = {}
    for row in rows:
        stats = by_endpoint.setdefault(
            row.endpoint,
            {"requests": 0, "failures": 0, "avg_ms": 0.0, "status_counts": {}},
        )
        stats["requests"] += 1
        stats["failures"] += 0 if row.ok else 1
        stats["avg_ms"] += row.elapsed_ms
        latencies_by_endpoint.setdefault(row.endpoint, []).append(row.elapsed_ms)
        status_key = str(row.status_code) if row.status_code is not None else "timeout_or_connection_error"
        status_counts = stats["status_counts"]
        if isinstance(status_counts, dict):
            status_counts[status_key] = int(status_counts.get(status_key, 0)) + 1
    for endpoint, stats in by_endpoint.items():
        count = int(stats["requests"])
        stats["avg_ms"] = round(float(stats["avg_ms"]) / count, 2) if count else 0.0
        stats["p95_ms"] = _p95(latencies_by_endpoint.get(endpoint, []))

    return {
        "schema_version": "moneybot.load_test.v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "test_window_utc": {"started_at": started_at_utc, "ended_at": ended_at_utc},
        "base_url": base_url,
        "virtual_users": users,
        "duration_seconds": duration_seconds,
        "database_flow_enabled": include_database_flow,
        "requests": len(rows),
        "requests_per_second": round(len(rows) / duration_seconds, 2) if duration_seconds else 0.0,
        "failures": len(failures),
        "failure_rate": round(len(failures) / len(rows), 4) if rows else 1.0,
        "throttled": len(throttled),
        "throttle_rate": round(len(throttled) / len(rows), 4) if rows else 0.0,
        "latency_ms": {
            "min": round(min(latencies), 2) if latencies else None,
            "avg": round(statistics.fmean(latencies), 2) if latencies else None,
            "p95": _p95(latencies),
            "max": round(max(latencies), 2) if latencies else None,
        },
        "by_endpoint": by_endpoint,
        "sample_failures": [asdict(row) for row in failures[:10]],
        "render_metrics_note": (
            "Use test_window_utc plus the test duration to inspect Render Metrics for CPU, memory, "
            "HTTP response times, HTTP status codes, and database activity for the same window."
        ),
    }


def run_load_test(
    *,
    base_url: str,
    users: int,
    duration_seconds: float,
    endpoints: tuple[str, ...] = DEFAULT_ENDPOINTS,
    timeout: float = 10.0,
    database_timeout: float = 30.0,
    think_time_seconds: float = 0.25,
    include_database_flow: bool = False,
    run_id: str | None = None,
    ramp_up_seconds: float = 0.0,
    rate_limit_token: str = "",
    database_setup_mode: str = "inline",
    database_setup_concurrency: int = 20,
) -> dict:
    if users < 1:
        raise ValueError("users must be at least 1")
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be greater than 0")
    if not endpoints:
        raise ValueError("at least one endpoint is required")
    if timeout <= 0:
        raise ValueError("timeout must be greater than 0")
    if database_timeout <= 0:
        raise ValueError("database_timeout must be greater than 0")
    if ramp_up_seconds < 0:
        raise ValueError("ramp_up_seconds must be greater than or equal to 0")
    if database_setup_mode not in DATABASE_SETUP_MODES:
        raise ValueError(f"database_setup_mode must be one of: {sorted(DATABASE_SETUP_MODES)}")
    if database_setup_concurrency < 1:
        raise ValueError("database_setup_concurrency must be at least 1")

    run_id = (run_id or uuid4().hex[:10]).lower()
    all_results: list[RequestResult] = []
    setup_results: list[RequestResult] = []
    prepared_sessions: dict[int, requests.Session] = {}
    headers = {"X-Load-Test-Token": rate_limit_token} if rate_limit_token else None
    measured_endpoints = endpoints
    measured_include_database_flow = include_database_flow
    if include_database_flow and database_setup_mode == "setup-first":
        measured_include_database_flow = False
        measured_endpoints = tuple(dict.fromkeys((*endpoints, *DATABASE_STEADY_STATE_ENDPOINTS)))
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(users, database_setup_concurrency),
            thread_name_prefix="moneybot-db-setup",
        ) as setup_executor:
            setup_futures = [
                setup_executor.submit(
                    _prepare_database_user,
                    user_id,
                    base_url=base_url,
                    database_timeout=database_timeout,
                    run_id=run_id,
                    headers=headers,
                )
                for user_id in range(users)
            ]
            for future in concurrent.futures.as_completed(setup_futures):
                user_id, session, user_setup_results = future.result()
                setup_results.extend(user_setup_results)
                if session is not None:
                    prepared_sessions[user_id] = session

    started_at_utc = datetime.now(timezone.utc).isoformat()
    stop_at = time.monotonic() + duration_seconds
    with concurrent.futures.ThreadPoolExecutor(max_workers=users, thread_name_prefix="moneybot-vu") as executor:
        futures = [
            executor.submit(
                _virtual_user,
                user_id,
                base_url=base_url,
                endpoints=measured_endpoints,
                duration_seconds=duration_seconds,
                timeout=timeout,
                database_timeout=database_timeout,
                think_time_seconds=think_time_seconds,
                stop_at=stop_at,
                include_database_flow=measured_include_database_flow,
                run_id=run_id,
                ramp_up_seconds=ramp_up_seconds,
                headers=headers,
                prepared_session=prepared_sessions.get(user_id),
            )
            for user_id in range(users)
        ]
        for future in concurrent.futures.as_completed(futures):
            all_results.extend(future.result())
    ended_at_utc = datetime.now(timezone.utc).isoformat()
    for session in prepared_sessions.values():
        close = getattr(session, "close", None)
        if callable(close):
            close()

    report = summarize(
        all_results,
        users=users,
        duration_seconds=duration_seconds,
        base_url=base_url,
        started_at_utc=started_at_utc,
        ended_at_utc=ended_at_utc,
        include_database_flow=include_database_flow,
    )
    setup_failures = [row for row in setup_results if not row.ok]
    report["database_setup"] = {
        "mode": database_setup_mode if include_database_flow else "disabled",
        "requests": len(setup_results),
        "failures": len(setup_failures),
        "failure_rate": round(len(setup_failures) / len(setup_results), 4) if setup_results else 0.0,
        "avg_ms": round(statistics.fmean([row.elapsed_ms for row in setup_results]), 2) if setup_results else None,
    }
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Moneybot's 200-virtual-user simulated API load test.")
    parser.add_argument("--base-url", default=os.environ.get("MONEYBOT_LOAD_TEST_BASE_URL", "http://127.0.0.1:5000"))
    parser.add_argument("--users", type=int, default=int(os.environ.get("MONEYBOT_LOAD_TEST_USERS", "200")))
    parser.add_argument("--duration-seconds", type=float, default=float(os.environ.get("MONEYBOT_LOAD_TEST_DURATION_SECONDS", "60")))
    parser.add_argument("--timeout-seconds", type=float, default=float(os.environ.get("MONEYBOT_LOAD_TEST_TIMEOUT_SECONDS", "10")))
    parser.add_argument("--database-timeout-seconds", type=float, default=float(os.environ.get("MONEYBOT_LOAD_TEST_DATABASE_TIMEOUT_SECONDS", "30")), help="Timeout for signup/login/watchlist database-flow requests.")
    parser.add_argument("--think-time-seconds", type=float, default=float(os.environ.get("MONEYBOT_LOAD_TEST_THINK_TIME_SECONDS", "0.25")))
    parser.add_argument("--ramp-up-seconds", type=float, default=float(os.environ.get("MONEYBOT_LOAD_TEST_RAMP_UP_SECONDS", "0")), help="Randomly stagger virtual-user startup over this many seconds.")
    parser.add_argument("--endpoint", action="append", dest="endpoints", help="Endpoint path to include; repeat to override defaults.")
    parser.add_argument("--output", default=os.environ.get("MONEYBOT_LOAD_TEST_OUTPUT", "data/load_test_200_vu_report.json"))
    parser.add_argument("--max-failure-rate", type=float, default=float(os.environ.get("MONEYBOT_LOAD_TEST_MAX_FAILURE_RATE", "0.05")))
    parser.add_argument("--max-throttle-rate", type=float, default=float(os.environ.get("MONEYBOT_LOAD_TEST_MAX_THROTTLE_RATE", "0.05")), help="Exit non-zero when HTTP 429 responses exceed this rate.")
    parser.add_argument("--max-p95-ms", type=float, default=float(os.environ.get("MONEYBOT_LOAD_TEST_MAX_P95_MS", "0")), help="Optional global p95 latency threshold in milliseconds; 0 disables this gate.")
    parser.add_argument("--rate-limit-token", default=os.environ.get("MONEYBOT_LOAD_TEST_RATE_LIMIT_TOKEN", ""), help="Optional token sent as X-Load-Test-Token to bypass rate limiting when the server is configured with LOAD_TEST_RATE_LIMIT_TOKEN.")
    parser.add_argument("--include-database-flow", action="store_true", default=os.environ.get("MONEYBOT_LOAD_TEST_INCLUDE_DATABASE_FLOW", "false").lower() == "true", help="Have each virtual user create/login/read/write portfolio data to exercise the database.")
    parser.add_argument("--database-setup-mode", choices=sorted(DATABASE_SETUP_MODES), default=os.environ.get("MONEYBOT_LOAD_TEST_DATABASE_SETUP_MODE", "inline"), help="Use inline to include signup/login setup in the measured window, or setup-first to authenticate users before measuring steady-state traffic.")
    parser.add_argument("--database-setup-concurrency", type=int, default=int(os.environ.get("MONEYBOT_LOAD_TEST_DATABASE_SETUP_CONCURRENCY", "20")), help="Maximum concurrent users during setup-first database preparation.")
    parser.add_argument("--run-id", default=os.environ.get("MONEYBOT_LOAD_TEST_RUN_ID", ""), help="Unique suffix for database test users; defaults to a random value.")
    args = parser.parse_args()
    if "--rate-limit-token" in sys.argv and not str(args.rate_limit_token or "").strip():
        parser.error("--rate-limit-token was provided but empty; export MONEYBOT_LOAD_TEST_RATE_LIMIT_TOKEN or omit the flag")
    return args


def main() -> int:
    args = parse_args()
    report = run_load_test(
        base_url=args.base_url,
        users=args.users,
        duration_seconds=args.duration_seconds,
        endpoints=tuple(args.endpoints or DEFAULT_ENDPOINTS),
        timeout=args.timeout_seconds,
        database_timeout=args.database_timeout_seconds,
        think_time_seconds=args.think_time_seconds,
        include_database_flow=args.include_database_flow,
        run_id=args.run_id or None,
        ramp_up_seconds=args.ramp_up_seconds,
        rate_limit_token=args.rate_limit_token,
        database_setup_mode=args.database_setup_mode,
        database_setup_concurrency=args.database_setup_concurrency,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    if report.get("database_setup", {}).get("failures", 0) > 0:
        return 1
    if report["failure_rate"] > args.max_failure_rate:
        return 1
    if report["throttle_rate"] > args.max_throttle_rate:
        return 1
    p95_ms = report.get("latency_ms", {}).get("p95")
    if args.max_p95_ms > 0 and isinstance(p95_ms, (int, float)) and p95_ms > args.max_p95_ms:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
