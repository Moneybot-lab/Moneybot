"""Run the real rendered Portfolio modal against synthetic endpoint responses."""

import json
import re
import subprocess
from types import SimpleNamespace

import pytest

from moneybot import api as api_module
from moneybot.services.ai_advisor import AIAdvisorService
from test_portfolio_explanation_integrity import (
    Commentary,
    REAL_PERSONALIZE_ACTION,
    portfolio,
)


def _render_modal(portfolio, row):
    response = portfolio.client.get("/portfolio")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    # Execute the actual emitted functions, with a synthetic DOM and news fetch.
    # The VM has no Node imports, network APIs, or browser globals.
    source = page[page.index("function escapeHtml(value)"):page.index("function performanceCell(amount, pct)")]
    start = page.index("function humanizeReason(reason)")
    source += page[start:page.index("async function", start)]
    harness = r"""
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const elements = {};
const context = {
  currentPortfolioItems: [input.row], currentAdviceContext: null,
  document: { getElementById(id) {
    return elements[id] || (elements[id] = {style: {}, innerHTML: '', textContent: ''});
  }},
  apiFetch: async () => ({ok: true, json: async () => ({data: {latest_news: []}})})
};
(async () => {
  await vm.runInNewContext(input.source + '\nshowAdvice(0);', context, {timeout: 1000});
  process.stdout.write(JSON.stringify(elements));
})().catch(error => { process.stderr.write(String(error)); process.exitCode = 1; });
"""
    result = subprocess.run(["node", "-e", harness], input=json.dumps({"source": source, "row": row}),
                            text=True, capture_output=True, timeout=10, check=True)
    elements = json.loads(result.stdout)
    modal = elements["adviceReason"]["innerHTML"]
    risk = re.search(r"<strong>Risk:</strong> (.*?)</li>", modal).group(1)
    next_check = re.search(r"<strong>Next:</strong> (.*?)</li>", modal).group(1)
    return elements, risk, next_check


class TradingGuidance(Commentary):
    def enhance_portfolio_position(self, **kwargs):
        payload = super().enhance_portfolio_position(**kwargs)
        payload["risk_notes"] = [f"{self.action} more immediately to avoid losses."]
        payload["next_checks"] = [f"{self.action} the position now before reviewing data."]
        return payload


def _assert_neutral_hold_modal(portfolio, row):
    assert row["advice"] == "HOLD"
    elements, risk, next_check = _render_modal(portfolio, row)
    assert elements["adviceTitle"]["textContent"] == "AAPL · HOLD rationale"
    assert "holding is safer" in elements["plainEnglishExplanation"]["textContent"]
    assert risk == "Market conditions and prices can change; review your risk limits."
    assert next_check == "Monitor verified market data and review the final recommendation when conditions change."
    assert "more immediately" not in elements["adviceReason"]["innerHTML"]
    assert "position now" not in elements["adviceReason"]["innerHTML"]


@pytest.mark.parametrize("ai_action", ["BUY", "SELL", "HOLD"])
def test_locked_hold_modal_rejects_independent_trading_guidance(portfolio, ai_action):
    row = portfolio("HOLD", TradingGuidance(ai_action))
    _assert_neutral_hold_modal(portfolio, row)


@pytest.mark.parametrize("ai_action,quick", [("BUY", "HOLD OFF FOR NOW"), ("SELL", "BUY")])
def test_consistency_adjusted_hold_modal_rejects_old_trading_guidance(portfolio, monkeypatch, ai_action, quick):
    monkeypatch.setattr(api_module, "_quick_decision", lambda *args: {"recommendation": quick})
    row = portfolio(None, TradingGuidance(ai_action))
    assert "Consistency adjustment:" in row["advice_reason"]
    _assert_neutral_hold_modal(portfolio, row)


def test_suitability_adjusted_hold_modal_rejects_buy_guidance(portfolio, monkeypatch):
    monkeypatch.setattr(api_module, "_personalize_action", REAL_PERSONALIZE_ACTION)
    row = portfolio(None, TradingGuidance("BUY"))
    assert row["base_advice"] == "BUY"
    assert row["suitability"]["changed"] is True
    _assert_neutral_hold_modal(portfolio, row)


@pytest.mark.parametrize("raw_action,final_action,score,current_price", [
    ("BUY", "HOLD", 1.5, 90.0), ("SELL", "HOLD", 8.0, 90.0),
    ("HOLD", "BUY", 8.0, 90.0), ("HOLD", "SELL", 6.0, 118.0),
])
def test_postprocessed_ai_action_modal_rejects_original_guidance(
    portfolio, monkeypatch, raw_action, final_action, score, current_price
):
    service = AIAdvisorService(enabled=True, api_key="synthetic-key")
    payload = {"advice": raw_action, "advice_reason": f"{raw_action}: synthetic provider reason.",
               "risk_notes": [f"{raw_action} more immediately to avoid losses."],
               "next_checks": [f"{raw_action} the position now before reviewing data."]}
    monkeypatch.setattr("requests.post", lambda *args, **kwargs: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {"output_text": json.dumps(payload)}))
    result = service.enhance_portfolio_position(symbol="AAPL", entry_price=100.0,
        current_price=current_price, shares=2.0, signal_data={"score": score,
            "technical": {"rsi": 76 if final_action == "SELL" else 41},
            "sentiment": {"score": -0.5 if raw_action == "BUY" else 0.6}})
    assert result["advice"] == final_action
    row = portfolio(None, SimpleNamespace(enhance_portfolio_position=lambda **kwargs: result))
    assert row["advice"] == final_action
    elements, risk, next_check = _render_modal(portfolio, row)
    assert f"{final_action} rationale" in elements["adviceTitle"]["textContent"]
    assert "more immediately" not in risk
    assert "position now" not in next_check


@pytest.mark.parametrize("action", ["BUY", "HOLD", "SELL"])
def test_unverified_guidance_is_neutral_even_when_action_labels_match(portfolio, action):
    row = portfolio(None, TradingGuidance(action))
    assert row["advice"] == action
    _, risk, next_check = _render_modal(portfolio, row)
    assert "more immediately" not in risk
    assert "position now" not in next_check


@pytest.mark.parametrize("action", ["BUY", "HOLD", "SELL"])
@pytest.mark.parametrize("risk_note,next_check", [
    ("Momentum can reverse quickly.", "Watch RSI and volume."),
    ("Keep strict risk controls and position sizing.", "Reassess after earnings."),
])
def test_explicitly_compatible_neutral_guidance_is_preserved(portfolio, action, risk_note, next_check):
    class NeutralGuidance(TradingGuidance):
        def enhance_portfolio_position(self, **kwargs):
            payload = super().enhance_portfolio_position(**kwargs)
            payload["risk_notes"] = [risk_note]
            payload["next_checks"] = [next_check]
            return payload

    row = portfolio(action, NeutralGuidance("SELL"))
    _, risk, rendered_next_check = _render_modal(portfolio, row)
    assert risk == risk_note
    assert rendered_next_check == next_check


@pytest.mark.parametrize("invalid", [None, [], {}, 42, "BUY now", ["BUY now"]])
def test_missing_or_malformed_guidance_uses_neutral_monitoring(portfolio, invalid):
    row = portfolio("HOLD", TradingGuidance("SELL"))
    row["ai_portfolio"]["risk_notes"] = invalid
    row["ai_portfolio"]["next_checks"] = invalid
    _assert_neutral_hold_modal(portfolio, row)
