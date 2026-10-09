import os
from datetime import datetime, timezone

from moneybot.app_factory import create_app
from moneybot.extensions import db
from moneybot.models import WatchlistItem
from moneybot.services.market_stream import InMemoryMarketStreamState, StreamEvent


def _app():
    os.environ["MONEYBOT_SECRET_KEY"] = "test-secret"
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    app = create_app()
    app.config.update(TESTING=True, LIVE_SSE_HEARTBEAT_SECONDS=2)
    return app


def _signup(client):
    response = client.post("/api/auth/signup", json={
        "name": "Live User", "username": "live_user", "email": "live@example.com",
        "password": "pw123", "password_confirmation": "pw123",
    })
    assert response.status_code == 201


def test_live_stream_requires_login_and_rejects_unowned_portfolio_symbol():
    app = _app()
    client = app.test_client()
    assert client.get("/api/live-market-stream?symbols=AAPL&once=1").status_code == 401
    _signup(client)
    assert client.get("/api/live-market-stream?symbols=AAPL&once=1").status_code == 400


def test_live_stream_emits_ready_quotes_heartbeat_and_cleans_demand():
    app = _app()
    client = app.test_client()
    _signup(client)
    with app.app_context():
        user_id = 1
        db.session.add(WatchlistItem(user_id=user_id, symbol="AAPL", buy_price=100, shares=2))
        db.session.commit()
    now = datetime.now(timezone.utc)
    state = InMemoryMarketStreamState()
    state.set_latest(StreamEvent(
        event_type="T", symbol="AAPL", event_timestamp=now, received_timestamp=now,
        sequence_number=7, provider_event_id="trade-7", payload={"price": 201.5}, quality_flags=(),
    ), ttl_seconds=120)
    app.extensions["market_stream_state"] = state

    response = client.get("/api/live-market-stream?symbols=AAPL&once=1", headers={"Last-Event-ID": "AAPL:T:6"})
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    assert "event: ready" in body and '"resume_from":"AAPL:T:6"' in body
    assert "event: quotes" in body and '"price":201.5' in body
    assert "event: heartbeat" in body
    assert state.desired_demand() == {}


def test_quick_scope_allows_one_requested_symbol_but_enforces_connection_cap():
    app = _app()
    app.config["LIVE_SSE_SYMBOL_CAP"] = 1
    client = app.test_client()
    _signup(client)

    response = client.get("/api/live-market-stream?scope=quick&symbols=MSFT,NVDA&once=1")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert '"symbols":["MSFT"]' in body
    assert "NVDA" not in body


def test_initial_live_metadata_preserves_lots_accounting_and_existing_quote_call_budget():
    app = _app()
    client = app.test_client()
    _signup(client)
    quote_calls = []
    now = datetime.now(timezone.utc)

    class Quotes:
        available = True

        def get_quote(self, symbol):
            quote_calls.append(symbol)
            return {
                "symbol": symbol, "price": 150 if self.available else None, "change_percent": 0,
                "quote_source": "massive", "source_mode": "rest",
                "event_timestamp": now.isoformat(), "received_timestamp": now.isoformat(),
                "live_data_available": True, "is_stale": False, "quality_flags": [],
            }

        def get_price_history_data(self, symbol, days):
            return {"closes": [150], "bars": [], "source": "synthetic"}

        def get_sector(self, symbol):
            return "Technology"

    app.extensions.update(market_data_service=Quotes(), ai_advisor_service=None, deterministic_quick_advisor=None, decision_logger=None)
    first = client.post("/api/user-watchlist", json={"symbol": "AAPL", "buy_price": 100, "shares": 2}).get_json()["item"]
    second = client.post("/api/user-watchlist", json={"symbol": "AAPL", "buy_price": 125, "shares": 1}).get_json()["item"]
    response = client.get("/api/user-watchlist")
    assert response.status_code == 200
    enriched = response.get_json()["enriched_items"]
    assert len(enriched) == 2 and {item["id"] for item in enriched} == {first["id"], second["id"]}
    assert quote_calls == ["AAPL", "AAPL"]  # Existing per-lot budget, no status fetch.
    assert all(item["current_price"] == 150 and item["live_market"]["source_mode"] == "rest" for item in enriched)
    assert {item["performance_amount"] for item in enriched} == {100.0, 25.0}
    summary = client.get("/api/portfolio-summary").get_json()
    assert summary["unrealized_gain_loss"] == 125 and summary["realized_gain_loss"] == 0
    assert summary["lifetime_gain_loss"] == 125 and summary["position_totals"][0]["open_shares"] == 3
    app.extensions["market_data_service"].available = False
    unavailable = client.get("/api/user-watchlist").get_json()["enriched_items"]
    assert {item["current_price"] for item in unavailable} == {100.0, 125.0}  # Existing bookkeeping basis fallback.
    assert all(item["live_market"]["price"] is None and item["live_market"]["is_stale"] for item in unavailable)
    assert all("price_unavailable" in item["live_market"]["quality_flags"] for item in unavailable)
    assert all("entry_price_fallback" in item["live_market"]["quality_flags"] for item in unavailable)
    assert all(item["live_market"]["source"] == "portfolio_entry_price" and item["live_market"]["observed_source"] == "massive" for item in unavailable)
    assert all(item["live_market"]["source_mode"] == "last_known" and item["live_market"]["event_timestamp"] is None for item in unavailable)
