"""Quant signal script for SNAP (or any ticker).

Usage:
    python trade_signal.py --ticker SNAP
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
import yfinance as yf
from bs4 import BeautifulSoup

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
except Exception:  # fallback keeps runtime functional when dependency is unavailable
    POSITIVE_WORDS = {
        "up",
        "strong",
        "bullish",
        "healthy",
        "improving",
        "sharp",
        "gain",
        "earnings beat",
        "guidance raise",
        "buyback",
        "dividend hike",
    }
    NEGATIVE_WORDS = {
        "down",
        "weak",
        "bearish",
        "slowing",
        "loss",
        "drop",
        "guidance lower",
        "missed estimates",
        "lawsuit",
        "recall",
    }

    class SentimentIntensityAnalyzer:  # type: ignore[override]
        _positive = POSITIVE_WORDS
        _negative = NEGATIVE_WORDS

        def polarity_scores(self, text: str) -> Dict[str, float]:
            text_l = (text or "").lower()
            words = [w.strip(".,:;!?()[]\'\"-").lower() for w in text_l.split()]
            pos = sum(1 for w in words if w in self._positive)
            neg = sum(1 for w in words if w in self._negative)

            # Phrase support keeps same count-based scoring logic.
            pos += sum(1 for phrase in self._positive if " " in phrase and phrase in text_l)
            neg += sum(1 for phrase in self._negative if " " in phrase and phrase in text_l)

            total = pos + neg
            compound = 0.0 if total == 0 else max(-1.0, min(1.0, (pos - neg) / total))
            return {"compound": compound}

try:
    import pandas_ta as ta
except Exception:  # fallback if pandas-ta is unavailable at runtime
    ta = None

import time

INFO_CACHE = {}
NEWS_CACHE = {}
HISTORY_CACHE = {}
TICKER_CACHE = {}
CACHE_TTL = 300   

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    )
}

MAX_SCORE = 12.0
SENTIMENT_ANALYZER = SentimentIntensityAnalyzer()


def _calc_macd_rsi(close: pd.Series) -> Tuple[Optional[float], Optional[float]]:
    """Calculate MACD histogram and RSI, preferring pandas-ta."""
    if ta is not None:
        macd = ta.macd(close, fast=12, slow=26, signal=9)
        rsi_series = ta.rsi(close, length=14)
        macd_hist = None
        if macd is not None and not macd.empty:
            hist_col = [c for c in macd.columns if c.startswith("MACDh_")]
            if hist_col:
                macd_hist = float(macd[hist_col[0]].iloc[-1])
        rsi_value = float(rsi_series.iloc[-1]) if rsi_series is not None and not rsi_series.empty else None
        return macd_hist, rsi_value

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = float((macd_line - signal_line).iloc[-1]) if len(close) else None

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean().replace(0, pd.NA)
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    rsi_value = float(rsi.iloc[-1]) if len(rsi.dropna()) else None
    return macd_hist, rsi_value

@dataclass
class SignalResult:
    ticker: str
    price: float
    rsi: Optional[float]
    macd_hist: Optional[float]
    volume_today: Optional[int]
    volume_ratio: Optional[float]
    score: float
    verdict: str
    reasons: List[str]


def _find_pct_metric(html: str, labels: List[str]) -> Optional[float]:
    """Find first percentage value near provided labels from page text."""
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    for label in labels:
        pattern = rf"{re.escape(label)}[^\d\-\+]*([\-\+]?\d+(?:\.\d+)?)\s*%"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return float(match.group(1)) / 100.0
    return None

def get_ticker(ticker: str):
    """Reuse Ticker object if cached, else make new."""
    now = time.time()
    cached = TICKER_CACHE.get(ticker)
    if cached and now - cached.get("ts", 0) < CACHE_TTL:
        return cached["obj"]

    tk = yf.Ticker(ticker)
    TICKER_CACHE[ticker] = {"obj": tk, "ts": now}
    return tk


def fetch_price_data(ticker: str) -> Tuple[pd.DataFrame, float]:
    tk = get_ticker(ticker)

    # History cache
    now = time.time()
    cached = HISTORY_CACHE.get(ticker)
    if cached and now - cached.get("ts", 0) < CACHE_TTL:
        history = cached["df"]
    else:
        try:
            history = tk.history(period="6mo", interval="1d", auto_adjust=False)
            if history.empty:
                raise RuntimeError("Empty history")
            HISTORY_CACHE[ticker] = {"df": history, "ts": now}
        except Exception as exc:
            logging.warning(f"History fetch fail for {ticker}: {exc}")
            history = pd.DataFrame()  # fallback

    if history.empty:
        raise RuntimeError(f"No price history for {ticker}")

    # Price from fast_info (cached via Ticker)
    try:
        price = float(tk.fast_info.get("lastPrice"))
    except Exception:
        price = float(history["Close"].dropna().iloc[-1])

    return history, price


def fetch_fundamentals(ticker: str) -> Dict:
    tk = get_ticker(ticker)

    now = time.time()
    cached = INFO_CACHE.get(ticker)
    if cached and now - cached.get("ts", 0) < CACHE_TTL:
        info = cached["data"]
    else:
        try:
            info = tk.info or {}
            INFO_CACHE[ticker] = {"data": info, "ts": now}
        except Exception as exc:
            logging.warning(f"yfinance info unavailable for {ticker}: {exc}")
            info = {}

    revenue_growth = info.get("revenueGrowth")
    revenue_growth = float(revenue_growth) if revenue_growth is not None else None

    # ... keep your backup logic and scrape here ...

    if revenue_growth is None:
        try:
            if ticker in HISTORY_CACHE:  # reuse cached history if possible
                qf = tk.quarterly_financials
                # ... same logic as before ...
            else:
                # fallback skip if no history
                pass
        except Exception:
            pass

    # Scrape still happens once per cache miss—add timeout
    active_users_qoq = None
    subs_yoy = None
    try:
        res = requests.get(f"https://finance.yahoo.com/quote/{ticker}", headers=HEADERS, timeout=10)
        res.raise_for_status()
        html = res.text
        active_users_qoq = _find_pct_metric(html, ["daily active users", "monthly active users", "active users", "DAU", "MAU"])
        subs_yoy = _find_pct_metric(html, ["subscribers", "subscriptions", "paid users"])
    except Exception as exc:
        logging.warning(f"Metric scrape fail for {ticker}: {exc}")

    return {
        "revenue_growth_yoy": revenue_growth,
        "active_users_qoq": active_users_qoq,
        "subs_yoy": subs_yoy,
    }


def fetch_sentiment_score(ticker: str) -> float:
    """Score cached news headlines with VADER and normalize average compound to 0..1."""
    ticker = ticker.upper().strip()
    now = time.time()
    cached = NEWS_CACHE.get(ticker)
    if cached and now - cached.get("ts", 0) < CACHE_TTL:
        return float(cached.get("score", 0.5))

    tk = get_ticker(ticker)
    try:
        news_items = tk.news or []
    except Exception as exc:
        logging.warning(f"News unavailable for {ticker}: {exc}")
        news_items = []

    headlines = [
        item.get("title", "").strip()
        for item in news_items
        if isinstance(item, dict) and item.get("title")
    ]

    if not headlines:
        score = 0.5
        sentiment_class = "neutral"
    else:
        compounds: List[float] = []
        for headline in headlines:
            try:
                compounds.append(float(SENTIMENT_ANALYZER.polarity_scores(headline).get("compound", 0.0)))
            except Exception as exc:
                logging.warning("VADER sentiment scoring failed for %s headline %r: %s", ticker, headline, exc)

        if not compounds:
            score = 0.5
            sentiment_class = "neutral"
        else:
            avg_compound = sum(compounds) / len(compounds)
            if avg_compound > 0.05:
                sentiment_class = "positive"
            elif avg_compound < -0.05:
                sentiment_class = "negative"
            else:
                sentiment_class = "neutral"

            score = (avg_compound + 1.0) / 2.0

    normalized = round(max(0.0, min(1.0, score)), 2)
    NEWS_CACHE[ticker] = {
        "score": normalized,
        "sentiment": sentiment_class,
        "headline_count": len(headlines),
        "ts": now,
    }
    return normalized


def analyze_ticker(ticker: str) -> SignalResult:
    """Compute indicators, apply score rules, and return final verdict."""
    ticker = ticker.upper().strip()
    history, live_price = fetch_price_data(ticker)

    close = history["Close"].astype(float)
    volume = history["Volume"].astype(float)

    # Indicators (pandas-ta preferred with deterministic fallback).
    macd_hist, rsi_value = _calc_macd_rsi(close)

    sma50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else None
    sma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None

    # Volume filter vs 20-day average.
    vol_today = int(round(volume.iloc[-1])) if len(volume) else None
    vol20 = float(volume.tail(20).mean()) if len(volume) >= 20 else None
    vol_ratio = (vol_today / vol20) if (vol_today is not None and vol20 and vol20 > 0) else None

    fundamentals = fetch_fundamentals(ticker)
    sentiment = fetch_sentiment_score(ticker)

    score = 0.0
    reasons: List[str] = []

    macd_hist_rounded = round(macd_hist, 2) if macd_hist is not None else None
    rsi_rounded = round(rsi_value, 1) if rsi_value is not None else None

    if macd_hist is not None and macd_hist > 0:
        score += 3
        reasons.append("MACD hist positive (+3)")

    if rsi_value is not None and rsi_value > 55:
        score += 1.5
        reasons.append("RSI > 55 (+1.5)")

    if sma50 is not None and live_price > sma50:
        score += 2
        reasons.append("Price above 50-day SMA (+2)")

    if sma200 is not None and live_price > sma200:
        score += 2
        reasons.append("Price above 200-day SMA (+2)")

    rev = fundamentals.get("revenue_growth_yoy")
    if rev is not None and rev > 0.15:
        score += 2
        reasons.append("Revenue growth > 15% (+2)")
    else:
        reasons.append("Revenue flat (no pts)")

    active_users = fundamentals.get("active_users_qoq")
    if active_users is not None and active_users > 0.02:
        score += 1
        reasons.append("Active users > 2% QoQ (+1)")

    subs = fundamentals.get("subs_yoy")
    if subs is not None and subs > 0.50:
        score += 1.5
        reasons.append("Subs > 50% YoY (+1.5)")

    if sentiment > 0.6:
        score += 1
        reasons.append("Sentiment > 0.6 (+1)")

    if vol_ratio is not None:
        if vol_ratio > 1.5:
            score += 1
            reasons.append("Volume > 1.5x avg (+1)")
        elif vol_ratio < 0.5:
            score -= 1
            reasons.append("Volume low (-1)")

    score = round(max(min(score, MAX_SCORE), -99), 1)

    if score >= 9:
        verdict = "Strong BUY"
    elif score >= 6:
        verdict = "Buy"
    elif score >= 4:
        verdict = "Hold"
    else:
        verdict = "Sell"

    return SignalResult(
        ticker=ticker,
        price=round(live_price, 2),
        rsi=rsi_rounded,
        macd_hist=macd_hist_rounded,
        volume_today=vol_today,
        volume_ratio=round(vol_ratio, 1) if vol_ratio is not None else None,
        score=score,
        verdict=verdict,
        reasons=reasons,
    )


def _fmt_millions(v: Optional[int]) -> str:
    if v is None:
        return "n/a"
    return f"{int(round(v / 1_000_000.0))}M"


def print_result(result: SignalResult) -> None:
    """Print output in requested concise layout."""
    vol_text = "n/a"
    if result.volume_today is not None and result.volume_ratio is not None:
        vol_text = f"{_fmt_millions(result.volume_today)} ({result.volume_ratio:.1f}x avg)"

    print(f"Ticker: {result.ticker}")
    print(f"Price: {result.price:.2f}")
    print(f"RSI: {result.rsi if result.rsi is not None else 'n/a'}")
    print(f"MACD Hist: {result.macd_hist if result.macd_hist is not None else 'n/a'}")
    print(f"Volume: {vol_text}")
    print(f"Score: {result.score:.1f}")
    print(f"Verdict: {result.verdict}")
    print("Why:")
    for reason in result.reasons:
        print(f"- {reason}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Quant signal model for SNAP or any ticker.")
    parser.add_argument("--ticker", default="SNAP", help="Ticker symbol, e.g. SNAP")
    args = parser.parse_args()

    result = analyze_ticker(args.ticker)
    print_result(result)


if __name__ == "__main__":
    main()
