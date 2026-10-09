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
from .market_data_providers import ExchangeCalendar, quote_freshness

LIVE_SCHEMA_VERSION = "live-market.v1"


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, OverflowError):
            return None
    else:
        return None
    # A local-machine timezone is not evidence of a source event's timezone.
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    try:
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _positive_price(value: Any) -> float | None:
    number = _number(value)
    return number if number is not None and number > 0 else None


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
    price_source: str | None = None
    market_session_context: str | None = None

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
            return _positive_price(payload.get("price")), None, None, None
        if event_type in {"A", "AM"}:
            return _positive_price(payload.get("close")), None, None, None
        bid, ask = _positive_price(payload.get("bid")), _positive_price(payload.get("ask"))
        midpoint = _positive_price(payload.get("midpoint"))
        if ((payload.get("bid") is not None and bid is None)
                or (payload.get("ask") is not None and ask is None)
                or (bid is not None and ask is not None and ask < bid)):
            midpoint = None
        # Recovery's selected price may be a trade or aggregate close. Retain
        # the genuine NBBO fields without reclassifying that price as midpoint.
        if event.get("source_mode") == "rest" and "recovery_price" in payload:
            return _positive_price(payload.get("recovery_price")), bid, ask, midpoint
        return midpoint, bid, ask, midpoint

    def _session_context(self, now: datetime, session: str) -> str:
        return f"{self.calendar.local_date(now).isoformat()}:{session}"

    def _freshest_stream(self, symbol: str) -> LiveQuote | None:
        candidates: list[tuple[datetime | None, LiveQuote]] = []
        now = self.clock()
        for event_type in ("T", "Q", "A", "AM"):
            event = self.state.get_latest(symbol, event_type)
            if not event:
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            recovery = event.get("source_mode") == "rest" and "recovery_price" in payload
            event_time = _parse_timestamp(payload.get("recovery_price_timestamp") if recovery else event.get("event_timestamp"))
            received = _parse_timestamp(event.get("received_timestamp"))
            price, bid, ask, midpoint = self._stream_price(event_type, event)
            age_ms, session, aged_stale, freshness_flags = quote_freshness(event_time, now, calendar=self.calendar)
            flags = [str(flag) for flag in event.get("quality_flags") or []]
            flags.extend(freshness_flags)
            price_source = (
                str(payload.get("recovery_price_source") or "rest_recovery_price") if recovery
                else {"T": "last_trade", "Q": "nbbo_midpoint", "A": "aggregate_close", "AM": "minute_close"}[event_type]
            )
            daily_close = price_source in {"day_close", "daily_close", "previous_close"} or "daily_close_not_realtime" in flags
            stale = bool(event.get("is_stale")) or aged_stale or daily_close or price is None
            if daily_close:
                flags.append("daily_close_not_realtime")
            if price is None:
                flags.append("data_missing")
            if stale:
                flags.append("stale")
            sequence = (event.get("sequence_number") or event.get("provider_event_id") or int(event_time.timestamp() * 1000)) if event_time else 0
            live = LiveQuote(
                symbol=symbol, price=price, bid=bid, ask=ask, midpoint=midpoint,
                event_timestamp=event_time.isoformat() if event_time else None,
                received_timestamp=received.isoformat() if received else None,
                age_ms=age_ms, market_session=session, source=str(event.get("source") or "massive"),
                source_mode=str(event.get("source_mode") or "websocket"), is_stale=stale,
                is_degraded=stale, quality_flags=tuple(dict.fromkeys(flags)), event_type=event_type,
                event_id=f"{symbol}:{event_type}:{sequence}",
                price_source=price_source, market_session_context=self._session_context(now, session),
            )
            if price is not None:
                candidates.append((event_time, live))
        if not candidates:
            return None
        # Eligible fresh observations win before comparing timestamps. Among
        # those, newest price-event time wins; ties preserve T, Q, A, AM order.
        # A stale or unknown-time candidate remains diagnostic fallback only.
        eligible = [candidate for candidate in candidates if not candidate[1].is_stale]
        return max(eligible or candidates, key=lambda item: item[0] or datetime.min.replace(tzinfo=timezone.utc))[1]

    def resolve(self, symbol: str) -> LiveQuote:
        symbol = str(symbol).strip().upper()
        stream = self._freshest_stream(symbol)
        if stream is not None and not stream.is_stale:
            return stream

        rest = self.rest_quote(symbol) or {}
        now = self.clock()
        rest_event_time = _parse_timestamp(rest.get("event_timestamp"))
        age_ms, session, aged_stale, freshness_flags = quote_freshness(rest_event_time, now, calendar=self.calendar)
        rest_price = _positive_price(rest.get("price"))
        rest_flags = [str(flag) for flag in rest.get("quality_flags") or []]
        rest_flags.extend(freshness_flags)
        price_source = str(rest.get("price_source")) if rest.get("price_source") else None
        daily_close = price_source in {"day_close", "daily_close", "previous_close"} or "daily_close_not_realtime" in rest_flags
        rest_stale = bool(rest.get("is_stale", not rest.get("live_data_available"))) or aged_stale or daily_close or rest_price is None
        if daily_close:
            rest_flags.append("daily_close_not_realtime")
        if rest_price is None:
            rest_flags.append("data_missing")
        if rest_stale:
            rest_flags.append("stale")
        if stream is not None:
            rest_flags.append("stream_stale_rest_fallback")
        event_id_value = int(rest_event_time.timestamp() * 1000) if rest_event_time else int(now.timestamp() * 1000)
        return LiveQuote(
            symbol=symbol, price=rest_price, bid=_positive_price(rest.get("bid")), ask=_positive_price(rest.get("ask")),
            midpoint=_positive_price(rest.get("midpoint")), event_timestamp=rest_event_time.isoformat() if rest_event_time else None,
            received_timestamp=rest.get("received_timestamp"), age_ms=age_ms,
            market_session=session, source=str(rest.get("source") or rest.get("quote_source") or "none"),
            source_mode=str(rest.get("source_mode") or (rest.get("diagnostics") or {}).get("source_mode") or "fallback"),
            is_stale=rest_stale, is_degraded=stream is not None or rest_stale,
            quality_flags=tuple(dict.fromkeys(rest_flags)), event_type=None,
            event_id=f"{symbol}:REST:{event_id_value}",
            price_source=price_source, market_session_context=self._session_context(now, session),
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
