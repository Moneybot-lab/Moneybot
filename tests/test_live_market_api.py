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
