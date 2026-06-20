import os

from moneybot.app_factory import create_app


class StubSuitabilityAdvisor:
    def predict_portfolio_position(self, *, symbol, entry_price, current_price, shares, signal_data, quote_data):
        return {
            "mode": "deterministic_model",
            "symbol": symbol,
            "advice": "BUY",
            "advice_reason": f"Deterministic portfolio signal for {symbol}.",
            "decision_source": "deterministic_model",
            "model_version": "alpha-atlas-v1",
            "probability_up": 0.71,
            "confidence": 71.0,
        }


def _client():
    os.environ["MONEYBOT_SECRET_KEY"] = "test-secret"
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    return create_app().test_client()


def _signup(client, *, email="profile@example.com", username="profile_user"):
    response = client.post(
        "/api/auth/signup",
        json={
            "name": "Profile User",
            "username": username,
            "email": email,
            "password": "pw123",
            "password_confirmation": "pw123",
        },
    )
    assert response.status_code == 201


def _complete_profile_payload(version=1):
    return {
        "profile_version": version,
        "primary_goal": "growth",
        "time_horizon_years": 10,
        "risk_tolerance": "moderate",
        "loss_capacity_percent": 25,
        "liquidity_need": "low",
        "experience_level": "intermediate",
        "account_type": "roth ira",
        "position_size_limit_percent": 12.5,
        "sector_limit_percent": 30,
        "excluded_sectors": ["Tobacco", "  Gambling  ", "tobacco"],
        "penny_stocks_allowed": False,
        "after_hours_alerts": True,
        "recommendation_style": "balanced",
        "change_reason": "Completed onboarding questionnaire",
    }


def test_investor_profile_requires_authentication():
    client = _client()

    get_response = client.get("/api/me/investor-profile")
    update_response = client.put("/api/me/investor-profile", json={"profile_version": 1, "primary_goal": "growth"})
    revisions_response = client.get("/api/me/investor-profile/revisions")

    assert get_response.status_code == 401
    assert update_response.status_code == 401
    assert revisions_response.status_code == 401


def test_get_investor_profile_creates_incomplete_profile_with_safe_effective_defaults():
    client = _client()
    _signup(client)

    response = client.get("/api/me/investor-profile")

    assert response.status_code == 200
    profile = response.get_json()["profile"]
    assert profile["profile_version"] == 1
    assert profile["profile_complete"] is False
    assert profile["primary_goal"] is None
    assert "risk_tolerance" in profile["missing_fields"]
    assert profile["effective_profile"]["risk_tolerance"] == "conservative"
    assert profile["effective_profile"]["position_size_limit_percent"] == 5.0
    assert profile["effective_profile"]["penny_stocks_allowed"] is False


def test_update_investor_profile_normalizes_values_completes_profile_and_records_revision():
    client = _client()
    _signup(client)
    client.get("/api/me/investor-profile")

    response = client.put("/api/me/investor-profile", json=_complete_profile_payload())

    assert response.status_code == 200
    profile = response.get_json()["profile"]
    assert profile["profile_version"] == 2
    assert profile["profile_complete"] is True
    assert profile["missing_fields"] == []
    assert profile["account_type"] == "roth_ira"
    assert profile["excluded_sectors"] == ["tobacco", "gambling"]
    assert profile["position_size_limit_percent"] == 12.5
    assert profile["questionnaire_completed_at"] is not None

    revisions_response = client.get("/api/me/investor-profile/revisions")
    assert revisions_response.status_code == 200
    revisions = revisions_response.get_json()["items"]
    assert len(revisions) == 1
    assert revisions[0]["profile_version"] == 2
    assert revisions[0]["previous_profile"]["primary_goal"] is None
    assert revisions[0]["new_profile"]["primary_goal"] == "growth"
    assert revisions[0]["change_reason"] == "Completed onboarding questionnaire"
    assert revisions[0]["source"] == "settings"


def test_update_investor_profile_rejects_invalid_and_unknown_fields():
    client = _client()
    _signup(client)

    response = client.put(
        "/api/me/investor-profile",
        json={
            "profile_version": 1,
            "risk_tolerance": "maximum",
            "time_horizon_years": 0,
            "penny_stocks_allowed": "yes",
            "secret_score": 99,
        },
    )

    assert response.status_code == 400
    data = response.get_json()
    assert data["error"] == "invalid investor profile"
    assert set(data["fields"]) == {
        "penny_stocks_allowed",
        "risk_tolerance",
        "time_horizon_years",
        "unknown_fields",
    }


def test_update_investor_profile_requires_current_version_and_returns_latest_profile():
    client = _client()
    _signup(client)
    first = client.put(
        "/api/me/investor-profile",
        json={"profile_version": 1, "primary_goal": "income"},
    )
    assert first.status_code == 200

    stale = client.put(
        "/api/me/investor-profile",
        json={"profile_version": 1, "primary_goal": "growth"},
    )

    assert stale.status_code == 409
    data = stale.get_json()
    assert data["error"] == "investor profile version conflict"
    assert data["current_profile"]["profile_version"] == 2
    assert data["current_profile"]["primary_goal"] == "income"


def test_no_op_profile_update_does_not_increment_version_or_add_revision():
    client = _client()
    _signup(client)
    first = client.put(
        "/api/me/investor-profile",
        json={"profile_version": 1, "primary_goal": "income"},
    )
    assert first.status_code == 200

    no_op = client.put(
        "/api/me/investor-profile",
        json={"profile_version": 2, "primary_goal": "income"},
    )

    assert no_op.status_code == 200
    assert no_op.get_json()["profile"]["profile_version"] == 2
    revisions = client.get("/api/me/investor-profile/revisions").get_json()["items"]
    assert len(revisions) == 1


def test_profile_revisions_are_isolated_to_authenticated_user():
    first_client = _client()
    _signup(first_client, email="first@example.com", username="first_user")
    update = first_client.put(
        "/api/me/investor-profile",
        json={"profile_version": 1, "primary_goal": "growth"},
    )
    assert update.status_code == 200

    second_client = _client()
    _signup(second_client, email="second@example.com", username="second_user")

    response = second_client.get("/api/me/investor-profile/revisions")

    assert response.status_code == 200
    assert response.get_json()["items"] == []


def test_incomplete_profile_softens_portfolio_buy_and_exposes_policy_trace():
    client = _client()
    client.application.extensions["deterministic_quick_advisor"] = StubSuitabilityAdvisor()
    client.application.extensions["ai_advisor_service"] = None
    captured_events = []

    class CapturingDecisionLogger:
        def log(self, **kwargs):
            captured_events.append(kwargs)

    client.application.extensions["decision_logger"] = CapturingDecisionLogger()
    client.application.extensions["market_data_service"].get_sector = lambda symbol: "Technology"
    client.application.extensions["market_data_service"].get_quote = lambda symbol: {
        "symbol": symbol,
        "price": 150.0,
        "change_percent": 6.0,
        "live_data_available": True,
        "quote_source": "test",
        "diagnostics": {"provider": "test", "error": None},
    }
    _signup(client, email="suitability@example.com", username="suitability_user")
    added = client.post("/api/user-watchlist", json={"symbol": "AAPL", "buy_price": 100, "shares": 1})
    assert added.status_code == 201

    response = client.get("/api/user-watchlist")

    assert response.status_code == 200
    item = response.get_json()["enriched_items"][0]
    assert item["base_advice"] == "BUY"
    assert item["advice"] == "HOLD"
    assert item["profile_version"] == 1
    assert item["profile_complete"] is False
    assert item["suitability"]["changed"] is True
    assert "position_limit_reached" in [rule["code"] for rule in item["suitability"]["applied_rules"]]
    assert "Profile adjustment:" in item["advice_reason"]
    assert len(captured_events) == 1
    logged = captured_events[0]
    assert logged["payload"]["profile_version"] == 1
    assert logged["payload"]["suitability_changed"] is True
    assert logged["snapshot"]["personalization"]["profile_version"] == 1
    assert "primary_goal" not in logged["snapshot"]["personalization"]


def test_completed_aggressive_profile_can_preserve_portfolio_buy():
    client = _client()
    client.application.extensions["deterministic_quick_advisor"] = StubSuitabilityAdvisor()
    client.application.extensions["ai_advisor_service"] = None
    client.application.extensions["market_data_service"].get_sector = lambda symbol: "Technology" if symbol == "AAPL" else "Software"
    client.application.extensions["market_data_service"].get_quote = lambda symbol: {
        "symbol": symbol,
        "price": 150.0,
        "change_percent": 6.0,
        "live_data_available": True,
        "quote_source": "test",
        "diagnostics": {"provider": "test", "error": None},
    }
    _signup(client, email="aggressive@example.com", username="aggressive_user")
    profile_response = client.put(
        "/api/me/investor-profile",
        json={
            "profile_version": 1,
            "primary_goal": "growth",
            "time_horizon_years": 10,
            "risk_tolerance": "aggressive",
            "loss_capacity_percent": 50,
            "liquidity_need": "low",
            "experience_level": "advanced",
            "account_type": "taxable",
            "position_size_limit_percent": 60,
            "sector_limit_percent": 80,
            "penny_stocks_allowed": True,
            "after_hours_alerts": True,
            "recommendation_style": "opportunity_seeking",
        },
    )
    assert profile_response.status_code == 200
    assert client.post("/api/user-watchlist", json={"symbol": "AAPL", "buy_price": 100, "shares": 1}).status_code == 201
    assert client.post("/api/user-watchlist", json={"symbol": "MSFT", "buy_price": 100, "shares": 1}).status_code == 201

    response = client.get("/api/user-watchlist")

    assert response.status_code == 200
    items = response.get_json()["enriched_items"]
    assert len(items) == 2
    assert all(item["base_advice"] == "BUY" for item in items)
    assert all(item["advice"] == "BUY" for item in items)
    assert all(item["suitability"]["changed"] is False for item in items)
    assert all(item["profile_version"] == 2 for item in items)
    assert all(item["profile_complete"] is True for item in items)


def test_authenticated_quick_ask_uses_shared_personalization_contract():
    client = _client()
    client.application.extensions["deterministic_quick_advisor"] = None
    client.application.extensions["market_data_service"].get_signal = lambda symbol: {
        "symbol": symbol,
        "action": "BUY",
        "technical": {"rsi": 25, "macd_histogram": 0.2},
        "sentiment": {"score": 0.8, "label": "positive"},
        "quote": {"symbol": symbol, "price": 150.0, "change_percent": 2.0, "quote_source": "test"},
    }
    client.application.extensions["market_data_service"].get_price_history = lambda symbol, days=30: [140, 145, 150]
    _signup(client, email="quick-profile@example.com", username="quick_profile")

    response = client.get("/api/quick-ask?symbol=AAPL")

    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["recommendation"] in {"BUY", "STRONG BUY"}
    assert data["personalization"]["base_action"] == "BUY"
    assert data["personalization"]["action"] == "HOLD"
    assert data["personalization"]["forecast_horizon"] == "short_term"
    assert data["personalization"]["policy_schema_version"] == "suitability.v1"
    assert data["market_data_provenance"]["quote_source"] == "test"
    assert data["market_data_provenance"]["mixed_sources"] is True


def test_model_health_reports_profile_counts_and_policy_metrics():
    client = _client()
    _signup(client, email="health-profile@example.com", username="health_profile")
    client.get("/api/me/investor-profile")

    response = client.get("/api/model-health")

    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["investor_profile_enabled"] is True
    assert data["suitability_policy_enabled"] is True
    assert data["profile_counts"] == {"total": 1, "complete": 0, "incomplete": 1}
    assert "evaluations" in data["personalization_metrics"]


def test_revision_history_prunes_records_outside_configured_retention():
    from datetime import datetime, timedelta

    from moneybot.extensions import db
    from moneybot.models import InvestorProfileRevision, User

    client = _client()
    client.application.config["INVESTOR_PROFILE_REVISION_RETENTION_DAYS"] = 30
    _signup(client, email="retention@example.com", username="retention_user")
    update = client.put(
        "/api/me/investor-profile",
        json={"profile_version": 1, "primary_goal": "growth"},
    )
    assert update.status_code == 200

    with client.application.app_context():
        user = User.query.filter_by(email="retention@example.com").one()
        revision = InvestorProfileRevision.query.filter_by(user_id=user.id).one()
        revision.created_at = datetime.utcnow() - timedelta(days=31)
        db.session.commit()

    response = client.get("/api/me/investor-profile/revisions")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["retention_days"] == 30
    assert payload["pruned_count"] == 1
    assert payload["items"] == []


def test_portfolio_aggregates_duplicate_sectors_with_partial_holdings():
    client = _client()
    client.application.extensions["deterministic_quick_advisor"] = StubSuitabilityAdvisor()
    client.application.extensions["ai_advisor_service"] = None
    client.application.extensions["market_data_service"].get_sector = lambda symbol: "Technology"
    client.application.extensions["market_data_service"].get_quote = lambda symbol: {
        "symbol": symbol,
        "price": 100.0,
        "change_percent": 1.0,
        "live_data_available": True,
        "quote_source": "test",
        "diagnostics": {"provider": "test", "error": None},
    }
    _signup(client, email="sector-aggregate@example.com", username="sector_aggregate")
    profile = client.put(
        "/api/me/investor-profile",
        json={
            "profile_version": 1,
            "primary_goal": "growth",
            "time_horizon_years": 10,
            "risk_tolerance": "aggressive",
            "loss_capacity_percent": 50,
            "liquidity_need": "low",
            "experience_level": "advanced",
            "account_type": "taxable",
            "position_size_limit_percent": 90,
            "sector_limit_percent": 80,
            "penny_stocks_allowed": True,
            "after_hours_alerts": True,
            "recommendation_style": "opportunity_seeking",
        },
    )
    assert profile.status_code == 200
    assert client.post("/api/user-watchlist", json={"symbol": "AAPL", "buy_price": 100, "shares": 0.5}).status_code == 201
    assert client.post("/api/user-watchlist", json={"symbol": "MSFT", "buy_price": 100, "shares": 1.5}).status_code == 201

    response = client.get("/api/user-watchlist")

    assert response.status_code == 200
    items = response.get_json()["enriched_items"]
    assert {item["position_weight_percent"] for item in items} == {25.0, 75.0}
    assert all(item["sector"] == "Technology" for item in items)
    assert all(item["sector_weight_percent"] == 100.0 for item in items)
    assert all(item["weight_basis"] == "invested_positions_only_cash_excluded" for item in items)


def test_quick_ask_uses_rest_snapshot_and_only_portfolio_registers_stream_demand():
    client = _client()
    client.application.extensions["deterministic_quick_advisor"] = None
    client.application.extensions["market_data_service"].get_signal = lambda symbol: {
        "symbol": symbol, "action": "HOLD", "score": 5.0,
        "quote": {"symbol": symbol, "price": 100.0, "change_percent": 0.0, "quote_source": "test"},
    }
    client.application.extensions["market_data_service"].get_price_history_data = lambda symbol, days=30: {
        "symbol": symbol, "closes": [99.0, 100.0], "source": "test", "source_mode": "fallback", "schema_version": "market-data.v1",
    }
    _signup(client, email="stream-demand@example.com", username="stream_demand")
    assert client.post("/api/user-watchlist", json={"symbol": "MSFT", "buy_price": 100, "shares": 1}).status_code == 201

    assert client.get("/api/quick-ask?symbol=AAPL").status_code == 200
    assert client.get("/api/user-watchlist").status_code == 200

    demand = client.application.extensions["market_stream_state"].desired_demand()
    assert not any(source.startswith("quick:") for source in demand)
    assert any(symbols == {"MSFT"} for source, symbols in demand.items() if source.startswith("portfolio:"))


def test_market_stream_health_requires_auth_and_returns_shadow_status():
    client = _client()
    assert client.get("/api/market-stream-health").status_code == 401
    _signup(client, email="stream-health@example.com", username="stream_health")
    state = client.application.extensions["market_stream_state"]
    state.set_health({"connection_state": "connected", "metrics": {"parse_failures": 0}}, ttl_seconds=30)

    response = client.get("/api/market-stream-health")

    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["shadow_mode"] is True
    assert data["worker_state"] == "connected"
    assert "no market event has arrived yet" in data["diagnosis"]
    assert data["worker"]["connection_state"] == "connected"
