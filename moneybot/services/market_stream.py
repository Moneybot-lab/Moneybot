from __future__ import annotations

import asyncio
import importlib
import json
import logging
import math
import random
import time
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Protocol

from .market_data_providers import ExchangeCalendar, MassiveRestClient, NormalizedQuote, ProviderError

STREAM_SCHEMA_VERSION = "market-stream.v1"
REDIS_KEY_VERSION = "v1"
ALLOWED_EVENT_TYPES = frozenset({"A", "AM", "Q", "T"})
EVENT_PREFIXES = {"A": "A", "AM": "AM", "Q": "Q", "T": "T"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _event_time(value: Any) -> datetime | None:
    return MassiveRestClient.normalize_timestamp(value)


@dataclass(frozen=True)
class StreamEvent:
    event_type: str
    symbol: str
    event_timestamp: datetime
    received_timestamp: datetime
    sequence_number: int | None
    provider_event_id: str | None
    payload: Mapping[str, Any]
    quality_flags: tuple[str, ...] = ()
    schema_version: str = STREAM_SCHEMA_VERSION
    source: str = "massive"
    source_mode: str = "websocket"

    @property
    def lag_ms(self) -> int:
        return max(0, int((self.received_timestamp - self.event_timestamp).total_seconds() * 1000))

    def serialized(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "event_timestamp": self.event_timestamp.isoformat(),
            "received_timestamp": self.received_timestamp.isoformat(),
            "payload": dict(self.payload),
            "quality_flags": list(self.quality_flags),
            "lag_ms": self.lag_ms,
        }


class StreamParseError(ValueError):
    pass


class MassiveStreamParser:
    def parse_message(self, raw_message: str | bytes, *, received_at: datetime | None = None) -> list[StreamEvent | dict[str, Any]]:
        received = received_at or _utc_now()
        try:
            decoded = raw_message.decode("utf-8") if isinstance(raw_message, bytes) else raw_message
            payload = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            raise StreamParseError(f"invalid websocket JSON: {exc}") from exc
        messages = payload if isinstance(payload, list) else [payload]
        if not all(isinstance(item, dict) for item in messages):
            raise StreamParseError("websocket message must contain objects")
        output: list[StreamEvent | dict[str, Any]] = []
        for item in messages:
            event_type = str(item.get("ev") or "").upper()
            if event_type == "STATUS":
                output.append({"event_type": "status", "status": str(item.get("status") or ""), "message": str(item.get("message") or "")})
                continue
            output.append(self.parse_event(item, received_at=received))
        return output

    def parse_event(self, item: Mapping[str, Any], *, received_at: datetime) -> StreamEvent:
        event_type = str(item.get("ev") or "").upper()
        if event_type not in ALLOWED_EVENT_TYPES:
            raise StreamParseError(f"unsupported event type: {event_type or 'missing'}")
        symbol = str(item.get("sym") or "").strip().upper()
        if not symbol or symbol == "*":
            raise StreamParseError("event is missing a concrete symbol")
        timestamp_value = item.get("e") if event_type in {"A", "AM"} else item.get("t")
        event_timestamp = _event_time(timestamp_value)
        if event_timestamp is None:
            raise StreamParseError("event is missing a valid timestamp")
        flags: list[str] = []
        sequence = _integer(item.get("q"))
        provider_event_id = str(item.get("i")) if item.get("i") is not None else None

        if event_type in {"A", "AM"}:
            normalized = {
                "open": _number(item.get("o")), "high": _number(item.get("h")), "low": _number(item.get("l")),
                "close": _number(item.get("c")), "volume": _number(item.get("v")), "vwap": _number(item.get("vw")),
                "accumulated_volume": _number(item.get("av")), "official_open": _number(item.get("op")),
                "window_start": _event_time(item.get("s")).isoformat() if _event_time(item.get("s")) else None,
                "window_end": event_timestamp.isoformat(), "average_trade_size": _number(item.get("z")),
            }
            if any(normalized[key] is None for key in ("open", "high", "low", "close", "volume")):
                flags.append("partial_aggregate")
        elif event_type == "Q":
            bid, ask = _number(item.get("bp")), _number(item.get("ap"))
            normalized = {
                "bid": bid, "ask": ask, "bid_size": _number(item.get("bs")), "ask_size": _number(item.get("as")),
                "midpoint": (bid + ask) / 2 if bid and ask and ask >= bid else None,
                "bid_exchange": item.get("bx"), "ask_exchange": item.get("ax"), "conditions": list(item.get("c") or []),
            }
            if bid is not None and ask is not None and ask < bid:
                flags.append("crossed_market")
            if bid is None or ask is None:
                flags.append("incomplete_nbbo")
        else:
            normalized = {
                "price": _number(item.get("p")), "size": _number(item.get("s")), "exchange": item.get("x"),
                "conditions": list(item.get("c") or []), "tape": item.get("z"),
            }
            if normalized["price"] is None:
                flags.append("missing_trade_price")

        return StreamEvent(
            event_type=event_type, symbol=symbol, event_timestamp=event_timestamp,
            received_timestamp=received_at, sequence_number=sequence, provider_event_id=provider_event_id,
            payload=normalized, quality_flags=tuple(flags),
        )


class MarketStreamStateRepository(Protocol):
    def set_latest(self, event: StreamEvent, *, ttl_seconds: int, stale: bool = False) -> float: ...
    def get_latest(self, symbol: str, event_type: str) -> dict[str, Any] | None: ...
    def mark_symbols_stale(self, symbols: Iterable[str], *, reason: str, ttl_seconds: int) -> None: ...
    def publish_updates(self, updates: list[dict[str, Any]]) -> int: ...
    def set_health(self, payload: Mapping[str, Any], *, ttl_seconds: int) -> None: ...
    def get_health(self) -> dict[str, Any]: ...
    def register_demand(self, source: str, symbols: Iterable[str], *, ttl_seconds: int) -> None: ...
    def clear_demand(self, source: str) -> None: ...
    def desired_demand(self) -> dict[str, set[str]]: ...
    def memory_usage_bytes(self) -> int | None: ...


class InMemoryMarketStreamState:
    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self.latest: dict[str, tuple[dict[str, Any], float]] = {}
        self.health: tuple[dict[str, Any], float] | None = None
        self.demands: dict[str, tuple[set[str], float]] = {}
        self.published: list[list[dict[str, Any]]] = []

    @staticmethod
    def _key(symbol: str, event_type: str) -> str:
        return f"moneybot:market:{REDIS_KEY_VERSION}:latest:{event_type}:{symbol.upper()}"

    def set_latest(self, event: StreamEvent, *, ttl_seconds: int, stale: bool = False) -> float:
        started = time.perf_counter()
        payload = event.serialized()
        payload["is_stale"] = bool(stale)
        self.latest[self._key(event.symbol, event.event_type)] = (payload, self.clock() + ttl_seconds)
        return (time.perf_counter() - started) * 1000

    def get_latest(self, symbol: str, event_type: str) -> dict[str, Any] | None:
        entry = self.latest.get(self._key(symbol, event_type))
        if not entry or entry[1] <= self.clock():
            return None
        return dict(entry[0])

    def mark_symbols_stale(self, symbols: Iterable[str], *, reason: str, ttl_seconds: int) -> None:
        wanted = {str(symbol).upper() for symbol in symbols}
        for key, (payload, _expires) in list(self.latest.items()):
            if payload.get("symbol") in wanted:
                updated = dict(payload)
                updated["is_stale"] = True
                updated["quality_flags"] = list(dict.fromkeys([*updated.get("quality_flags", []), reason]))
                self.latest[key] = (updated, self.clock() + ttl_seconds)

    def publish_updates(self, updates: list[dict[str, Any]]) -> int:
        self.published.append([dict(item) for item in updates])
        return len(updates)

    def set_health(self, payload: Mapping[str, Any], *, ttl_seconds: int) -> None:
        self.health = (dict(payload), self.clock() + ttl_seconds)

    def get_health(self) -> dict[str, Any]:
        if not self.health or self.health[1] <= self.clock():
            return {}
        return dict(self.health[0])

    def register_demand(self, source: str, symbols: Iterable[str], *, ttl_seconds: int) -> None:
        normalized = {str(symbol).strip().upper() for symbol in symbols if str(symbol).strip() and str(symbol).strip() != "*"}
        self.demands[source] = (normalized, self.clock() + ttl_seconds)

    def clear_demand(self, source: str) -> None:
        self.demands.pop(source, None)

    def desired_demand(self) -> dict[str, set[str]]:
        now = self.clock()
        expired = [source for source, (_symbols, expiry) in self.demands.items() if expiry <= now]
        for source in expired:
            self.demands.pop(source, None)
        return {source: set(symbols) for source, (symbols, _expiry) in self.demands.items()}

    def memory_usage_bytes(self) -> int | None:
        return len(json.dumps({key: value[0] for key, value in self.latest.items()}))


class RedisMarketStreamState:
    def __init__(self, redis_url: str, *, namespace: str = "moneybot:market:v1") -> None:
        redis_module = importlib.import_module("redis")
        self.client = redis_module.Redis.from_url(redis_url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)
        self.namespace = namespace.rstrip(":")

    def _latest_key(self, symbol: str, event_type: str) -> str:
        return f"{self.namespace}:latest:{event_type}:{symbol.upper()}"

    def set_latest(self, event: StreamEvent, *, ttl_seconds: int, stale: bool = False) -> float:
        started = time.perf_counter()
        payload = event.serialized(); payload["is_stale"] = bool(stale)
        self.client.set(self._latest_key(event.symbol, event.event_type), json.dumps(payload, separators=(",", ":")), ex=ttl_seconds)
        return (time.perf_counter() - started) * 1000

    def get_latest(self, symbol: str, event_type: str) -> dict[str, Any] | None:
        raw = self.client.get(self._latest_key(symbol, event_type))
        return json.loads(raw) if raw else None

    def mark_symbols_stale(self, symbols: Iterable[str], *, reason: str, ttl_seconds: int) -> None:
        for symbol in {str(item).upper() for item in symbols}:
            for event_type in ALLOWED_EVENT_TYPES:
                key = self._latest_key(symbol, event_type)
                raw = self.client.get(key)
                if not raw:
                    continue
                payload = json.loads(raw); payload["is_stale"] = True
                payload["quality_flags"] = list(dict.fromkeys([*payload.get("quality_flags", []), reason]))
                self.client.set(key, json.dumps(payload, separators=(",", ":")), ex=ttl_seconds)

    def publish_updates(self, updates: list[dict[str, Any]]) -> int:
        if not updates:
            return 0
        self.client.publish(f"{self.namespace}:updates", json.dumps({"schema_version": STREAM_SCHEMA_VERSION, "updates": updates}, separators=(",", ":")))
        return len(updates)

    def set_health(self, payload: Mapping[str, Any], *, ttl_seconds: int) -> None:
        self.client.set(f"{self.namespace}:health", json.dumps(dict(payload), separators=(",", ":")), ex=ttl_seconds)

    def get_health(self) -> dict[str, Any]:
        raw = self.client.get(f"{self.namespace}:health")
        return json.loads(raw) if raw else {}

    def register_demand(self, source: str, symbols: Iterable[str], *, ttl_seconds: int) -> None:
        normalized = sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip() and str(symbol).strip() != "*"})
        self.client.set(f"{self.namespace}:demand:{source}", json.dumps(normalized), ex=ttl_seconds)
        self.client.sadd(f"{self.namespace}:demand-sources", source)

    def clear_demand(self, source: str) -> None:
        self.client.delete(f"{self.namespace}:demand:{source}")
        self.client.srem(f"{self.namespace}:demand-sources", source)

    def desired_demand(self) -> dict[str, set[str]]:
        output: dict[str, set[str]] = {}
        for source in self.client.smembers(f"{self.namespace}:demand-sources"):
            raw = self.client.get(f"{self.namespace}:demand:{source}")
            if raw:
                output[source] = set(json.loads(raw))
            else:
                self.client.srem(f"{self.namespace}:demand-sources", source)
        return output

    def memory_usage_bytes(self) -> int | None:
        info = self.client.info("memory")
        return _integer(info.get("used_memory"))


@dataclass(frozen=True)
class SubscriptionPlan:
    desired_by_event: Mapping[str, frozenset[str]]
    reference_counts: Mapping[str, int]
    rejected_symbols: tuple[str, ...]

    @property
    def symbols(self) -> frozenset[str]:
        combined: set[str] = set()
        for symbols in self.desired_by_event.values():
            combined.update(symbols)
        return frozenset(combined)


class SubscriptionManager:
    def __init__(self, *, global_symbol_cap: int = 250, quote_cap: int = 100, trade_cap: int = 50, server_symbols: Iterable[str] = ()) -> None:
        self.global_symbol_cap = max(1, int(global_symbol_cap))
        self.quote_cap = max(0, int(quote_cap))
        self.trade_cap = max(0, int(trade_cap))
        self.server_symbols = tuple(self._normalize(server_symbols))

    @staticmethod
    def _normalize(symbols: Iterable[str]) -> list[str]:
        normalized: list[str] = []
        for value in symbols:
            symbol = str(value).strip().upper()
            if not symbol or symbol == "*" or "*" in symbol:
                continue
            if symbol not in normalized:
                normalized.append(symbol)
        return normalized

    def plan(self, demand: Mapping[str, set[str]]) -> SubscriptionPlan:
        counts: Counter[str] = Counter()
        for symbol in self.server_symbols:
            counts[symbol] += 1
        for source in sorted(demand):
            for symbol in self._normalize(sorted(demand[source])):
                counts[symbol] += 1
        server = list(self.server_symbols)
        dynamic = sorted((symbol for symbol in counts if symbol not in server), key=lambda symbol: (-counts[symbol], symbol))
        ordered = [*server, *dynamic]
        accepted = ordered[: self.global_symbol_cap]
        rejected = tuple(ordered[self.global_symbol_cap :])
        quote_requested = set(self.server_symbols)
        trade_requested = set(self.server_symbols)
        for source, symbols in demand.items():
            normalized = set(self._normalize(symbols))
            if source.startswith(("quick:", "clearview:", "liquidity:")):
                quote_requested.update(normalized)
            if source.startswith(("ticks:", "trades:")):
                trade_requested.update(normalized)
        quote_symbols = [symbol for symbol in accepted if symbol in quote_requested][: self.quote_cap]
        trade_symbols = [symbol for symbol in accepted if symbol in trade_requested][: self.trade_cap]
        desired = {
            "A": frozenset(accepted),
            "AM": frozenset(accepted),
            "Q": frozenset(quote_symbols),
            "T": frozenset(trade_symbols),
        }
        return SubscriptionPlan(desired_by_event=desired, reference_counts=dict(counts), rejected_symbols=rejected)

    @staticmethod
    def commands(actual: Mapping[str, set[str]], plan: SubscriptionPlan) -> tuple[list[str], list[str]]:
        subscribe: list[str] = []
        unsubscribe: list[str] = []
        for event_type in ALLOWED_EVENT_TYPES:
            wanted = set(plan.desired_by_event.get(event_type, frozenset()))
            current = set(actual.get(event_type, set()))
            subscribe.extend(f"{EVENT_PREFIXES[event_type]}.{symbol}" for symbol in sorted(wanted - current))
            unsubscribe.extend(f"{EVENT_PREFIXES[event_type]}.{symbol}" for symbol in sorted(current - wanted))
        if any("*" in channel for channel in [*subscribe, *unsubscribe]):
            raise ValueError("wildcard subscriptions are forbidden")
        return subscribe, unsubscribe


@dataclass
class StreamMetrics:
    reconnect_count: int = 0
    messages_received: Counter[str] = field(default_factory=Counter)
    parse_failures: int = 0
    duplicates: int = 0
    out_of_order: int = 0
    sequence_gaps: int = 0
    dropped_events: int = 0
    coalesced_events: int = 0
    rest_recovery_count: int = 0
    rest_recovery_failures: int = 0
    rest_recovery_duration_ms: float = 0.0
    redis_write_latency_ms: deque[float] = field(default_factory=lambda: deque(maxlen=10000))
    event_lag_ms: deque[float] = field(default_factory=lambda: deque(maxlen=10000))
    shadow_comparisons: int = 0
    shadow_discrepancies: int = 0
    slow_consumer_events: int = 0

    @staticmethod
    def _percentile(values: Iterable[float], percentile: float) -> float | None:
        ordered = sorted(values)
        if not ordered:
            return None
        index = min(len(ordered) - 1, max(0, math.ceil(percentile * len(ordered)) - 1))
        return round(float(ordered[index]), 2)

    def snapshot(self) -> dict[str, Any]:
        valid_messages = sum(self.messages_received.values())
        parse_success_percent = round(valid_messages / (valid_messages + self.parse_failures) * 100, 5) if valid_messages + self.parse_failures else None
        return {
            "reconnect_count": self.reconnect_count,
            "parse_success_percent": parse_success_percent,
            "messages_received": dict(self.messages_received),
            "parse_failures": self.parse_failures, "duplicates": self.duplicates,
            "out_of_order": self.out_of_order, "sequence_gaps": self.sequence_gaps,
            "dropped_events": self.dropped_events, "coalesced_events": self.coalesced_events,
            "rest_recovery_count": self.rest_recovery_count, "rest_recovery_failures": self.rest_recovery_failures,
            "rest_recovery_duration_ms": round(self.rest_recovery_duration_ms, 2),
            "event_to_redis_lag_ms": {"p50": self._percentile(self.event_lag_ms, .50), "p95": self._percentile(self.event_lag_ms, .95), "p99": self._percentile(self.event_lag_ms, .99)},
            "redis_write_latency_ms": {"p50": self._percentile(self.redis_write_latency_ms, .50), "p95": self._percentile(self.redis_write_latency_ms, .95), "p99": self._percentile(self.redis_write_latency_ms, .99)},
            "shadow_comparisons": self.shadow_comparisons, "shadow_discrepancies": self.shadow_discrepancies,
            "slow_consumer_events": self.slow_consumer_events,
        }


@dataclass
class WorkerConfig:
    enabled: bool = False
    shadow_mode: bool = True
    websocket_url: str = "wss://socket.massive.com/stocks"
    symbol_cap: int = 250
    quote_cap: int = 100
    trade_cap: int = 50
    state_ttl_seconds: int = 120
    stale_ttl_seconds: int = 300
    health_ttl_seconds: int = 30
    demand_ttl_seconds: int = 90
    reconcile_seconds: float = 2.0
    publish_coalesce_ms: int = 250
    heartbeat_timeout_seconds: float = 45.0
    reconnect_min_seconds: float = 1.0
    reconnect_max_seconds: float = 30.0
    max_queue: int = 64
    acknowledgement_timeout_seconds: float = 10.0
    rest_shadow_tolerance_bps: float = 50.0
    shadow_compare_seconds: float = 30.0
    slow_consumer_lag_ms: float = 2000.0
    recovery_concurrency: int = 8
    server_symbols: tuple[str, ...] = ()


class MassiveWebSocketWorker:
    def __init__(
        self,
        *,
        api_key: str,
        state: MarketStreamStateRepository,
        rest_client: MassiveRestClient,
        config: WorkerConfig,
        connect_factory: Callable[..., Any],
        clock: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], Any] = asyncio.sleep,
        rng: random.Random | None = None,
        demand_loader: Callable[[], Mapping[str, Iterable[str]]] | None = None,
    ) -> None:
        self.api_key = api_key
        self.state = state
        self.rest_client = rest_client
        self.config = config
        self.connect_factory = connect_factory
        self.clock = clock
        self.sleep = sleep
        self.rng = rng or random.Random()
        self.demand_loader = demand_loader
        self.parser = MassiveStreamParser()
        self.subscriptions = SubscriptionManager(global_symbol_cap=config.symbol_cap, quote_cap=config.quote_cap, trade_cap=config.trade_cap, server_symbols=config.server_symbols)
        self.metrics = StreamMetrics()
        self.actual: dict[str, set[str]] = {event: set() for event in ALLOWED_EVENT_TYPES}
        self._last_event: dict[tuple[str, str], tuple[datetime, int | None, str | None]] = {}
        self._pending_updates: dict[tuple[str, str], dict[str, Any]] = {}
        self._last_publish_monotonic = time.monotonic()
        self._connection_state = "disabled" if not config.enabled else "starting"
        self._last_message_at: datetime | None = None
        self._connected_at: datetime | None = None
        self._last_error: str | None = None
        self._stop = False

    async def _send_action(self, websocket: Any, action: str, channels: list[str], *, check_ack: bool = False) -> None:
        if not channels:
            return
        await websocket.send(json.dumps({"action": action, "params": ",".join(channels)}))
        if not check_ack:
            return
        deadline = time.monotonic() + self.config.acknowledgement_timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"Massive WebSocket {action} acknowledgement timed out")
            raw = await asyncio.wait_for(websocket.recv(), timeout=remaining)
            responses = self.parser.parse_message(raw, received_at=self.clock())
            statuses = [item for item in responses if isinstance(item, dict) and item.get("event_type") == "status"]
            if any(item.get("status") == "success" for item in statuses):
                return
            if statuses:
                raise RuntimeError(f"Massive WebSocket {action} acknowledgement failed: {statuses}")
            await self.process_raw_message(raw)

    async def authenticate(self, websocket: Any) -> None:
        await websocket.send(json.dumps({"action": "auth", "params": self.api_key}))
        deadline = time.monotonic() + self.config.acknowledgement_timeout_seconds
        observed: list[dict[str, Any]] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"Massive WebSocket authentication timed out: {observed}")
            raw = await asyncio.wait_for(websocket.recv(), timeout=remaining)
            responses = self.parser.parse_message(raw, received_at=self.clock())
            statuses = [item for item in responses if isinstance(item, dict)]
            observed.extend(statuses)
            if any(item.get("status") == "auth_success" for item in statuses):
                return
            if any(item.get("status") in {"auth_failed", "error"} for item in statuses):
                raise RuntimeError(f"Massive WebSocket authentication failed: {observed}")

    async def _refresh_external_demand(self) -> None:
        if self.demand_loader is None:
            return
        loaded = await asyncio.to_thread(self.demand_loader)
        for source, symbols in loaded.items():
            self.state.register_demand(source, symbols, ttl_seconds=self.config.demand_ttl_seconds)

    async def reconcile(self, websocket: Any, *, check_ack: bool = False) -> SubscriptionPlan:
        await self._refresh_external_demand()
        plan = self.subscriptions.plan(self.state.desired_demand())
        subscribe, unsubscribe = self.subscriptions.commands(self.actual, plan)
        await self._send_action(websocket, "unsubscribe", unsubscribe, check_ack=check_ack)
        await self._send_action(websocket, "subscribe", subscribe, check_ack=check_ack)
        for channel in unsubscribe:
            event_type, symbol = channel.split(".", 1); self.actual[event_type].discard(symbol)
        for channel in subscribe:
            event_type, symbol = channel.split(".", 1); self.actual[event_type].add(symbol)
        return plan

    def _accept_event(self, event: StreamEvent) -> tuple[bool, bool]:
        key = (event.event_type, event.symbol)
        previous = self._last_event.get(key)
        gap = False
        if previous:
            previous_time, previous_sequence, previous_id = previous
            if event.provider_event_id and previous_id == event.provider_event_id:
                self.metrics.duplicates += 1; return False, False
            if event.sequence_number is not None and previous_sequence is not None:
                if event.sequence_number == previous_sequence:
                    self.metrics.duplicates += 1; return False, False
                if event.sequence_number < previous_sequence:
                    self.metrics.out_of_order += 1; return False, False
                if event.sequence_number > previous_sequence + 1:
                    self.metrics.sequence_gaps += 1; gap = True
            elif event.event_timestamp < previous_time:
                self.metrics.out_of_order += 1; return False, False
            elif event.event_timestamp == previous_time and event.payload == self.state.get_latest(event.symbol, event.event_type):
                self.metrics.duplicates += 1; return False, False
        self._last_event[key] = (event.event_timestamp, event.sequence_number, event.provider_event_id)
        return True, gap

    async def _recover_symbol(self, symbol: str, *, reason: str) -> None:
        started = time.perf_counter(); self.metrics.rest_recovery_count += 1
        self.state.mark_symbols_stale([symbol], reason=reason, ttl_seconds=self.config.stale_ttl_seconds)
        try:
            result = await asyncio.to_thread(self.rest_client.get_quote, symbol)
            quote: NormalizedQuote = result.data
            event = StreamEvent(
                event_type="Q", symbol=symbol, event_timestamp=quote.event_timestamp or result.received_timestamp,
                received_timestamp=result.received_timestamp, sequence_number=quote.sequence_number,
                provider_event_id=quote.provider_event_id,
                payload={"bid": quote.bid, "ask": quote.ask, "bid_size": quote.bid_size, "ask_size": quote.ask_size, "midpoint": quote.midpoint, "recovery_price": quote.price},
                quality_flags=("rest_recovery", reason), source_mode="rest",
            )
            self.state.set_latest(event, ttl_seconds=self.config.state_ttl_seconds, stale=quote.is_stale)
        except (ProviderError, RuntimeError, ValueError):
            self.metrics.rest_recovery_failures += 1
            logging.exception("REST recovery failed for %s", symbol)
        finally:
            self.metrics.rest_recovery_duration_ms += (time.perf_counter() - started) * 1000

    async def _recover_symbols(self, symbols: Iterable[str], *, reason: str) -> None:
        semaphore = asyncio.Semaphore(max(1, self.config.recovery_concurrency))

        async def recover(symbol: str) -> None:
            async with semaphore:
                await self._recover_symbol(symbol, reason=reason)

        await asyncio.gather(*(recover(symbol) for symbol in symbols))

    async def process_raw_message(self, raw: str | bytes) -> None:
        try:
            items = self.parser.parse_message(raw, received_at=self.clock())
        except StreamParseError:
            self.metrics.parse_failures += 1
            return
        for item in items:
            if isinstance(item, dict):
                continue
            self.metrics.messages_received[item.event_type] += 1
            accepted, gap = self._accept_event(item)
            if not accepted:
                continue
            write_latency = self.state.set_latest(item, ttl_seconds=self.config.state_ttl_seconds)
            self.metrics.redis_write_latency_ms.append(write_latency)
            total_lag = item.lag_ms + write_latency
            self.metrics.event_lag_ms.append(total_lag)
            if total_lag > self.config.slow_consumer_lag_ms:
                self.metrics.slow_consumer_events += 1
            key = (item.event_type, item.symbol)
            if key in self._pending_updates:
                self.metrics.coalesced_events += 1
            self._pending_updates[key] = {"event_type": item.event_type, "symbol": item.symbol, "event_timestamp": item.event_timestamp.isoformat()}
            if gap:
                await self._recover_symbol(item.symbol, reason="sequence_gap")
        await self.flush_updates_if_due()

    async def flush_updates_if_due(self, *, force: bool = False) -> None:
        elapsed_ms = (time.monotonic() - self._last_publish_monotonic) * 1000
        if not force and elapsed_ms < self.config.publish_coalesce_ms:
            return
        updates = list(self._pending_updates.values())
        if updates:
            self.state.publish_updates(updates)
            self._pending_updates.clear()
        self._last_publish_monotonic = time.monotonic()

    async def shadow_compare(self, symbols: Iterable[str]) -> None:
        for symbol in symbols:
            stream = self.state.get_latest(symbol, "T") or self.state.get_latest(symbol, "A") or self.state.get_latest(symbol, "AM")
            if not stream:
                continue
            stream_price = _number((stream.get("payload") or {}).get("price") or (stream.get("payload") or {}).get("close"))
            if stream_price is None or stream_price <= 0:
                continue
            try:
                result = await asyncio.to_thread(self.rest_client.get_quote, symbol)
            except ProviderError:
                continue
            rest_price = result.data.price
            if rest_price is None or rest_price <= 0:
                continue
            self.metrics.shadow_comparisons += 1
            difference_bps = abs(stream_price - rest_price) / rest_price * 10_000
            if difference_bps > self.config.rest_shadow_tolerance_bps:
                self.metrics.shadow_discrepancies += 1

    def health_payload(self, plan: SubscriptionPlan | None = None) -> dict[str, Any]:
        actual_counts = {event: len(symbols) for event, symbols in self.actual.items()}
        desired_counts = {event: len(symbols) for event, symbols in (plan.desired_by_event.items() if plan else [])}
        return {
            "schema_version": STREAM_SCHEMA_VERSION, "enabled": self.config.enabled, "shadow_mode": self.config.shadow_mode,
            "connection_state": self._connection_state, "last_message_at": self._last_message_at.isoformat() if self._last_message_at else None,
            "connected_at": self._connected_at.isoformat() if self._connected_at else None,
            "last_error": self._last_error,
            "websocket_url": self.config.websocket_url,
            "server_symbols": list(self.config.server_symbols),
            "desired_symbols": sorted(plan.symbols) if plan else [],
            "desired_subscription_counts": desired_counts, "actual_subscription_counts": actual_counts,
            "symbol_budget": self.config.symbol_cap, "redis_memory_bytes": self.state.memory_usage_bytes(),
            "metrics": self.metrics.snapshot(), "updated_at": self.clock().isoformat(),
        }

    async def run_connection(self, websocket: Any) -> None:
        self._connection_state = "authenticating"
        await self.authenticate(websocket)
        self._connection_state = "connected"
        self._connected_at = self.clock()
        self._last_error = None
        logging.info("Massive WebSocket authenticated url=%s", self.config.websocket_url)
        plan = await self.reconcile(websocket, check_ack=True)
        logging.info(
            "Massive WebSocket subscriptions active symbols=%s counts=%s shadow_mode=%s",
            sorted(plan.symbols),
            {event: len(symbols) for event, symbols in plan.desired_by_event.items()},
            self.config.shadow_mode,
        )
        self.state.set_health(self.health_payload(plan), ttl_seconds=self.config.health_ttl_seconds)
        next_reconcile = time.monotonic() + self.config.reconcile_seconds
        next_shadow_compare = time.monotonic() + self.config.shadow_compare_seconds
        while not self._stop:
            timeout = min(self.config.heartbeat_timeout_seconds, max(.1, next_reconcile - time.monotonic()))
            try:
                raw = await asyncio.wait_for(websocket.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                if self._last_message_at and (self.clock() - self._last_message_at).total_seconds() >= self.config.heartbeat_timeout_seconds:
                    raise RuntimeError("Massive WebSocket heartbeat timeout")
            else:
                self._last_message_at = self.clock()
                await self.process_raw_message(raw)

            if time.monotonic() >= next_reconcile:
                plan = await self.reconcile(websocket, check_ack=True)
                next_reconcile = time.monotonic() + self.config.reconcile_seconds
            await self.flush_updates_if_due()
            if self.config.shadow_mode and time.monotonic() >= next_shadow_compare:
                await self.shadow_compare(plan.symbols)
                next_shadow_compare = time.monotonic() + self.config.shadow_compare_seconds
            self.state.set_health(self.health_payload(plan), ttl_seconds=self.config.health_ttl_seconds)

    async def run(self) -> None:
        if not self.config.enabled:
            self._connection_state = "disabled"
            self.state.set_health(self.health_payload(), ttl_seconds=self.config.health_ttl_seconds)
            return
        attempt = 0
        while not self._stop:
            await self._refresh_external_demand()
            plan = self.subscriptions.plan(self.state.desired_demand())
            if not plan.symbols:
                self._connection_state = "idle_no_demand"
                self.state.set_health(self.health_payload(plan), ttl_seconds=self.config.health_ttl_seconds)
                await self.sleep(self.config.reconcile_seconds)
                continue
            try:
                async with self.connect_factory(
                    self.config.websocket_url, open_timeout=10, ping_interval=20, ping_timeout=20,
                    close_timeout=10, max_size=1_048_576, max_queue=self.config.max_queue,
                ) as websocket:
                    if attempt:
                        self.metrics.reconnect_count += 1
                    self.actual = {event: set() for event in ALLOWED_EVENT_TYPES}
                    await self.run_connection(websocket)
                    attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._connection_state = "reconnecting"
                self._last_error = f"{type(exc).__name__}: {exc}"
                logging.exception("Massive WebSocket connection failed; reconnecting symbols=%s", sorted(plan.symbols))
                self.actual = {event: set() for event in ALLOWED_EVENT_TYPES}
                self.state.mark_symbols_stale(plan.symbols, reason="stream_disconnected", ttl_seconds=self.config.stale_ttl_seconds)
                await self._recover_symbols(plan.symbols, reason="stream_reconnect")
                self.state.set_health(self.health_payload(plan), ttl_seconds=self.config.health_ttl_seconds)
                delay = min(self.config.reconnect_max_seconds, self.config.reconnect_min_seconds * (2**attempt))
                delay *= self.rng.uniform(.8, 1.2)
                attempt += 1
                await self.sleep(delay)

    def stop(self) -> None:
        self._stop = True


def worker_config_from_env(env: Mapping[str, str]) -> WorkerConfig:
    def enabled(name: str, default: str) -> bool:
        return str(env.get(name, default)).strip().lower() in {"1", "true", "yes", "on"}

    server_symbols = tuple(symbol.strip().upper() for symbol in str(env.get("MASSIVE_STREAM_SERVER_SYMBOLS", "SPY,QQQ")).split(",") if symbol.strip() and "*" not in symbol)
    return WorkerConfig(
        enabled=enabled("MASSIVE_STREAM_ENABLED", "false"),
        shadow_mode=enabled("MASSIVE_STREAM_SHADOW_MODE", "true"),
        websocket_url=str(env.get("MASSIVE_STREAM_URL", "wss://socket.massive.com/stocks")),
        symbol_cap=int(env.get("MASSIVE_STREAM_SYMBOL_CAP", "250")),
        quote_cap=int(env.get("MASSIVE_STREAM_QUOTE_CAP", "100")),
        trade_cap=int(env.get("MASSIVE_STREAM_TRADE_CAP", "50")),
        state_ttl_seconds=int(env.get("MASSIVE_STREAM_STATE_TTL_SECONDS", "120")),
        stale_ttl_seconds=int(env.get("MASSIVE_STREAM_STALE_TTL_SECONDS", "300")),
        health_ttl_seconds=int(env.get("MASSIVE_STREAM_HEALTH_TTL_SECONDS", "30")),
        demand_ttl_seconds=int(env.get("MASSIVE_STREAM_DEMAND_TTL_SECONDS", "90")),
        reconcile_seconds=float(env.get("MASSIVE_STREAM_RECONCILE_SECONDS", "2")),
        publish_coalesce_ms=int(env.get("MASSIVE_STREAM_PUBLISH_COALESCE_MS", "250")),
        heartbeat_timeout_seconds=float(env.get("MASSIVE_STREAM_HEARTBEAT_TIMEOUT_SECONDS", "45")),
        reconnect_min_seconds=float(env.get("MASSIVE_STREAM_RECONNECT_MIN_SECONDS", "1")),
        reconnect_max_seconds=float(env.get("MASSIVE_STREAM_RECONNECT_MAX_SECONDS", "30")),
        max_queue=int(env.get("MASSIVE_STREAM_MAX_QUEUE", "64")),
        acknowledgement_timeout_seconds=float(env.get("MASSIVE_STREAM_ACK_TIMEOUT_SECONDS", "10")),
        rest_shadow_tolerance_bps=float(env.get("MASSIVE_STREAM_REST_TOLERANCE_BPS", "50")),
        shadow_compare_seconds=float(env.get("MASSIVE_STREAM_SHADOW_COMPARE_SECONDS", "30")),
        slow_consumer_lag_ms=float(env.get("MASSIVE_STREAM_SLOW_CONSUMER_LAG_MS", "2000")),
        recovery_concurrency=int(env.get("MASSIVE_STREAM_RECOVERY_CONCURRENCY", "8")),
        server_symbols=server_symbols,
    )


def create_stream_state(redis_url: str | None) -> MarketStreamStateRepository:
    return RedisMarketStreamState(redis_url) if redis_url else InMemoryMarketStreamState()


def register_demand_safely(state: MarketStreamStateRepository | None, source: str, symbols: Iterable[str], *, ttl_seconds: int) -> bool:
    if state is None:
        return False
    try:
        state.register_demand(source, symbols, ttl_seconds=ttl_seconds)
        return True
    except Exception:  # noqa: BLE001
        logging.exception("Unable to register market-stream demand source=%s", source)
        return False
