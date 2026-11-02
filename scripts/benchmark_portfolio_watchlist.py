"""Offline, synthetic full-route profile; never connects to a real provider.

Run unchanged on baseline and repair, with the same arguments. Measurements are
aggregated in a fixed set of stage names: no account, symbol, SQL parameters,
provider payload, prompt, token, or decision record is exported.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
import functools
import hashlib
import json
import math
from pathlib import Path
import resource
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests
from flask import Flask
from sqlalchemy import event

from moneybot import api
from moneybot.extensions import db
from moneybot.models import InvestorProfile, User, WatchlistItem
from moneybot.services.ai_advisor import AIAdvisorService
from moneybot.services.alpha_atlas_feature_contract import ALPHA_ATLAS_V2_FEATURES
from moneybot.services.decision_log import DecisionLogger
from moneybot.services.deterministic_advisor import DeterministicQuickAdvisor
from moneybot.services.deterministic_model import BaselineModelArtifact
from moneybot.services.market_data import MarketDataService
from moneybot.services.market_data_providers import NormalizedBar, NormalizedQuote, ProviderResult
from moneybot.services.suitability_policy import PersonalizationRuntime

STAMP = datetime(2026, 10, 30, 20, 0, tzinfo=timezone.utc)
AI_SOURCE_SHA256 = hashlib.sha256((ROOT / "moneybot/services/ai_advisor.py").read_bytes()).hexdigest()


class Measurements:
    def __init__(self):
        self.reset()

    def reset(self):
        self.stages = defaultdict(lambda: dict(calls=0, seconds=0.0, max_seconds=0.0, errors=0, timeouts=0))
        self.hits = self.misses = self.active = self.peak = 0

    @contextmanager
    def measure(self, name, provider=False):
        stage = self.stages[name]
        start = time.perf_counter()
        if provider:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            yield
        except Exception as exc:
            stage["errors"] += 1
            stage["timeouts"] += isinstance(exc, requests.Timeout)
            raise
        finally:
            elapsed = time.perf_counter() - start
            stage["calls"] += 1
            stage["seconds"] += elapsed
            stage["max_seconds"] = max(stage["max_seconds"], elapsed)
            if provider:
                self.active -= 1

    def wrap(self, name, fn):
        @functools.wraps(fn)
        def measured(*args, **kwargs):
            with self.measure(name):
                return fn(*args, **kwargs)
        return measured


class SyntheticProviders:
    def __init__(self, measurements, market_delay, ai_delay, failure):
        self.m, self.market_delay, self.ai_delay, self.failure = measurements, market_delay, ai_delay, failure

    def get_quote(self, symbol):
        with self.m.measure("provider.quote.synthetic_massive", provider=True):
            time.sleep(self.market_delay)
            price = 100.0 + int(symbol[1:])
            quote = NormalizedQuote(
                symbol=symbol, bid=None, ask=None, bid_size=None, ask_size=None, midpoint=None,
                last_trade_price=price, last_trade_size=1, price=price, price_source="last_trade",
                price_reason="synthetic fixture", event_timestamp=STAMP, received_timestamp=STAMP,
                age_ms=0, market_session="closed", source="massive", source_mode="rest",
                is_stale=True, quality_flags=("stale",), previous_close=price / 1.02, change_percent=2.0)
            return ProviderResult(quote, "massive", "quote", "synthetic", STAMP, self.market_delay * 1000)

    def get_aggregates(self, symbol, **kwargs):
        with self.m.measure("provider.history.synthetic_massive", provider=True):
            time.sleep(self.market_delay)
            price = 100.0 + int(symbol[1:])
            bars = [NormalizedBar(symbol, 1, "day", STAMP - timedelta(days=29-i),
                                  price, price+1, price-1, price, 1000, price, 10, True)
                    for i in range(30)]
            return ProviderResult(bars, "massive", "history", "synthetic", STAMP, self.market_delay * 1000)

    def ticker(self, symbol):
        providers = self

        class Ticker:
            @property
            def info(self):
                with providers.m.measure("provider.sector.synthetic_yfinance", provider=True):
                    time.sleep(providers.market_delay)
                    return {"sector": "Technology" if int(symbol[1:]) % 2 else "Industrials"}

        return Ticker()

    def post(self, *args, **kwargs):
        with self.m.measure("provider.ai.synthetic_openai", provider=True):
            time.sleep(self.ai_delay)
            if self.failure == "timeout":
                raise requests.Timeout("synthetic timeout")
            if self.failure == "server_error":
                raise requests.HTTPError(response=SimpleNamespace(status_code=503))
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"output_text": json.dumps({
                "advice": "HOLD", "advice_reason": "HOLD: synthetic response.",
                "risk_notes": ["risk one", "risk two"], "next_checks": ["check one", "check two"]})})


def forbidden(*args, **kwargs):
    raise AssertionError("Live network forbidden in portfolio benchmark")


@contextmanager
def scenario(directory, positions, unique, failure, market_delay=.001, ai_delay=.01):
    """Real endpoint, auth, SQLite ORM, caches, guarded model, policy and logging."""
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic-only", SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
                      API_RATE_LIMIT_MAX_REQUESTS=100000)
    db.init_app(app)
    app.register_blueprint(api.api_bp)
    m = Measurements()
    providers = SyntheticProviders(m, market_delay, ai_delay, failure)
    market = MarketDataService(clock=lambda: STAMP)
    model = BaselineModelArtifact("alpha-atlas-v2", list(ALPHA_ATLAS_V2_FEATURES),
                                 [0.]*len(ALPHA_ATLAS_V2_FEATURES), [1.]*len(ALPHA_ATLAS_V2_FEATURES),
                                 [.1]*len(ALPHA_ATLAS_V2_FEATURES), 0., .55)
    model_path = Path(directory) / "synthetic-model.json"
    model_path.write_text(json.dumps(model.to_dict()))
    advisor = DeterministicQuickAdvisor(enabled=True, artifact_path=str(model_path))
    advisor.set_market_history_service(market)
    ai = AIAdvisorService(enabled=True, api_key="synthetic-only", timeout_s=6, failure_cooldown_s=120)
    log_path = Path(directory) / "synthetic-decisions.jsonl"
    logger = DecisionLogger(enabled=True, output_path=str(log_path))
    app.extensions.update(market_data_service=market, ai_advisor_service=ai,
                          deterministic_quick_advisor=advisor, decision_logger=logger,
                          personalization_runtime=PersonalizationRuntime(
                              profile_enabled=True, policy_enabled=True, mode="enforce"))
    with app.app_context():
        db.create_all()
        db.session.add_all([User(id=1, name="Synthetic", username="one", email="one@example.invalid", password_hash="none"),
                            User(id=2, name="Other", username="two", email="two@example.invalid", password_hash="none"),
                            InvestorProfile(user_id=1), InvestorProfile(user_id=2)])
        db.session.add_all([WatchlistItem(user_id=1, symbol=f"S{i % unique:02}", buy_price=80+i,
                                         shares=i+1, original_shares=i+1, created_at=STAMP.replace(tzinfo=None)+timedelta(seconds=i))
                            for i in range(positions)])
        db.session.add(WatchlistItem(user_id=2, symbol="S99", buy_price=50, shares=3, original_shares=3))
        db.session.commit()
        engine = db.engine
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = 1
    api._RATE.clear()
    query_start = []

    def before_query(*args):
        query_start.append(time.perf_counter())

    def after_query(conn, cursor, statement, *args):
        elapsed = time.perf_counter() - query_start.pop()
        for name in ("database", "database.watchlist" if "FROM watchlist_items" in statement else "database.other"):
            stage = m.stages[name]
            stage["calls"] += 1
            stage["seconds"] += elapsed
            stage["max_seconds"] = max(stage["max_seconds"], elapsed)

    with ExitStack() as stack:
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "socket.create_connection",
                       "requests.sessions.Session.request"):
            stack.enter_context(patch(target, forbidden))
        stack.enter_context(patch("requests.post", providers.post))
        stack.enter_context(patch("moneybot.services.market_data.yf.Ticker", providers.ticker))
        stack.enter_context(patch.object(market, "_massive_client", lambda: providers))
        stack.enter_context(patch("moneybot.services.deterministic_advisor.predict_proba", forbidden))
        stack.enter_context(patch("moneybot.services.deterministic_advisor.make_forecast", forbidden))
        for obj, method, name in [(api, "_session_expired", "authentication"),
                                 (api, "_decision_context_for_user", "profile_context"),
                                 (api, "_ensure_investor_profile", "profile_lookup"),
                                 (api, "_register_stream_demand", "stream_demand"),
                                 (api, "_quick_decision", "quick_alignment"),
                                 (api, "_personalize_action", "suitability"),
                                 (api, "jsonify", "serialization"),
                                 (market, "get_quote", "quote"), (market, "get_price_history_data", "history"),
                                 (market, "get_sector", "sector"), (ai, "enhance_portfolio_position", "ai"),
                                 (advisor, "predict_portfolio_position", "deterministic"), (logger, "log", "decision_logging")]:
            stack.enter_context(patch.object(obj, method, m.wrap(name, getattr(obj, method))))
        for cache in (market.quote_cache, market.history_cache, market.sector_cache):
            original = cache.get

            def get(key, original=original):
                result = original(key)
                if result is None:
                    m.misses += 1
                else:
                    m.hits += 1
                return result

            stack.enter_context(patch.object(cache, "get", get))
        event.listen(engine, "before_cursor_execute", before_query)
        event.listen(engine, "after_cursor_execute", after_query)
        try:
            yield SimpleNamespace(app=app, client=client, m=m, market=market, ai=ai, providers=providers,
                                  advisor=advisor, log_path=log_path)
        finally:
            event.remove(engine, "before_cursor_execute", before_query)
            event.remove(engine, "after_cursor_execute", after_query)
            with app.app_context():
                db.session.remove()
                engine.dispose()


def stable_payload(payload):
    """Exclude only clocks/IDs and the documented provider-error vs cooldown reason."""
    payload = json.loads(json.dumps(payload))
    payload.pop("request_id", None)
    for row in payload["enriched_items"]:
        row["live_market"].pop("age_ms", None)
        if row.get("ai_portfolio", {}).get("reason") in {"provider_error", "cooldown_after_failure"}:
            row["ai_portfolio"]["reason"] = "provider_unavailable"
    return payload


def run_case(directory, positions, unique, failure, cache_state, samples, market_delay, ai_delay):
    observations = []
    with scenario(directory, positions, unique, failure, market_delay, ai_delay) as s:
        for _ in range(samples):
            for cache in (s.market.quote_cache, s.market.history_cache, s.market.sector_cache):
                cache._store.clear()
            if cache_state == "warm":
                for i in range(unique):
                    symbol = f"S{i:02}"
                    s.market.get_quote(symbol)
                    s.market.get_price_history_data(symbol)
                    s.market.get_sector(symbol)
            # Each sample begins outside cooldown; warm means market cache only.
            s.ai._disabled_until = 0
            s.log_path.unlink(missing_ok=True)
            s.m.reset()
            cpu_start, start = time.process_time(), time.perf_counter()
            response = s.client.get("/api/user-watchlist")
            wall, cpu = time.perf_counter() - start, time.process_time() - cpu_start
            assert response.status_code == 200
            payload = response.get_json()
            assert len(payload["enriched_items"]) == positions
            assert s.log_path.read_text().count("\n") == positions
            observations.append(dict(wall_seconds=wall, cpu_seconds=cpu, bytes=len(response.data),
                stages=dict(s.m.stages), cache_hits=s.m.hits, cache_misses=s.m.misses, peak_concurrency=s.m.peak,
                semantic_sha256=hashlib.sha256(json.dumps(stable_payload(payload), sort_keys=True).encode()).hexdigest()))
    wall_times = sorted(o["wall_seconds"] for o in observations)
    stages = {}
    for name in sorted({name for o in observations for name in o["stages"]}):
        values = [o["stages"].get(name, {}) for o in observations]
        stages[name] = dict(median_seconds=statistics.median(v.get("seconds", 0) for v in values),
                           max_seconds=max(v.get("seconds", 0) for v in values),
                           max_call_seconds=max(v.get("max_seconds", 0) for v in values),
                           calls_min=min(v.get("calls", 0) for v in values),
                           calls_max=max(v.get("calls", 0) for v in values),
                           errors_max=max(v.get("errors", 0) for v in values),
                           timeouts_max=max(v.get("timeouts", 0) for v in values))
    return dict(positions=positions, unique_symbols=unique, failure=failure, cache=cache_state, samples=samples,
                median_seconds=statistics.median(wall_times), p95_seconds=wall_times[math.ceil(.95*samples)-1] if samples >= 20 else None,
                max_seconds=max(wall_times), median_cpu_seconds=statistics.median(o["cpu_seconds"] for o in observations),
                stages=stages, peak_concurrency=max(o["peak_concurrency"] for o in observations),
                cache_lookup_hit_rate=sum(o["cache_hits"] for o in observations) / max(1, sum(
                    o["cache_hits"]+o["cache_misses"] for o in observations)),
                semantic_sha256=sorted({o["semantic_sha256"] for o in observations}),
                response_bytes_min=min(o["bytes"] for o in observations),
                response_bytes_max=max(o["bytes"] for o in observations),
                wall_seconds=[o["wall_seconds"] for o in observations],
                cpu_seconds=[o["cpu_seconds"] for o in observations])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--market-delay", type=float, default=.001)
    parser.add_argument("--ai-delay", type=float, default=.01)
    args = parser.parse_args()
    if not 1 <= args.samples <= 100 or not 0 <= args.market_delay <= 1 or not 0 <= args.ai_delay <= 6:
        parser.error("samples must be 1..100; market delay 0..1s; AI delay 0..6s")
    cases = []
    with tempfile.TemporaryDirectory(prefix="watchlist-benchmark-") as directory:
        for failure in ("success", "timeout", "server_error"):
            for cache_state in ("cold", "warm"):
                for positions, unique in ((1, 1), (5, 5), (10, 10), (20, 20), (20, 5)):
                    cases.append(run_case(directory, positions, unique, failure, cache_state,
                                          args.samples, args.market_delay, args.ai_delay))
    result = dict(schema="portfolio-benchmark.v1", revision=subprocess.check_output(
                      ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  ai_source_sha256=AI_SOURCE_SHA256,
                  fixture=dict(market_delay_seconds=args.market_delay, ai_delay_seconds=args.ai_delay,
                               ai_configured_timeout_seconds=6, cooldown_seconds=120,
                               provider_io="synthetic sleep only; no network", p95_method="nearest rank; >=20 samples",
                               cache="real MarketDataService TTL caches; AI cooldown reset before each sample"),
                  peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, cases=cases)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Wrote {len(cases)} synthetic cases to {args.output}", flush=True)


if __name__ == "__main__":
    main()
