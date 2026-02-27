import os

from moneybot.app_factory import create_app


class StubMarketService:
    def get_market_indices(self):
        return [{"name": "Dow Jones", "symbol": "^DJI", "price": 39000.0, "change_percent": 0.4, "series": [1, 2, 3]}]

    def get_stable_watchlist(self):
        return [{"symbol": "MSFT", "company": "Microsoft", "price": 420.12, "signal_score": 8.0}]

    def get_hot_momentum_buys(self):
        return [{"symbol": "NVDA", "price": 900.33, "score": 9.4, "rationale": "Strong breakout"}]

    def get_wells_picks(self):
        return [{"investor": "Warren Buffett", "top_stocks": ["AAPL", "AXP", "KO", "OXY", "BAC"]}]


def _client():
    os.environ["MONEYBOT_SECRET_KEY"] = "test-secret"
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    app = create_app()
    app.extensions["market_data_service"] = StubMarketService()
    return app.test_client()


def test_market_overview_endpoint_returns_items():
    client = _client()
    res = client.get("/api/market-overview")
    assert res.status_code == 200
    data = res.get_json()
    assert data["items"][0]["symbol"] == "^DJI"


def test_tab_data_endpoints_return_items():
    client = _client()

    stable = client.get("/api/stable-watchlist")
    momentum = client.get("/api/hot-momentum-buys")
    wells = client.get("/api/wells-picks")

    assert stable.status_code == 200
    assert momentum.status_code == 200
    assert wells.status_code == 200

    assert stable.get_json()["items"][0]["symbol"] == "MSFT"
    assert momentum.get_json()["items"][0]["symbol"] == "NVDA"
    assert wells.get_json()["items"][0]["investor"] == "Warren Buffett"
