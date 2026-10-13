import copy
import json

import pytest

from moneybot.services.ai_advisor import AIAdvisorService


def _portfolio_advice(monkeypatch, provider_advice, *, signal=None, entry_price=100.0, current_price=100.0):
    """Exercise the real Portfolio response path with one synthetic provider response."""
    service = AIAdvisorService(enabled=True, provider="openai", api_key="synthetic-test-key")
    signal = signal or {"score": 5.0, "technical": {"rsi": 50}, "sentiment": {"score": 0.0}}
    original_signal = copy.deepcopy(signal)
    calls = []

    class SyntheticResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"output_text": json.dumps(provider_advice)}

    def synthetic_post(url, *, headers, json, timeout):
        calls.append((url, json, timeout))
        assert headers["Authorization"] == "Bearer synthetic-test-key"
        return SyntheticResponse()

    monkeypatch.setattr("moneybot.services.ai_advisor.requests.post", synthetic_post)
    result = service.enhance_portfolio_position(
        symbol="SYNTH",
        entry_price=entry_price,
        current_price=current_price,
        shares=3.0,
        signal_data=signal,
    )

    assert len(calls) == 1
    assert calls[0][0] == "https://api.openai.com/v1/responses"
    assert calls[0][1]["model"] == "gpt-5-mini"
    assert calls[0][1]["reasoning"] == {"effort": "low"}
    assert calls[0][1]["max_output_tokens"] == 220
    context = json.loads(calls[0][1]["input"].split("Context: ", 1)[1])
    assert context["entry_price"] == entry_price
    assert context["current_price"] == current_price
    assert context["shares"] == 3.0
    assert context["position_pnl_pct"] == round((current_price - entry_price) / entry_price * 100, 2)
    assert context["score"] == signal["score"]
    assert context["technical"] == signal["technical"]
    assert context["sentiment"] == signal["sentiment"]
    assert signal == original_signal
    assert result["mode"] == "ai_enhanced"
    return result


def _provider_advice(action, reason):
    return {
        "advice": action,
        "advice_reason": reason,
        "risk_notes": ["Synthetic risk one.", "Synthetic risk two."],
        "next_checks": ["Synthetic check one.", "Synthetic check two."],
    }


@pytest.mark.parametrize(
    "model_action,model_reason,score,sentiment,rsi,current_price,final_action",
    [
        ("BUY", "Buy this dip now.", 1.5, -0.5, 41, 90.0, "HOLD"),
        ("SELL", "Sell the position now.", 8.0, 0.2, 41, 90.0, "HOLD"),
        ("HOLD", "Hold the position without adding shares.", 8.0, 0.6, 41, 90.0, "BUY"),
        ("HOLD", "Hold the position without selling shares.", 6.0, 0.2, 76, 118.0, "SELL"),
    ],
    ids=["weak-buy-to-hold", "strong-drawdown-sell-to-hold", "strong-drawdown-hold-to-buy", "extended-hold-to-sell"],
)
def test_postprocessed_portfolio_action_replaces_obsolete_model_explanation(
    monkeypatch, model_action, model_reason, score, sentiment, rsi, current_price, final_action
):
    result = _portfolio_advice(
        monkeypatch,
        _provider_advice(model_action, model_reason),
        signal={"score": score, "technical": {"rsi": rsi}, "sentiment": {"score": sentiment}},
        current_price=current_price,
    )

    assert result["advice"] == final_action
    assert model_reason not in result["advice_reason"]
    assert final_action in result["advice_reason"].upper()
    assert result["risk_notes"] == ["Synthetic risk one.", "Synthetic risk two."]
    assert result["next_checks"] == ["Synthetic check one.", "Synthetic check two."]


@pytest.mark.parametrize("model_action", [None, "WAIT", 7, ["BUY"]], ids=["null", "invalid-enum", "numeric", "list"])
def test_invalid_model_action_preserves_hold_selection_without_unattributed_buy_reason(monkeypatch, model_action):
    reason = "Buy this dip now."
    result = _portfolio_advice(monkeypatch, _provider_advice(model_action, reason))

    assert result["advice"] == "HOLD"
    assert reason not in result["advice_reason"]
    assert "HOLD" in result["advice_reason"].upper()


def test_missing_model_action_preserves_hold_selection_without_unattributed_buy_reason(monkeypatch):
    payload = _provider_advice("BUY", "Buy this dip now.")
    payload.pop("advice")
    result = _portfolio_advice(monkeypatch, payload)

    assert result["advice"] == "HOLD"
    assert payload["advice_reason"] not in result["advice_reason"]
    assert "HOLD" in result["advice_reason"].upper()


@pytest.mark.parametrize("model_action", ["BUY", "HOLD", "SELL"])
@pytest.mark.parametrize("model_reason", [None, "", "   ", 42, ["Buy now"], {"instruction": "Buy now"}])
def test_invalid_model_reason_uses_selected_action_without_changing_decision(monkeypatch, model_action, model_reason):
    result = _portfolio_advice(monkeypatch, _provider_advice(model_action, model_reason))

    assert result["advice"] == model_action
    assert model_action in result["advice_reason"].upper()
    assert "Buy now" not in result["advice_reason"]
    assert "42" not in result["advice_reason"]


@pytest.mark.parametrize("model_action", ["BUY", "HOLD", "SELL"])
def test_missing_model_reason_uses_selected_action_without_changing_decision(monkeypatch, model_action):
    payload = _provider_advice(model_action, "unused")
    payload.pop("advice_reason")
    result = _portfolio_advice(monkeypatch, payload)

    assert result["advice"] == model_action
    assert model_action in result["advice_reason"].upper()


@pytest.mark.parametrize(
    "model_action,model_reason",
    [
        ("BUY", "Buy with controlled position sizing under the supplied signals."),
        ("HOLD", "Hold while monitoring the supplied signals."),
        ("SELL", "Sell with controlled position sizing under the supplied signals."),
        (" sell ", "Sell with controlled position sizing under the supplied signals."),
    ],
)
def test_unchanged_valid_model_advice_preserves_matching_commentary(monkeypatch, model_action, model_reason):
    result = _portfolio_advice(monkeypatch, _provider_advice(model_action, model_reason))

    assert result["advice"] == model_action.strip().upper()
    assert result["advice_reason"] == model_reason


def test_empty_provider_object_keeps_existing_hold_default_with_matching_explanation(monkeypatch):
    result = _portfolio_advice(monkeypatch, {})

    assert result["advice"] == "HOLD"
    assert "HOLD" in result["advice_reason"].upper()
