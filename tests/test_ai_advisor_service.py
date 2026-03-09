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
