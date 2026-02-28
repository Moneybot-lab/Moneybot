from __future__ import annotations

from typing import Any, Dict, List, Optional

DIP_THRESHOLD = 0.05
PROFIT_MIN = 10.0


def _f(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _trend3_is_down(points: Optional[List[float]]) -> bool:
    if not points or len(points) < 3:
        return False
    a, b, c = points[-3:]
    return a > b > c


def compute_user_advice(
    symbol: str,
    entry_price: Optional[float],
    quote: Dict[str, Any],
    technical: Dict[str, Any],
    sentiment: Dict[str, Any],
    base_action: str,
    hybrid_score: Optional[float],
    trend3_closes: Optional[List[float]] = None,
    dip_threshold: float = DIP_THRESHOLD,
    profit_min: float = PROFIT_MIN,
) -> Dict[str, Any]:
    current_price = _f(quote.get("price"))
    change_percent = _f(quote.get("change_percent"))
    entry = _f(entry_price)

    rsi = _f(technical.get("rsi"))
    macd_hist = _f(technical.get("macd_histogram"))
    trend = (technical.get("trend") or "unknown").lower()

    sentiment_score = _f(sentiment.get("score"))
    sentiment_label = sentiment.get("label") or "neutral"
    headlines = sentiment.get("headlines") or []

    fallback_notes: List[str] = []
    if current_price is None:
        fallback_notes.append("quote.price missing")
    if rsi is None:
        fallback_notes.append("RSI defaulted")
        rsi = 50.0
    if macd_hist is None:
        fallback_notes.append("MACD defaulted")
        macd_hist = 0.0
    if sentiment_score is None:
        fallback_notes.append("sentiment defaulted")
        sentiment_score = 0.5

    pnl_per_share = None
    pnl_percent = None
    if entry and current_price is not None and entry > 0:
        pnl_per_share = current_price - entry
        pnl_percent = (pnl_per_share / entry) * 100

    risk_flags: List[str] = []
    profit_flags: List[str] = []

    trend3_down = _trend3_is_down(trend3_closes)

    if rsi is not None and rsi >= 70:
        risk_flags.append("RSI overbought (>=70)")
    if macd_hist is not None and macd_hist < 0:
        risk_flags.append("MACD momentum turning down")
    if sentiment_score is not None and sentiment_score <= 0.35:
        risk_flags.append("Negative news sentiment")
    if trend3_down:
        risk_flags.append("3-day trend down")

    if pnl_percent is not None and pnl_percent >= profit_min:
        profit_flags.append(f"Profit >= {profit_min:.0f}%")

    advice = (base_action or "HOLD").upper()
    rule = "base action"
    trigger = ""

    if current_price is None:
        advice = "HOLD"
        rule = "Price unavailable fallback"
        trigger = "Live price unavailable; holding while using technical/sentiment fallback defaults."
    else:
        oversold_turning_up = (
            (rsi is not None and rsi <= 35)
            and (macd_hist is not None and macd_hist > 0)
            and (sentiment_score is not None and sentiment_score >= 0.55)
        )
        dipped_from_entry = (
            entry is not None
            and current_price is not None
            and current_price <= entry * (1 - dip_threshold)
        )
        rose_from_entry = (
            entry is not None
            and current_price is not None
            and pnl_percent is not None
            and pnl_percent >= profit_min
        )

        if dipped_from_entry or oversold_turning_up:
            advice = "BUY"
            rule = "Buy-low rule"
            if dipped_from_entry and pnl_percent is not None:
                trigger = f"Price is down {abs(pnl_percent):.2f}% from your entry, triggering a buy-the-dip signal."
            else:
                trigger = "Oversold reversal conditions triggered a buy signal."
        elif rose_from_entry and (risk_flags or advice == "SELL"):
            advice = "SELL"
            rule = "Sell-high rule"
            trigger = f"Price is up {pnl_percent:.2f}% from your entry; gains plus risk signals trigger profit-taking."
        elif pnl_percent is not None and pnl_percent < 0:
            if (sentiment_score is not None and sentiment_score <= 0.25) and trend == "bearish" and (rsi is not None and rsi >= 45):
                advice = "SELL"
                rule = "Strong downside risk while in loss"
                trigger = f"Price is below entry by {abs(pnl_percent):.2f}% with bearish technicals; loss-control sell triggered."
            else:
                advice = "HOLD"
                rule = "Loss but no strong forced-sell risk"
                trigger = f"Price is below entry by {abs(pnl_percent):.2f}%, but downside confirmation is not strong enough to force a sell."
        elif pnl_percent is not None and -5 <= pnl_percent <= 15 and not risk_flags:
            advice = "HOLD"
            rule = "Hold-steady range"
            trigger = f"Price is {pnl_percent:.2f}% versus entry, inside the hold zone without major risk flags."
        elif advice not in {"BUY", "HOLD", "SELL"}:
            advice = "HOLD"
            rule = "Fallback hold"
            trigger = "Fallback hold due to non-standard base action."

    # Confidence score starts from hybrid score when available.
    confidence_score = float(hybrid_score) if hybrid_score is not None else 5.0
    sentiment_trigger: Optional[str] = None
    if sentiment_score is not None and sentiment_score > 0.6:
        confidence_score += 1.5
        sentiment_trigger = "Sentiment boost: +1.5"
    elif sentiment_score is not None and sentiment_score < 0.4:
        confidence_score -= 1.0

    # Confidence score starts from hybrid score when available.
    confidence_score = float(hybrid_score) if hybrid_score is not None else 5.0
    sentiment_trigger: Optional[str] = None
    if sentiment_score is not None and sentiment_score > 0.6:
        confidence_score += 1.5
        sentiment_trigger = "Sentiment boost: +1.5"
    elif sentiment_score is not None and sentiment_score < 0.4:
        confidence_score -= 1.0

    headline = headlines[0] if headlines else "No major headline available."
    trigger_text = f"{sentiment_trigger}. " if sentiment_trigger else ""
    reason_summary = (
        f"Entry={entry if entry is not None else 'n/a'}, Current={current_price if current_price is not None else 'n/a'}, "
        f"PnL%={round(pnl_percent,2) if pnl_percent is not None else 'n/a'}. "
        f"RSI={rsi if rsi is not None else 'n/a'}, MACD_hist={round(macd_hist,3) if macd_hist is not None else 'n/a'}, "
        f"Sentiment={sentiment_label}. Rule: {rule}. {trigger_text}Headline: {headline}"
    )

    return {
        "symbol": symbol,
        "entry_price": entry,
        "quote": {
            "price": current_price if current_price is not None else quote.get("price"),
            "change_percent": change_percent if change_percent is not None else quote.get("change_percent"),
        },
        "technical": {
            "rsi": rsi,
            "macd_histogram": macd_hist,
            "trend": trend,
        },
        "sentiment": {
            "score": sentiment_score,
            "label": sentiment_label,
            "headlines": headlines,
        },
        "hybrid_score": hybrid_score,
        "confidence_score": round(confidence_score, 2),
        "base_action": (base_action or "HOLD").upper(),
        "advice": advice,
        "reason_summary": reason_summary,
        "trigger": trigger,
        "risk_flags": risk_flags,
        "profit_flags": profit_flags,
        "unrealized_pnl_per_share": pnl_per_share,
        "unrealized_pnl_percent": pnl_percent,
    }
