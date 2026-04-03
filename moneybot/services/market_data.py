from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests
import yfinance as yf

from trade_signal import analyze_ticker
from .deterministic_advisor import DeterministicQuickAdvisor


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
    def __init__(
        self,
        timeout_s: int = 8,
        retries: int = 2,
        deterministic_quick_advisor: DeterministicQuickAdvisor | None = None,
        deterministic_momentum_enabled: bool = True,
    ):
        self.timeout_s = timeout_s
        self.retries = retries
        self.deterministic_quick_advisor = deterministic_quick_advisor
        self.deterministic_momentum_enabled = bool(deterministic_momentum_enabled)
        self.quote_cache = TTLCache(ttl_seconds=20)
        self.signal_cache = TTLCache(ttl_seconds=20)
        self.sector_cache = TTLCache(ttl_seconds=3600)
        self.company_snapshot_cache = TTLCache(ttl_seconds=600)
        self._company_snapshot_backoff_until = 0.0
        self._market_timezone = ZoneInfo("America/New_York")
        self._daily_lists_last_refreshed_at: datetime | None = None
        self._daily_lists_cache: dict[str, list[Dict[str, Any]]] = {}
        self._logged_missing_finnhub_key = False
        self._logged_missing_massive_key = False


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
            {"name": "Dow", "symbol": "^DJI", "quote_symbol": "DIA"},
            {"name": "S&P 500", "symbol": "^GSPC", "quote_symbol": "SPY"},
            {"name": "Nasdaq", "symbol": "^IXIC", "quote_symbol": "QQQ"},
            {"name": "Gold", "symbol": "GC=F", "quote_symbol": "GLD"},
            {"name": "Bitcoin", "symbol": "BTC-USD", "quote_symbol": "IBIT"},
        ]
        out: list[Dict[str, Any]] = []
        for item in symbols:
            try:
                quote = self.get_quote(item["quote_symbol"])
                ticker = yf.Ticker(item["symbol"])
                hist = ticker.history(period="1mo", interval="1d")
                if hist is None or hist.empty:
                    raise ValueError("history unavailable")
                closes = [round(float(v), 2) for v in hist["Close"].tail(15)]
                price = quote.get("price") if isinstance(quote.get("price"), (int, float)) else closes[-1]
                change_raw = quote.get("change_percent")
                if isinstance(change_raw, (int, float)):
                    change = float(change_raw)
                else:
                    prev = closes[-2] if len(closes) > 1 else closes[-1]
                    change = ((float(price) - prev) / prev) * 100 if prev else 0
                out.append({
                    "name": item["name"],
                    "symbol": item["symbol"],
                    "price": round(float(price), 2),
                    "change_percent": round(change, 2),
                    "series": closes,
                    "quote_source": quote.get("quote_source"),
                })
            except Exception as exc:  # noqa: BLE001
                logging.warning("Index fetch failed for %s: %s", item["symbol"], exc)

        return out if len(out) == len(symbols) else self._mock_market_indices()

    def get_stable_watchlist(self) -> list[Dict[str, Any]]:
        self._maybe_refresh_daily_lists()
        return [dict(item) for item in self._daily_lists_cache.get("stable", [])]


    @staticmethod
    def _clean_deterministic_rationale(rationale: str) -> str:
        text = (rationale or "").strip()
        lowered = text.lower()
        if lowered.startswith("deterministic model"):
            marker = "based on threshold"
            marker_index = lowered.find(marker)
            if marker_index != -1:
                return text[marker_index:].strip().capitalize()
        return text

    def _predict_hot_momentum_decision(
        self,
        *,
        symbol: str,
        signal: Dict[str, Any],
        quote: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        """
        Resolve deterministic momentum decision for live endpoint usage.

        Priority:
        1) live deterministic rollout path
        2) model-healthy shadow path (bypass rollout/dry-run for hot momentum endpoint)
        3) None => rule-based fallback
        """
        if not self.deterministic_momentum_enabled or self.deterministic_quick_advisor is None:
            return None

        advisor = self.deterministic_quick_advisor
        try:
            live = advisor.predict_quick_decision(
                signal_data=signal,
                quote_data=quote,
                symbol=symbol,
            )
            if live is not None:
                return live

            model_healthy = bool(getattr(advisor, "artifact", None) is not None and not getattr(advisor, "load_error", None))
            if model_healthy:
                shadow = advisor.predict_shadow_decision(
                    signal_data=signal,
                    quote_data=quote,
                )
                if shadow is not None:
                    logging.info(
                        "hot_momentum_buys promoted deterministic shadow scoring to live path symbol=%s reason=rollout_or_dry_run_gate",
                        symbol,
                    )
                    return shadow
                logging.warning(
                    "hot_momentum_buys fallback to rule_based symbol=%s reason=model_healthy_but_shadow_unavailable",
                    symbol,
                )
                return None

            logging.warning(
                "hot_momentum_buys fallback to rule_based symbol=%s reason=model_unavailable load_error=%s",
                symbol,
                getattr(advisor, "load_error", None),
            )
            return None
        except Exception as exc:  # noqa: BLE001
            logging.warning(
                "hot_momentum_buys fallback to rule_based symbol=%s reason=deterministic_scoring_exception error=%s",
                symbol,
                exc,
            )
            return None

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

    def get_company_snapshot(self, symbol: str) -> Dict[str, Any]:
        ticker_symbol = symbol.upper()
        cache_key = f"company:{ticker_symbol}"
        cached = self.company_snapshot_cache.get(cache_key)
        if cached:
            return cached

        default_name = ticker_symbol
        default_summary = "Company overview unavailable."
        latest_news: list[Dict[str, str]] = []

        now = time.time()
        if now < self._company_snapshot_backoff_until:
            payload = {
                "symbol": ticker_symbol,
                "company_name": default_name,
                "summary": default_summary,
                "latest_news": latest_news,
            }
            self.company_snapshot_cache.set(cache_key, payload)
            return payload

        try:
            ticker = yf.Ticker(ticker_symbol)
            info = ticker.info or {}
            company_name = str(info.get("longName") or info.get("shortName") or default_name)
            summary = str(
                info.get("longBusinessSummary")
                or info.get("description")
                or default_summary
            )
            summary = summary[:320].strip() + ("..." if len(summary) > 320 else "")

            news_items = ticker.news or []
            for item in news_items:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                publisher = str(item.get("publisher") or "").strip()
                if not title or title.lower() == "untitled":
                    continue
                if not publisher or publisher.lower() == "unknown source":
                    continue
                latest_news.append(
                    {
                        "title": title,
                        "publisher": publisher,
                        "link": str(item.get("link") or ""),
                    }
                )
                if len(latest_news) == 3:
                    break

            payload = {
                "symbol": ticker_symbol,
                "company_name": company_name,
                "summary": summary,
                "latest_news": latest_news,
            }
            self.company_snapshot_cache.set(cache_key, payload)
            return payload
        except Exception as exc:  # noqa: BLE001
            if "Too Many Requests" in str(exc):
                self._company_snapshot_backoff_until = now + 300.0
            logging.warning("Company snapshot fetch failed for %s: %s", ticker_symbol, exc)
            payload = {
                "symbol": ticker_symbol,
                "company_name": default_name,
                "summary": default_summary,
                "latest_news": latest_news,
            }
            self.company_snapshot_cache.set(cache_key, payload)
            return payload

    def get_price_history(self, symbol: str, days: int = 30) -> list[float]:
        try:
            ticker = yf.Ticker(symbol.upper())
            hist = ticker.history(period="3mo", interval="1d")
            if hist is None or hist.empty:
                return []
            closes = [round(float(v), 2) for v in hist["Close"].tail(max(days, 1))]
            return closes
        except Exception as exc:  # noqa: BLE001
            logging.warning("History fetch failed for %s: %s", symbol, exc)
            return []

    def get_sector(self, symbol: str) -> str:
        cache_key = f"sector:{symbol.upper()}"
        cached = self.sector_cache.get(cache_key)
        if cached:
            return str(cached.get("sector") or "Unknown")

        sector = "Unknown"
        try:
            ticker = yf.Ticker(symbol.upper())
            info = ticker.info or {}
            sector = str(info.get("sector") or info.get("industry") or "Unknown")
        except Exception as exc:  # noqa: BLE001
            logging.warning("Sector fetch failed for %s: %s", symbol, exc)

        self.sector_cache.set(cache_key, {"sector": sector})
        return sector



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
        self._maybe_refresh_daily_lists()
        return [dict(item) for item in self._daily_lists_cache.get("stable", [])]


    @staticmethod
    def _clean_deterministic_rationale(rationale: str) -> str:
        text = (rationale or "").strip()
        lowered = text.lower()
        if lowered.startswith("deterministic model"):
            marker = "based on threshold"
            marker_index = lowered.find(marker)
            if marker_index != -1:
                return text[marker_index:].strip().capitalize()
        return text

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

    def get_sector(self, symbol: str) -> str:
        cache_key = f"sector:{symbol.upper()}"
        cached = self.sector_cache.get(cache_key)
        if cached:
            return str(cached.get("sector") or "Unknown")

        sector = "Unknown"
        try:
            ticker = yf.Ticker(symbol.upper())
            info = ticker.info or {}
            sector = str(info.get("sector") or info.get("industry") or "Unknown")
        except Exception as exc:  # noqa: BLE001
            logging.warning("Sector fetch failed for %s: %s", symbol, exc)

        self.sector_cache.set(cache_key, {"sector": sector})
        return sector



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
            {"name": "Dow", "symbol": "^DJI", "quote_symbol": "DIA"},
            {"name": "S&P 500", "symbol": "^GSPC", "quote_symbol": "SPY"},
            {"name": "Nasdaq", "symbol": "^IXIC", "quote_symbol": "QQQ"},
            {"name": "Gold", "symbol": "GC=F", "quote_symbol": "GLD"},
            {"name": "Bitcoin", "symbol": "BTC-USD", "quote_symbol": "IBIT"},
        ]
        out: list[Dict[str, Any]] = []
        for item in symbols:
            quote = self.get_quote(item["quote_symbol"])
            quote_price = quote.get("price")
            quote_change = quote.get("change_percent")
            price: float | None = float(quote_price) if isinstance(quote_price, (int, float)) else None
            change: float | None = float(quote_change) if isinstance(quote_change, (int, float)) else None
            closes: list[float] = []

            try:
                quote = self.get_quote(item["quote_symbol"])
                ticker = yf.Ticker(item["symbol"])
                hist = ticker.history(period="1mo", interval="1d")
                if hist is not None and not hist.empty:
                    closes = [round(float(v), 2) for v in hist["Close"].tail(15)]
            except Exception as exc:  # noqa: BLE001
                logging.warning("Index history fetch failed for %s: %s", item["symbol"], exc)

            if not closes:
                if price is not None:
                    closes = [round(price, 2)] * 15
                else:
                    mock_item = next((m for m in self._mock_market_indices() if m["symbol"] == item["symbol"]), None)
                    closes = list(mock_item["series"]) if mock_item else []

            if price is None and closes:
                price = closes[-1]

            if change is None and closes:
                prev = closes[-2] if len(closes) > 1 else closes[-1]
                change = ((float(price) - prev) / prev) * 100 if prev and price is not None else 0.0

            if price is None:
                mock_item = next((m for m in self._mock_market_indices() if m["symbol"] == item["symbol"]), None)
                if mock_item:
                    out.append(dict(mock_item))
                    continue

            out.append({
                "name": item["name"],
                "symbol": item["symbol"],
                "price": round(float(price), 2),
                "change_percent": round(float(change or 0.0), 2),
                "series": closes,
                "quote_source": quote.get("quote_source"),
            })

        return out if len(out) == len(symbols) else self._mock_market_indices()

    def _score_from_signal(self, signal: Dict[str, Any], default_score: float) -> float:
        raw_score = signal.get("score")
        if isinstance(raw_score, (int, float)):
            return round(float(raw_score), 2)
        return round(float(default_score), 2)

    @staticmethod
    def _change_percent_sort_value(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _reason_from_signal(self, signal: Dict[str, Any], fallback: str) -> str:
        reasons = signal.get("reasons") or signal.get("rationale") or []
        if isinstance(reasons, list) and reasons:
            return str(reasons[0])
        if isinstance(reasons, str) and reasons.strip():
            return reasons.strip()
        return fallback

    def _is_buy_like(self, signal: Dict[str, Any]) -> bool:
        action = str(signal.get("action") or signal.get("verdict") or "").upper()
        return action in {"BUY", "STRONG BUY"}

    def get_stable_watchlist(self) -> list[Dict[str, Any]]:
        candidates = [
            {"symbol": "MSFT", "company": "Microsoft", "price": 418.2, "signal_score": 7.9, "transparency": "Strong balance sheet and recurring revenue."},
            {"symbol": "JNJ", "company": "Johnson & Johnson", "price": 154.6, "signal_score": 7.6, "transparency": "Defensive healthcare earnings profile."},
            {"symbol": "PG", "company": "Procter & Gamble", "price": 168.4, "signal_score": 7.3, "transparency": "Staples demand supports steadier growth."},
            {"symbol": "KO", "company": "Coca-Cola", "price": 60.2, "signal_score": 7.1, "transparency": "Global cash generation and lower volatility."},
            {"symbol": "PEP", "company": "PepsiCo", "price": 173.8, "signal_score": 7.0, "transparency": "Diversified beverage/snack resilience."},
            {"symbol": "MCD", "company": "McDonald's", "price": 287.4, "signal_score": 6.9, "transparency": "Durable consumer demand and predictable cash flow."},
            {"symbol": "WMT", "company": "Walmart", "price": 71.3, "signal_score": 6.8, "transparency": "Scale and defensive consumer spending profile."},
            {"symbol": "COST", "company": "Costco", "price": 738.5, "signal_score": 6.7, "transparency": "Membership model supports resilient margins."},
        ]

        enriched: list[Dict[str, Any]] = []
        for item in candidates:
            quote = self.get_quote(item["symbol"])
            signal = self.get_signal(item["symbol"])
            merged = dict(item)
            if isinstance(quote.get("price"), (int, float)):
                merged["price"] = float(quote["price"])
            merged["signal_score"] = self._score_from_signal(signal, item["signal_score"])
            merged["transparency"] = self._reason_from_signal(signal, item["transparency"])
            merged["change_percent"] = quote.get("change_percent")
            merged["quote_source"] = quote.get("quote_source")
            merged["live_data_available"] = bool(quote.get("live_data_available"))
            merged["qualified"] = bool(merged["live_data_available"] and merged["signal_score"] >= 6.5)
            enriched.append(merged)

        qualified = [item for item in enriched if item["qualified"]]
        pool = qualified if len(qualified) >= 5 else sorted(enriched, key=lambda x: x["signal_score"], reverse=True)
        selected = sorted(
            pool,
            key=lambda x: (x["signal_score"], self._change_percent_sort_value(x.get("change_percent"))),
            reverse=True,
        )[:5]
        for item in selected:
            item.pop("qualified", None)
        return selected


    @staticmethod
    def _clean_deterministic_rationale(rationale: str) -> str:
        text = (rationale or "").strip()
        lowered = text.lower()
        if lowered.startswith("deterministic model"):
            marker = "based on threshold"
            marker_index = lowered.find(marker)
            if marker_index != -1:
                return text[marker_index:].strip().capitalize()
        return text

    def get_hot_momentum_buys(self) -> list[Dict[str, Any]]:
        candidates = [
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
            {"symbol": "CCL", "price": 16.1, "score": 7.1, "rationale": "Travel beta with strong volume participation."},
            {"symbol": "RUN", "price": 13.5, "score": 7.0, "rationale": "High-beta clean energy momentum candidate."},
            {"symbol": "SOUN", "price": 5.82, "score": 8.7, "rationale": "AI voice momentum with elevated volume."},
            {"symbol": "RKLB", "price": 6.11, "score": 8.4, "rationale": "Small-cap space momentum setup."},
            {"symbol": "JOBY", "price": 5.02, "score": 7.7, "rationale": "eVTOL speculation trend continuation."},
            {"symbol": "ACHR", "price": 4.26, "score": 7.6, "rationale": "Aerospace momentum with strong social chatter."},
            {"symbol": "IONQ", "price": 12.14, "score": 8.2, "rationale": "Quantum-theme momentum flow."},
            {"symbol": "ASTS", "price": 10.53, "score": 8.0, "rationale": "Satellite connectivity beta with breakout profile."},
            {"symbol": "HIMS", "price": 14.22, "score": 7.9, "rationale": "Direct-to-consumer healthcare trend strength."},
            {"symbol": "HOOD", "price": 19.72, "score": 7.8, "rationale": "Retail trading beta in risk-on sessions."},
            {"symbol": "AFRM", "price": 34.8, "score": 7.7, "rationale": "Fintech momentum with high-vol ranges."},
            {"symbol": "UPST", "price": 25.6, "score": 7.6, "rationale": "Credit AI theme with speculative bid."},
            {"symbol": "OPEN", "price": 3.11, "score": 7.4, "rationale": "High-beta housing rebound candidate."},
            {"symbol": "RIVN", "price": 11.92, "score": 7.4, "rationale": "EV swing momentum in news-heavy periods."},
            {"symbol": "CHPT", "price": 1.89, "score": 7.3, "rationale": "Charging infrastructure speculative rotation."},
            {"symbol": "BTBT", "price": 2.77, "score": 7.2, "rationale": "Crypto miner beta with intraday momentum."},
            {"symbol": "CLSK", "price": 18.42, "score": 7.5, "rationale": "Mining efficiency narrative with risk-on flow."},
            {"symbol": "T", "price": 17.11, "score": 6.9, "rationale": "Low-vol telecom catch-up swing candidate."},
            {"symbol": "WBD", "price": 8.64, "score": 7.0, "rationale": "Media re-rating momentum setup."},
        ]

        enriched: list[Dict[str, Any]] = []
        for item in candidates:
            quote = self.get_quote(item["symbol"])
            signal = self.get_signal(item["symbol"])
            merged = dict(item)
            if isinstance(quote.get("price"), (int, float)):
                merged["price"] = float(quote["price"])
            merged["score"] = self._score_from_signal(signal, item["score"])
            merged["rationale"] = self._reason_from_signal(signal, item["rationale"])
            merged["change_percent"] = quote.get("change_percent")
            merged["quote_source"] = quote.get("quote_source")
            merged["live_data_available"] = bool(quote.get("live_data_available"))

            deterministic_decision = self._predict_hot_momentum_decision(
                symbol=item["symbol"],
                signal=signal,
                quote=quote,
            )

            if deterministic_decision is not None:
                prob_up = float(deterministic_decision.get("probability_up") or 0.0)
                merged["score"] = round(prob_up * 10.0, 2)
                merged["rationale"] = self._clean_deterministic_rationale(str(deterministic_decision.get("rationale") or merged["rationale"]))
                merged["decision_source"] = str(deterministic_decision.get("decision_source") or "deterministic_model")
                merged["model_version"] = deterministic_decision.get("model_version")
                merged["probability_up"] = deterministic_decision.get("probability_up")
                merged["confidence"] = deterministic_decision.get("confidence")
                merged["qualified"] = bool(
                    merged["live_data_available"]
                    and deterministic_decision.get("recommendation") in {"BUY", "STRONG BUY"}
                )
            else:
                merged["decision_source"] = "rule_based"
                merged["qualified"] = bool(merged["live_data_available"] and merged["score"] >= 7.0 and self._is_buy_like(signal))

            enriched.append(merged)

        target_count = 20
        price_cap = 100.0
        qualified = [item for item in enriched if item["qualified"]]
        pool = qualified if len(qualified) >= target_count else sorted(enriched, key=lambda x: x["score"], reverse=True)
        sorted_pool = sorted(
            pool,
            key=lambda x: (x["score"], self._change_percent_sort_value(x.get("change_percent"))),
            reverse=True,
        )
        under_cap = [item for item in sorted_pool if isinstance(item.get("price"), (int, float)) and float(item["price"]) <= price_cap]
        if len(under_cap) >= target_count:
            selected = under_cap[:target_count]
        else:
            remainder = [item for item in sorted_pool if item not in under_cap]
            selected = (under_cap + remainder)[:target_count]
        for item in selected:
            item.pop("qualified", None)
        return selected

    def get_wells_picks(self) -> list[Dict[str, Any]]:
        investors = [
            {"investor": "Warren Buffett", "stocks": ["AAPL", "AXP", "KO", "OXY", "BAC", "CVX", "AMZN", "V"]},
            {"investor": "Cathie Wood", "stocks": ["TSLA", "ROKU", "COIN", "SQ", "CRSP", "PATH", "TDOC", "U"]},
            {"investor": "Ray Dalio", "stocks": ["JNJ", "PG", "PEP", "XOM", "PFE", "WMT", "UNH", "MRK"]},
        ]

        out: list[Dict[str, Any]] = []
        for investor in investors:
            ranked: list[Dict[str, Any]] = []
            for ticker in investor["stocks"]:
                quote = self.get_quote(ticker)
                signal = self.get_signal(ticker)
                score = self._score_from_signal(signal, 6.5)
                raw_change = quote.get("change_percent")
                performance = round(float(raw_change), 2) if isinstance(raw_change, (int, float)) else 0.0
                ranked.append(
                    {
                        "ticker": ticker,
                        "price": float(quote["price"]) if isinstance(quote.get("price"), (int, float)) else "DATA_MISSING",
                        "performance": performance,
                        "quote_source": quote.get("quote_source"),
                        "live_data_available": bool(quote.get("live_data_available")),
                        "score": score,
                        "qualified": bool(bool(quote.get("live_data_available")) and score >= 6.5 and self._is_buy_like(signal)),
                    }
                )

            qualified = [item for item in ranked if item["qualified"]]
            pool = qualified if len(qualified) >= 5 else sorted(ranked, key=lambda x: x["score"], reverse=True)
            selected = sorted(pool, key=lambda x: (x["score"], x["performance"]), reverse=True)[:5]
            for stock in selected:
                stock.pop("qualified", None)
                stock.pop("score", None)
            out.append({"investor": investor["investor"], "stocks": selected})

        return out

    def _fallback_quote(self, symbol: str, error: str) -> Dict[str, Any]:
        return {
            "symbol": symbol,
            "price": "DATA_MISSING",
            "change_percent": "DATA_MISSING",
            "live_data_available": False,
            "quote_source": "yfinance",
            "diagnostics": {"provider": "yfinance", "error": error},
        }

    def _get_massive_key(self) -> tuple[str | None, str | None]:
        key_env_names = ("MASSIVE_API_KEY", "POLYGON_API_KEY")
        for env_name in key_env_names:
            raw = os.environ.get(env_name)
            if raw and raw.strip():
                return raw.strip(), env_name
        return None, None

    def _get_twelve_data_key(self) -> tuple[str | None, str | None]:
        key_env_names = ("TWELVE_DATA_API_KEY", "TWELVEDATA_API_KEY")
        for env_name in key_env_names:
            raw = os.environ.get(env_name)
            if raw and raw.strip():
                return raw.strip(), env_name
        return None, None

    def _get_finnhub_key(self) -> tuple[str | None, str | None]:
        key_env_names = ("FINNHUB_API_KEY", "FINNHUB_TOKEN", "X_FINNHUB_TOKEN")
        for env_name in key_env_names:
            raw = os.environ.get(env_name)
            if raw and raw.strip():
                return raw.strip(), env_name
        return None, None

    def get_quote(self, symbol: str) -> Dict[str, Any]:
        cache_key = symbol.upper()
        cached = self.quote_cache.get(cache_key)
        if cached:
            return cached

        def _yfinance_quote() -> Dict[str, Any]:
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

                    return {
                        "symbol": cache_key,
                        "price": float(price) if price is not None else "DATA_MISSING",
                        "change_percent": float(change) if change is not None else "DATA_MISSING",
                        "live_data_available": price is not None and change is not None,
                        "quote_source": "yfinance",
                        "diagnostics": {"provider": "yfinance", "error": None},
                    }
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
                    logging.warning("Quote fetch failed for %s: %s", cache_key, exc)
                    if "Too Many Requests" in last_error:
                        break
                    time.sleep(0.15)

            return self._fallback_quote(cache_key, last_error)

        massive_key, massive_key_source = self._get_massive_key()
        massive_error: str | None = None
        if massive_key:
            try:
                resp = requests.get(
                    f"https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers/{cache_key}",
                    params={"apiKey": massive_key},
                    timeout=self.timeout_s,
                )
                resp.raise_for_status()
                data = resp.json() or {}
                ticker_data = data.get("ticker") or {}
                day = ticker_data.get("day") or {}
                prev_day = ticker_data.get("prevDay") or {}

                price = day.get("c")
                prev_close = prev_day.get("c")
                change_percent = None
                if price not in (None, 0) and prev_close not in (None, 0):
                    change_percent = ((float(price) - float(prev_close)) / float(prev_close)) * 100

                if price not in (None, 0) and change_percent is not None:
                    payload = {
                        "symbol": cache_key,
                        "price": float(price),
                        "change_percent": float(change_percent),
                        "live_data_available": True,
                        "quote_source": "massive",
                        "diagnostics": {"provider": "massive", "error": None, "massive_key_source": massive_key_source},
                    }
                    self.quote_cache.set(cache_key, payload)
                    return payload

                massive_error = f"incomplete_response:{data}"
                logging.warning("Massive returned incomplete quote for %s: %s", cache_key, data)
            except Exception as exc:  # noqa: BLE001
                massive_error = str(exc)
                logging.warning("Massive quote fetch failed for %s: %s", cache_key, exc)
        else:
            massive_error = "missing_api_key"
            if not self._logged_missing_massive_key:
                logging.info(
                    "Massive key missing; quote requests will use Finnhub/yfinance fallback until MASSIVE_API_KEY is set."
                )
                self._logged_missing_massive_key = True

        finnhub_key, finnhub_key_source = self._get_finnhub_key()
        finnhub_error: str | None = None
        if finnhub_key:
            try:
                resp = requests.get(
                    "https://finnhub.io/api/v1/quote",
                    params={"symbol": cache_key, "token": finnhub_key},
                    headers={"X-Finnhub-Token": finnhub_key},
                    timeout=self.timeout_s,
                )
                resp.raise_for_status()
                data = resp.json() or {}
                price = data.get("c")
                prev_close = data.get("pc")
                change_percent = data.get("dp")

                if price not in (None, 0):
                    if change_percent is None and prev_close not in (None, 0):
                        change_percent = ((float(price) - float(prev_close)) / float(prev_close)) * 100

                    if change_percent is not None:
                        payload = {
                            "symbol": cache_key,
                            "price": float(price),
                            "change_percent": float(change_percent),
                            "live_data_available": True,
                            "quote_source": "finnhub",
                            "diagnostics": {"provider": "finnhub", "error": None, "finnhub_key_source": finnhub_key_source},
                        }
                        self.quote_cache.set(cache_key, payload)
                        return payload

                finnhub_error = f"incomplete_response:{data}"
                logging.warning("Finnhub returned incomplete quote for %s: %s", cache_key, data)
            except Exception as exc:  # noqa: BLE001
                finnhub_error = str(exc)
                logging.warning("Finnhub quote fetch failed for %s: %s", cache_key, exc)
        else:
            finnhub_error = "missing_api_key"
            if not self._logged_missing_finnhub_key:
                logging.info(
                    "Finnhub key missing; quote requests will use Twelve Data/yfinance fallback until FINNHUB_API_KEY/FINNHUB_TOKEN is set."
                )
                self._logged_missing_finnhub_key = True

        twelve_data_key, twelve_data_key_source = self._get_twelve_data_key()
        twelve_data_error: str | None = None
        if twelve_data_key:
            try:
                resp = requests.get(
                    "https://api.twelvedata.com/quote",
                    params={"symbol": cache_key, "apikey": twelve_data_key},
                    timeout=self.timeout_s,
                )
                resp.raise_for_status()
                data = resp.json() or {}
                if data.get("status") == "error":
                    raise RuntimeError(str(data.get("message") or data))

                price_raw = data.get("close")
                prev_close_raw = data.get("previous_close")
                change_percent_raw = data.get("percent_change")

                price = float(price_raw) if price_raw not in (None, "") else None
                change_percent = float(change_percent_raw) if change_percent_raw not in (None, "") else None

                if change_percent is None and price is not None and prev_close_raw not in (None, "", 0, "0"):
                    prev_close = float(prev_close_raw)
                    if prev_close:
                        change_percent = ((price - prev_close) / prev_close) * 100

                if price is not None and change_percent is not None:
                    payload = {
                        "symbol": cache_key,
                        "price": float(price),
                        "change_percent": float(change_percent),
                        "live_data_available": True,
                        "quote_source": "twelve_data",
                        "diagnostics": {
                            "provider": "twelve_data",
                            "error": None,
                            "twelve_data_key_source": twelve_data_key_source,
                        },
                    }
                    self.quote_cache.set(cache_key, payload)
                    return payload

                twelve_data_error = f"incomplete_response:{data}"
                logging.warning("Twelve Data returned incomplete quote for %s: %s", cache_key, data)
            except Exception as exc:  # noqa: BLE001
                twelve_data_error = str(exc)
                logging.warning("Twelve Data quote fetch failed for %s: %s", cache_key, exc)
        else:
            twelve_data_error = "missing_api_key"

        fallback = _yfinance_quote()
        fallback_diagnostics = fallback.get("diagnostics") or {}
        fallback_diagnostics["massive_attempted"] = bool(massive_key)
        fallback_diagnostics["massive_key_source"] = massive_key_source
        fallback_diagnostics["massive_error"] = massive_error
        fallback_diagnostics["finnhub_attempted"] = bool(finnhub_key)
        fallback_diagnostics["finnhub_key_source"] = finnhub_key_source
        fallback_diagnostics["finnhub_error"] = finnhub_error
        fallback_diagnostics["twelve_data_attempted"] = bool(twelve_data_key)
        fallback_diagnostics["twelve_data_key_source"] = twelve_data_key_source
        fallback_diagnostics["twelve_data_error"] = twelve_data_error
        fallback["diagnostics"] = fallback_diagnostics
        self.quote_cache.set(cache_key, fallback)
        return fallback

    def get_signal(self, symbol: str) -> Dict[str, Any]:
        cache_key = symbol.upper()
        cached = self.signal_cache.get(cache_key)
        if cached:
            return cached

        quote = self.get_quote(cache_key)
        if not quote.get("live_data_available"):
            payload = {
                "symbol": cache_key,
                "action": "HOLD",
                "verdict": "HOLD",
                "hybrid_score": None,
                "score": None,
                "technical": {"rsi": None, "macd_histogram": None, "trend": "unknown"},
                "rsi": None,
                "macd_hist": None,
                "volume_today": None,
                "volume_ratio": None,
                "sentiment": {"score": None, "label": "n/a", "headlines": []},
                "rationale": ["Signal skipped because quote data was unavailable."],
                "reasons": ["Signal skipped because quote data was unavailable."],
                "quote": quote,
                "quote_data_available": False,
                "diagnostics": {"provider": "yfinance", "error": "quote_unavailable"},
            }
            self.signal_cache.set(cache_key, payload)
            return payload

        try:
            result = analyze_ticker(cache_key)
            verdict = "STRONG BUY" if (result.score is not None and result.score >= 9) else result.verdict.upper()
            payload = {
                "symbol": cache_key,
                "action": verdict,
                "verdict": verdict,
                "hybrid_score": result.score,
                "score": result.score,
                "technical": {"rsi": result.rsi, "macd_histogram": result.macd_hist},
                "rsi": result.rsi,
                "macd_hist": result.macd_hist,
                "volume_today": result.volume_today,
                "volume_ratio": result.volume_ratio,
                "sentiment": {"score": None, "label": "n/a", "headlines": []},
                "rationale": result.reasons,
                "reasons": result.reasons,
                "quote": quote,
                "quote_data_available": bool(quote.get("live_data_available")),
                "diagnostics": {"provider": "yfinance", "error": None},
            }
        except Exception as exc:  # noqa: BLE001
            logging.warning("Signal fetch failed for %s: %s", cache_key, exc)
            payload = {
                "symbol": cache_key,
                "action": "HOLD",
                "verdict": "HOLD",
                "hybrid_score": None,
                "score": None,
                "technical": {"rsi": None, "macd_histogram": None, "trend": "unknown"},
                "rsi": None,
                "macd_hist": None,
                "volume_today": None,
                "volume_ratio": None,
                "sentiment": {"score": None, "label": "n/a", "headlines": []},
                "rationale": ["Signal unavailable; using safe fallback."],
                "reasons": ["Signal unavailable; using safe fallback."],
                "quote": quote,
                "quote_data_available": bool(quote.get("live_data_available")),
                "diagnostics": {"provider": "yfinance", "error": str(exc)},
            }

        self.signal_cache.set(cache_key, payload)
        return payload
