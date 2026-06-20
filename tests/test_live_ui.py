import os

from moneybot.app_factory import create_app


def _client():
    os.environ["MONEYBOT_SECRET_KEY"] = "test-secret"
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    return create_app().test_client()


def test_home_quick_ask_uses_rest_without_live_status_noise_and_keeps_profile_adjustment_ui():
    client = _client()
    html = client.get("/").get_data(as_text=True)
    js = client.get("/static/js/home.js").get_data(as_text=True)
    assert 'id="quickLiveStatus"' not in html
    assert "new EventSource('/api/live-market-stream?scope=quick" not in js
    assert "price uses a REST snapshot" not in js
    assert "Profile adjusted" in js
    assert 'href="/settings"' in js


def test_portfolio_has_live_price_pnl_reconnect_and_controlled_refresh_ui():
    client = _client()
    html = client.get("/portfolio").get_data(as_text=True)
    assert 'id="portfolioLiveStatus"' in html
    assert "new EventSource('/api/live-market-stream?scope=portfolio" in html
    assert "applyPortfolioLiveQuotes" in html
    assert "item.performance_amount = (quote.price - item.entry_price) * shares" in html
    assert "recommendation_refresh" in html
    assert "without generating a new AI narrative" in html
