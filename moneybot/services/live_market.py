from __future__ import annotations

import json
import math
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable, Iterable

from .market_stream import MarketStreamStateRepository
from .market_data_providers import ExchangeCalendar

LIVE_SCHEMA_VERSION = "live-market.v1"


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class LiveQuote:
    symbol: str
    price: float | None
    bid: float | None
    ask: float | None
    midpoint: float | None
    event_timestamp: str | None
    received_timestamp: str | None
    age_ms: int | None
    market_session: str | None
    source: str
    source_mode: str
    is_stale: bool
    is_degraded: bool
    quality_flags: tuple[str, ...]
    event_type: str | None
    event_id: str
    schema_version: str = LIVE_SCHEMA_VERSION

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        data["quality_flags"] = list(self.quality_flags)
        return data


class LiveQuoteResolver:
    def __init__(self, *, state: MarketStreamStateRepository, rest_quote: Callable[[str], dict[str, Any]], clock: Callable[[], datetime] | None = None) -> None:
        self.state = state
        self.rest_quote = rest_quote
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.calendar = ExchangeCalendar()

    @staticmethod
    def _stream_price(event_type: str, event: dict[str, Any]) -> tuple[float | None, float | None, float | None, float | None]:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if event_type == "T":
            return _number(payload.get("price")), None, None, None
        if event_type in {"A", "AM"}:
            return _number(payload.get("close")), None, None, None
        bid, ask = _number(payload.get("bid")), _number(payload.get("ask"))
        midpoint = _number(payload.get("midpoint"))
        return midpoint, bid, ask, midpoint

    def _freshest_stream(self, symbol: str) -> LiveQuote | None:
        candidates: list[tuple[datetime, LiveQuote]] = []
        now = self.clock()
        for event_type in ("T", "Q", "A", "AM"):
            event = self.state.get_latest(symbol, event_type)
            if not event:
                continue
            event_time = _parse_timestamp(event.get("event_timestamp"))
            received = _parse_timestamp(event.get("received_timestamp"))
            price, bid, ask, midpoint = self._stream_price(event_type, event)
            flags = tuple(str(flag) for flag in event.get("quality_flags") or [])
            session = self.calendar.session_at(event_time or now)
            age_ms = max(0, int((now - event_time).total_seconds() * 1000)) if event_time else None
            threshold_ms = 15_000 if session == "regular" else (60_000 if session in {"pre", "after"} else 86_400_000)
            stale = bool(event.get("is_stale")) or event_time is None or (age_ms is not None and age_ms > threshold_ms)
            sequence = (event.get("sequence_number") or event.get("provider_event_id") or int(event_time.timestamp() * 1000)) if event_time else 0
            live = LiveQuote(
                symbol=symbol, price=price, bid=bid, ask=ask, midpoint=midpoint,
                event_timestamp=event_time.isoformat() if event_time else None,
                received_timestamp=received.isoformat() if received else None,
                age_ms=age_ms, market_session=session, source=str(event.get("source") or "massive"),
                source_mode=str(event.get("source_mode") or "websocket"), is_stale=stale,
                is_degraded=stale, quality_flags=flags, event_type=event_type,
                event_id=f"{symbol}:{event_type}:{sequence}",
            )
            if event_time and price is not None:
                candidates.append((event_time, live))
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]

    def resolve(self, symbol: str) -> LiveQuote:
        symbol = str(symbol).strip().upper()
        stream = self._freshest_stream(symbol)
        if stream is not None and not stream.is_stale:
            return stream

        rest = self.rest_quote(symbol) or {}
        rest_event_time = _parse_timestamp(rest.get("event_timestamp"))
        rest_stale = bool(rest.get("is_stale", not rest.get("live_data_available")))
        rest_price = _number(rest.get("price"))
        rest_flags = [str(flag) for flag in rest.get("quality_flags") or []]
        if stream is not None:
            rest_flags.append("stream_stale_rest_fallback")
        event_id_value = int(rest_event_time.timestamp() * 1000) if rest_event_time else int(self.clock().timestamp() * 1000)
        return LiveQuote(
            symbol=symbol, price=rest_price, bid=_number(rest.get("bid")), ask=_number(rest.get("ask")),
            midpoint=_number(rest.get("midpoint")), event_timestamp=rest.get("event_timestamp"),
            received_timestamp=rest.get("received_timestamp"), age_ms=rest.get("age_ms"),
            market_session=rest.get("market_session"), source=str(rest.get("source") or rest.get("quote_source") or "none"),
            source_mode=str(rest.get("source_mode") or (rest.get("diagnostics") or {}).get("source_mode") or "fallback"),
            is_stale=rest_stale, is_degraded=stream is not None or rest_stale,
            quality_flags=tuple(dict.fromkeys(rest_flags)), event_type=None,
            event_id=f"{symbol}:REST:{event_id_value}",
        )


@dataclass
class TriggerMemory:
    last_price: float | None = None
    last_spread_bps: float | None = None
    pending_rule: str | None = None
    pending_since: float | None = None
    last_fired_rule: str | None = None
    last_recommendation_state: str | None = None
    last_fired_at: float | None = None


class ControlledTriggerEngine:
    def __init__(self, *, enabled: bool = True, debounce_seconds: float = 15.0, cooldown_seconds: float = 300.0, hysteresis_percent: float = 0.5, clock: Callable[[], float] = time.time) -> None:
        self.enabled = enabled
        self.debounce_seconds = max(0.0, debounce_seconds)
        self.cooldown_seconds = max(0.0, cooldown_seconds)
        self.hysteresis_percent = max(0.0, hysteresis_percent)
        self.clock = clock
        self.memory: dict[tuple[int, str], TriggerMemory] = {}
        self.metrics = Counter()
        self.lock = Lock()

    def evaluate(
        self,
        *,
        user_id: int,
        symbol: str,
        event_type: str | None,
        price: float | None,
        market_session: str | None,
        after_hours_allowed: bool,
        recommendation_state: str | None = None,
        price_threshold: float | None = None,
        concentration_crossed: bool = False,
        spread_bps: float | None = None,
        invalidation_reason: str | None = None,
        profile_version: int | None = None,
        market_data_version: str = LIVE_SCHEMA_VERSION,
    ) -> dict[str, Any]:
        now = self.clock()
        symbol = symbol.upper()
        key = (user_id, symbol)
        with self.lock:
            state = self.memory.setdefault(key, TriggerMemory())
            if not self.enabled:
                self.metrics["suppressed_emergency_disabled"] += 1
                return {"fire": False, "reason": "emergency_disabled"}
            if market_session in {"pre", "after", "closed"} and not after_hours_allowed:
                self.metrics["suppressed_after_hours"] += 1
                return {"fire": False, "reason": "after_hours_disabled"}

            rule = None
            if invalidation_reason:
                rule = "snapshot_invalidated"
            elif event_type == "AM":
                rule = "minute_bar_closed"
            elif concentration_crossed:
                rule = "suitability_boundary_crossed"
            elif price is not None and price_threshold is not None:
                band = abs(price_threshold) * self.hysteresis_percent / 100
                was_below = state.last_price is not None and state.last_price < price_threshold - band
                is_above = price >= price_threshold + band
                if (was_below and is_above) or (state.pending_rule == "price_threshold_crossed" and is_above):
                    rule = "price_threshold_crossed"
            if spread_bps is not None and state.last_spread_bps is not None and abs(spread_bps - state.last_spread_bps) >= 25:
                rule = rule or "liquidity_changed"

            state.last_price = price if price is not None else state.last_price
            state.last_spread_bps = spread_bps if spread_bps is not None else state.last_spread_bps
            if rule is None:
                state.pending_rule = None; state.pending_since = None
                return {"fire": False, "reason": "no_controlled_boundary"}
            if state.pending_rule != rule:
                state.pending_rule = rule; state.pending_since = now
                self.metrics["debounced"] += 1
                return {"fire": False, "reason": "debouncing", "rule": rule}
            if state.pending_since is not None and now - state.pending_since < self.debounce_seconds:
                self.metrics["debounced"] += 1
                return {"fire": False, "reason": "debouncing", "rule": rule}
            if state.last_fired_at is not None and now - state.last_fired_at < self.cooldown_seconds:
                self.metrics["suppressed_cooldown"] += 1
                return {"fire": False, "reason": "cooldown", "rule": rule}
            if state.last_fired_rule == rule and state.last_recommendation_state == recommendation_state:
                self.metrics["suppressed_duplicate"] += 1
                return {"fire": False, "reason": "duplicate", "rule": rule}

            state.last_fired_rule = rule; state.last_recommendation_state = recommendation_state
            state.last_fired_at = now; state.pending_rule = None; state.pending_since = None
            self.metrics["fired"] += 1
            return {
                "fire": True, "reason": rule, "rule": rule, "symbol": symbol,
                "profile_version": profile_version, "market_data_version": market_data_version,
                "event_type": event_type, "price": price, "fired_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            }

    def snapshot(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "tracked_symbols": len(self.memory), **dict(self.metrics)}


def sse_encode(*, event: str, data: dict[str, Any], event_id: str | None = None, retry_ms: int | None = None) -> str:
    lines: list[str] = []
    if event_id:
        lines.append(f"id: {event_id}")
    if retry_ms is not None:
        lines.append(f"retry: {int(retry_ms)}")
    lines.append(f"event: {event}")
    serialized = json.dumps(data, separators=(",", ":"), sort_keys=True)
    lines.extend(f"data: {line}" for line in serialized.splitlines() or [""])
    return "\n".join(lines) + "\n\n"
