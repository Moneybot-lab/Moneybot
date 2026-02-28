from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import yfinance as yf

from trade_signal import analyze_ticker


@dataclass
class TTLCacheEntry:
    value: Dict[str, Any]
    ts: float


class TTLCache:
    def __init__(self, ttl_seconds: int = 30):
        self.ttl = ttl_seconds
        self._store: Dict[str, TTLCacheEntry] = {}

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        entry = self._store.get(key)
        if not entry:
            return None
        if time.time() - entry.ts > self.ttl:
            self._store.pop(key, None)
            return None
        return entry.value

    def set(self, key: str, value: Dict[str, Any]) -> None:
        self._store[key] = TTLCacheEntry(value=value, ts=time.time())


class MarketDataService:
    def __init__(self, timeout_s: int = 8, retries: int = 2):
        self.timeout_s = timeout_s
        self.retries = retries
        self.quote_cache = TTLCache(ttl_seconds=20)
        self.signal_cache = TTLCache(ttl_seconds=20)



    def _mock_market_indices(self) -> list[Dict[str, Any]]:
        return [
            {"name": "Dow", "symbol": "^DJI", "price": 39210.4, "change_percent": 0.52, "series": [38800, 38940, 39020, 39105, 39210]},
            {"name": "S&P 500", "symbol": "^GSPC", "price": 5245.1, "change_percent": 0.44, "series": [5188, 5204, 5218, 5231, 5245]},
            {"name": "Nasdaq", "symbol": "^IXIC", "price": 16592.3, "change_percent": 0.71, "series": [16280, 16355, 16430, 16501, 16592]},
            {"name": "Gold", "symbol": "GC=F", "price": 2340.8, "change_percent": -0.18, "series": [2356, 2351, 2348, 2344, 2340]},
            {"name": "Bitcoin", "symbol": "BTC-USD", "price": 61110.2, "change_percent": -0.93, "series": [62400, 62020, 61680, 61390, 61110]},
        ]

    def get_market_indices(self) -> list[Dict[str, Any]]:
        symbols = [
            {"name": "Dow", "symbol": "^DJI"},
            {"name": "S&P 500", "symbol": "^GSPC"},
            {"name": "Nasdaq", "symbol": "^IXIC"},
            {"name": "Gold", "symbol": "GC=F"},
            {"name": "Bitcoin", "symbol": "BTC-USD"},
        ]
        out: list[Dict[str, Any]] = []
        for item in symbols:
            try:
                ticker = yf.Ticker(item["symbol"])
                hist = ticker.history(period="1mo", interval="1d")
                if hist is None or hist.empty:
                    raise ValueError("history unavailable")
                closes = [round(float(v), 2) for v in hist["Close"].tail(15)]
                price = closes[-1]
                prev = closes[-2] if len(closes) > 1 else closes[-1]
                change = ((price - prev) / prev) * 100 if prev else 0
                out.append({
                    "name": item["name"],
                    "symbol": item["symbol"],
                    "price": price,
                    "change_percent": round(change, 2),
                    "series": closes,
                })
            except Exception as exc:  # noqa: BLE001
                logging.warning("Index fetch failed for %s: %s", item["symbol"], exc)

        return out if len(out) == len(symbols) else self._mock_market_indices()

    def get_stable_watchlist(self) -> list[Dict[str, Any]]:
        return [
            {"symbol": "MSFT", "company": "Microsoft", "price": 418.2, "signal_score": 7.9, "transparency": "Strong balance sheet and recurring revenue."},
            {"symbol": "JNJ", "company": "Johnson & Johnson", "price": 154.6, "signal_score": 7.6, "transparency": "Defensive healthcare earnings profile."},
            {"symbol": "PG", "company": "Procter & Gamble", "price": 168.4, "signal_score": 7.3, "transparency": "Staples demand supports steadier growth."},
            {"symbol": "KO", "company": "Coca-Cola", "price": 60.2, "signal_score": 7.1, "transparency": "Global cash generation and lower volatility."},
            {"symbol": "PEP", "company": "PepsiCo", "price": 173.8, "signal_score": 7.0, "transparency": "Diversified beverage/snack resilience."},
        ]

    def get_hot_momentum_buys(self) -> list[Dict[str, Any]]:
        return [
            {"symbol": "SOFI", "price": 9.84, "score": 9.4, "rationale": "Member growth trend and improving margins."},
            {"symbol": "PLUG", "price": 3.72, "score": 9.1, "rationale": "High-volume breakout setup in clean-energy swing."},
            {"symbol": "LCID", "price": 2.98, "score": 8.9, "rationale": "Speculative EV rebound momentum."},
            {"symbol": "NIO", "price": 4.31, "score": 8.6, "rationale": "Delivery stabilization and trend reversal watch."},
            {"symbol": "RIOT", "price": 11.42, "score": 8.3, "rationale": "Crypto-beta momentum with strong intraday ranges."},
            {"symbol": "MARA", "price": 17.38, "score": 8.1, "rationale": "Bitcoin-linked upside bursts."},
            {"symbol": "AAL", "price": 13.24, "score": 7.8, "rationale": "Airline demand strength and technical continuation."},
            {"symbol": "UAL", "price": 43.12, "score": 7.6, "rationale": "Sector relative strength with improving trend."},
            {"symbol": "F", "price": 12.55, "score": 7.4, "rationale": "Low-priced cyclical with renewed momentum interest."},
            {"symbol": "PFE", "price": 28.77, "score": 7.2, "rationale": "Defensive rotation candidate near support."},
        ]

    def get_wells_picks(self) -> list[Dict[str, Any]]:
        return [
            {"investor": "Warren Buffett", "stocks": [{"ticker": "AAPL", "price": 191.2, "performance": 1.42}, {"ticker": "AXP", "price": 227.1, "performance": 0.81}, {"ticker": "KO", "price": 60.2, "performance": 0.33}, {"ticker": "OXY", "price": 62.6, "performance": -0.48}, {"ticker": "BAC", "price": 37.4, "performance": 0.57}]},
            {"investor": "Cathie Wood", "stocks": [{"ticker": "TSLA", "price": 178.4, "performance": 2.38}, {"ticker": "ROKU", "price": 59.6, "performance": 1.02}, {"ticker": "COIN", "price": 223.7, "performance": -1.12}, {"ticker": "SQ", "price": 73.2, "performance": 0.91}, {"ticker": "CRSP", "price": 61.2, "performance": -0.33}]},
            {"investor": "Ray Dalio", "stocks": [{"ticker": "JNJ", "price": 154.6, "performance": 0.28}, {"ticker": "PG", "price": 168.4, "performance": 0.21}, {"ticker": "PEP", "price": 173.8, "performance": 0.36}, {"ticker": "XOM", "price": 113.4, "performance": -0.14}, {"ticker": "PFE", "price": 28.8, "performance": 0.42}]},
        ]
    def _fallback_quote(self, symbol: str, error: str) -> Dict[str, Any]:
        return {
            "symbol": symbol,
            "price": "DATA_MISSING",
            "change_percent": "DATA_MISSING",
            "live_data_available": False,
            "diagnostics": {"provider": "yfinance", "error": error},
        }

    def get_quote(self, symbol: str) -> Dict[str, Any]:
        cache_key = symbol.upper()
        cached = self.quote_cache.get(cache_key)
        if cached:
            return cached

        last_error = "unknown"
        for _ in range(self.retries + 1):
            try:
                ticker = yf.Ticker(cache_key)
                info = ticker.info or {}
                price = info.get("regularMarketPrice") or info.get("currentPrice")
                prev = info.get("regularMarketPreviousClose") or info.get("previousClose")
                change = info.get("regularMarketChangePercent")

                if (price is None or change is None) and prev not in (None, 0):
                    change = ((price - prev) / prev) * 100 if price is not None else None

                if price is None:
                    hist = ticker.history(period="5d", interval="1d")
                    if hist is not None and not hist.empty:
                        price = float(hist["Close"].iloc[-1])
                        if len(hist.index) > 1:
                            prev = float(hist["Close"].iloc[-2])
                        if prev not in (None, 0):
                            change = ((price - prev) / prev) * 100

                payload = {
                    "symbol": cache_key,
                    "price": float(price) if price is not None else "DATA_MISSING",
                    "change_percent": float(change) if change is not None else "DATA_MISSING",
                    "live_data_available": price is not None and change is not None,
                    "diagnostics": {"provider": "yfinance", "error": None},
                }
                self.quote_cache.set(cache_key, payload)
                return payload
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                logging.warning("Quote fetch failed for %s: %s", cache_key, exc)
                time.sleep(0.15)

        fallback = self._fallback_quote(cache_key, last_error)
        self.quote_cache.set(cache_key, fallback)
        return fallback

    def get_signal(self, symbol: str) -> Dict[str, Any]:
        cache_key = symbol.upper()
        cached = self.signal_cache.get(cache_key)
        if cached:
            return cached

        quote = self.get_quote(cache_key)
        try:
            result = analyze_ticker(cache_key)
            payload = {
                "symbol": cache_key,
                "action": result.verdict.upper(),
                "hybrid_score": result.score,
                "technical": {"rsi": result.rsi, "macd_histogram": result.macd_hist},
                "sentiment": {"score": None, "label": "n/a", "headlines": []},
                "rationale": result.reasons,
                "quote": quote,
                "quote_data_available": bool(quote.get("live_data_available")),
                "diagnostics": {"provider": "yfinance", "error": None},
            }
        except Exception as exc:  # noqa: BLE001
            logging.warning("Signal fetch failed for %s: %s", cache_key, exc)
            payload = {
                "symbol": cache_key,
                "action": "HOLD",
                "hybrid_score": None,
                "technical": {"rsi": None, "macd_histogram": None, "trend": "unknown"},
                "sentiment": {"score": None, "label": "n/a", "headlines": []},
                "rationale": ["Signal unavailable; using safe fallback."],
                "quote": quote,
                "quote_data_available": bool(quote.get("live_data_available")),
                "diagnostics": {"provider": "yfinance", "error": str(exc)},
            }

        self.signal_cache.set(cache_key, payload)
        return payload
