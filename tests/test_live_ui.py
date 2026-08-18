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
    assert 'id="quickProfileNote"' in html
    assert "Profile adjusted" in js
    assert "The market signal still matters" in js
    assert "risk tolerance, time horizon, or suitability guardrails" in js
    assert 'href="/settings"' in js


def test_market_cards_format_index_levels_and_usd_instruments_with_absolute_change():
    client = _client()
    js = client.get("/static/js/home.js").get_data(as_text=True)
    assert "function marketValue(item, value)" in js
    assert "function marketChange(item)" in js
    assert "item?.instrument_type !== 'index'" in js
    assert "${marketChange(item)}" in js
    assert "label:(context)=>marketValue(item, context.parsed.y)" in js
    assert "if(value === null || value === undefined || value === '') return 'Unavailable'" in js
    for stale_value in ("39210.4", "5245.1", "16592.3", "2340.8", "78.42", "61110.2"):
        assert stale_value not in js


def test_portfolio_has_live_price_pnl_reconnect_and_controlled_refresh_ui():
    client = _client()
    html = client.get("/portfolio").get_data(as_text=True)
    assert 'id="portfolioLiveStatus"' in html
    assert "new EventSource('/api/live-market-stream?scope=portfolio" in html
    assert "applyPortfolioLiveQuotes" in html
    assert "item.performance_amount = (quote.price - item.entry_price) * shares" in html
    assert "recommendation_refresh" in html
    assert "without generating a new AI narrative" in html
