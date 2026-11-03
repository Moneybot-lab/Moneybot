"""Opt-in, bounded diagnostics. Never retain arguments, results or exception text."""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import json
import logging
import math
import random
from threading import Lock
import time
import uuid

import requests

STAGES = frozenset({
    "authentication", "profile", "stream_demand", "database", "serialization",
    "view.watchlist", "view.summary", "view.stable", "market.quote", "market.history",
    "market.sector", "market.signal", "market.company", "market.analysis",
    "advice.deterministic", "advice.ai", "advice.alignment", "suitability", "decision.logging",
    "provider.openai", "provider.massive", "provider.finnhub", "provider.twelve_data",
    "provider.yahoo.info", "provider.yahoo.history", "provider.yahoo.news", "provider.yahoo.financials",
    "provider.yahoo.http", "provider.google_news",
})
COUNTERS = frozenset({
    "cache.hit", "cache.miss", "ai.disabled", "ai.unsupported", "ai.cooldown_skip",
    "ai.malformed", "ai.missing_fields", "ai.parse_or_other_error", "ai.enhanced", "ai.cooldown_timeout", "ai.cooldown_5xx",
    "decision.write", "decision.write_error",
})
MILESTONES = frozenset({"market_inputs_complete", "advice_complete"})
OUTCOMES = ("ok", "connect_timeout", "read_timeout", "timeout", "http_4xx", "http_5xx", "error")
ENDPOINTS = {"api.user_watchlist", "api.portfolio_summary", "api.stable_watchlist"}
_active: ContextVar[Trace | None] = ContextVar("portfolio_timing", default=None)
_logger = logging.getLogger("moneybot.portfolio_timing")


def _number(value, default, maximum):
    try:
        number = float(value)
        return min(maximum, max(0.0, number)) if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _setting_number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


class Trace:
    def __init__(self, endpoint):
        self.endpoint = endpoint
        self.trace_id = uuid.uuid4().hex
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.start = time.perf_counter()
        self.cpu_start = time.thread_time()
        self.stages = {}
        self.counters = {}
        self.milestones = {}
        self.emitted = False

    def observe(self, name, started, outcome="ok"):
        if name not in STAGES or outcome not in OUTCOMES:
            return
        elapsed = max(0.0, (time.perf_counter() - started) * 1000)
        stats = self.stages.setdefault(name, {"calls": 0, "total_ms": 0.0, "max_ms": 0.0, "outcomes": {}})
        stats["calls"] += 1
        stats["total_ms"] += elapsed
        stats["max_ms"] = max(stats["max_ms"], elapsed)
        stats["outcomes"][outcome] = stats["outcomes"].get(outcome, 0) + 1


def count(name):
    trace = _active.get()
    if trace is not None and name in COUNTERS:
        trace.counters[name] = trace.counters.get(name, 0) + 1


def mark(name):
    trace = _active.get()
    if trace is not None and name in MILESTONES:
        trace.milestones[name] = round((time.perf_counter() - trace.start) * 1000, 3)


def _exception_outcome(exc):
    # yfinance uses curl_cffi, whose timeout classes do not inherit Requests'
    # exceptions. Inspect only fixed class identifiers; never exception text.
    if type(exc).__module__ == "curl_cffi.requests.exceptions":
        category = {"ConnectTimeout": "connect_timeout", "ReadTimeout": "read_timeout", "Timeout": "timeout"}.get(type(exc).__name__)
        if category is not None:
            return category
    if isinstance(exc, requests.ConnectTimeout):
        return "connect_timeout"
    if isinstance(exc, requests.ReadTimeout):
        return "read_timeout"
    if isinstance(exc, (requests.Timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, requests.HTTPError):
        outcome = _status_outcome(getattr(exc.response, "status_code", None))
        return outcome if outcome != "ok" else "error"
    return "error"


def _status_outcome(status):
    if isinstance(status, int):
        if status >= 500:
            return "http_5xx"
        if status >= 400:
            return "http_4xx"
    return "ok"


def call(name, fn, *args, **kwargs):
    trace = _active.get()
    if trace is None or name not in STAGES:
        return fn(*args, **kwargs)
    started = time.perf_counter()
    outcome = "ok"
    try:
        result = fn(*args, **kwargs)
        if name.startswith("provider."):
            outcome = _status_outcome(getattr(result, "status_code", None))
        return result
    except Exception as exc:
        outcome = _exception_outcome(exc)
        raise
    finally:
        trace.observe(name, started, outcome)


def timed(name):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            return call(name, fn, *args, **kwargs)
        return wrapped
    return decorate


class Sampler:
    """At most five starts/minute/worker, automatically expires within an hour."""
    def __init__(self, rate=0, per_minute=2, duration=900):
        self.rate = _number(rate, 0, 1)
        self.limit = int(_number(per_minute, 2, 5))
        self.expires = time.monotonic() + _number(duration, 900, 3600)
        self.window = None
        self.used = 0
        self.lock = Lock()

    def select(self):
        if self.rate <= 0 or self.limit <= 0:
            return False
        now = time.monotonic()
        if now >= self.expires or random.random() >= self.rate:
            return False
        with self.lock:
            window = int(now // 60)
            if self.window != window:
                self.window, self.used = window, 0
            if self.used >= self.limit:
                return False
            self.used += 1
            return True


def install_request_timing(app, db):
    """Install before the API blueprint so auth and all DB calls are included."""
    from flask import g, request
    from sqlalchemy import event

    sampler = Sampler(app.config.get("PORTFOLIO_TIMING_SAMPLE_RATE", 0),
                      app.config.get("PORTFOLIO_TIMING_MAX_PER_MINUTE", 2),
                      app.config.get("PORTFOLIO_TIMING_DURATION_SECONDS", 900))
    app.extensions["portfolio_timing_sampler"] = sampler

    @app.before_request
    def start_timing():
        if request.endpoint in ENDPOINTS and sampler.select():
            trace = Trace(request.endpoint)
            g.portfolio_timing_trace = trace
            g.portfolio_timing_token = _active.set(trace)

    def emit(trace, status, size):
        if trace.emitted:
            return
        trace.emitted = True
        ai = app.extensions.get("ai_advisor_service")
        # Only allowlisted booleans/numbers from the actual service, never its
        # API key/model/prompt, Flask session, headers, URL, symbols or payload.
        settings = {
            "ai_enabled": bool(getattr(ai, "enabled", False)),
            "ai_timeout_s": _setting_number(getattr(ai, "timeout_s", None)),
            "ai_cooldown_s": _setting_number(getattr(ai, "failure_cooldown_s", None)),
            "decision_logging_enabled": bool(getattr(app.extensions.get("decision_logger"), "enabled", False)),
        }
        record = dict(schema="portfolio_timing.v1", trace_id=trace.trace_id, endpoint=trace.endpoint,
                      started_at=trace.started_at, status=status, response_bytes=size,
                      wall_ms=round((time.perf_counter()-trace.start)*1000, 3),
                      request_thread_cpu_ms=round((time.thread_time()-trace.cpu_start)*1000, 3),
                      settings=settings, stages=trace.stages, counters=trace.counters,
                      milestones_ms=trace.milestones)
        for stats in record["stages"].values():
            for field in ("total_ms", "max_ms"):
                stats[field] = round(stats[field], 3)
        try:
            _logger.info("PORTFOLIO_TIMING %s", json.dumps(record, separators=(",", ":")))
        except Exception:
            # A diagnostic logging failure must not change portfolio behavior.
            pass

    @app.after_request
    def finish_timing(response):
        trace = getattr(g, "portfolio_timing_trace", None)
        if trace is not None:
            # Do not consume a response iterator or perform a second serialization.
            emit(trace, response.status_code, response.content_length)
            response.headers["X-Portfolio-Timing-ID"] = trace.trace_id
        return response

    @app.teardown_request
    def clear_timing(error):
        trace = getattr(g, "portfolio_timing_trace", None)
        token = g.pop("portfolio_timing_token", None)
        try:
            if trace is not None and not trace.emitted:
                emit(trace, 500 if error is not None else None, None)
        finally:
            if token is not None:
                _active.reset(token)

    # Cursor hooks keep only a clock value. Never retain SQL/parameters or values.
    def before_sql(conn, cursor, statement, parameters, context, executemany):
        if _active.get() is not None:
            context._portfolio_timing_started = time.perf_counter()

    def finish_sql(context, outcome):
        started = getattr(context, "_portfolio_timing_started", None)
        trace = _active.get()
        if started is not None and trace is not None:
            trace.observe("database", started, outcome)
            del context._portfolio_timing_started

    def after_sql(conn, cursor, statement, parameters, context, executemany):
        finish_sql(context, "ok")

    def error_sql(exception_context):
        finish_sql(exception_context.execution_context, "error")

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", before_sql)
        event.listen(db.engine, "after_cursor_execute", after_sql)
        event.listen(db.engine, "handle_error", error_sql)
