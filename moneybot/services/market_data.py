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
