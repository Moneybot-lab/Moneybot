"""Offline streaming lifecycle, financial parity and revision fences."""
import json
import logging
import subprocess
import sys
from unittest.mock import patch

import pytest

from moneybot.extensions import db
from moneybot.models import InvestorProfile, WatchlistItem
from moneybot.services import request_timing as timing
from moneybot.services.portfolio_enrichment import admit
from scripts.benchmark_portfolio_watchlist import scenario


FINANCIAL = ("id", "symbol", "shares", "entry_price", "current_price", "history30", "history30_bars",
             "history30_source", "quote_source", "quote_diagnostics", "performance_percent",
             "performance_amount", "today_change_percent", "today_change_amount", "position_weight_percent",
             "sector_weight_percent", "weight_basis")


def events(response):
    return [json.loads(chunk) for chunk in response.response]


@pytest.mark.parametrize("failure", ["success", "timeout", "server_error", "disabled", "missing_market"])
def test_initial_snapshot_precedes_ai_and_final_financial_advice_parity(tmp_path, failure):
    with scenario(tmp_path, 5, 2, failure, 0, 0) as s:
        if failure == "disabled":
            s.ai.enabled = False
        if failure == "missing_market":
            s.market.get_quote = lambda *_: {}
            s.market.get_price_history_data = lambda *_, **__: {}
        response = s.client.get("/api/user-watchlist/stream", buffered=False)
        try:
            iterator = iter(response.response)
            snapshot = json.loads(next(iterator))
            assert snapshot["type"] == "snapshot"
            assert s.m.stages["provider.ai.synthetic_openai"]["calls"] == 0
            initial = snapshot["enriched_items"]
            assert len(initial) == 5
            if failure != "disabled":
                assert all(row["enrichment_state"] == "pending" and row["ai_portfolio"] is None for row in initial)
            rest = [json.loads(chunk) for chunk in iterator]
            assert rest[-1]["type"] == "complete"
            updates = {event["item"]["id"]: event["item"] for event in rest if event["type"] == "advice"}
            assert len(updates) == (0 if failure == "disabled" else 5)
            assert s.m.stages["deterministic"]["calls"] == 5  # no new forecasts for second phase
        finally:
            response.close()
        log = [json.loads(line) for line in s.log_path.read_text().splitlines()]
        assert len(log) == (5 if failure == "disabled" else 10)
        identities = [(r["payload"]["portfolio_observation_index"], r["payload"]["advice_phase"]) for r in log]
        assert len(set(identities)) == len(log)
        # Repeated-symbol lots are intentionally separate observations, never forecasts.
        assert all(r.get("forecast") is None for r in log)
        assert all(r["decision_source"] == "rule_based" for r in log)
        assert {r["payload"]["portfolio_view_id"] for r in log} == {snapshot["view_id"]}
        s.ai._disabled_until = 0
        legacy = s.client.get("/api/user-watchlist").get_json()["enriched_items"]
        for before, control in zip(initial, legacy):
            assert {k: before[k] for k in FINANCIAL} == {k: control[k] for k in FINANCIAL}
            assert before["live_market"]["quality_flags"] == control["live_market"]["quality_flags"]
            final = {**before, **updates.get(before["id"], {})}
            for field in ("advice", "advice_reason", "base_advice", "suitability", "deterministic_portfolio"):
                assert final[field] == control[field]


@pytest.mark.parametrize("change", ["sell", "delete", "quantity", "basis", "profile", "other_lot"])
def test_mutated_context_expires_without_ai_or_enriched_log(tmp_path, change):
    with scenario(tmp_path, 3, 2, "success", 0, 0) as s:
        response = s.client.get("/api/user-watchlist/stream", buffered=False)
        iterator = iter(response.response)
        snapshot = json.loads(next(iterator))
        with s.app.app_context():
            lot = db.session.get(WatchlistItem, snapshot["items"][0]["id"])
            if change == "sell": lot.shares = 0
            elif change == "delete": db.session.delete(lot)
            elif change == "quantity": lot.shares = 99
            elif change == "basis": lot.buy_price = 999
            elif change == "other_lot": db.session.get(WatchlistItem, snapshot["items"][-1]["id"]).shares = 99
            else: InvestorProfile.query.filter_by(user_id=1).one().risk_tolerance = "low"
            db.session.commit()
        assert json.loads(next(iterator))["type"] == "expired"
        response.close()
        assert s.m.stages["provider.ai.synthetic_openai"]["calls"] == 0
        assert len(s.log_path.read_text().splitlines()) == 3


def test_mutation_during_provider_call_discards_result(tmp_path):
    with scenario(tmp_path, 2, 2, "success", 0, 0) as s:
        original = s.providers.post
        def post(*args, **kwargs):
            result = original(*args, **kwargs)
            lot = WatchlistItem.query.filter_by(user_id=1).first()
            lot.shares = 0
            db.session.commit()
            return result
        with patch("requests.post", post):
            response = s.client.get("/api/user-watchlist/stream", buffered=False)
            assert [e["type"] for e in events(response)] == ["snapshot", "expired"]
            response.close()
        assert len(s.log_path.read_text().splitlines()) == 2


def test_disconnect_stops_work_and_releases_admission(tmp_path):
    with scenario(tmp_path, 5, 5, "success", 0, 0) as s:
        response = s.client.get("/api/user-watchlist/stream", buffered=False)
        next(iter(response.response))
        response.close()
        assert s.m.stages["provider.ai.synthetic_openai"]["calls"] == 0
        release = admit(1)
        assert release is not None
        release()
        release()  # response close/finally may both release
        assert len(s.log_path.read_text().splitlines()) == 5


def test_auth_isolation_busy_snapshot_and_no_automatic_ai_retry(tmp_path):
    with scenario(tmp_path, 3, 2, "success", 0, 0) as s:
        assert s.app.test_client().get("/api/user-watchlist/stream").status_code == 401
        release = admit(1)
        try:
            assert s.client.get("/api/user-watchlist/stream").status_code == 429
            data = s.client.get("/api/user-watchlist?defer_ai=1").get_json()
            assert len(data["enriched_items"]) == 3
            assert all(r["enrichment_state"] == "fallback" for r in data["enriched_items"])
            release()
            other = s.app.test_client()
            with other.session_transaction() as session: session["user_id"] = 2
            response = other.get("/api/user-watchlist/stream", buffered=False)
            snapshot = json.loads(next(iter(response.response)))
            assert [r["symbol"] for r in snapshot["items"]] == ["S99"]
            response.close()
            assert s.m.stages["provider.ai.synthetic_openai"]["calls"] == 0
        finally:
            release()


def test_admission_bounded_across_threads_and_account_fence_across_processes():
    one = admit(1)
    try:
        assert one and admit(2) is None
        code = "from moneybot.services.portfolio_enrichment import admit; assert admit(1) is None; release=admit(2); assert release; release()"
        subprocess.run([sys.executable, "-c", code], check=True, timeout=10)
    finally:
        one()


def test_stream_sampling_emits_at_completion_not_first_chunk(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="moneybot.portfolio_timing")
    with scenario(tmp_path, 2, 2, "success", 0, .001) as s:
        s.app.config.update(PORTFOLIO_TIMING_SAMPLE_RATE=1)
        timing.install_request_timing(s.app, db)
        response = s.client.get("/api/user-watchlist/stream", buffered=False)
        iterator = iter(response.response)
        next(iterator)
        assert not [r for r in caplog.records if r.name == "moneybot.portfolio_timing"]
        list(iterator)
        response.close()
        records = [json.loads(r.message.split("PORTFOLIO_TIMING ")[1]) for r in caplog.records if r.name == "moneybot.portfolio_timing"]
        assert len(records) == 1
        trace = records[0]
        assert trace["stages"]["provider.openai"]["calls"] == 2
        assert trace["milestones_ms"]["initial_portfolio_ready"] < trace["milestones_ms"]["advice_complete"] < trace["wall_ms"]
        assert timing._active.get() is None
        for secret in ("S00", "shares", "synthetic-only", "user_id"):
            assert secret not in json.dumps(trace)


def test_enrichment_expiry_stops_before_next_provider_call(tmp_path, monkeypatch):
    with scenario(tmp_path, 2, 2, 'success', 0, 0) as s:
        response = s.client.get('/api/user-watchlist/stream', buffered=False)
        iterator = iter(response.response)
        assert json.loads(next(iterator))['type'] == 'snapshot'
        monkeypatch.setattr('moneybot.api.MAX_ENRICHMENT_SECONDS', -1)
        assert json.loads(next(iterator))['type'] == 'expired'
        response.close()
        assert s.m.stages['provider.ai.synthetic_openai']['calls'] == 0


def test_added_lot_during_market_preparation_invalidates_initial_snapshot(tmp_path):
    with scenario(tmp_path, 2, 2, 'success', 0, 0) as s:
        original = s.market.get_sector
        def sector(symbol):
            result = original(symbol)
            db.session.add(WatchlistItem(user_id=1, symbol='S98', shares=2, buy_price=100))
            db.session.commit()
            return result
        with patch.object(s.market, 'get_sector', sector):
            response = s.client.get('/api/user-watchlist/stream', buffered=False)
            assert [e['type'] for e in events(response)] == ['expired']
            response.close()
        assert s.m.stages['provider.ai.synthetic_openai']['calls'] == 0
        assert not s.log_path.exists()


def test_stream_failure_and_diagnostics_disconnect_release_all_state(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger='moneybot.portfolio_timing')
    with scenario(tmp_path, 2, 2, 'success', 0, 0) as s:
        s.app.config.update(PORTFOLIO_TIMING_SAMPLE_RATE=1)
        timing.install_request_timing(s.app, db)
        response = s.client.get('/api/user-watchlist/stream', buffered=False)
        next(iter(response.response))
        response.close()
        assert timing._active.get() is None
        assert len([r for r in caplog.records if r.name == 'moneybot.portfolio_timing']) == 1
        with patch('moneybot.api._watchlist_events', side_effect=RuntimeError('sensitive financial data')):
            response = s.client.get('/api/user-watchlist/stream', buffered=False)
            assert events(response) == [{'type':'failed'}]
            response.close()
        release = admit(1)
        assert release
        release()
        assert 'sensitive financial data' not in caplog.text


def test_inflight_ai_leaves_other_request_threads_and_database_available(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    entered, finish = Event(), Event()
    with scenario(tmp_path, 2, 2, 'success', 0, 0) as s:
        original = s.providers.post
        def post(*args, **kwargs):
            entered.set()
            assert finish.wait(5)
            return original(*args, **kwargs)
        def consume():
            response = s.client.get('/api/user-watchlist/stream', buffered=False)
            try:
                return events(response)
            finally:
                response.close()
        with patch('requests.post', post), ThreadPoolExecutor(max_workers=4) as pool:
            future = pool.submit(consume)
            try:
                assert entered.wait(5)
                other = s.app.test_client()
                with other.session_transaction() as session: session['user_id'] = 1
                assert other.get('/api/user-watchlist/stream').status_code == 429
                assert len(other.get('/api/user-watchlist?skip_market_data=1').get_json()['items']) == 2
            finally:
                finish.set()
            assert future.result(timeout=5)[-1]['type'] == 'complete'


def test_unstarted_sampled_wsgi_stream_releases_fence_and_context(tmp_path, caplog):
    from flask import session
    caplog.set_level(logging.INFO, logger='moneybot.portfolio_timing')
    with scenario(tmp_path, 2, 2, 'success', 0, 0) as s:
        s.app.config.update(PORTFOLIO_TIMING_SAMPLE_RATE=1)
        timing.install_request_timing(s.app, db)
        with s.app.test_request_context('/api/user-watchlist/stream'):
            session['user_id'] = 1
            response = s.app.full_dispatch_request()
            assert response.is_streamed
            response.close()  # No next(), no paid work, no leaked Flask context.
        assert timing._active.get() is None
        assert s.m.stages['provider.ai.synthetic_openai']['calls'] == 0
        release = admit(1)
        assert release
        release()
        assert len([r for r in caplog.records if r.name == 'moneybot.portfolio_timing']) == 1
