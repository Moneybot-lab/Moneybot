"""Synthetic Portfolio regressions: explanations follow the selected action."""

import time
from types import SimpleNamespace

import pytest
import requests

from moneybot import api as api_module
from moneybot.app_factory import create_app
from moneybot.services.ai_advisor import AIAdvisorService

REAL_PERSONALIZE_ACTION = api_module._personalize_action


class Market:
    def get_quote(self, symbol):
        return {"symbol": symbol, "price": 120.0, "change_percent": 6.0,
                "quote_source": "synthetic", "live_data_available": True}

    def get_price_history_data(self, symbol, days=30):
        return {"closes": [100.0, 110.0, 120.0], "bars": [], "source": "synthetic"}

    def get_sector(self, symbol):
        return "Technology"


class Deterministic:
    def __init__(self, action):
        self.action = action

    def predict_portfolio_position(self, **kwargs):
        return {"advice": self.action, "advice_reason": f"{self.action}: authoritative synthetic reason.",
                "decision_source": "deterministic_model", "probability_up": 0.71,
                "confidence": 71.0, "model_version": "synthetic", "pnl_percent": 20.0}


class Commentary:
    def __init__(self, action, mode="ai_enhanced", reason=None):
        self.action, self.mode = action, mode
        self.reason = reason or f"{action}: independent synthetic commentary."

    def enhance_portfolio_position(self, **kwargs):
        return {"advice": self.action, "advice_reason": self.reason, "mode": self.mode,
                "risk_notes": [], "next_checks": [], "provider": "synthetic", "model": "synthetic"}


@pytest.fixture
def portfolio(monkeypatch, tmp_path):
    # These tests never use configured credentials or a real provider.
    for name, value in {"MONEYBOT_SECRET_KEY": "synthetic-test-key", "DATABASE_URL": "sqlite:///:memory:",
                        "MONEYBOT_RUNTIME_DIR": str(tmp_path), "MONEYBOT_PERSISTENT_DATA_DIR": str(tmp_path),
                        "AI_ENABLED": "false", "AI_API_KEY": "", "REDIS_URL": "",
                        "DECISION_LOGGING_ENABLED": "false", "DETERMINISTIC_QUICK_ENABLED": "false",
                        "DETERMINISTIC_CALIBRATION_AUTO_APPLY_PLAN": "false"}.items():
        monkeypatch.setenv(name, value)

    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected live HTTP request in synthetic Portfolio test")

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    app = create_app()
    app.config["TESTING"] = True
    app.extensions["market_data_service"] = Market()
    app.extensions["deterministic_quick_advisor"] = None
    app.extensions["decision_logger"] = None
    api_module._RATE.clear()
    monkeypatch.setattr(api_module, "_quick_decision", lambda *args: {"recommendation": "HOLD"})

    def unchanged_policy(**kwargs):
        action = kwargs["base_action"]
        return SimpleNamespace(action=action, payload=lambda: {"changed": False, "applied_rules": []})

    monkeypatch.setattr(api_module, "_personalize_action", unchanged_policy)
    client = app.test_client()
    assert client.post("/api/auth/signup", json={"name": "Synthetic User", "username": "synthetic",
        "email": "synthetic@example.com", "password": "pw", "password_confirmation": "pw"}).status_code == 201
    assert client.post("/api/user-watchlist", json={"symbol": "AAPL", "buy_price": 100, "shares": 2}).status_code == 201

    def fetch(action, ai):
        app.extensions["deterministic_quick_advisor"] = Deterministic(action) if isinstance(action, str) else action
        app.extensions["ai_advisor_service"] = ai
        response = client.get("/api/user-watchlist")
        assert response.status_code == 200
        row = response.get_json()["enriched_items"][0]
        assert row["score"] == 8.0
        assert row["current_price"] == 120.0
        assert row["performance_percent"] == 20.0
        assert row["performance_amount"] == 40.0
        assert row["position_weight_percent"] == 100.0
        if action:
            assert row["deterministic_portfolio"]["probability_up"] == 0.71
            assert row["deterministic_portfolio"]["confidence"] == 71.0
        return row

    fetch.client = client
    return fetch


@pytest.mark.parametrize("locked,ai", [("HOLD", "SELL"), ("HOLD", "BUY"), ("BUY", "HOLD"),
                                      ("BUY", "SELL"), ("SELL", "BUY"), ("SELL", "HOLD")])
def test_locked_action_retains_its_authoritative_reason(portfolio, locked, ai):
    row = portfolio(locked, Commentary(ai))
    assert row["advice"] == locked
    assert row["advice_reason"] == f"{locked}: authoritative synthetic reason."


def test_locked_reason_survives_conflicting_application_fallback(portfolio):
    row = portfolio("HOLD", Commentary("SELL", mode="rule_based"))
    assert row["advice"] == "HOLD"
    assert row["advice_reason"] == "HOLD: authoritative synthetic reason."


@pytest.mark.parametrize("reason", [None, ""])
def test_missing_locked_reason_does_not_inherit_another_decisions_reason(portfolio, reason):
    class MissingReason(Deterministic):
        def predict_portfolio_position(self, **kwargs):
            payload = super().predict_portfolio_position(**kwargs)
            payload["advice_reason"] = reason
            return payload

    row = portfolio(MissingReason("HOLD"), Commentary("SELL"))
    assert row["advice"] == "HOLD"
    assert row["advice_reason"] == "HOLD: Selected by the existing deterministic portfolio rules."


def test_invalid_deterministic_action_cannot_donate_a_reason(portfolio):
    row = portfolio("INVALID", None)
    assert row["advice"] == "HOLD"
    assert row["advice_reason"] == "Live quote shows strong positive momentum today."


@pytest.mark.parametrize("failure", ["disabled", "cooldown", "timeout", "invalid", "incomplete"])
def test_locked_reason_survives_provider_failure_modes(portfolio, monkeypatch, failure):
    svc = AIAdvisorService(enabled=failure != "disabled", api_key="synthetic-key")
    calls = []
    if failure == "cooldown":
        svc._disabled_until = time.time() + 100

    def post(*args, **kwargs):
        calls.append(kwargs)
        if failure == "timeout":
            raise requests.Timeout("synthetic timeout")
        raw = "invalid JSON" if failure == "invalid" else '{"advice":"SELL"}'
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"output_text": raw})

    monkeypatch.setattr(requests, "post", post)
    row = portfolio("HOLD", svc)
    assert row["advice"] == "HOLD"
    assert row["advice_reason"] == "HOLD: authoritative synthetic reason."
    assert len(calls) == (0 if failure in {"disabled", "cooldown"} else 2 if failure in {"timeout", "invalid"} else 1)


@pytest.mark.parametrize("action", ["BUY", "HOLD", "SELL"])
def test_unlocked_matching_commentary_keeps_existing_ai_path(portfolio, action):
    row = portfolio(None, Commentary(action))
    assert row["advice"] == action
    assert row["advice_reason"] == f"{action}: independent synthetic commentary."


def test_matching_ai_does_not_replace_a_locked_reason(portfolio):
    row = portfolio("BUY", Commentary("BUY"))
    assert row["advice"] == "BUY"
    assert row["advice_reason"] == "BUY: authoritative synthetic reason."


def test_unlocked_disabled_fallback_keeps_existing_action_and_matching_reason(portfolio):
    row = portfolio(None, AIAdvisorService(enabled=False))
    assert row["advice"] == "SELL"
    assert row["advice_reason"] == "Position is extended above your entry; consider taking profit into strength."


def test_invalid_ai_action_cannot_replace_the_existing_reason(portfolio):
    row = portfolio(None, Commentary("INVALID", reason="SELL: unrelated advice."))
    assert row["advice"] == "HOLD"
    assert row["advice_reason"] == "Live quote shows strong positive momentum today."


@pytest.mark.parametrize("initial,quick", [("SELL", "BUY"), ("BUY", "HOLD OFF FOR NOW")])
@pytest.mark.parametrize("locked", [False, True])
def test_consistency_adjustment_replaces_obsolete_reason(portfolio, monkeypatch, initial, quick, locked):
    monkeypatch.setattr(api_module, "_quick_decision", lambda *args: {"recommendation": quick})
    row = portfolio(initial if locked else None, Commentary(initial, reason="Obsolete independent action justification."))
    assert row["advice"] == "HOLD"
    assert "Consistency adjustment:" in row["advice_reason"]
    assert "HOLD" in row["advice_reason"]
    assert "Obsolete" not in row["advice_reason"]
    assert "authoritative synthetic reason" not in row["advice_reason"]


@pytest.mark.parametrize("locked", [False, True])
def test_real_suitability_adjustment_replaces_obsolete_buy_reason(portfolio, monkeypatch, locked):
    monkeypatch.setattr(api_module, "_personalize_action", REAL_PERSONALIZE_ACTION)
    monkeypatch.setattr(api_module, "_quick_decision", lambda *args: {"recommendation": "BUY"})
    row = portfolio("BUY" if locked else None, Commentary("BUY"))
    assert row["base_advice"] == "BUY"
    assert row["advice"] == "HOLD"
    assert row["suitability"]["changed"] is True
    assert "Profile adjustment:" in row["advice_reason"]
    assert "authoritative synthetic reason" not in row["advice_reason"]
    for rule in row["suitability"]["applied_rules"]:
        assert rule["message"] in row["advice_reason"]


@pytest.mark.parametrize("mode", ["shadow", "off"])
def test_suitability_without_enforcement_keeps_the_selected_reason(portfolio, monkeypatch, mode):
    from flask import current_app

    def real_policy(**kwargs):
        current_app.extensions["personalization_runtime"].mode = mode
        return REAL_PERSONALIZE_ACTION(**kwargs)

    monkeypatch.setattr(api_module, "_personalize_action", real_policy)
    row = portfolio("BUY", Commentary("SELL"))
    assert row["advice"] == "BUY"
    assert row["suitability"]["changed"] is False
    assert row["advice_reason"] == "BUY: authoritative synthetic reason."
