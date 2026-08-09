from __future__ import annotations

from typing import Any

import pandas as pd
import yfinance as yf

from .market_data import MarketDataService


class MassivePreferredHistoryDownload:
    """Shared daily-history downloader: Massive first, yfinance as visible fallback."""

    def __init__(self, service: MarketDataService | None = None) -> None:
        self.service = service or MarketDataService()
        self.massive_requests = 0
        self.massive_successes = 0
        self.yfinance_fallbacks = 0
        self._provider_by_symbol: dict[str, str] = {}

    def __call__(self, symbol: str, *, start: str, end: str, **kwargs: Any):
        symbol_key = str(symbol).upper()
        self.massive_requests += 1
        try:
            payload = self.service.get_massive_aggregates(
                symbol_key,
                multiplier=1,
                timespan="day",
                start=start,
                end=end,
                adjusted=True,
            )
            bars = payload.get("bars") if isinstance(payload, dict) else []
            rows: list[tuple[object, float]] = []
            for bar in bars or []:
                date_value = bar.get("date") or bar.get("timestamp_utc") or bar.get("t")
                close = (
                    bar.get("close") if bar.get("close") is not None else bar.get("c")
                )
                if date_value is not None and close is not None:
                    rows.append((pd.Timestamp(date_value).date(), float(close)))
            if rows:
                self.massive_successes += 1
                self._provider_by_symbol[symbol_key] = "massive"
                return pd.DataFrame(
                    {"Close": [row[1] for row in rows]},
                    index=pd.to_datetime([row[0] for row in rows]),
                )
        except Exception:  # noqa: BLE001
            pass
        self.yfinance_fallbacks += 1
        self._provider_by_symbol[symbol_key] = "yfinance"
        return yf.download(symbol_key, start=start, end=end, **kwargs)

    def provider_for_symbol(self, symbol: str) -> str:
        return self._provider_by_symbol.get(str(symbol).upper(), "unknown")

    def diagnostics_payload(self) -> dict[str, object]:
        return {
            "outcome_history_preferred_provider": "massive",
            "outcome_history_fallback_provider": "yfinance",
            "massive_history_requests": self.massive_requests,
            "massive_history_successes": self.massive_successes,
            "yfinance_history_fallbacks": self.yfinance_fallbacks,
        }
