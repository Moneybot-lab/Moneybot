"""A failing provider must not consume two timeouts for every portfolio lot."""

import json
from types import SimpleNamespace

import pytest
import requests

from moneybot.services.ai_advisor import AIAdvisorService


def position(service, **overrides):
    inputs = dict(symbol="TEST", entry_price=100, current_price=120, shares=2,
                  signal_data={"score": 5, "technical": {}, "sentiment": {}})
    inputs.update(overrides)
    return service.enhance_portfolio_position(**inputs)


def success():
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
        "output_text": json.dumps({"advice": "SELL", "advice_reason": "SELL: synthetic reason.",
                                   "risk_notes": ["a", "b"], "next_checks": ["c", "d"]})})


@pytest.mark.parametrize("failure", ["timeout", "server_error"])
def test_exhausted_transient_failures_enter_existing_cooldown(monkeypatch, failure):
    now = [1000.0]
    monkeypatch.setattr("moneybot.services.ai_advisor.time.time", lambda: now[0])
    service = AIAdvisorService(enabled=True, api_key="synthetic", timeout_s=6, failure_cooldown_s=120)
    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs["timeout"])
        now[0] += 6
        if failure == "timeout":
            raise requests.Timeout("synthetic")
        raise requests.HTTPError(response=SimpleNamespace(status_code=503))

    monkeypatch.setattr(requests, "post", post)
    first = position(service)
    assert first["reason"] == "provider_error"
    for price in (80, 100, 120):
        result = position(service, current_price=price)
        expected = service._portfolio_fallback(entry_price=100, current_price=price,
                                               signal_score=5, reason="cooldown_after_failure")
        assert result == expected  # each lot still gets its own calculations
    assert calls == [6, 6]
    assert service._disabled_until == 1132.0  # cooldown starts after the last attempt
    now[0] = 1132.0
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: success())
    assert position(service)["mode"] == "ai_enhanced"


@pytest.mark.parametrize("failure", ["timeout", "server_error"])
def test_successful_retry_does_not_enter_cooldown(monkeypatch, failure):
    service = AIAdvisorService(enabled=True, api_key="synthetic")
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            if failure == "timeout":
                raise requests.Timeout()
            raise requests.HTTPError(response=SimpleNamespace(status_code=502))
        return success()

    monkeypatch.setattr(requests, "post", post)
    assert position(service)["mode"] == "ai_enhanced"
    assert position(service)["mode"] == "ai_enhanced"
    assert len(calls) == 3
    assert service._disabled_until == 0


def test_cooldown_does_not_share_position_advice_between_service_instances(monkeypatch):
    first = AIAdvisorService(enabled=True, api_key="synthetic")
    second = AIAdvisorService(enabled=True, api_key="synthetic")
    first._disabled_until = float("inf")
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: success())
    assert position(first, current_price=80)["advice"] == "BUY"
    assert position(first, current_price=120)["advice"] == "SELL"
    assert position(second)["mode"] == "ai_enhanced"


def test_cooldown_diagnostic_is_bounded_and_contains_no_position_data(monkeypatch, caplog):
    service = AIAdvisorService(enabled=True, api_key="private-test-token")

    def timeout(*args, **kwargs):
        raise requests.Timeout("sensitive provider response must not be logged")

    monkeypatch.setattr(requests, "post", timeout)
    for _ in range(20):
        position(service, symbol="PRIVATE_SYMBOL", entry_price=123456)
    messages = [record.message for record in caplog.records if "AI portfolio provider cooldown" in record.message]
    assert len(messages) == 1
    assert "failure=timeout" in messages[0]
    assert all(value not in messages[0] for value in ("PRIVATE_SYMBOL", "123456", "private-test-token", "sensitive"))
