import os

from moneybot.app_factory import create_app
from moneybot import api as api_module




class StubAIAdvisorService:
    def enhance_quick_decision(self, *, symbol, quick_decision, signal_data, quote_data):
        return {
            "mode": "ai_enhanced",
            "narrative": f"Aggressive signal for {symbol}: {quick_decision['recommendation']}",
            "risk_notes": ["Use strict stops.", "Expect high volatility."],
            "next_checks": ["Watch volume.", "Re-check sentiment."],
            "provider": "stub",
            "model": "stub-fast",
        }

class StubMarketService:
    def get_market_indices(self):
        return [{"name": "Dow Jones", "symbol": "^DJI", "price": 39000.0, "change_percent": 0.4, "series": [1, 2, 3]}]

    def get_stable_watchlist(self):
        return [{"symbol": "MSFT", "company": "Microsoft", "price": 420.12, "signal_score": 8.0}]

    def get_hot_momentum_buys(self):
        return [{"symbol": "NVDA", "price": 900.33, "score": 9.4, "rationale": "Strong breakout"}]

    def get_wells_picks(self):
        return [{"investor": "Warren Buffett", "stocks": [{"ticker": "AAPL", "price": 190.0, "performance": 1.2}]}]

    def get_quote(self, symbol):
        return {"symbol": symbol, "price": 150.25, "change_percent": 1.2, "quote_source": "finnhub", "diagnostics": {"provider": "finnhub", "error": None}}

    def get_signal(self, symbol):
        return {
            "symbol": symbol,
            "action": "HOLD",
            "technical": {"rsi": 52, "macd_histogram": 0.18},
            "sentiment": {"label": "positive", "score": 0.62},
            "quote": self.get_quote(symbol),
        }

    def get_company_snapshot(self, symbol):
        return {"symbol": symbol, "company_name": f"{symbol} Corp", "summary": "Test summary."}


def _client():
    os.environ["MONEYBOT_SECRET_KEY"] = "test-secret"
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    os.environ.pop("SMTP_HOST", None)
    os.environ.pop("SMTP_USER", None)
    os.environ.pop("PASSWORD_RESET_FROM_EMAIL", None)
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
    assert wells.get_json()["items"][0]["stocks"][0]["ticker"] == "AAPL"


def test_quick_ask_returns_shopping_friendly_recommendation_scale():
    client = _client()
    res = client.get("/api/quick-ask?symbol=AAPL")
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["recommendation"] in {"STRONG BUY", "BUY", "HOLD OFF FOR NOW"}
    assert data["recommendation"] == "BUY"
    assert "Momentum" in data["rationale"] or "signal" in data["rationale"]
    assert data["quote_source"] == "finnhub"
    assert data["quote_diagnostics"]["provider"] == "finnhub"


def test_quick_ask_normalizes_symbol_from_url_like_input():
    client = _client()
    res = client.get('/api/quick-ask?symbol=%2Fapi%2Fquote%3Fsymbol%3DTSLA')
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["symbol"] == "TSLA"


def test_quick_ask_includes_ai_fallback_payload_when_ai_not_configured():
    client = _client()
    res = client.get("/api/quick-ask?symbol=AAPL")
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["ai"]["mode"] == "rule_based"
    assert data["ai_status"] == "fallback"
    assert data["ai_mode"] == "rule_based"
    assert "not financial advice" in data["ai"]["risk_notes"][1].lower()


def test_quick_ask_uses_ai_extension_when_present():
    client = _client()
    client.application.extensions["ai_advisor_service"] = StubAIAdvisorService()

    res = client.get("/api/quick-ask?symbol=TSLA")
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["ai"]["mode"] == "ai_enhanced"
    assert data["ai_status"] == "working"
    assert data["ai_mode"] == "ai_enhanced"
    assert data["ai"]["provider"] == "stub"
    assert "TSLA" in data["ai"]["narrative"]


def test_explain_recommendation_returns_plain_english_text():
    client = _client()
    res = client.post(
        "/api/explain-recommendation",
        json={"recommendation": "BUY", "reason": "Momentum and sentiment both look positive."},
    )
    assert res.status_code == 200
    explanation = res.get_json()["data"]["explanation"]
    assert "good time to buy" in explanation.lower()
    assert "plain english" in explanation.lower()


def test_company_details_is_accessible_without_authentication():
    client = _client()
    res = client.get("/api/company-details?symbol=AAPL")
    assert res.status_code == 200
    payload = res.get_json()["data"]
    assert payload["symbol"] == "AAPL"


def test_signup_rejects_mismatched_password_confirmation():
    client = _client()
    signup = client.post(
        "/api/auth/signup",
        json={"email": "mismatch@b.com", "password": "pw1", "password_confirmation": "pw2"},
    )
    assert signup.status_code == 400
    assert signup.get_json()["error"] == "passwords do not match"


def test_user_watchlist_exposes_quote_source_diagnostics():
    client = _client()
    signup = client.post("/api/auth/signup", json={"email": "a@b.com", "password": "pw", "password_confirmation": "pw"})
    assert signup.status_code == 201
    add = client.post("/api/user-watchlist", json={"symbol": "AAPL", "buy_price": 100, "shares": 1})
    assert add.status_code == 201

    res = client.get("/api/user-watchlist")
    assert res.status_code == 200
    enriched = res.get_json()["enriched_items"][0]
    assert enriched["quote_source"] == "finnhub"
    assert enriched["quote_diagnostics"]["provider"] == "finnhub"


def test_forgot_password_returns_generic_success_message():
    client = _client()
    signup = client.post("/api/auth/signup", json={"email": "recover@b.com", "password": "pw", "password_confirmation": "pw"})
    assert signup.status_code == 201

    res = client.post("/api/auth/forgot-password", json={"email": "recover@b.com"})
    assert res.status_code == 200
    payload = res.get_json()
    assert payload["ok"] is True
    assert "If an account exists" in payload["message"]
    assert payload["email_delivery_configured"] is False


def test_forgot_password_requires_email():
    client = _client()
    res = client.post("/api/auth/forgot-password", json={})
    assert res.status_code == 400
    assert res.get_json()["error"] == "email required"


def test_sell_watchlist_item_records_realized_gain_and_reduces_shares():
    client = _client()
    signup = client.post("/api/auth/signup", json={"email": "sell@b.com", "password": "pw", "password_confirmation": "pw"})
    assert signup.status_code == 201

    add = client.post("/api/user-watchlist", json={"symbol": "AAPL", "buy_price": 100, "shares": 10})
    assert add.status_code == 201
    item_id = add.get_json()["item"]["id"]

    sell = client.post(f"/api/user-watchlist/{item_id}/sell", json={"sold_price": 120, "shares_sold": 4})
    assert sell.status_code == 200
    payload = sell.get_json()
    assert payload["removed"] is False
    assert payload["remaining_item"]["shares"] == 6.0
    assert payload["sold_trade"]["realized_amount"] == 80.0

    watchlist = client.get("/api/user-watchlist")
    assert watchlist.status_code == 200
    assert watchlist.get_json()["items"][0]["shares"] == 6.0

    sold_trades = client.get("/api/sold-trades")
    assert sold_trades.status_code == 200
    sold_payload = sold_trades.get_json()
    assert sold_payload["total_realized"] == 80.0
    assert sold_payload["items"][0]["symbol"] == "AAPL"


def test_sell_watchlist_item_removes_position_when_all_shares_are_sold():
    client = _client()
    signup = client.post("/api/auth/signup", json={"email": "sellall@b.com", "password": "pw", "password_confirmation": "pw"})
    assert signup.status_code == 201

    add = client.post("/api/user-watchlist", json={"symbol": "TSLA", "buy_price": 200, "shares": 2})
    assert add.status_code == 201
    item_id = add.get_json()["item"]["id"]

    sell = client.post(f"/api/user-watchlist/{item_id}/sell", json={"sold_price": 180, "shares_sold": 2})
    assert sell.status_code == 200
    payload = sell.get_json()
    assert payload["removed"] is True
    assert payload["remaining_item"] is None
    assert payload["sold_trade"]["realized_amount"] == -40.0

    watchlist = client.get("/api/user-watchlist")
    assert watchlist.status_code == 200
    assert watchlist.get_json()["items"] == []


def test_sell_watchlist_item_rejects_selling_more_than_owned():
    client = _client()
    signup = client.post("/api/auth/signup", json={"email": "over@b.com", "password": "pw", "password_confirmation": "pw"})
    assert signup.status_code == 201

    add = client.post("/api/user-watchlist", json={"symbol": "MSFT", "buy_price": 50, "shares": 1})
    assert add.status_code == 201
    item_id = add.get_json()["item"]["id"]

    sell = client.post(f"/api/user-watchlist/{item_id}/sell", json={"sold_price": 55, "shares_sold": 2})
    assert sell.status_code == 400
    assert sell.get_json()["error"] == "shares_sold cannot exceed current shares"


def test_forgot_password_sends_reset_email_for_existing_user(monkeypatch):
    client = _client()
    signup = client.post("/api/auth/signup", json={"email": "mailer@b.com", "password": "pw", "password_confirmation": "pw"})
    assert signup.status_code == 201

    captured = {}

    def fake_send_reset_email(email, reset_link):
        captured["email"] = email
        captured["reset_link"] = reset_link
        return True

    monkeypatch.setattr(api_module, "_send_reset_email", fake_send_reset_email)

    res = client.post("/api/auth/forgot-password", json={"email": "mailer@b.com"})
    assert res.status_code == 200
    assert captured["email"] == "mailer@b.com"
    assert "reset-password" in captured["reset_link"] and "token=" in captured["reset_link"]


def test_reset_password_updates_credentials_and_allows_login():
    client = _client()
    signup = client.post("/api/auth/signup", json={"email": "reset@b.com", "password": "oldpw", "password_confirmation": "oldpw"})
    assert signup.status_code == 201

    with client.application.app_context():
        user = api_module.User.query.filter_by(email="reset@b.com").first()
        token = api_module._password_reset_serializer().dumps({"user_id": user.id})

    reset = client.post("/api/auth/reset-password", json={"token": token, "password": "newpw"})
    assert reset.status_code == 200

    old_login = client.post("/api/auth/login", json={"email": "reset@b.com", "password": "oldpw"})
    assert old_login.status_code == 401

    new_login = client.post("/api/auth/login", json={"email": "reset@b.com", "password": "newpw"})
    assert new_login.status_code == 200


def test_password_reset_email_config_helper_reads_runtime_config():
    os.environ["MONEYBOT_SECRET_KEY"] = "test-secret"
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["SMTP_HOST"] = "smtp.example.com"
    os.environ["PASSWORD_RESET_FROM_EMAIL"] = "noreply@example.com"
    app = create_app()

    with app.app_context():
        assert api_module._password_reset_email_configured() is True
