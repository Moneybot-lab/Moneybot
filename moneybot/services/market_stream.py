from __future__ import annotations

import asyncio
import importlib
import json
import logging
import math
import os
import random
import re
import uuid
import time
from collections import Counter, OrderedDict, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from threading import RLock
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Protocol

from .market_data_providers import ExchangeCalendar, MassiveRestClient, NormalizedQuote, ProviderError
from .stream_integrity import (COMMIT_LUA, STALE_LUA, MAX_WATERMARKS, IntegrityRejection, MemoryWatermarks,
                               QUALITY_LUA, VERIFY_LUA, Watermark, timestamp_from_us, timestamp_us)
from . import stream_initialization

STREAM_SCHEMA_VERSION = "market-stream.v1"
REDIS_KEY_VERSION = "v1"
ALLOWED_EVENT_TYPES = frozenset({"A", "AM", "Q", "T"})
EVENT_PREFIXES = {"A": "A", "AM": "AM", "Q": "Q", "T": "T"}
_SIP_CALENDAR = ExchangeCalendar()


class _BoundedCooldown(OrderedDict):
    """Eviction never gives back an unexpired request budget.

    Unknown symbols conservatively inherit the most recent retired timestamp.
    Recovery and shadow have separate instances and never share that barrier.
    """
    def __init__(self, capacity: int = 1024):
        super().__init__()
        self.capacity = capacity
        self.retired_at: float | None = None

    def get(self, key, default=None):
        return super().get(key, self.retired_at if self.retired_at is not None else default)

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.move_to_end(key)
        if len(self) > self.capacity:
            _, retired = self.popitem(last=False)
            self.retired_at = max(retired, self.retired_at if self.retired_at is not None else retired)


def normalize_source_revision(value: Any) -> str | None:
    """Deployment metadata is a claim, not attestation; invalid input stays absent."""
    return value.lower() if isinstance(value, str) and re.fullmatch(r'[0-9a-fA-F]{40}', value) else None


def stream_commit_version(value: Any) -> tuple[str, int] | None:
    """Internal store revision, scoped to a worker and ticker; never SIP q."""
    if not isinstance(value, Mapping):
        return None
    epoch, revision = value.get("worker_instance_id"), value.get("revision")
    if not isinstance(epoch, str) or type(revision) is not int or revision <= 0:
        return None
    try:
        if str(uuid.UUID(epoch)) != epoch:
            return None
    except ValueError:
        return None
    return epoch, revision


def _trusted_utc(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("an aware clock/timestamp is required")
    return value.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _condition_values(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _event_time(value: Any) -> datetime | None:
    return MassiveRestClient.normalize_timestamp(value)


def _stored_event_time(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return timestamp.astimezone(timezone.utc) if timestamp.tzinfo is not None else None
    return _event_time(value)


def _bounded_reconnect_delay(*, attempt: int, min_seconds: float, max_seconds: float, jitter: float) -> float:
    capped_attempt = max(0, min(int(attempt), 30))
    base_delay = min(max_seconds, min_seconds * (2**capped_attempt))
    return base_delay * jitter


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
    stream_commit: Mapping[str, Any] | None = None

    @property
    def trade_identity(self) -> tuple[str, int, int, str] | None:
        """Only reported integer venues establish a complete trade-ID scope.

        Absent TRF is unknown here; the saved contract defines no not-applicable
        sentinel. Do not coerce missing, boolean or string venues into an ID.
        """
        exchange, trf = self.payload.get("exchange"), self.payload.get("trf_id")
        if (self.event_type != "T" or not isinstance(self.provider_event_id, str) or not self.provider_event_id or
                type(exchange) is not int or exchange < 0 or
                type(trf) is not int or trf < 0):
            return None
        return self.symbol, exchange, trf, self.provider_event_id

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
        # Q.i carries indicators, not a unique quote identity. Keep the
        # existing trade-ID behavior without inventing an aggregate contract.
        provider_event_id = str(item.get("i")) if event_type != "Q" and item.get("i") is not None else None

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
                "bid_exchange": item.get("bx"), "ask_exchange": item.get("ax"), "conditions": _condition_values(item.get("c")),
                "indicators": _condition_values(item.get("i")),
            }
            if bid is not None and ask is not None and ask < bid:
                flags.append("crossed_market")
            if bid is None or ask is None:
                flags.append("incomplete_nbbo")
        else:
            # The trade contract supplies a string ID. Coercing malformed IDs
            # would invent equality before applying the ordering safeguards.
            if not isinstance(item.get("i"), str):
                provider_event_id = None
            normalized = {
                "price": _number(item.get("p")), "size": _number(item.get("s")), "exchange": item.get("x"),
                "trf_id": item.get("trfi"),
                "conditions": _condition_values(item.get("c")), "tape": item.get("z"),
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
    def integrity_floor(self, symbol: str, event_type: str) -> tuple[datetime | None, bool]: ...
    def integrity_verified(self, symbol: str, event_type: str, stamp: datetime, version: tuple[str, int]) -> bool: ...
    def integrity_quality(self, symbol: str, event_type: str, stamp: datetime, version: tuple[str, int]) -> str: ...
    def hold_quality(self, symbol: str, event_type: str, owner: str, serial: int, reason: str) -> None: ...
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
        self.latest: OrderedDict[str, tuple[dict[str, Any], float]] = OrderedDict()
        self.health: tuple[dict[str, Any], float] | None = None
        self.demands: dict[str, tuple[set[str], float]] = {}
        self.published: list[list[dict[str, Any]]] = []
        self.integrity = MemoryWatermarks()
        self.quality: dict[str, tuple[str, int, str]] = {}
        self._commit_lock = RLock()

    @staticmethod
    def _key(symbol: str, event_type: str) -> str:
        return f"moneybot:market:{REDIS_KEY_VERSION}:latest:{event_type}:{symbol.upper()}"

    def set_latest(self, event: StreamEvent, *, ttl_seconds: int, stale: bool = False) -> float:
        with self._commit_lock:
            return self._set_latest(event, ttl_seconds=ttl_seconds, stale=stale)

    def _set_latest(self, event: StreamEvent, *, ttl_seconds: int, stale: bool = False) -> float:
        started = time.perf_counter()
        payload = event.serialized()
        payload["is_stale"] = bool(stale)
        version = stream_commit_version(getattr(event, "stream_commit", None))
        if version is not None:
            field = f"{event.event_type}:{event.symbol}"
            stamp, token = timestamp_us(event.event_timestamp), f"{version[0]}:{version[1]}"
            if event.source_mode == "rest":
                for channel in ALLOWED_EVENT_TYPES:
                    floor, _ = self.integrity.floor(f"{channel}:{event.symbol}")
                    if floor > stamp or (channel == event.event_type and floor == stamp):
                        raise IntegrityRejection("recovery_integrity_hold")
            self.integrity.reserve(field, stamp, token, recovery=event.source_mode == "rest")
        self.latest[self._key(event.symbol, event.event_type)] = (payload, self.clock() + ttl_seconds)
        self.latest.move_to_end(self._key(event.symbol, event.event_type))
        if len(self.latest) > MAX_WATERMARKS:
            self.latest.popitem(last=False)
        if version is not None:
            self.integrity.commit(field, stamp, token)
            self.quality = {key: value for key, value in self.quality.items() if key in self.integrity.records}
            self.quality[field] = (token, 0, 'LIVE')
        return (time.perf_counter() - started) * 1000

    def integrity_floor(self, symbol: str, event_type: str) -> tuple[datetime | None, bool]:
        with self._commit_lock:
            floor, uncertain = self.integrity.floor(f"{event_type}:{symbol}")
        return (timestamp_from_us(floor) if floor else None), uncertain

    def integrity_verified(self, symbol: str, event_type: str, stamp: datetime, version: tuple[str, int]) -> bool:
        with self._commit_lock:
            record = self.integrity.records.get(f"{event_type}:{symbol}")
            return (record is not None and record.status == 'c' and record.committed_us == timestamp_us(stamp)
                    and record.token == f"{version[0]}:{version[1]}")

    def integrity_quality(self, symbol: str, event_type: str, stamp: datetime, version: tuple[str, int]) -> str:
        with self._commit_lock:
            if not self.integrity_verified(symbol, event_type, stamp, version):
                return 'UNKNOWN'
            token, _, quality = self.quality.get(f'{event_type}:{symbol}', ('', 0, 'UNKNOWN'))
            return quality if token == f'{version[0]}:{version[1]}' else 'UNKNOWN'

    def hold_quality(self, symbol: str, event_type: str, owner: str, serial: int, reason: str) -> None:
        with self._commit_lock:
            field = f'{event_type}:{symbol}'
            current = self.quality.get(field)
            if current and current[0].startswith(owner + ':') and serial > current[1]:
                self.quality[field] = (current[0], serial, 'DEGRADED')

    def integrity_preflight(self, *, required=None, known_contexts=()) -> dict[str, str]:
        return {'status': 'COMPATIBLE_VERIFIED_STATE', 'scope': 'process_local_only'}

    def aggregate_context(self, symbol: str, event_type: str):
        return None

    def get_latest(self, symbol: str, event_type: str) -> dict[str, Any] | None:
        with self._commit_lock:
            entry = self.latest.get(self._key(symbol, event_type))
            if not entry or entry[1] <= self.clock():
                self.latest.pop(self._key(symbol, event_type), None)
                return None
            return dict(entry[0])

    def mark_symbols_stale(self, symbols: Iterable[str], *, reason: str, ttl_seconds: int) -> None:
        with self._commit_lock:
            self._mark_symbols_stale(symbols, reason=reason, ttl_seconds=ttl_seconds)

    def _mark_symbols_stale(self, symbols: Iterable[str], *, reason: str, ttl_seconds: int) -> None:
        wanted = {str(symbol).upper() for symbol in symbols}
        for key, (payload, _expires) in list(self.latest.items()):
            if payload.get("symbol") in wanted:
                updated = dict(payload)
                updated["is_stale"] = True
                updated["quality_flags"] = list(dict.fromkeys([*updated.get("quality_flags", []), reason]))
                self.latest[key] = (updated, self.clock() + ttl_seconds)

    def publish_updates(self, updates: list[dict[str, Any]]) -> int:
        self.published.append([dict(item) for item in updates])
        if len(self.published) > MAX_WATERMARKS:
            del self.published[0]
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
        with self._commit_lock:
            return len(json.dumps({key: value[0] for key, value in self.latest.items()}))


class RedisMarketStreamState:
    def __init__(self, redis_url: str, *, namespace: str = "moneybot:market:v1") -> None:
        redis_module = importlib.import_module("redis")
        self.client = redis_module.Redis.from_url(redis_url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)
        self.namespace = namespace.rstrip(":")
        self.integrity_authorization = None
        self._expected_integrity_control = ''
        authorization_path = os.environ.get('MASSIVE_STREAM_INTEGRITY_AUTHORIZATION_FILE')
        if authorization_path:
            with open(authorization_path, encoding='utf-8') as handle:
                self.integrity_authorization = json.load(handle)
            self._expected_integrity_control = stream_initialization.validate_authorization(self.integrity_authorization, self.namespace, _utc_now())

    def integrity_preflight(self, *, required=None, known_contexts=()) -> dict[str, str]:
        result = stream_initialization.preflight(self.client, self.namespace, getattr(self, 'integrity_authorization', None),
            required=required, known_contexts=known_contexts)
        if result.get('status') == 'COMPATIBLE_VERIFIED_STATE':
            self._expected_integrity_control = stream_initialization.validate_authorization(self.integrity_authorization, self.namespace, _utc_now())
        return result

    def reconcile_integrity(self, authorization, *, now=None):
        result = stream_initialization.reconcile(self.client, self.namespace, authorization, now=now)
        self.integrity_authorization = dict(authorization)
        self._expected_integrity_control = result['completion_record']
        return result

    def _latest_key(self, symbol: str, event_type: str) -> str:
        return f"{self.namespace}:latest:{event_type}:{symbol.upper()}"

    def set_latest(self, event: StreamEvent, *, ttl_seconds: int, stale: bool = False) -> float:
        started = time.perf_counter()
        payload = event.serialized(); payload["is_stale"] = bool(stale)
        encoded = json.dumps(payload, separators=(",", ":"))
        version = stream_commit_version(getattr(event, "stream_commit", None))
        if version is None:
            # Existing direct fixture/legacy repository writes retain their API.
            # Every worker price write carries a local commit identity.
            if self.client.get(f'{self.namespace}:integrity-control'):
                raise IntegrityRejection('legacy_writer_fenced')
            self.client.set(self._latest_key(event.symbol, event.event_type), encoded, ex=ttl_seconds)
        else:
            floor = 0
            channels = ALLOWED_EVENT_TYPES if event.source_mode == "rest" else (event.event_type,)
            for channel in channels:
                current = self.get_latest(event.symbol, channel)
                current_time = _stored_event_time(current.get("event_timestamp")) if current else None
                if current_time is not None:
                    floor = max(floor, timestamp_us(current_time))
            result = self.client.eval(COMMIT_LUA, 6, self._latest_key(event.symbol, event.event_type),
                f"{self.namespace}:integrity", f"{self.namespace}:integrity-lru",
                f"{self.namespace}:integrity-quality", f"{self.namespace}:integrity-control", f"{self.namespace}:integrity-context",
                f"{event.event_type}:{event.symbol}", timestamp_us(event.event_timestamp), f"{version[0]}:{version[1]}",
                encoded, ttl_seconds, MAX_WATERMARKS, int(event.source_mode == "rest"), floor,
                *(f"{channel}:{event.symbol}" for channel in ("Q", "T", "A", "AM")),
                json.dumps({'timestamp_us': timestamp_us(event.event_timestamp), 'sequence_number': event.sequence_number,
                            'provider_event_id': event.provider_event_id, 'token': f'{version[0]}:{version[1]}'}, separators=(',', ':')),
                getattr(self, '_expected_integrity_control', ''))
            if result not in ("committed", b"committed"):
                raise IntegrityRejection(result.decode() if isinstance(result, bytes) else str(result))
            # Issued only after the price-commit response arrived. A lost price
            # reply leaves pending quality even when the first script reached c.
            confirmation = self.publish_quality(event.symbol, event.event_type, version, 0, 'confirmed', 'write_acknowledged')
            if confirmation not in {'updated', 'superseded'}:
                raise IntegrityRejection('confirmation_not_published')
        return (time.perf_counter() - started) * 1000

    def integrity_floor(self, symbol: str, event_type: str) -> tuple[datetime | None, bool]:
        key = f"{self.namespace}:integrity"
        schema = self.client.hget(key, "__schema__")
        if schema is None:
            # Absence cannot distinguish a new datastore from lost history.
            # A separately reviewed migration must seed this non-expiring
            # metadata; a worker must never invent a fresh epoch on restart.
            raise IntegrityRejection("integrity_state_missing")
        control = self.client.get(f'{self.namespace}:integrity-control')
        if not control or not control.startswith('ready|2|') or schema != control or control != getattr(self, '_expected_integrity_control', ''):
            raise IntegrityRejection("invalid_integrity_schema")
        value = self.client.hget(key, f"{event_type}:{symbol}")
        if value is not None:
            record = Watermark.decode(value.decode() if isinstance(value, bytes) else value)
            return timestamp_from_us(record.reserved_us), record.status == 'u'
        retired = int(self.client.hget(key, "__retired__") or 0)
        return (timestamp_from_us(retired) if retired else None), bool(retired)

    def integrity_verified(self, symbol: str, event_type: str, stamp: datetime, version: tuple[str, int]) -> bool:
        return self.integrity_quality(symbol, event_type, stamp, version) in {'LIVE', 'DEGRADED'}

    def integrity_quality(self, symbol: str, event_type: str, stamp: datetime, version: tuple[str, int]) -> str:
        result = self.client.eval(VERIFY_LUA, 4, f'{self.namespace}:integrity', f'{self.namespace}:integrity-lru',
            f'{self.namespace}:integrity-quality', f'{self.namespace}:integrity-control',
            f'{event_type}:{symbol}', timestamp_us(stamp), f'{version[0]}:{version[1]}', getattr(self, '_expected_integrity_control', ''))
        return result if result in {'LIVE', 'DEGRADED', 'UNACKNOWLEDGED'} else 'UNKNOWN'

    def publish_quality(self, symbol, event_type, version, serial, status, reason):
        if status not in {'confirmed', 'held'} or type(serial) is not int or serial < 0 or '|' in reason:
            raise ValueError('invalid_quality_publication')
        return self.client.eval(QUALITY_LUA, 3, f'{self.namespace}:integrity', f'{self.namespace}:integrity-quality',
            f'{self.namespace}:integrity-control', f'{event_type}:{symbol}', f'{version[0]}:{version[1]}', serial, status, reason,
            getattr(self, '_expected_integrity_control', ''))

    def hold_quality(self, symbol: str, event_type: str, owner: str, serial: int, reason: str) -> None:
        current = self.get_latest(symbol, event_type)
        version = stream_commit_version(current.get('stream_commit')) if current else None
        if version is not None and version[0] == owner:
            self.publish_quality(symbol, event_type, version, serial, 'held', reason)

    def aggregate_context(self, symbol, event_type):
        raw = self.client.hget(f'{self.namespace}:integrity-context', f'{event_type}:{symbol}')
        if raw is None:
            return None
        value = json.loads(raw)
        record_raw = self.client.hget(f'{self.namespace}:integrity', f'{event_type}:{symbol}')
        record = Watermark.decode(record_raw) if record_raw else None
        if (record is None or record.status != 'c' or record.committed_us != value.get('timestamp_us')
                or value.get('token') != record.token or type(value.get('sequence_number')) is not int):
            raise IntegrityRejection('aggregate_context_unconfirmed')
        return (timestamp_from_us(value['timestamp_us']), value['sequence_number'], value['provider_event_id'], None)

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
                self.client.eval(STALE_LUA, 2, key, f'{self.namespace}:integrity-control', raw, json.dumps(payload, separators=(",", ":")), ttl_seconds,
                    getattr(self, '_expected_integrity_control', ''))

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
    ambiguous_sequence_regressions: int = 0
    freshness_holds: int = 0
    timestamp_rejections: int = 0
    invalid_clock_frames: int = 0
    dropped_events: int = 0
    coalesced_events: int = 0
    rest_recovery_count: int = 0
    rest_recovery_failures: int = 0
    rest_recovery_duration_ms: float = 0.0
    rest_recovery_queued: int = 0
    rest_recovery_deduped: int = 0
    rest_recovery_queue_drops: int = 0
    redis_write_latency_ms: deque[float] = field(default_factory=lambda: deque(maxlen=10000))
    # Preserve legacy age-plus-own-write samples; they are not local elapsed time.
    event_lag_ms: deque[float] = field(default_factory=lambda: deque(maxlen=10000))
    provider_event_age_ms: deque[float] = field(default_factory=lambda: deque(maxlen=1000))
    local_ingest_to_store_ms: deque[float] = field(default_factory=lambda: deque(maxlen=1000))
    shadow_comparisons: int = 0
    shadow_discrepancies: int = 0
    shadow_skipped: int = 0
    shadow_budget_exhausted: int = 0
    rest_recovery_rejected: int = 0
    stale_state_writes_rejected: int = 0
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
            "ambiguous_sequence_regressions": self.ambiguous_sequence_regressions,
            "freshness_holds": self.freshness_holds, "timestamp_rejections": self.timestamp_rejections,
            "invalid_clock_frames": self.invalid_clock_frames,
            "dropped_events": self.dropped_events, "coalesced_events": self.coalesced_events,
            "rest_recovery_count": self.rest_recovery_count, "rest_recovery_failures": self.rest_recovery_failures,
            "rest_recovery_duration_ms": round(self.rest_recovery_duration_ms, 2),
            "rest_recovery_queued": self.rest_recovery_queued,
            "rest_recovery_deduped": self.rest_recovery_deduped,
            "rest_recovery_queue_drops": self.rest_recovery_queue_drops,
            "event_to_redis_lag_ms": {"p50": self._percentile(self.event_lag_ms, .50), "p95": self._percentile(self.event_lag_ms, .95), "p99": self._percentile(self.event_lag_ms, .99)},
            "redis_write_latency_ms": {"p50": self._percentile(self.redis_write_latency_ms, .50), "p95": self._percentile(self.redis_write_latency_ms, .95), "p99": self._percentile(self.redis_write_latency_ms, .99)},
            "provider_event_age_ms": {"p50": self._percentile(self.provider_event_age_ms, .50), "p95": self._percentile(self.provider_event_age_ms, .95), "p99": self._percentile(self.provider_event_age_ms, .99)},
            "local_ingest_to_store_ms": {"p50": self._percentile(self.local_ingest_to_store_ms, .50), "p95": self._percentile(self.local_ingest_to_store_ms, .95), "p99": self._percentile(self.local_ingest_to_store_ms, .99)},
            "shadow_comparisons": self.shadow_comparisons, "shadow_discrepancies": self.shadow_discrepancies,
            "shadow_skipped": self.shadow_skipped, "shadow_budget_exhausted": self.shadow_budget_exhausted,
            "rest_recovery_rejected": self.rest_recovery_rejected,
            "stale_state_writes_rejected": self.stale_state_writes_rejected,
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
    publish_coalesce_ms: int = 100
    heartbeat_timeout_seconds: float = 0.0
    reconnect_min_seconds: float = 1.0
    reconnect_max_seconds: float = 30.0
    max_queue: int = 1024
    acknowledgement_timeout_seconds: float = 10.0
    rest_shadow_tolerance_bps: float = 50.0
    shadow_compare_seconds: float = 30.0
    shadow_batch_timeout_seconds: float = 10.0
    shadow_batch_symbol_limit: int = 50
    slow_consumer_lag_ms: float = 2000.0
    health_update_seconds: float = 5.0
    recovery_concurrency: int = 2
    recovery_queue_max: int = 512
    recovery_cooldown_seconds: float = 30.0
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
        source_revision: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.state = state
        self.rest_client = rest_client
        self.config = config
        self.connect_factory = connect_factory
        self.clock = clock
        self.worker_instance_id = str(uuid.uuid4())
        self._quality_revision = 0
        started = clock()
        self.worker_started_at = _trusted_utc(started)
        self.source_revision = normalize_source_revision(source_revision)
        self.sleep = sleep
        self.rng = rng or random.Random()
        self.demand_loader = demand_loader
        self.parser = MassiveStreamParser()
        self.subscriptions = SubscriptionManager(global_symbol_cap=config.symbol_cap, quote_cap=config.quote_cap, trade_cap=config.trade_cap, server_symbols=config.server_symbols)
        self.metrics = StreamMetrics()
        self.actual: dict[str, set[str]] = {event: set() for event in ALLOWED_EVENT_TYPES}
        # One observation per existing event/symbol key, never an ID history.
        self._last_event: dict[tuple[str, str], tuple[
            datetime, int | None, Any,
            date | None,
        ]] = {}
        self._pending_updates: dict[tuple[str, str], dict[str, Any]] = {}
        self._last_publish_monotonic = time.monotonic()
        self._connection_state = "disabled" if not config.enabled else "starting"
        self._last_message_at: datetime | None = None
        self._connected_at: datetime | None = None
        self._last_error: str | None = None
        self._stop = False
        self._recovery_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=max(1, config.recovery_queue_max))
        self._recovery_tasks: set[asyncio.Task[None]] = set()
        self._recovery_inflight: set[str] = set()
        self._last_recovery_monotonic = _BoundedCooldown()
        self._shadow_inflight: dict[str, asyncio.Future[None]] = {}
        self._last_shadow_monotonic = _BoundedCooldown()
        # One admitted operation at a time: neither Redis ordering nor the
        # executor queue depends on the number of callers waiting for admission.
        self._state_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="market-stream-state")
        self._state_lock = asyncio.Lock()
        self._processing_lock = asyncio.Lock()
        self._pending_stale: dict[str, tuple[str, int]] = {}
        self._recovery_generation: dict[str, int] = {}
        self._last_health_monotonic = float("-inf")
        self._closed = False
        self._rest_executor = ThreadPoolExecutor(max_workers=max(1, config.recovery_concurrency), thread_name_prefix="market-stream-rest")
        self._rest_slots = asyncio.Semaphore(max(1, config.recovery_concurrency))
        self._rest_futures: dict[str, asyncio.Future[Any]] = {}
        self._shadow_task: asyncio.Task[None] | None = None
        self._symbol_generation: Counter[str] = Counter()
        self._stored_timestamps: dict[tuple[str, str], datetime | None] = {}
        self._history_keys: set[tuple[str, str]] = set()
        self._shadow_offset = 0
        # Bounded diagnostics, not ordering history or a new recovery queue.
        self._freshness_degraded: dict[tuple[str, str], str] = {}
        self._freshness_degraded_overflow = False
        self._clock_trusted = True
        self._stream_revision = 0  # Worker-scoped allocation, not a price/q watermark.

    async def _state_call(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        async with self._state_lock:
            future = asyncio.get_running_loop().run_in_executor(self._state_executor, partial(function, *args, **kwargs))
            # Canceling an await cannot cancel an already-running Redis write.
            # Keep admission locked until it finishes, then propagate cancellation.
            cancelled = False
            while True:
                try:
                    result = await asyncio.shield(future)
                    break
                except asyncio.CancelledError:
                    if future.cancelled():
                        raise
                    cancelled = True
            if cancelled:
                raise asyncio.CancelledError
            return result

    async def _publish_health(self, plan: SubscriptionPlan | None = None, *, force: bool = False) -> None:
        interval = min(max(.1, self.config.health_update_seconds), max(.1, self.config.health_ttl_seconds / 2))
        if not force and time.monotonic() - self._last_health_monotonic < interval:
            return
        # Copy the bounded samples on the loop; sorting/calculation and Redis
        # INFO/SET run off-loop without iterating concurrently-mutated samples.
        metrics = replace(self.metrics, messages_received=Counter(self.metrics.messages_received),
                          redis_write_latency_ms=tuple(self.metrics.redis_write_latency_ms),
                          event_lag_ms=tuple(self.metrics.event_lag_ms),
                          provider_event_age_ms=tuple(self.metrics.provider_event_age_ms),
                          local_ingest_to_store_ms=tuple(self.metrics.local_ingest_to_store_ms))
        def publish() -> None:
            self.state.set_health(self.health_payload(plan, metrics=metrics), ttl_seconds=self.config.health_ttl_seconds)
        await self._state_call(publish)
        self._last_health_monotonic = time.monotonic()

    async def _flush_pending_stale(self) -> None:
        pending = dict(self._pending_stale)
        for symbol, (reason, generation) in pending.items():
            def mark_if_unchanged() -> None:
                if self._symbol_generation[symbol] == generation:
                    self.state.mark_symbols_stale([symbol], reason=reason, ttl_seconds=self.config.stale_ttl_seconds)
            try:
                await self._state_call(mark_if_unchanged)
            finally:
                if self._pending_stale.get(symbol) == (reason, generation):
                    self._pending_stale.pop(symbol, None)

    async def aclose(self) -> None:
        """Join owned tasks and admitted state work before releasing resources."""
        if self._closed:
            return
        self.stop()
        tasks = list(self._recovery_tasks)
        if self._shadow_task is not None:
            tasks.append(self._shadow_task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # Cancellation does not release slots occupied by synchronous provider
        # calls. Join those calls (provider timeout/retry remains unchanged).
        futures = list(self._rest_futures.values())
        if futures:
            await asyncio.gather(*(asyncio.shield(future) for future in futures), return_exceptions=True)
        self._rest_executor.shutdown(wait=False, cancel_futures=True)
        while not self._recovery_queue.empty():
            symbol, _reason = self._recovery_queue.get_nowait()
            self._recovery_inflight.discard(symbol)
            self._recovery_generation.pop(symbol, None)
            self._recovery_queue.task_done()
        await self._flush_pending_stale()
        async with self._state_lock:
            self._state_executor.shutdown(wait=False, cancel_futures=True)
        self._closed = True

    def _trusted_clock(self) -> datetime | None:
        try:
            now = _trusted_utc(self.clock())
        except Exception:  # A failed clock must not admit data or invent current time.
            self._clock_trusted = False
            return None
        self._clock_trusted = True
        return now

    def _record_websocket_frame(self) -> None:
        now = self._trusted_clock()
        if now is not None:
            self._last_message_at = now

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
            ingest_started = time.monotonic()
            self._record_websocket_frame()
            responses = await self._state_call(self.parser.parse_message, raw, received_at=self.clock())
            statuses = [item for item in responses if isinstance(item, dict) and item.get("event_type") == "status"]
            if any(item.get("status") == "success" for item in statuses):
                return
            if statuses:
                raise RuntimeError(f"Massive WebSocket {action} acknowledgement failed: {statuses}")
            await self.process_raw_message(raw, ingest_started_monotonic=ingest_started)

    async def authenticate(self, websocket: Any) -> None:
        await websocket.send(json.dumps({"action": "auth", "params": self.api_key}))
        deadline = time.monotonic() + self.config.acknowledgement_timeout_seconds
        observed: list[dict[str, Any]] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"Massive WebSocket authentication timed out: {observed}")
            raw = await asyncio.wait_for(websocket.recv(), timeout=remaining)
            self._record_websocket_frame()
            responses = await self._state_call(self.parser.parse_message, raw, received_at=self.clock())
            statuses = [item for item in responses if isinstance(item, dict)]
            observed.extend(statuses)
            if any(item.get("status") == "auth_success" for item in statuses):
                return
            if any(item.get("status") in {"auth_failed", "error"} for item in statuses):
                raise RuntimeError(f"Massive WebSocket authentication failed: {observed}")

    async def _refresh_external_demand(self) -> None:
        if self.demand_loader is None:
            return
        loaded = await self._state_call(self.demand_loader)
        for source, symbols in loaded.items():
            await self._state_call(self.state.register_demand, source, symbols, ttl_seconds=self.config.demand_ttl_seconds)

    async def reconcile(self, websocket: Any, *, check_ack: bool = False) -> SubscriptionPlan:
        await self._refresh_external_demand()
        plan = self.subscriptions.plan(await self._state_call(self.state.desired_demand))
        readiness = await self._state_call(self.state.integrity_preflight, required=plan.desired_by_event,
                                          known_contexts=set(self._last_event))
        if readiness.get('status') != 'COMPATIBLE_VERIFIED_STATE':
            raise IntegrityRejection(str(readiness))
        subscribe, unsubscribe = self.subscriptions.commands(self.actual, plan)
        await self._send_action(websocket, "unsubscribe", unsubscribe, check_ack=check_ack)
        await self._send_action(websocket, "subscribe", subscribe, check_ack=check_ack)
        for channel in unsubscribe:
            event_type, symbol = channel.split(".", 1); self.actual[event_type].discard(symbol)
        for channel in subscribe:
            event_type, symbol = channel.split(".", 1); self.actual[event_type].add(symbol)
        await self._state_call(self._retire_inactive_history)
        return plan

    def _retire_inactive_history(self) -> None:
        # Roster changes, not every market message, scan the bounded histories.
        # An owned async request pins its generation until completion/cleanup.
        pinned = (set().union(*self.actual.values()) | set(self._recovery_inflight)
                  | set(self._recovery_generation) | set(self._rest_futures)
                  | set(self._shadow_inflight) | set(self._pending_stale)
                  | {key[1] for key in self._pending_updates})
        for key in tuple(self._history_keys):
            if key[1] not in pinned:
                self._last_event.pop(key, None)
                self._stored_timestamps.pop(key, None)
                self._history_keys.discard(key)
        retained = {key[1] for key in self._history_keys} | pinned
        for symbol in tuple(self._symbol_generation):
            if symbol not in retained:
                self._symbol_generation.pop(symbol, None)

    def _reserve_history(self, event: StreamEvent) -> bool:
        key = (event.event_type, event.symbol)
        if key not in self._history_keys:
            if len(self._history_keys) >= max(1, self.config.symbol_cap) * len(ALLOWED_EVENT_TYPES):
                self._hold_freshness(event, "history_capacity_hold")
                return False
            self._history_keys.add(key)
        return True

    def _ordering_context(self, event: StreamEvent) -> tuple[Any, Any]:
        """Pure tentative context. Only a successful store may commit it."""
        previous = self._last_event.get((event.event_type, event.symbol))
        session_date = previous[3] if previous else None
        if event.event_type in {"Q", "T"}:
            event_date = _SIP_CALENDAR.local_date(event.event_timestamp)
            if (session_date is None or event_date > session_date) and _SIP_CALENDAR.is_trading_day(event_date):
                session_date, previous = event_date, None
            scope = event.trade_identity if event.event_type == "T" else None
            # A full normalized observation is required, never a bare trade ID.
            identity = (scope or event.provider_event_id, dict(event.payload))
        else:
            if previous is None:
                previous = self.state.aggregate_context(event.symbol, event.event_type)
            identity = event.provider_event_id
        return previous, (event.event_timestamp, event.sequence_number, identity, session_date)

    def _hold_freshness(self, event: StreamEvent, reason: str) -> None:
        key = (event.event_type, event.symbol)
        limit = max(1, self.config.symbol_cap) * len(ALLOWED_EVENT_TYPES)
        if key in self._freshness_degraded or len(self._freshness_degraded) < limit:
            self._freshness_degraded[key] = reason
        else:
            # No silent eviction or unbounded symbol list; unknown remainder
            # stays explicit until this worker is replaced.
            self._freshness_degraded_overflow = True
        self._quality_revision += 1
        try:
            self.state.hold_quality(event.symbol, event.event_type, self.worker_instance_id, self._quality_revision, reason)
        except Exception as exc:
            # Stop admission if quality cannot be published. Never continue a
            # healthy loop that silently lost a known hold. Last published
            # health still has its explicit TTL; no instantaneous failure
            # detector is claimed across an unavailable datastore.
            raise IntegrityRejection('quality_publication_unknown_operator_required') from exc

    def _accept_event(self, event: StreamEvent, *, stored_time: datetime | None = None) -> tuple[bool, bool]:
        """Tentative admission; mutates diagnostics but no price/order watermark."""
        previous, proposed = self._ordering_context(event)
        sip_event = event.event_type in {"Q", "T"}
        if not sip_event and previous is None and stored_time is not None:
            # Timestamp-only retirement metadata cannot reconstruct aggregate
            # sequence ordering. Do not turn eviction/restart into a reset.
            self.metrics.freshness_holds += 1
            self._hold_freshness(event, "aggregate_ordering_context_unavailable")
            return False, False
        if sip_event and stored_time == event.event_timestamp and (not previous or previous[0] != stored_time):
            self.metrics.freshness_holds += 1
            self._hold_freshness(event, "equal_time_unbound_ordering")
            return False, False
        gap = False
        if previous:
            previous_time, previous_sequence, previous_id, _ = previous
            if sip_event:
                if proposed[:3] == previous[:3]:
                    self.metrics.duplicates += 1
                    return False, False
            elif event.provider_event_id and previous_id == event.provider_event_id:
                self.metrics.duplicates += 1
                return False, False
            if event.sequence_number is not None and previous_sequence is not None:
                if event.sequence_number < previous_sequence:
                    if sip_event:
                        self.metrics.ambiguous_sequence_regressions += 1
                        # Newer trusted price time suffices for latest-state
                        # causality; it does NOT establish that a SIP reset occurred.
                        if event.event_timestamp > previous_time:
                            return True, False
                        self.metrics.freshness_holds += 1
                        self._hold_freshness(event, "equal_time_sequence_ambiguity")
                        return False, False
                    self.metrics.out_of_order += 1
                    return False, False
                if event.sequence_number == previous_sequence:
                    if sip_event:
                        if event.event_timestamp > previous_time:
                            return True, False
                        self.metrics.freshness_holds += 1
                        self._hold_freshness(event, "equal_time_sequence_ambiguity")
                        return False, False
                    self.metrics.duplicates += 1
                    return False, False
                if event.sequence_number > previous_sequence + 1:
                    self.metrics.sequence_gaps += 1
                    gap = not sip_event
            elif event.event_timestamp == previous_time and sip_event:
                self.metrics.freshness_holds += 1
                self._hold_freshness(event, "equal_time_missing_sequence")
                return False, False
            elif event.event_timestamp < previous_time and not sip_event:
                self.metrics.out_of_order += 1
                return False, False
        return True, gap

    def _commit_ordering(self, event: StreamEvent) -> None:
        _, proposed = self._ordering_context(event)
        key = (event.event_type, event.symbol)
        self._last_event[key] = proposed
        self._stored_timestamps[key] = event.event_timestamp
        self._symbol_generation[event.symbol] += 1
        self._freshness_degraded.pop(key, None)

    def _committed_time_floor(self, symbol: str, event_type: str, stored_time: datetime | None = None) -> datetime | None:
        """A missing/reloadable cache cannot erase an acknowledged commit."""
        key = (event_type, symbol)
        previous = self._last_event.get(key)
        durable_time, _ = self.state.integrity_floor(symbol, event_type)
        values = [stored_time, self._stored_timestamps.get(key), previous[0] if previous else None, durable_time]
        return max((value for value in values if value is not None), default=None)

    async def _rest_call(self, symbol: str, *, reserved: bool = False, recovery: bool = False) -> Any:
        """Shared bounded admission for every comparison and recovery request."""
        if not reserved:
            now = time.monotonic()
            last = self._last_recovery_monotonic.get(symbol)
            if symbol in self._recovery_inflight or (last is not None and now - last < self.config.recovery_cooldown_seconds):
                return None
            if recovery:
                self._recovery_inflight.add(symbol)
                self._last_recovery_monotonic[symbol] = now
        shadow_claim = None
        if not recovery:
            now = time.monotonic()
            last = self._last_shadow_monotonic.get(symbol)
            if symbol in self._shadow_inflight or (last is not None and now - last < self.config.recovery_cooldown_seconds):
                return None
            # Claim before waiting for a shared slot, so a recovery cannot
            # overtake even a shadow request that has not been submitted yet.
            shadow_claim = asyncio.get_running_loop().create_future()
            self._shadow_inflight[symbol] = shadow_claim
            self._last_shadow_monotonic[symbol] = now

        def finish_shadow() -> None:
            if self._shadow_inflight.get(symbol) is shadow_claim:
                self._shadow_inflight.pop(symbol, None)
            if shadow_claim is not None and not shadow_claim.done():
                shadow_claim.set_result(None)

        submitted = False
        try:
            if recovery:
                shadow = self._shadow_inflight.get(symbol)
                if shadow is not None:
                    # Completion conveys no provider result/exception: a failed
                    # comparison must not abort the queued real recovery. Do not
                    # occupy a shared slot or cancel the shadow while waiting.
                    await asyncio.shield(shadow)
            await self._rest_slots.acquire()
            if self._stop:
                self._rest_slots.release()
                return None
            future = asyncio.get_running_loop().run_in_executor(self._rest_executor, self.rest_client.get_quote, symbol)
            self._rest_futures[symbol] = future
            submitted = True
            def finished(done: asyncio.Future[Any]) -> None:
                if self._rest_futures.get(symbol) is done:
                    self._rest_futures.pop(symbol, None)
                # Queued recovery owns the symbol through its guarded state
                # store. If canceled, its worker removes the generation claim
                # and this callback releases the remaining active-call claim.
                if shadow_claim is not None:
                    finish_shadow()
                elif symbol not in self._recovery_generation:
                    self._recovery_inflight.discard(symbol)
                self._rest_slots.release()
                # Also retrieve exceptions when a timed-out/canceled batch no
                # longer awaits this uncancelable synchronous provider call.
                if not done.cancelled():
                    done.exception()
            future.add_done_callback(finished)
            return await asyncio.shield(future)
        finally:
            if not submitted:
                if shadow_claim is not None:
                    finish_shadow()
                else:
                    self._recovery_inflight.discard(symbol)

    async def _recover_symbol(self, symbol: str, *, reason: str) -> None:
        started = time.perf_counter(); self.metrics.rest_recovery_count += 1
        admission_generation = self._recovery_generation.get(symbol, self._symbol_generation[symbol])
        def mark_and_capture() -> int | None:
            if self._symbol_generation[symbol] != admission_generation:
                return None
            self.state.mark_symbols_stale([symbol], reason=reason, ttl_seconds=self.config.stale_ttl_seconds)
            return self._symbol_generation[symbol]
        generation = await self._state_call(mark_and_capture)
        if generation is None:
            self.metrics.rest_recovery_rejected += 1
            return
        try:
            result = await self._rest_call(symbol, reserved=symbol in self._recovery_inflight, recovery=True)
            if result is None:
                return
            quote: NormalizedQuote = result.data
            price = _number(quote.price)
            if price is None or price <= 0:
                self.metrics.rest_recovery_failures += 1
                return
            event_time = quote.event_timestamp
            now = self.clock()
            # An untrustworthy source time must not establish a persistent
            # watermark that later rejects correctly dated stream quotes.
            if (not isinstance(event_time, datetime) or event_time.tzinfo is None
                    or not isinstance(now, datetime) or now.tzinfo is None or event_time > now):
                self.metrics.rest_recovery_rejected += 1
                return
            event = StreamEvent(
                event_type="Q", symbol=symbol, event_timestamp=event_time,
                received_timestamp=result.received_timestamp, sequence_number=quote.sequence_number,
                provider_event_id=quote.provider_event_id,
                payload={"bid": quote.bid, "ask": quote.ask, "bid_size": quote.bid_size, "ask_size": quote.ask_size,
                         "midpoint": quote.midpoint, "recovery_price": price,
                         "recovery_price_source": getattr(quote, "price_source", None),
                         "recovery_price_timestamp": event_time.isoformat()},
                quality_flags=("rest_recovery", reason), source_mode="rest",
            )
            def store_if_current() -> bool:
                # Fail closed on missing source time, any accepted event during
                # the request, or a newer stored observation on any channel.
                if event_time is None or generation != self._symbol_generation[symbol]:
                    return False
                if not self._reserve_history(event):
                    return False
                for event_type in ALLOWED_EVENT_TYPES:
                    existing = self.state.get_latest(symbol, event_type)
                    existing_time = _stored_event_time(existing.get("event_timestamp")) if existing else None
                    try:
                        existing_time = self._committed_time_floor(symbol, event_type, existing_time)
                    except IntegrityRejection as exc:
                        self._hold_freshness(event, str(exc))
                        return False
                    if existing_time is not None and (existing_time > event_time or (event_type == "Q" and existing_time == event_time)):
                        # An equal-time replacement of the same Q channel has
                        # no verified correction semantics; preserve its value.
                        return False
                self._stream_revision += 1
                protected = replace(event, stream_commit={"worker_instance_id": self.worker_instance_id, "revision": self._stream_revision})
                try:
                    self.state.set_latest(protected, ttl_seconds=self.config.state_ttl_seconds, stale=quote.is_stale)
                except IntegrityRejection:
                    self._hold_freshness(event, "recovery_integrity_hold")
                    return False
                except Exception:
                    self._hold_freshness(event, "state_write_outcome_unknown")
                    raise
                self._stored_timestamps[("Q", symbol)] = event_time
                self._freshness_degraded.pop(("Q", symbol), None)
                return True
            if not await self._state_call(store_if_current):
                self.metrics.rest_recovery_rejected += 1
        except (ProviderError, RuntimeError, ValueError):
            self.metrics.rest_recovery_failures += 1
            logging.exception("REST recovery failed for %s", symbol)
        finally:
            self.metrics.rest_recovery_duration_ms += (time.perf_counter() - started) * 1000

    async def _recover_symbols(self, symbols: Iterable[str], *, reason: str) -> None:
        # Compatibility helper for explicit synthetic callers; production
        # reconnect only enqueues the bounded work and resumes reconnecting.
        for symbol in sorted(set(symbols)):
            self._queue_recovery(symbol, reason=reason)
        await self.drain_recoveries()

    def _ensure_recovery_workers(self) -> None:
        live_tasks = {task for task in self._recovery_tasks if not task.done()}
        self._recovery_tasks = live_tasks
        target = max(1, self.config.recovery_concurrency)
        for _ in range(max(0, target - len(live_tasks))):
            task = asyncio.create_task(self._recovery_worker())
            self._recovery_tasks.add(task)
            task.add_done_callback(self._recovery_tasks.discard)

    async def _recovery_worker(self) -> None:
        while True:
            symbol, reason = await self._recovery_queue.get()
            try:
                await self._recover_symbol(symbol, reason=reason)
            except Exception:  # Keep bounded workers alive after repository failures.
                self.metrics.rest_recovery_failures += 1
                logging.exception("Recovery job failed for %s", symbol)
            finally:
                if symbol not in self._rest_futures or symbol in self._shadow_inflight:
                    self._recovery_inflight.discard(symbol)
                self._recovery_generation.pop(symbol, None)
                self._recovery_queue.task_done()

    def _queue_recovery(self, symbol: str, *, reason: str) -> None:
        normalized = str(symbol or "").strip().upper()
        if not normalized:
            return
        now = time.monotonic()
        last = self._last_recovery_monotonic.get(normalized)
        if normalized in self._recovery_inflight or (last is not None and now - last < self.config.recovery_cooldown_seconds):
            self.metrics.rest_recovery_deduped += 1
            return
        self._ensure_recovery_workers()
        try:
            self._recovery_queue.put_nowait((normalized, reason))
        except asyncio.QueueFull:
            self.metrics.rest_recovery_queue_drops += 1
            if len(self._pending_stale) < max(1, self.config.symbol_cap) or normalized in self._pending_stale:
                self._pending_stale[normalized] = (reason, self._symbol_generation[normalized])
            else:
                self._freshness_degraded_overflow = True
            return
        self._recovery_inflight.add(normalized)
        self._recovery_generation[normalized] = self._symbol_generation[normalized]
        self._last_recovery_monotonic[normalized] = now
        self.metrics.rest_recovery_queued += 1

    async def drain_recoveries(self) -> None:
        await self._recovery_queue.join()

    def _cancel_recovery_workers(self) -> None:
        for task in list(self._recovery_tasks):
            task.cancel()

    async def process_raw_message(self, raw: str | bytes, *, ingest_started_monotonic: float | None = None) -> None:
        # Runtime callers start immediately after application recv returns;
        # direct callers start here, before lock/executor admission and parsing.
        # End at successful store completion delivered back to the loop. This
        # includes prior events in this frame, not later coalesced publication,
        # protocol buffering or unmeasurable provider/network transit time.
        ingest_started = time.monotonic() if ingest_started_monotonic is None else ingest_started_monotonic
        # Parsing large frames and the unchanged sequence policy share the
        # ordered state executor. Each event awaits admission, yielding the loop.
        async with self._processing_lock:
            now = self._trusted_clock()
            if now is None:
                self.metrics.invalid_clock_frames += 1
                return
            try:
                items = await self._state_call(self.parser.parse_message, raw, received_at=now)
            except StreamParseError:
                self.metrics.parse_failures += 1
                return
            for item in items:
                if isinstance(item, dict):
                    continue
                self.metrics.messages_received[item.event_type] += 1
                def accept_and_store() -> tuple[bool, bool, float, bool]:
                    # No skew allowance is established by the repository. Use
                    # the aware frame-admission clock, never monotonic market time.
                    try:
                        timestamp = _trusted_utc(item.event_timestamp)
                    except (TypeError, ValueError, OverflowError):
                        timestamp = None
                    if timestamp is None or timestamp > now:
                        self.metrics.timestamp_rejections += 1
                        self._hold_freshness(item, "invalid_or_future_timestamp")
                        return False, False, 0.0, False
                    key = (item.event_type, item.symbol)
                    if not self._reserve_history(item):
                        self.metrics.freshness_holds += 1
                        return False, False, 0.0, False
                    existing_time = self._stored_timestamps.get(key)
                    if key not in self._stored_timestamps:
                        existing = self.state.get_latest(item.symbol, item.event_type)
                        existing_time = _stored_event_time(existing.get("event_timestamp")) if existing else None
                    try:
                        existing_time = self._committed_time_floor(item.symbol, item.event_type, existing_time)
                    except IntegrityRejection as exc:
                        self.metrics.freshness_holds += 1
                        self._hold_freshness(item, str(exc))
                        return False, False, 0.0, False
                    if existing_time is not None and timestamp < existing_time:
                        self.metrics.stale_state_writes_rejected += 1
                        return False, False, 0.0, False
                    accepted, gap = self._accept_event(item, stored_time=existing_time)
                    if not accepted:
                        return accepted, gap, 0.0, False
                    self._stream_revision += 1
                    committed = replace(item, stream_commit={"worker_instance_id": self.worker_instance_id, "revision": self._stream_revision})
                    try:
                        latency = self.state.set_latest(committed, ttl_seconds=self.config.state_ttl_seconds)
                    except IntegrityRejection as exc:
                        self.metrics.freshness_holds += 1
                        self._hold_freshness(item, str(exc))
                        return False, False, 0.0, False
                    except Exception:
                        # A failed reply may follow a successful Redis SET.
                        # Reload persisted time next admission; never reuse the
                        # allocated revision or assume a cached older price won.
                        self._stored_timestamps.pop(key, None)
                        self._hold_freshness(item, "state_write_outcome_unknown")
                        raise
                    # No ordering/session/timestamp/generation mutation before
                    # successful persistence. A failed write is safely retryable.
                    self._commit_ordering(item)
                    return accepted, gap, latency, True
                accepted, gap, write_latency, stored = await self._state_call(accept_and_store)
                local_elapsed_ms = max(0.0, (time.monotonic() - ingest_started) * 1000) if stored else None
                if accepted and gap:
                    self._queue_recovery(item.symbol, reason="sequence_gap")
                if not stored:
                    continue
                self.metrics.redis_write_latency_ms.append(write_latency)
                provider_age_ms = item.lag_ms
                self.metrics.provider_event_age_ms.append(provider_age_ms)
                self.metrics.local_ingest_to_store_ms.append(local_elapsed_ms)
                total_lag = provider_age_ms + write_latency
                self.metrics.event_lag_ms.append(total_lag)
                if total_lag > self.config.slow_consumer_lag_ms:
                    self.metrics.slow_consumer_events += 1
                key = (item.event_type, item.symbol)
                if key in self._pending_updates:
                    self.metrics.coalesced_events += 1
                self._pending_updates[key] = {"event_type": item.event_type, "symbol": item.symbol, "event_timestamp": item.event_timestamp.isoformat(),
                                              "stream_commit": {"worker_instance_id": self.worker_instance_id, "revision": self._stream_revision}}
            await self._flush_pending_stale()
            await self.flush_updates_if_due()

    async def flush_updates_if_due(self, *, force: bool = False) -> None:
        elapsed_ms = (time.monotonic() - self._last_publish_monotonic) * 1000
        if not force and elapsed_ms < self.config.publish_coalesce_ms:
            return
        updates = list(self._pending_updates.values())
        if updates:
            await self._state_call(self.state.publish_updates, updates)
            self._pending_updates.clear()
        self._last_publish_monotonic = time.monotonic()

    def _start_shadow_compare(self, symbols: Iterable[str]) -> None:
        if self._stop or (self._shadow_task is not None and not self._shadow_task.done()):
            return
        # Stable rotation prevents the work cap from permanently excluding the
        # tail of a capped subscription plan. Only one batch task is created.
        ordered = sorted(set(symbols))[: self.config.symbol_cap]
        if not ordered:
            return
        offset = self._shadow_offset % len(ordered)
        rotated = ordered[offset:] + ordered[:offset]
        limit = min(len(ordered), max(1, self.config.shadow_batch_symbol_limit))
        self._shadow_offset = (offset + limit) % len(ordered)
        self._shadow_task = asyncio.create_task(self.shadow_compare(rotated[:limit]))
        def completed(task: asyncio.Task[None]) -> None:
            if not task.cancelled() and task.exception() is not None:
                logging.error("Shadow comparison batch failed", exc_info=task.exception())
        self._shadow_task.add_done_callback(completed)

    async def shadow_compare(self, symbols: Iterable[str]) -> None:
        deadline = time.monotonic() + max(.01, self.config.shadow_batch_timeout_seconds)
        limit = max(1, min(self.config.symbol_cap, self.config.shadow_batch_symbol_limit))
        calendar = ExchangeCalendar()
        for index, symbol in enumerate(symbols):
            remaining = deadline - time.monotonic()
            if index >= limit or remaining <= 0:
                self.metrics.shadow_budget_exhausted += 1
                return
            def candidate() -> tuple[datetime, float] | None:
                now = self.clock()
                session = calendar.session_at(now)
                # A closed market is not a meaningful live shadow comparison.
                if session == "closed":
                    return None
                threshold = 15 if session == "regular" else 60
                candidates = []
                for event_type in ("T", "A", "AM"):
                    stored = self.state.get_latest(symbol, event_type)
                    if not stored or stored.get("is_stale") or stored.get("source_mode") != "websocket":
                        continue
                    timestamp = _stored_event_time(stored.get("event_timestamp"))
                    payload = stored.get("payload") or {}
                    price = _number(payload.get("price") if event_type == "T" else payload.get("close"))
                    if timestamp is None or price is None or price <= 0 or not 0 <= (now - timestamp).total_seconds() <= threshold:
                        continue
                    candidates.append((timestamp, price))
                return max(candidates, default=None, key=lambda item: item[0])
            try:
                comparison = await asyncio.wait_for(self._state_call(candidate), timeout=remaining)
            except asyncio.TimeoutError:
                self.metrics.shadow_budget_exhausted += 1
                return
            if comparison is None:
                self.metrics.shadow_skipped += 1
                continue
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.metrics.shadow_budget_exhausted += 1
                    return
                result = await asyncio.wait_for(self._rest_call(symbol), timeout=remaining)
            except asyncio.TimeoutError:
                self.metrics.shadow_budget_exhausted += 1
                return
            except (ProviderError, RuntimeError, ValueError):
                self.metrics.shadow_skipped += 1
                continue
            rest_price = _number(result.data.price) if result is not None else None
            rest_time = getattr(result.data, "event_timestamp", None) if result is not None else None
            session = calendar.session_at(self.clock())
            threshold = 15 if session == "regular" else 60
            trustworthy_rest = (isinstance(rest_time, datetime) and rest_time.tzinfo is not None
                                and 0 <= (self.clock() - rest_time).total_seconds() <= threshold
                                and not getattr(result.data, "is_stale", True)
                                and getattr(result.data, "price_source", None) != "daily_close") if result is not None else False
            if rest_price is None or rest_price <= 0 or not trustworthy_rest:
                self.metrics.shadow_skipped += 1
                continue
            self.metrics.shadow_comparisons += 1
            difference_bps = abs(comparison[1] - rest_price) / rest_price * 10_000
            if difference_bps > self.config.rest_shadow_tolerance_bps:
                self.metrics.shadow_discrepancies += 1

    def health_payload(self, plan: SubscriptionPlan | None = None, *, metrics: StreamMetrics | None = None) -> dict[str, Any]:
        actual_counts = {event: len(symbols) for event, symbols in self.actual.items()}
        desired_counts = {event: len(symbols) for event, symbols in (plan.desired_by_event.items() if plan else [])}
        metrics = (metrics or self.metrics).snapshot()
        metrics["rest_recovery_queue_depth"] = self._recovery_queue.qsize()
        metrics["rest_recovery_inflight"] = len(self._recovery_inflight)
        metrics["rest_request_active"] = len(self._rest_futures)
        metrics["shadow_batch_active"] = bool(self._shadow_task is not None and not self._shadow_task.done())
        now = self._trusted_clock()
        freshness = {
            "state": "degraded" if self._freshness_degraded or self._freshness_degraded_overflow or now is None else "no_known_hold",
            "clock_trusted": now is not None,
            "held_channels": [{"event_type": key[0], "symbol": key[1], "reason": reason}
                              for key, reason in sorted(self._freshness_degraded.items())],
            "overflow": self._freshness_degraded_overflow,
            "stream_revision": self._stream_revision,
            "ttl_seconds": self.config.health_ttl_seconds,
        }
        return {
            "worker_instance_id": self.worker_instance_id,
            "worker_started_at": self.worker_started_at.isoformat(),
            "source_revision": self.source_revision,
            "schema_version": STREAM_SCHEMA_VERSION, "enabled": self.config.enabled, "shadow_mode": self.config.shadow_mode,
            "connection_state": self._connection_state, "last_message_at": self._last_message_at.isoformat() if self._last_message_at else None,
            "connected_at": self._connected_at.isoformat() if self._connected_at else None,
            "last_error": self._last_error,
            "websocket_url": self.config.websocket_url,
            "server_symbols": list(self.config.server_symbols),
            "desired_symbols": sorted(plan.symbols) if plan else [],
            "desired_subscription_counts": desired_counts, "actual_subscription_counts": actual_counts,
            "symbol_budget": self.config.symbol_cap, "redis_memory_bytes": self.state.memory_usage_bytes(),
            "metrics": metrics, "freshness": freshness, "updated_at": now.isoformat() if now is not None else None,
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
        await self._publish_health(plan, force=True)
        next_reconcile = time.monotonic() + self.config.reconcile_seconds
        next_shadow_compare = time.monotonic() + self.config.shadow_compare_seconds
        while not self._stop:
            reconcile_wait = max(.1, next_reconcile - time.monotonic())
            heartbeat_timeout = self.config.heartbeat_timeout_seconds
            timeout = min(heartbeat_timeout, reconcile_wait) if heartbeat_timeout > 0 else reconcile_wait
            try:
                raw = await asyncio.wait_for(websocket.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                if heartbeat_timeout > 0 and self._last_message_at and (self.clock() - self._last_message_at).total_seconds() >= heartbeat_timeout:
                    raise RuntimeError("Massive WebSocket heartbeat timeout")
            else:
                ingest_started = time.monotonic()
                self._record_websocket_frame()
                await self.process_raw_message(raw, ingest_started_monotonic=ingest_started)

            if time.monotonic() >= next_reconcile:
                plan = await self.reconcile(websocket, check_ack=True)
                next_reconcile = time.monotonic() + self.config.reconcile_seconds
            await self.flush_updates_if_due()
            if self.config.shadow_mode and time.monotonic() >= next_shadow_compare:
                self._start_shadow_compare(plan.symbols)
                next_shadow_compare = time.monotonic() + self.config.shadow_compare_seconds
            await self._publish_health(plan)

    async def run(self) -> None:
        try:
            await self._run()
        finally:
            await self.aclose()

    async def _run(self) -> None:
        if not self.config.enabled:
            self._connection_state = "disabled"
            await self._publish_health(force=True)
            return
        readiness = await self._state_call(self.state.integrity_preflight)
        if readiness.get('status') != 'COMPATIBLE_VERIFIED_STATE':
            self._connection_state = 'integrity_blocked'
            self._last_error = str(readiness)
            await self._publish_health(force=True)
            return
        attempt = 0
        while not self._stop:
            await self._refresh_external_demand()
            plan = self.subscriptions.plan(await self._state_call(self.state.desired_demand))
            readiness = await self._state_call(self.state.integrity_preflight, required=plan.desired_by_event,
                                              known_contexts=set(self._last_event))
            if readiness.get('status') != 'COMPATIBLE_VERIFIED_STATE':
                self._connection_state = 'integrity_blocked'
                self._last_error = str(readiness)
                await self._publish_health(plan, force=True)
                return
            if not plan.symbols:
                self._connection_state = "idle_no_demand"
                await self._publish_health(plan)
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
            except IntegrityRejection as exc:
                self._connection_state = 'integrity_blocked'
                self._last_error = str(exc)
                await self._publish_health(plan, force=True)
                return
            except Exception as exc:  # noqa: BLE001
                self._connection_state = "reconnecting"
                self._last_error = f"{type(exc).__name__}: {exc}"
                logging.exception("Massive WebSocket connection failed; reconnecting symbols=%s", sorted(plan.symbols))
                self.actual = {event: set() for event in ALLOWED_EVENT_TYPES}
                await self._state_call(self.state.mark_symbols_stale, plan.symbols, reason="stream_disconnected", ttl_seconds=self.config.stale_ttl_seconds)
                for symbol in sorted(plan.symbols):
                    self._queue_recovery(symbol, reason="stream_reconnect")
                await self._publish_health(plan, force=True)
                delay = _bounded_reconnect_delay(
                    attempt=attempt,
                    min_seconds=self.config.reconnect_min_seconds,
                    max_seconds=self.config.reconnect_max_seconds,
                    jitter=self.rng.uniform(.8, 1.2),
                )
                attempt = min(attempt + 1, 30)
                await self.sleep(delay)

    def stop(self) -> None:
        self._stop = True
        self._cancel_recovery_workers()
        if self._shadow_task is not None:
            self._shadow_task.cancel()


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
        publish_coalesce_ms=int(env.get("MASSIVE_STREAM_PUBLISH_COALESCE_MS", "100")),
        heartbeat_timeout_seconds=float(env.get("MASSIVE_STREAM_HEARTBEAT_TIMEOUT_SECONDS", "0")),
        reconnect_min_seconds=float(env.get("MASSIVE_STREAM_RECONNECT_MIN_SECONDS", "1")),
        reconnect_max_seconds=float(env.get("MASSIVE_STREAM_RECONNECT_MAX_SECONDS", "30")),
        max_queue=int(env.get("MASSIVE_STREAM_MAX_QUEUE", "1024")),
        acknowledgement_timeout_seconds=float(env.get("MASSIVE_STREAM_ACK_TIMEOUT_SECONDS", "10")),
        rest_shadow_tolerance_bps=float(env.get("MASSIVE_STREAM_REST_TOLERANCE_BPS", "50")),
        shadow_compare_seconds=float(env.get("MASSIVE_STREAM_SHADOW_COMPARE_SECONDS", "30")),
        slow_consumer_lag_ms=float(env.get("MASSIVE_STREAM_SLOW_CONSUMER_LAG_MS", "2000")),
        recovery_concurrency=int(env.get("MASSIVE_STREAM_RECOVERY_CONCURRENCY", "2")),
        recovery_queue_max=int(env.get("MASSIVE_STREAM_RECOVERY_QUEUE_MAX", "512")),
        recovery_cooldown_seconds=float(env.get("MASSIVE_STREAM_RECOVERY_COOLDOWN_SECONDS", "30")),
        server_symbols=server_symbols,
    )


def create_stream_state(redis_url: str | None, *, writer: bool = False) -> MarketStreamStateRepository:
    from .stream_activation import InactiveAuthorityState, ReaderAuthorityState
    authority = os.environ.get('MASSIVE_STREAM_AUTHORITY', 'disabled')
    role = os.environ.get('MASSIVE_STREAM_PROCESS_ROLE', 'reader')
    if role not in {'reader', 'writer'}:
        raise IntegrityRejection('invalid_authority_process_role')
    if authority == 'disabled' and not writer:
        return InactiveAuthorityState()
    if authority not in {'disabled', 'legacy', 'postgres_option_a'}:
        raise IntegrityRejection('unsupported_stream_authority')
    if authority != 'postgres_option_a':
        raise IntegrityRejection('postgres_authority_activation_required')
    if writer and role != 'writer':
        raise IntegrityRejection('explicit_writer_role_required')
    if not redis_url:
        raise IntegrityRejection('postgres_authority_requires_cache_configuration')
    from .stream_postgres_state import from_environment
    state = from_environment(redis_url)
    return state if writer else ReaderAuthorityState(state)


def register_demand_safely(state: MarketStreamStateRepository | None, source: str, symbols: Iterable[str], *, ttl_seconds: int) -> bool:
    if state is None:
        return False
    try:
        state.register_demand(source, symbols, ttl_seconds=ttl_seconds)
        return True
    except Exception:  # noqa: BLE001
        logging.exception("Unable to register market-stream demand source=%s", source)
        return False
