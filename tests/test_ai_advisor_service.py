from moneybot.services.ai_advisor import AIAdvisorService


def _sample_inputs():
    quick = {"recommendation": "BUY", "rationale": "Momentum and sentiment align."}
    signal = {
        "action": "BUY",
        "score": 8.1,
        "technical": {"rsi": 44, "macd_histogram": 0.22},
        "sentiment": {"label": "positive", "score": 0.66},
    }
    quote = {"price": 150.5, "change_percent": 1.1, "quote_source": "finnhub"}
    return quick, signal, quote


def test_enhance_quick_decision_returns_fallback_when_disabled():
    quick, signal, quote = _sample_inputs()
    svc = AIAdvisorService(enabled=False)

    out = svc.enhance_quick_decision(
        symbol="AAPL",
        quick_decision=quick,
        signal_data=signal,
        quote_data=quote,
    )

    assert out["mode"] == "rule_based"
    assert out["provider"] == "none"


def test_enhance_quick_decision_parses_openai_json(monkeypatch):
    quick, signal, quote = _sample_inputs()
    svc = AIAdvisorService(enabled=True, provider="openai", api_key="x-test")

    monkeypatch.setattr(
        svc,
        "_openai_response",
        lambda _: '{"narrative":"Aggressive BUY setup.","risk_notes":["Use stop-losses.","Volatility risk."],"next_checks":["Confirm volume.","Watch news."]}',
    )

    out = svc.enhance_quick_decision(
        symbol="AAPL",
        quick_decision=quick,
        signal_data=signal,
        quote_data=quote,
    )

    assert out["mode"] == "ai_enhanced"
    assert out["provider"] == "openai"
    assert out["narrative"] == "Aggressive BUY setup."
    assert len(out["risk_notes"]) == 2


def test_enhance_quick_decision_uses_cooldown_after_failure(monkeypatch):
    quick, signal, quote = _sample_inputs()
    svc = AIAdvisorService(enabled=True, provider="openai", api_key="x-test", failure_cooldown_s=600)

    state = {"calls": 0}

    def fake_openai_response(_prompt):
        state["calls"] += 1
        raise TimeoutError("timeout")

    monkeypatch.setattr(svc, "_openai_response", fake_openai_response)

    first = svc.enhance_quick_decision(
        symbol="TSLA",
        quick_decision=quick,
        signal_data=signal,
        quote_data=quote,
    )
    second = svc.enhance_quick_decision(
        symbol="TSLA",
        quick_decision=quick,
        signal_data=signal,
        quote_data=quote,
    )

    assert first["mode"] == "rule_based"
    assert second["mode"] == "rule_based"
    assert state["calls"] == 1


def test_openai_response_falls_back_to_output_content_text(monkeypatch):
    svc = AIAdvisorService(enabled=True, provider="openai", api_key="x-test")

    class StubResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "output": [
                    {
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"narrative":"N","risk_notes":["r1","r2"],"next_checks":["c1","c2"]}',
                            }
                        ]
                    }
                ]
            }

    monkeypatch.setattr("moneybot.services.ai_advisor.requests.post", lambda *args, **kwargs: StubResp())
    out = svc._openai_response("prompt")

    assert out is not None
    assert '"narrative":"N"' in out


def test_openai_response_strips_markdown_fences(monkeypatch):
    svc = AIAdvisorService(enabled=True, provider="openai", api_key="x-test")

    class StubResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "output_text": "```json\n{\"narrative\":\"N\",\"risk_notes\":[\"r1\",\"r2\"],\"next_checks\":[\"c1\",\"c2\"]}\n```"
            }

    monkeypatch.setattr("moneybot.services.ai_advisor.requests.post", lambda *args, **kwargs: StubResp())
    out = svc._openai_response("prompt")

    assert out is not None
    assert not out.startswith("```")
    assert out.startswith("{")


def test_enhance_quick_decision_uses_cache_for_same_inputs(monkeypatch):
    quick, signal, quote = _sample_inputs()
    svc = AIAdvisorService(enabled=True, provider="openai", api_key="x-test", cache_ttl_s=600)

    calls = {"n": 0}

    def fake_openai_response(_prompt):
        calls["n"] += 1
        return '{"narrative":"Cached answer","risk_notes":["r1","r2"],"next_checks":["c1","c2"]}'

    monkeypatch.setattr(svc, "_openai_response", fake_openai_response)

    first = svc.enhance_quick_decision(
        symbol="AAPL",
        quick_decision=quick,
        signal_data=signal,
        quote_data=quote,
    )
    second = svc.enhance_quick_decision(
        symbol="AAPL",
        quick_decision=quick,
        signal_data=signal,
        quote_data=quote,
    )

    assert first["mode"] == "ai_enhanced"
    assert second["mode"] == "ai_enhanced"
    assert calls["n"] == 1


def test_enhance_quick_decision_skips_ai_for_low_signal_context(monkeypatch):
    svc = AIAdvisorService(enabled=True, provider="openai", api_key="x-test")
    quick = {"recommendation": "HOLD OFF FOR NOW", "rationale": "Revenue flat (no pts)"}
    signal = {
        "action": "SELL",
        "score": 0.0,
        "technical": {"rsi": 26.8, "macd_histogram": -0.22},
        "sentiment": {"score": None, "label": "n/a", "headlines": []},
    }
    quote = {"price": 12.15, "change_percent": -1.54, "quote_source": "massive"}

    called = {"n": 0}

    def fake_openai_response(_prompt):
        called["n"] += 1
        return "{}"

    monkeypatch.setattr(svc, "_openai_response", fake_openai_response)

    out = svc.enhance_quick_decision(
        symbol="F",
        quick_decision=quick,
        signal_data=signal,
        quote_data=quote,
    )

    assert out["mode"] == "skipped_low_signal"
    assert called["n"] == 0
