"""Diagnostics must observe, never change, portfolio behavior or leak inputs."""
from concurrent.futures import ThreadPoolExecutor
import json
import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests
from sqlalchemy import text

from moneybot.extensions import db
from moneybot.services import request_timing as timing
from scripts.benchmark_portfolio_watchlist import scenario, stable_payload


def records(caplog):
    return [json.loads(record.message.split("PORTFOLIO_TIMING ", 1)[1])
            for record in caplog.records if record.name == "moneybot.portfolio_timing"]


def enable(app, **settings):
    app.config.update(PORTFOLIO_TIMING_SAMPLE_RATE=1, PORTFOLIO_TIMING_MAX_PER_MINUTE=5,
                      PORTFOLIO_TIMING_DURATION_SECONDS=900, **settings)
    timing.install_request_timing(app, db)


@pytest.mark.parametrize("failure", ["success", "timeout", "server_error"])
def test_sampled_full_route_is_equivalent_and_reports_exact_stages(tmp_path, caplog, failure):
    caplog.set_level(logging.INFO, logger="moneybot.portfolio_timing")
    with scenario(tmp_path, 5, 5, failure, market_delay=0, ai_delay=0) as s:
        enable(s.app)
        response = s.client.get("/api/user-watchlist", headers={"X-Request-ID": "private-account-id"})
        assert response.status_code == 200
        sample, = records(caplog)
        assert response.headers["X-Portfolio-Timing-ID"] == sample["trace_id"]
        assert sample["settings"] == dict(ai_enabled=True, ai_timeout_s=6, ai_cooldown_s=120,
                                         decision_logging_enabled=True)
        assert sample["stages"]["database"]["calls"] == 3
        assert sample["stages"]["advice.ai"]["calls"] == 5
        assert sample["stages"]["provider.openai"]["calls"] == (5 if failure == "success" else 2)
        assert sample["counters"]["decision.write"] == 5
        assert sample["stages"]["market.sector"]["calls"] == 5
        assert sample["milestones_ms"]["market_inputs_complete"] <= sample["milestones_ms"]["advice_complete"] <= sample["wall_ms"]
        assert sample["stages"]["serialization"]["calls"] == 1
        assert sample["response_bytes"] == len(response.data)
        assert timing._active.get() is None
        if failure != "success":
            assert sample["counters"]["ai.cooldown_skip"] == 4
            assert sample["stages"]["provider.openai"]["outcomes"] == ({"timeout": 2} if failure == "timeout" else {"http_5xx": 2})
        encoded = json.dumps(sample)
        for forbidden in ("private-account-id", "S00", "S01", "entry_price", "shares", "synthetic-only", "one@example.invalid", "prompt", "Authorization"):
            assert forbidden not in encoded
        s.app.extensions["portfolio_timing_sampler"].rate = 0
        s.ai._disabled_until = 0
        control = s.client.get("/api/user-watchlist")
        assert stable_payload(response.get_json()) == stable_payload(control.get_json())
        assert "X-Portfolio-Timing-ID" not in control.headers
        assert len(records(caplog)) == 1


@pytest.mark.parametrize("mode,expected", [("malformed", "ai.malformed"), ("missing", "ai.missing_fields"), ("bad_json", "ai.parse_or_other_error")])
def test_malformed_and_incomplete_responses_do_not_change_cooldown(tmp_path, caplog, mode, expected):
    caplog.set_level(logging.INFO, logger="moneybot.portfolio_timing")
    with scenario(tmp_path, 5, 5, "success", market_delay=0, ai_delay=0) as s:
        enable(s.app)

        def response_json():
            if mode == "bad_json":
                raise ValueError("sensitive provider payload")
            return {"output_text": "not JSON" if mode == "malformed" else '{"advice":"HOLD"}'}

        with patch("requests.post", lambda *args, **kwargs: SimpleNamespace(
                status_code=200, raise_for_status=lambda: None, json=response_json)):
            assert s.client.get("/api/user-watchlist").status_code == 200
        sample, = records(caplog)
        assert sample["counters"][expected] == (10 if mode == "malformed" else 5)
        assert s.ai._disabled_until == 0  # PR #667 intentionally does not cool these paths
        assert "sensitive provider payload" not in json.dumps(sample)


def test_budget_expiry_and_disabled_fast_path(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(timing.time, "monotonic", lambda: now[0])
    sampler = timing.Sampler(rate=1, per_minute=1000000, duration=999999)
    with ThreadPoolExecutor(max_workers=10) as pool:
        assert sum(pool.map(lambda _: sampler.select(), range(40))) == 5
    now[0] = 60
    assert sampler.select()
    now[0] = 3600
    assert not sampler.select()
    disabled = timing.Sampler(rate="invalid")
    monkeypatch.setattr(timing.random, "random", lambda: pytest.fail("disabled sampling must not draw"))
    assert not disabled.select()


def test_allowlists_and_thread_local_collectors():
    def collect(value):
        trace = timing.Trace("api.user_watchlist")
        token = timing._active.set(trace)
        try:
            timing.count("private holding")
            timing.mark("private account")
            timing.call("unknown symbol", lambda: None)
            for _ in range(value):
                timing.call("market.quote", lambda: None)
            return trace
        finally:
            timing._active.reset(token)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.map(collect, [2, 7])
    assert a.stages["market.quote"]["calls"] == 2
    assert b.stages["market.quote"]["calls"] == 7
    assert not a.counters and not a.milestones
    assert set(a.stages) == {"market.quote"}
    assert timing._active.get() is None


@pytest.mark.parametrize("exception,category", [(requests.ConnectTimeout, "connect_timeout"),
                                                (requests.ReadTimeout, "read_timeout"),
                                                (requests.Timeout, "timeout"), (ValueError, "error")])
def test_provider_exception_identity_and_categories(exception, category):
    trace = timing.Trace("api.user_watchlist")
    token = timing._active.set(trace)
    error = exception("private response")
    try:
        def fail():
            raise error
        with pytest.raises(exception) as raised:
            timing.call("provider.massive", fail)
        assert raised.value is error
        assert trace.stages["provider.massive"]["outcomes"] == {category: 1}
        assert "private response" not in json.dumps(trace.stages)
    finally:
        timing._active.reset(token)


def test_errors_and_logging_failures_do_not_leak_or_change_responses(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="moneybot.portfolio_timing")
    with scenario(tmp_path, 1, 1, "success", market_delay=0, ai_delay=0) as s:
        enable(s.app)
        denied = s.app.test_client().get("/api/user-watchlist")
        assert denied.status_code == 401
        assert len(records(caplog)) == 1 and records(caplog)[0]["status"] == 401
        with patch.object(timing._logger, "info", side_effect=OSError("synthetic log failure")):
            assert s.client.get("/api/user-watchlist").status_code == 200
        assert timing._active.get() is None


def test_sql_errors_are_counted_without_statement_or_parameters(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="moneybot.portfolio_timing")
    with scenario(tmp_path, 1, 1, "success", market_delay=0, ai_delay=0) as s:
        enable(s.app)
        trace = timing.Trace("api.user_watchlist")
        token = timing._active.set(trace)
        try:
            with s.app.app_context():
                with pytest.raises(Exception):
                    db.session.execute(text("select * from private_nonexistent_table"))
                db.session.rollback()
            assert trace.stages["database"]["outcomes"] == {"error": 1}
            assert "private_nonexistent_table" not in json.dumps(trace.stages)
        finally:
            timing._active.reset(token)


def test_unhandled_request_error_emits_once_and_clears_context(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="moneybot.portfolio_timing")
    with scenario(tmp_path, 1, 1, "success", market_delay=0, ai_delay=0) as s:
        enable(s.app)
        with patch("moneybot.api._decision_context_for_user", side_effect=RuntimeError("private details")):
            with pytest.raises(RuntimeError):
                s.client.get("/api/user-watchlist")
        sample, = records(caplog)
        assert sample["status"] == 500
        assert "private details" not in json.dumps(sample)
        assert timing._active.get() is None
        assert s.client.get("/api/user-watchlist").status_code == 200
        assert len(records(caplog)) == 2
        assert records(caplog)[1]["trace_id"] != sample["trace_id"]


def test_durations_include_nested_provider_wait_without_double_counting_wall(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(timing.time, "perf_counter", lambda: now[0])
    trace = timing.Trace("api.user_watchlist")
    token = timing._active.set(trace)
    try:
        def provider():
            now[0] += 3
            return SimpleNamespace(status_code=200)
        timing.call("advice.ai", lambda: timing.call("provider.openai", provider))
        assert trace.stages["advice.ai"]["total_ms"] == 3000
        assert trace.stages["provider.openai"]["total_ms"] == 3000
        assert (now[0]-trace.start)*1000 == 3000  # inclusive stages overlap
    finally:
        timing._active.reset(token)


@pytest.mark.parametrize("name,category", [("ConnectTimeout", "connect_timeout"),
                                         ("ReadTimeout", "read_timeout"), ("Timeout", "timeout")])
def test_yahoo_curl_timeout_categories(name, category):
    from curl_cffi.requests import exceptions
    error = getattr(exceptions, name)("private provider URL")
    assert timing._exception_outcome(error) == category
