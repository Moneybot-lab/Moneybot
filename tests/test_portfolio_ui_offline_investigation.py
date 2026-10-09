"""Offline C5 regressions execute actual inline portfolio UI functions.

Historical test names identify the previously reproduced failures; assertions
now require accurate source/status labels after the authorized repair. Node
uses a synthetic DOM and EventSource; all inputs and clocks are local.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import socket
import subprocess
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from moneybot.services.live_market import LiveQuoteResolver
from moneybot.services.market_stream import InMemoryMarketStreamState, StreamEvent
from moneybot.services.market_data_providers import normalized_fallback_quote


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 9, 15, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _deny_python_network(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError("This offline investigation must not use network")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


def _quote(*, symbol="AAPL", price=102.0, age_seconds=1, stale=False):
    return {
        "symbol": symbol,
        "price": price,
        "event_timestamp": (NOW - timedelta(seconds=age_seconds)).isoformat(),
        "received_timestamp": NOW.isoformat(),
        "age_ms": age_seconds * 1000,
        "source": "massive",
        "source_mode": "rest",
        "market_session": "regular",
        "live_data_available": price is not None and not stale,
        "is_stale": stale,
        "quality_flags": ["stale"] if stale else [],
    }


def _event(state, *, symbol="AAPL", event_type="T", price=101.0, age_seconds=1, sequence=7):
    timestamp = NOW - timedelta(seconds=age_seconds)
    state.set_latest(
        StreamEvent(
            event_type=event_type,
            symbol=symbol,
            event_timestamp=timestamp,
            received_timestamp=NOW,
            sequence_number=sequence,
            provider_event_id=f"synthetic-{sequence}",
            payload={"close" if event_type in {"A", "AM"} else "price": price},
            quality_flags=(),
        ),
        ttl_seconds=600,
    )


def _item(symbol="AAPL", live=None):
    item = {"symbol": symbol, "current_price": 100.0, "entry_price": 90.0, "shares": 2}
    if live is not None:
        item["current_price"] = live["price"]
        item["live_market"] = live
    return item


def _ui(items, scenario, *, actual_render=False):
    """Run production functions without loading Flask or accessing any endpoint."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for actual inline-JavaScript characterization")
    source = (ROOT / "moneybot/app_factory.py").read_text()
    ui_source = source.split("              function formatMoney(v){", 1)[1]
    ui_source = "function formatMoney(v){" + ui_source.split(
        "              function adviceBadge(value){", 1
    )[0]
    selection_source = "function selectPortfolioRows(data){" + source.split(
        "              function selectPortfolioRows(data){", 1
    )[1].split("              function renderRows(items){", 1)[0]
    render_source = "function renderRows(items){" + source.split(
        "              function renderRows(items){", 1
    )[1].split("              async function load(){", 1)[0]
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const status = {textContent: '', style: {}};
const elements = {rows:{innerHTML:''}, realizedPnl:{textContent:'$80.00'}, lifetimePnl:{textContent:'$250.00'}};
const sandbox = {
  document: {getElementById: (id) => id === 'portfolioLiveStatus' ? status : (elements[id] || null)},
  window: {events:{}, addEventListener(name, callback) { this.events[name] = callback; }},
  EventSource: class {
    static instances = [];
    constructor(url) {
      this.url = url; this.listeners = {}; this.closed = false;
      this.constructor.instances.push(this);
    }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    close() { this.closed = true; }
    emit(name, data) { this.listeners[name]?.({data: JSON.stringify(data)}); }
  },
  setTimeout: (callback, delay) => { sandbox.timers.push({callback, delay}); return sandbox.timers.length; },
  clearTimeout: (timer) => { sandbox.clearedTimers.push(timer); },
  renderRows: (items) => { sandbox.renderCount += 1; },
  escapeHtml: (value) => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;'),
  load: () => { sandbox.loadCount += 1; },
  tickerButton: (value) => String(value), displayValue: (value) => String(value),
  performanceCell: (amount, percent) => String(amount), amountCell: (value) => String(value),
  adviceButton: (item, idx) => String(item.advice || ''), renderTrend: () => {},
  Date: class extends Date { static now() { return input.now_ms; } },
  elements, status, items: input.items, timers: [], clearedTimers: [], renderCount: 0, loadCount: 0,
};
// No require, fetch, process, socket, or browser network implementation exists
// in the execution context. EventSource above only stores callbacks.
const setup = `let currentPortfolioItems = items; let portfolioEventSource = null; let portfolioReconnectTimer = null; let portfolioBrowserState = 'idle'; let portfolioProviderHealth = null; let portfolioProviderHealthReceivedAt = 0; const rowsEl = elements.rows;`;
const capture = `function capture(){ return {
  items: JSON.parse(JSON.stringify(currentPortfolioItems)),
  cells: currentPortfolioItems.map(livePriceCell), status: status.textContent,
  background: status.style.background, renderCount, loadCount,
  rowsHtml: elements.rows.innerHTML, realized: elements.realizedPnl.textContent, lifetime: elements.lifetimePnl.textContent,
  timerCount: timers.length, sources: EventSource.instances.map((source) => ({url: source.url, closed: source.closed})),
}; }`;
const render = input.actual_render ? input.render + '\nconst originalRenderRows = renderRows; renderRows = (items) => {renderCount += 1; originalRenderRows(items);};\n' : '';
const result = vm.runInNewContext(setup + input.source + input.selection + render + capture + input.scenario, sandbox, {timeout: 2000});
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        [node, "-e", harness],
        input=json.dumps({"items": items, "source": ui_source, "selection": selection_source, "render": render_source,
                          "actual_render": actual_render, "scenario": scenario, "now_ms": NOW.timestamp() * 1000}),
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
        cwd=ROOT,
    )
    return json.loads(completed.stdout)


def test_initial_watchlist_row_without_live_metadata_is_labelled_live_unknown():
    # Missing enrichment metadata cannot establish live/fresh status.
    result = _ui([_item()], "capture();")
    assert "Last known · Status unknown · session n/a · unknown" in result["cells"][0]


def test_fresh_rest_without_stream_is_rendered_live_instead_of_rest_source():
    resolver = LiveQuoteResolver(state=InMemoryMarketStreamState(clock=lambda: 0.0), rest_quote=lambda _: _quote(), clock=lambda: NOW)
    quote = resolver.resolve("AAPL").payload()
    assert quote["source_mode"] == "rest" and quote["is_degraded"] is False
    result = _ui([_item()], "applyPortfolioLiveQuotes(" + json.dumps([quote]) + "); capture();")
    assert "REST · Fresh · regular" in result["cells"][0]
    assert "WebSocket · Live" not in result["cells"][0]
    assert "Prices: 1 fresh" in result["status"] and "Provider status unknown" in result["status"]


def test_stale_rest_is_rendered_stale():
    quote = LiveQuoteResolver(
        state=InMemoryMarketStreamState(clock=lambda: 0.0), rest_quote=lambda _: _quote(age_seconds=45, stale=True), clock=lambda: NOW
    ).resolve("AAPL").payload()
    result = _ui([_item()], "applyPortfolioLiveQuotes(" + json.dumps([quote]) + "); capture();")
    assert quote["source_mode"] == "rest" and quote["is_stale"]
    assert "REST · Stale · regular" in result["cells"][0]
    assert result["background"] == "#fef3c7"


def test_stale_stream_with_fresh_rest_has_rest_fallback_label():
    state = InMemoryMarketStreamState(clock=lambda: 0.0)
    _event(state, age_seconds=45)
    quote = LiveQuoteResolver(state=state, rest_quote=lambda _: _quote(), clock=lambda: NOW).resolve("AAPL").payload()
    assert quote["is_degraded"] and not quote["is_stale"]
    assert "stream_stale_rest_fallback" in quote["quality_flags"]
    result = _ui([_item()], "applyPortfolioLiveQuotes(" + json.dumps([quote]) + "); capture();")
    assert "REST · Fresh · Degraded fallback · regular" in result["cells"][0]
    assert "1 fresh; 1 degraded" in result["status"]


def test_fresh_aggregate_survives_old_trade_and_requires_no_rest():
    state = InMemoryMarketStreamState(clock=lambda: 0.0)
    _event(state, age_seconds=90)
    _event(state, event_type="A", age_seconds=1, price=103.0, sequence=8)

    def forbidden_rest(_):
        raise AssertionError("Fresh aggregate must not require REST")

    quote = LiveQuoteResolver(state=state, rest_quote=forbidden_rest, clock=lambda: NOW).resolve("AAPL").payload()
    assert quote["event_type"] == "A" and quote["price"] == 103.0
    assert quote["source_mode"] == "websocket" and not quote["is_stale"]
    result = _ui([_item()], "applyPortfolioLiveQuotes(" + json.dumps([quote]) + "); capture();")
    assert "WebSocket · Live · regular" in result["cells"][0]


def test_thin_symbol_old_trade_is_not_proof_of_provider_disconnect():
    state = InMemoryMarketStreamState(clock=lambda: 0.0)
    _event(state, symbol="THIN", age_seconds=45)
    state.set_health({"connection_state": "connected", "last_event_at_utc": NOW.isoformat()}, ttl_seconds=600)
    quote = LiveQuoteResolver(state=state, rest_quote=lambda _: _quote(symbol="THIN"), clock=lambda: NOW).resolve("THIN").payload()
    assert state.get_health()["connection_state"] == "connected"
    assert quote["is_degraded"] and quote["source_mode"] == "rest"
    result = _ui([_item("THIN")], "applyPortfolioLiveQuotes(" + json.dumps([quote]) + "); capture();")
    assert "Prices: 1 fresh; 1 degraded" in result["status"]
    assert "Provider status unknown" in result["status"]


def test_provider_disconnected_health_is_not_included_in_ui_quote_status():
    state = InMemoryMarketStreamState(clock=lambda: 0.0)
    state.set_health({"connection_state": "disconnected"}, ttl_seconds=600)
    quote = LiveQuoteResolver(state=state, rest_quote=lambda _: _quote(), clock=lambda: NOW).resolve("AAPL").payload()
    assert "connection_state" not in quote
    result = _ui([_item()], "startPortfolioLive(); portfolioEventSource.emit('quotes', " + json.dumps({"quotes": [quote]}) + "); capture();")
    assert state.get_health()["connection_state"] == "disconnected"
    assert "Browser price feed connected" in result["status"]
    assert "Provider status unknown" in result["status"]


def test_connected_provider_with_no_symbol_event_is_still_not_known_to_ui():
    state = InMemoryMarketStreamState(clock=lambda: 0.0)
    state.set_health({"connection_state": "connected", "last_event_at_utc": NOW.isoformat()}, ttl_seconds=600)
    quote = LiveQuoteResolver(state=state, rest_quote=lambda _: _quote(), clock=lambda: NOW).resolve("AAPL").payload()
    assert "connection_state" not in quote and quote["source_mode"] == "rest"
    result = _ui([_item()], "startPortfolioLive(); portfolioEventSource.emit('quotes', " + json.dumps({"quotes": [quote]}) + "); capture();")
    assert "Browser price feed connected" in result["status"]
    assert "Provider status unknown" in result["status"]
    assert "REST · Fresh · regular" in result["cells"][0]


def test_actual_sse_route_suppresses_same_id_fresh_to_stale_transition(monkeypatch):
    # Minimal Flask request context invokes the actual decorated route/generator.
    # It has no application factory, database, Redis, provider, or HTTP listener.
    from flask import Flask, g, session
    from moneybot import api

    state = InMemoryMarketStreamState(clock=lambda: 0.0)
    fresh = _quote()
    stale = {**fresh, "is_stale": True, "live_data_available": False, "quality_flags": ["stale"]}
    inputs = iter([fresh, stale])
    resolved = []

    def synthetic_rest(symbol):
        resolved.append(symbol)
        return next(inputs)

    resolver = LiveQuoteResolver(state=state, rest_quote=synthetic_rest, clock=lambda: NOW)
    # Real resolver uses the timestamp alone for REST identity; quality changes
    # do not change that ID. Assert before driving the actual SSE route.
    fresh_payload = LiveQuoteResolver(state=state, rest_quote=lambda _: fresh, clock=lambda: NOW).resolve("AAPL").payload()
    stale_payload = LiveQuoteResolver(state=state, rest_quote=lambda _: stale, clock=lambda: NOW).resolve("AAPL").payload()
    assert fresh_payload["event_id"] == stale_payload["event_id"]
    assert not fresh_payload["is_stale"] and stale_payload["is_stale"]

    ticks = iter([100.0, 103.0])
    sleeps = []
    monkeypatch.setattr(api, "time", SimpleNamespace(time=lambda: next(ticks), sleep=lambda interval: sleeps.append(interval)))
    monkeypatch.setattr(api, "_symbols_for_live_user", lambda _: {"AAPL"})
    monkeypatch.setattr(api, "_decision_context_for_user", lambda _: SimpleNamespace(after_hours_alerts=False, profile_version=1))
    monkeypatch.setattr(api, "_live_quote_resolver", lambda: resolver)
    app = Flask(__name__)
    app.secret_key = "offline-investigation"
    app.config.update(TESTING=True, LIVE_SSE_HEARTBEAT_SECONDS=2, LIVE_SSE_INTERVAL_SECONDS=0.25)
    app.extensions["market_stream_state"] = state
    with app.test_request_context("/api/live-market-stream?symbols=AAPL", headers={"Last-Event-ID": "prior-id"}):
        session["user_id"] = 1
        g.request_id = "synthetic-sse-quality"
        response = api.live_market_stream()
        stream = iter(response.response)
        try:
            ready, quotes, heartbeat_one, stale_quotes, heartbeat_two = [next(stream) for _ in range(5)]
            assert "event: ready" in ready and '"resume_from":"prior-id"' in ready
            assert "event: quotes" in quotes and '"is_stale":false' in quotes
            assert "event: heartbeat" in heartbeat_one
            # A second iteration emits the meaningful stale transition despite the same ID.
            assert "event: quotes" in stale_quotes and '"is_stale":true' in stale_quotes
            assert "event: heartbeat" in heartbeat_two
            assert resolved == ["AAPL", "AAPL"] and sleeps == [0.25]
            assert response.mimetype == "text/event-stream"
            assert response.headers["Cache-Control"] == "no-store"
        finally:
            stream.close()
        assert state.desired_demand() == {}


def test_browser_sse_error_keeps_price_and_row_label_without_rest_fetch():
    state = InMemoryMarketStreamState(clock=lambda: 0.0)
    _event(state)
    quote = LiveQuoteResolver(state=state, rest_quote=lambda _: {}, clock=lambda: NOW).resolve("AAPL").payload()
    result = _ui([_item(live=quote)], "startPortfolioLive(); portfolioEventSource.onerror(); capture();")
    assert "Browser price feed reconnecting" in result["status"]
    assert "Manual Refresh Portfolio remains available" in result["status"]
    assert result["items"][0]["current_price"] == 101.0
    assert "Last known · Stale · regular" in result["cells"][0]
    assert result["loadCount"] == 0 and result["timerCount"] == 0
    assert result["sources"][0]["closed"] is False


def test_heartbeat_overwrites_stale_quote_banner_without_revalidating_rows():
    quote = LiveQuoteResolver(
        state=InMemoryMarketStreamState(clock=lambda: 0.0), rest_quote=lambda _: _quote(stale=True, age_seconds=60), clock=lambda: NOW
    ).resolve("AAPL").payload()
    result = _ui([_item()], "startPortfolioLive(); portfolioEventSource.emit('quotes', " + json.dumps({"quotes": [quote]}) + "); portfolioEventSource.emit('heartbeat', {symbols: ['AAPL']}); capture();")
    assert result["items"][0]["live_market"]["is_stale"] is True
    assert "REST · Stale · regular" in result["cells"][0]
    assert "Browser price feed connected" in result["status"]
    assert "Provider status unknown" in result["status"]
    assert "Prices: 1 stale; 1 degraded" in result["status"]
    assert result["background"] == "#fef3c7"


def test_mixed_portfolio_partial_update_hides_other_rows_staleness():
    stale = {**_quote(symbol="THIN", stale=True, age_seconds=60), "is_degraded": True}
    fresh = {**_quote(), "source_mode": "websocket", "is_degraded": False}
    result = _ui([_item("AAPL"), _item("THIN", stale)], "applyPortfolioLiveQuotes(" + json.dumps([fresh]) + "); capture();")
    assert result["items"][1]["live_market"]["is_stale"] is True
    assert "REST · Stale · regular" in result["cells"][1]
    assert "Mixed quality · Prices: 1 fresh, 1 stale; 1 degraded" in result["status"]


def test_mixed_portfolio_full_batch_marks_banner_degraded():
    quotes = [
        {**_quote(), "source_mode": "websocket", "is_degraded": False},
        {**_quote(symbol="THIN", stale=True, age_seconds=60), "is_degraded": True},
    ]
    result = _ui([_item(), _item("THIN")], "applyPortfolioLiveQuotes(" + json.dumps(quotes) + "); capture();")
    assert "WebSocket · Live · regular" in result["cells"][0] and "Stale · regular" in result["cells"][1]
    assert result["background"] == "#fef3c7"


def test_missing_quote_price_cannot_replace_last_known_value_or_row_metadata():
    fresh = {**_quote(), "source_mode": "websocket", "is_degraded": False}
    missing = {**_quote(price=None, stale=True), "is_degraded": True}
    result = _ui([_item(live=fresh)], "applyPortfolioLiveQuotes(" + json.dumps([missing]) + "); capture();")
    assert result["items"][0]["current_price"] == 102.0
    assert result["items"][0]["live_market"]["is_stale"] is True
    assert result["items"][0]["live_market"]["source_mode"] == "last_known"
    assert "price_unavailable" in result["items"][0]["live_market"]["quality_flags"]
    assert "Last known · Stale" in result["cells"][0]
    assert result["renderCount"] == 1
    assert result["background"] == "#fef3c7"


def test_sse_reconnect_heartbeat_recovers_banner_without_new_quotes():
    result = _ui([_item()], "startPortfolioLive(); portfolioEventSource.onerror(); portfolioEventSource.emit('heartbeat', {}); capture();")
    assert "Browser price feed connected" in result["status"]
    assert "Provider status unknown" in result["status"]
    assert len(result["sources"]) == 1 and result["sources"][0]["closed"] is False
    assert result["loadCount"] == 0 and result["items"][0]["current_price"] == 100.0


def test_sse_error_then_heartbeat_then_fresh_quote_updates_retained_stale_row():
    stale = {**_quote(price=100.0, stale=True, age_seconds=60), "is_degraded": True}
    fresh = {**_quote(price=108.0), "source_mode": "websocket", "event_type": "T", "is_degraded": False}
    scenario = (
        "startPortfolioLive(); portfolioEventSource.onerror(); const interrupted = capture(); "
        "portfolioEventSource.emit('heartbeat', {}); const heartbeat = capture(); "
        "portfolioEventSource.emit('quotes', " + json.dumps({"quotes": [fresh]}) + "); "
        "({interrupted, heartbeat, recovered: capture()});"
    )
    result = _ui([_item(live=stale)], scenario)
    assert "Browser price feed reconnecting" in result["interrupted"]["status"]
    assert "Last known · Stale · regular" in result["interrupted"]["cells"][0]
    assert result["interrupted"]["items"][0]["current_price"] == 100.0
    assert "Browser price feed connected" in result["heartbeat"]["status"]
    assert "Provider status unknown" in result["heartbeat"]["status"]
    assert "Last known · Stale · regular" in result["heartbeat"]["cells"][0]
    assert result["heartbeat"]["items"][0]["current_price"] == 100.0
    assert result["recovered"]["items"][0]["current_price"] == 108.0
    assert result["recovered"]["items"][0]["live_market"]["is_stale"] is False
    assert "WebSocket · Live · regular" in result["recovered"]["cells"][0]
    assert result["recovered"]["renderCount"] == 2 and result["recovered"]["loadCount"] == 0
    assert len(result["recovered"]["sources"]) == 1


def test_restart_closes_old_eventsource_and_retains_scope_and_symbols():
    result = _ui([_item(), _item("THIN")], "startPortfolioLive(); startPortfolioLive(); capture();")
    assert len(result["sources"]) == 2 and result["sources"][0]["closed"] is True
    assert result["sources"][1]["url"] == "/api/live-market-stream?scope=portfolio&symbols=AAPL%2CTHIN"
    assert "Browser price feed: connecting" in result["status"]
    assert "Prices: 2 status unknown" in result["status"]


def test_quote_updates_do_not_change_advice_or_schedule_ai_refresh():
    item = {**_item(), "advice": "HOLD", "advice_reason": "Synthetic existing recommendation", "score": 6}
    quote = {**_quote(price=108.0), "is_degraded": False}
    result = _ui([item], "startPortfolioLive(); portfolioEventSource.emit('quotes', " + json.dumps({"quotes": [quote]}) + "); capture();")
    assert result["items"][0]["advice"] == "HOLD"
    assert result["items"][0]["score"] == 6
    assert result["items"][0]["performance_amount"] == 36.0
    assert result["loadCount"] == 0 and result["timerCount"] == 0


def test_duplicate_portfolio_lots_still_aggregate_without_new_payload_fields():
    lots = [{**_item(), "id": 1}, {**_item(), "id": 2, "entry_price": 110.0, "shares": 1}]
    result = _ui(lots, "currentPortfolioItems = selectPortfolioRows({enriched_items: items}); capture();")
    position = result["items"][0]
    assert len(result["items"]) == 1 and len(position["lots"]) == 2
    assert position["shares"] == 3 and position["remaining_cost_basis"] == 290
    assert position["performance_amount"] == 10
    assert "Last known · Status unknown · session n/a · unknown" in result["cells"][0]


def _provider_health(connection_state="connected", **overrides):
    return {
        "schema_version": "provider-stream-health.v1", "provider": "massive",
        "connection_state": connection_state, "verified": True,
        "observed_at_utc": NOW.isoformat(), "age_ms": 1000, "ttl_seconds": 30,
        **overrides,
    }


@pytest.mark.parametrize("provider_state", ["disconnected", "reconnecting", "connected"])
def test_browser_connection_and_verified_provider_state_are_independent(provider_state):
    fresh_rest = {**_quote(), "is_degraded": False}
    payload = {"quotes": [fresh_rest], "provider_health": _provider_health(provider_state)}
    result = _ui([_item()], "startPortfolioLive(); portfolioEventSource.emit('quotes', " + json.dumps(payload) + "); capture();")
    assert "Browser price feed connected" in result["status"]
    assert f"Provider stream {provider_state}" in result["status"]
    assert "REST · Fresh" in result["cells"][0]
    assert "WebSocket · Live" not in result["cells"][0]


@pytest.mark.parametrize("overrides", [
    {"verified": False}, {"age_ms": 30_001}, {"age_ms": -1}, {"age_ms": None},
    {"observed_at_utc": None}, {"schema_version": "unverified-schema"},
    {"ttl_seconds": 0}, {"connection_state": "invented-healthy-state"},
])
def test_unverified_or_expired_provider_health_is_unknown(overrides):
    result = _ui([_item()], "startPortfolioLive(); portfolioEventSource.emit('heartbeat', " + json.dumps({"provider_health": _provider_health(**overrides)}) + "); capture();")
    assert "Browser price feed connected" in result["status"]
    assert "Provider status unknown" in result["status"]
    assert "Provider stream connected" not in result["status"]


def test_provider_metadata_expires_locally_without_reusing_healthy_claim():
    scenario = (
        "startPortfolioLive(); portfolioEventSource.emit('heartbeat', "
        + json.dumps({"provider_health": _provider_health()})
        + "); const before = capture(); Date.now = () => " + str(int(NOW.timestamp() * 1000 + 30_000))
        + "; updatePortfolioLiveStatus(); ({before, after:capture()});"
    )
    result = _ui([_item()], scenario)
    assert "Provider stream connected" in result["before"]["status"]
    assert "Provider status unknown" in result["after"]["status"]


def test_provider_reconnect_recovery_needs_a_new_quote_to_restore_price_quality():
    fresh = {**_quote(), "source_mode": "websocket", "is_degraded": False}
    scenario = (
        "startPortfolioLive(); portfolioEventSource.emit('quotes', " + json.dumps({"quotes": [fresh], "provider_health": _provider_health()})
        + "); portfolioEventSource.onerror(); portfolioEventSource.emit('heartbeat', " + json.dumps({"provider_health": _provider_health("reconnecting")})
        + "); const reconnecting = capture(); portfolioEventSource.emit('heartbeat', " + json.dumps({"provider_health": _provider_health()})
        + "); const connected = capture(); portfolioEventSource.emit('quotes', " + json.dumps({"quotes": [{**fresh, "price": 108.0}], "provider_health": _provider_health()})
        + "); ({reconnecting, connected, recovered:capture()});"
    )
    result = _ui([_item()], scenario)
    assert "Provider stream reconnecting" in result["reconnecting"]["status"]
    assert "Last known · Stale" in result["reconnecting"]["cells"][0]
    assert "Provider stream connected" in result["connected"]["status"]
    assert "Last known · Stale" in result["connected"]["cells"][0]
    assert "WebSocket · Live" in result["recovered"]["cells"][0]
    assert result["recovered"]["loadCount"] == 0


def test_missing_price_and_absent_last_known_value_render_unavailable():
    missing = {**_quote(price=None, stale=True), "is_degraded": True}
    item = {**_item(), "current_price": None}
    result = _ui([item], "applyPortfolioLiveQuotes(" + json.dumps([missing]) + "); capture();")
    assert result["items"][0]["current_price"] is None
    assert "Price unavailable" in result["cells"][0]
    assert "Prices: 1 unavailable; 1 degraded" in result["status"]
    assert result["loadCount"] == 0


@pytest.mark.parametrize("price", [0.0, -1.0])
def test_invalid_nonpositive_price_is_unavailable_and_cannot_replace_valid_value(price):
    invalid = {**_quote(price=price), "is_stale": False, "is_degraded": False}
    result = _ui([_item()], "applyPortfolioLiveQuotes(" + json.dumps([invalid]) + "); capture();")
    assert result["items"][0]["current_price"] == 100
    assert "Last known · Stale · Price unavailable" in result["cells"][0]
    assert "WebSocket · Live" not in result["cells"][0] and "REST · Fresh" not in result["cells"][0]


def test_status_only_update_keeps_price_time_and_diagnostics_separate():
    fresh = {**_quote(), "source_mode": "websocket", "is_degraded": False}
    missing = {**_quote(price=None, stale=True, age_seconds=0), "is_degraded": True}
    result = _ui([_item(live=fresh)], "applyPortfolioLiveQuotes(" + json.dumps([missing]) + "); capture();")
    live = result["items"][0]["live_market"]
    assert live["event_timestamp"] == fresh["event_timestamp"]
    assert live["observed_event_timestamp"] == missing["event_timestamp"]
    assert live["observed_source_mode"] == "rest"
    assert live["source_mode"] == "last_known" and live["is_stale"]
    assert "Last known · Stale · Price unavailable" in result["cells"][0]


def test_session_transition_metadata_and_status_do_not_refresh_advice():
    fresh = {**_quote(), "source_mode": "websocket", "is_degraded": False, "event_id": "same-id"}
    after_hours = {**fresh, "market_session": "after_hours", "source_mode": "rest", "is_stale": True, "is_degraded": True}
    item = {**_item(live=fresh), "advice": "HOLD", "advice_reason": "Existing rationale", "score": 6}
    result = _ui([item], "startPortfolioLive(); portfolioEventSource.emit('quotes', " + json.dumps({"quotes": [after_hours]}) + "); capture();")
    assert "REST · Stale · after_hours" in result["cells"][0]
    assert result["items"][0]["advice"] == "HOLD" and result["items"][0]["score"] == 6
    assert result["loadCount"] == 0 and result["timerCount"] == 0


def test_actual_grouped_row_render_preserves_unrealized_realized_lifetime_and_advice():
    lots = [
        {**_item(), "id": 1, "advice": "HOLD", "today_change_amount": 3},
        {**_item(), "id": 2, "entry_price": 110.0, "shares": 1, "today_change_amount": -1},
    ]
    quote = {**_quote(price=108.0), "is_degraded": False}
    scenario = "currentPortfolioItems = selectPortfolioRows({enriched_items:items}); applyPortfolioLiveQuotes(" + json.dumps([quote]) + "); const priced = capture(); applyPortfolioLiveQuotes(" + json.dumps([{**quote, "price": None, "is_stale": True}]) + "); ({priced, retained:capture()});"
    result = _ui(lots, scenario, actual_render=True)
    for phase in result.values():
        position = phase["items"][0]
        assert len(position["lots"]) == 2 and position["shares"] == 3
        assert position["remaining_cost_basis"] == 290
        assert position["performance_amount"] == pytest.approx(34)
        assert position["performance_percent"] == pytest.approx((108 / (290 / 3) - 1) * 100)
        assert position["today_change_amount"] == 2
        assert position["advice"] == "HOLD"
        assert "2 acquisition lots" in phase["rowsHtml"] and "$324" in phase["rowsHtml"]
        assert phase["realized"] == "$80.00" and phase["lifetime"] == "$250.00"
        assert phase["loadCount"] == 0 and phase["timerCount"] == 0


def test_browser_cleanup_closes_eventsource_without_refresh_or_recreation():
    result = _ui([_item()], "startPortfolioLive(); window.events.beforeunload(); capture();")
    assert len(result["sources"]) == 1 and result["sources"][0]["closed"] is True
    assert result["loadCount"] == 0 and result["timerCount"] == 0


@pytest.mark.parametrize("source", ["finnhub", "twelve_data", "yfinance"])
@pytest.mark.parametrize("age_seconds, expected", [(1, "REST · Fresh"), (90, "REST · Stale")])
def test_actual_normalized_http_fallback_quote_retains_source_mode_and_rest_label(source, age_seconds, expected):
    rest = normalized_fallback_quote(
        symbol="AAPL", price=102.0, change_percent=1.0, source=source,
        event_timestamp=NOW - timedelta(seconds=age_seconds), received_timestamp=NOW,
    )
    quote = LiveQuoteResolver(state=InMemoryMarketStreamState(clock=lambda: 0.0), rest_quote=lambda _: rest, clock=lambda: NOW).resolve("AAPL").payload()
    assert quote["source_mode"] == "fallback" and quote["source"] == source
    result = _ui([_item()], "applyPortfolioLiveQuotes(" + json.dumps([quote]) + "); capture();")
    assert result["items"][0]["live_market"]["source_mode"] == "fallback"
    assert expected in result["cells"][0]
    assert "WebSocket · Live" not in result["cells"][0]


def test_fallback_daily_close_is_stale_even_when_timestamp_is_recent():
    rest = normalized_fallback_quote(
        symbol="AAPL", price=102.0, change_percent=1.0, source="yfinance",
        event_timestamp=NOW - timedelta(seconds=1), received_timestamp=NOW,
        quality_flags=("fallback_provider", "daily_close_not_realtime"),
    )
    quote = LiveQuoteResolver(state=InMemoryMarketStreamState(clock=lambda: 0.0), rest_quote=lambda _: rest, clock=lambda: NOW).resolve("AAPL").payload()
    assert quote["is_stale"]
    result = _ui([_item()], "applyPortfolioLiveQuotes(" + json.dumps([quote]) + "); capture();")
    assert "REST · Stale" in result["cells"][0] and "REST · Fresh" not in result["cells"][0]


def test_unknown_or_purchase_fallback_never_invents_rest_transport():
    rest = normalized_fallback_quote(
        symbol="AAPL", price=90.0, change_percent=None, source="purchase_price",
        event_timestamp=NOW - timedelta(seconds=1), received_timestamp=NOW,
    )
    quote = LiveQuoteResolver(state=InMemoryMarketStreamState(clock=lambda: 0.0), rest_quote=lambda _: rest, clock=lambda: NOW).resolve("AAPL").payload()
    result = _ui([_item()], "applyPortfolioLiveQuotes(" + json.dumps([quote]) + "); capture();")
    assert "Last known · Status unknown" in result["cells"][0]
    assert "REST ·" not in result["cells"][0] and "WebSocket · Live" not in result["cells"][0]


def test_actual_initial_entry_basis_metadata_is_not_presented_as_market_last_known():
    from moneybot import api

    metadata = api._initial_live_quote_metadata(
        "AAPL", {"price": None, "source": "massive", "source_mode": "rest", "event_timestamp": None},
        displayed_price=90.0,
    )
    item = {**_item(), "current_price": 90.0, "live_market": metadata}
    result = _ui([item], "startPortfolioLive(); capture();")
    assert "entry_price_fallback" in metadata["quality_flags"]
    assert "Price unavailable" in result["cells"][0]
    assert "Cost basis: $90" in result["cells"][0]
    assert "Last known · Stale" not in result["cells"][0] and "REST · Stale" not in result["cells"][0]
    assert result["items"][0]["current_price"] == 90.0
    assert "Prices: 1 unavailable" in result["status"]


@pytest.mark.parametrize("source_mode, expected", [("rest", "REST · Stale"), ("websocket", "Last known · Stale")])
def test_heartbeat_ages_fresh_price_when_quote_delivery_stalls(source_mode, expected):
    quote = {**_quote(), "source_mode": source_mode, "is_degraded": False}
    scenario = (
        "startPortfolioLive(); portfolioEventSource.emit('quotes', " + json.dumps({"quotes": [quote]})
        + "); const fresh = capture(); Date.now = () => " + str(int(NOW.timestamp() * 1000 + 14_001))
        + "; portfolioEventSource.emit('heartbeat', " + json.dumps({"provider_health": _provider_health()})
        + "); ({fresh, aged:capture()});"
    )
    result = _ui([_item()], scenario)
    assert "Prices: 1 fresh" in result["fresh"]["status"]
    assert expected in result["aged"]["cells"][0]
    assert "Prices: 1 stale; 1 degraded" in result["aged"]["status"]
    assert "Provider stream connected" in result["aged"]["status"]
    assert result["aged"]["items"][0]["current_price"] == 102
    assert result["aged"]["loadCount"] == 0 and result["aged"]["timerCount"] == 0
    assert result["aged"]["renderCount"] == result["fresh"]["renderCount"]
