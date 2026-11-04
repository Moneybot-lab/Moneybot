"""Synthetic Responses envelopes; no prompts/payloads in timing diagnostics."""
import json
from types import SimpleNamespace

import pytest
import requests

from moneybot.services.ai_advisor import AIAdvisorService
from moneybot.services import request_timing as timing

ADVICE = {"advice": "HOLD", "advice_reason": "Hold with controlled risk.",
          "risk_notes": ["one", "two"], "next_checks": ["one", "two"]}


def call(service):
    return service.enhance_portfolio_position(symbol="PRIVATE", entry_price=100, current_price=100,
                                             shares=7, signal_data={"score": 5})


def response(data):
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: data)


@pytest.mark.parametrize("envelope", [
    {"output_text": json.dumps(ADVICE)}, {"output_parsed": ADVICE},
    {"output": [{"type": "reasoning", "summary": []}, {"content": [{"type": "output_text", "text": json.dumps(ADVICE)}]}]},
    {"output": [{"content": [{"type": "output_text", "text": {"value": json.dumps(ADVICE)}}]}]},
    {"output": [{"content": [{"json": ADVICE}]}]},
    {"output": [{"content": [{"parsed": ADVICE}]}]},
])
def test_supported_response_variants_succeed_first_time(monkeypatch, envelope):
    calls = []
    def post(*args, **kwargs):
        calls.append(kwargs["json"])
        return response(envelope)
    monkeypatch.setattr(requests, "post", post)
    service = AIAdvisorService(enabled=True, api_key="synthetic")
    assert call(service)["mode"] == "ai_enhanced"
    assert len(calls) == 1
    assert calls[0]["max_output_tokens"] == 420
    assert calls[0]["reasoning"] == {"effort": "minimal"}
    assert service._disabled_until == 0


@pytest.mark.parametrize("bad,category", [
    ({"status": "incomplete", "output_text": json.dumps(ADVICE), "incomplete_details": {"reason": "max_output_tokens"}}, "ai.incomplete"),
    ({"output_text": '{"advice":"HOLD"'}, "ai.invalid_json"),
    ({"output_text": '{"advice":"HOLD"}'}, "ai.missing_fields"),
    ({"output_parsed": {**ADVICE, "risk_notes": []}}, "ai.invalid_fields"),
    ({"output_parsed": {**ADVICE, "advice": "WAIT"}}, "ai.invalid_fields"),
    ({"output_parsed": {**ADVICE, "next_checks": [1, 2]}}, "ai.invalid_fields"),
    ({"output": [{"content": [{"type": "refusal", "refusal": "PRIVATE response"}]}]}, "ai.invalid_json"),
    ({"status": "failed", "output_text": json.dumps(ADVICE)}, "ai.unsuccessful_status"),
    ([], "ai.invalid_envelope"),
])
def test_invalid_first_response_retries_once_then_success_without_cooldown(monkeypatch, bad, category):
    replies = iter([bad, {"status": "completed", "output_parsed": ADVICE,
                         "usage": {"input_tokens": 300, "output_tokens": 120, "output_tokens_details": {"reasoning_tokens": 12}}}])
    monkeypatch.setattr(requests, "post", lambda *a, **kw: response(next(replies)))
    service = AIAdvisorService(enabled=True, api_key="PRIVATE-key")
    trace = timing.Trace("api.user_watchlist")
    token = timing._active.set(trace)
    try:
        assert call(service)["mode"] == "ai_enhanced"
    finally:
        timing._active.reset(token)
    assert trace.counters[category] == 1
    assert trace.counters["ai.retry_valid"] == 1
    assert trace.counters["ai.output_tokens"] == 120
    assert trace.counters["ai.reasoning_tokens"] == 12
    assert service._disabled_until == 0 and getattr(service, "_portfolio_invalid_until", 0) == 0
    assert "PRIVATE" not in json.dumps(trace.counters)


def test_exhausted_invalid_pause_is_portfolio_only_and_recovers(monkeypatch):
    now = [1000.]
    monkeypatch.setattr("moneybot.services.ai_advisor.time.time", lambda: now[0])
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        return response({"output_text": "truncated"} if now[0] < 1120 else {"output_parsed": ADVICE})
    monkeypatch.setattr(requests, "post", post)
    service = AIAdvisorService(enabled=True, api_key="synthetic", failure_cooldown_s=120)
    assert call(service)["mode"] == "rule_based"
    for _ in range(28): assert call(service)["mode"] == "rule_based"
    assert len(calls) == 2 and service._disabled_until == 0
    now[0] = 1120
    assert call(service)["mode"] == "ai_enhanced"


@pytest.mark.parametrize("failure,attempts", [(requests.ConnectTimeout(), 2), (requests.ReadTimeout(), 2),
    (requests.HTTPError(response=SimpleNamespace(status_code=400)), 1),
    (requests.HTTPError(response=SimpleNamespace(status_code=429)), 1),
    (requests.HTTPError(response=SimpleNamespace(status_code=503)), 2)])
def test_exhausted_provider_failure_remains_bounded(monkeypatch, failure, attempts):
    calls = []
    def post(*args, **kwargs): calls.append(1); raise failure
    monkeypatch.setattr(requests, "post", post)
    service = AIAdvisorService(enabled=True, api_key="synthetic")
    for _ in range(5): assert call(service)["mode"] == "rule_based"
    assert len(calls) == attempts and service._disabled_until > 0
