from __future__ import annotations

import math
import time
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time as dt_time, timedelta, timezone
from email.utils import parsedate_to_datetime
from threading import Lock
from typing import Any, Callable, Mapping
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

NORMALIZED_MARKET_DATA_SCHEMA = "market-data.v1"


class ProviderError(RuntimeError):
    code = "provider_error"

    def __init__(self, message: str, *, status_code: int | None = None, retry_after_seconds: float | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class ProviderAuthenticationError(ProviderError):
    code = "authentication_failed"


class ProviderForbiddenError(ProviderError):
    code = "forbidden"


class ProviderRateLimitError(ProviderError):
    code = "rate_limited"


class ProviderUnavailableError(ProviderError):
    code = "unavailable"


class ProviderResponseError(ProviderError):
    code = "invalid_response"


class ProviderUnsupportedError(ProviderError):
    code = "unsupported"


@dataclass(frozen=True)
class NormalizedQuote:
    symbol: str
    bid: float | None
    ask: float | None
    bid_size: float | None
    ask_size: float | None
    midpoint: float | None
    last_trade_price: float | None
    last_trade_size: float | None
    price: float | None
    price_source: str | None
    price_reason: str
    event_timestamp: datetime | None
    received_timestamp: datetime
    age_ms: int | None
    market_session: str
    source: str
    source_mode: str
    is_stale: bool
    quality_flags: tuple[str, ...] = ()
    sequence_number: int | None = None
    provider_event_id: str | None = None
    request_id: str | None = None
    previous_close: float | None = None
    change_percent: float | None = None
    schema_version: str = NORMALIZED_MARKET_DATA_SCHEMA

    def payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["event_timestamp"] = self.event_timestamp.isoformat() if self.event_timestamp else None
        payload["received_timestamp"] = self.received_timestamp.isoformat()
        payload["quality_flags"] = list(self.quality_flags)
        return payload


@dataclass(frozen=True)
class NormalizedBar:
    symbol: str
    multiplier: int
    timespan: str
    start_timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float | None
    transactions: int | None
    adjusted_for_splits: bool
    source: str = "massive"
    source_mode: str = "rest"
    schema_version: str = NORMALIZED_MARKET_DATA_SCHEMA

    def payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["start_timestamp"] = self.start_timestamp.isoformat()
        return payload


@dataclass(frozen=True)
class ProviderResult:
    data: Any
    provider: str
    endpoint: str
    request_id: str | None
    received_timestamp: datetime
    latency_ms: float
    cache_status: str = "miss"
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


class MarketDataProvider(ABC):
    name: str

    @abstractmethod
    def get_quote(self, symbol: str) -> ProviderResult:
        raise NotImplementedError

    @abstractmethod
    def get_aggregates(
        self,
        symbol: str,
        *,
        multiplier: int,
        timespan: str,
        start: date | datetime | str,
        end: date | datetime | str,
        adjusted: bool = True,
        limit: int = 5000,
    ) -> ProviderResult:
        raise NotImplementedError


class ExchangeCalendar:
    """Small NYSE holiday/session calendar used until a full calendar dependency is justified."""

    timezone = ZoneInfo("America/New_York")

    @staticmethod
    def _observed(day: date) -> date:
        if day.weekday() == 5:
            return day - timedelta(days=1)
        if day.weekday() == 6:
            return day + timedelta(days=1)
        return day

    @staticmethod
    def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
        day = date(year, month, 1)
        offset = (weekday - day.weekday()) % 7
        return day + timedelta(days=offset + (n - 1) * 7)

    @staticmethod
    def _last_weekday(year: int, month: int, weekday: int) -> date:
        if month == 12:
            day = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            day = date(year, month + 1, 1) - timedelta(days=1)
        return day - timedelta(days=(day.weekday() - weekday) % 7)

    @staticmethod
    def _easter(year: int) -> date:
        a = year % 19
        b, c = divmod(year, 100)
        d, e = divmod(b, 4)
        f = (b + 8) // 25
        g = (b - f + 1) // 3
        h = (19 * a + b - d - g + 15) % 30
        i, k = divmod(c, 4)
        l = (32 + 2 * e + 2 * i - h - k) % 7
        m = (a + 11 * h + 22 * l) // 451
        month = (h + l - 7 * m + 114) // 31
        day = ((h + l - 7 * m + 114) % 31) + 1
        return date(year, month, day)

    def holidays(self, year: int) -> set[date]:
        holidays = {
            self._observed(date(year, 1, 1)),
            self._nth_weekday(year, 1, 0, 3),
            self._nth_weekday(year, 2, 0, 3),
            self._easter(year) - timedelta(days=2),
            self._last_weekday(year, 5, 0),
            self._observed(date(year, 7, 4)),
            self._nth_weekday(year, 9, 0, 1),
            self._nth_weekday(year, 11, 3, 4),
            self._observed(date(year, 12, 25)),
        }
        if year >= 2022:
            holidays.add(self._observed(date(year, 6, 19)))
        return holidays

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays(day.year)

    def session_at(self, timestamp: datetime) -> str:
        local = timestamp.astimezone(self.timezone)
        if not self.is_trading_day(local.date()):
            return "closed"
        current = local.time().replace(tzinfo=None)
        if dt_time(4, 0) <= current < dt_time(9, 30):
            return "pre"
        if dt_time(9, 30) <= current < dt_time(16, 0):
            return "regular"
        if dt_time(16, 0) <= current < dt_time(20, 0):
            return "after"
        return "closed"


class ProviderMetrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self.calls = Counter()
        self.errors = Counter()
        self.cache = Counter()
        self.latency_total_ms = Counter()
        self.stale_responses = Counter()

    def record(self, *, endpoint: str, latency_ms: float, cache_status: str, error_code: str | None = None, stale: bool = False) -> None:
        with self._lock:
            self.calls[endpoint] += 1
            self.latency_total_ms[endpoint] += latency_ms
            self.cache[cache_status] += 1
            if error_code:
                self.errors[f"{endpoint}:{error_code}"] += 1
            if stale:
                self.stale_responses[endpoint] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            average_latency = {
                endpoint: round(self.latency_total_ms[endpoint] / count, 2)
                for endpoint, count in self.calls.items()
                if count
            }
            return {
                "calls": dict(self.calls),
                "errors": dict(self.errors),
                "cache": dict(self.cache),
                "average_latency_ms": average_latency,
                "stale_responses": dict(self.stale_responses),
            }


@dataclass
class _CacheEntry:
    value: ProviderResult | ProviderError
    expires_at: float


class MassiveRestClient(MarketDataProvider):
    name = "massive"

    def __init__(
        self,
        *,
        api_key: str,
        key_source: str = "MASSIVE_API_KEY",
        base_url: str = "https://api.massive.com",
        timeout_seconds: float = 6.0,
        retries: int = 2,
        retry_backoff_seconds: float = 0.2,
        quote_cache_seconds: float = 2.0,
        reference_cache_seconds: float = 86400.0,
        negative_cache_seconds: float = 30.0,
        regular_stale_seconds: float = 15.0,
        extended_stale_seconds: float = 60.0,
        closed_stale_seconds: float = 86400.0,
        http_get: Callable[..., Any] | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        calendar: ExchangeCalendar | None = None,
    ) -> None:
        self.api_key = api_key
        self.key_source = key_source
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout_seconds = timeout_seconds
        self.retries = max(0, retries)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self.quote_cache_seconds = max(0.0, quote_cache_seconds)
        self.reference_cache_seconds = max(0.0, reference_cache_seconds)
        self.negative_cache_seconds = max(0.0, negative_cache_seconds)
        self.regular_stale_seconds = max(0.0, regular_stale_seconds)
        self.extended_stale_seconds = max(0.0, extended_stale_seconds)
        self.closed_stale_seconds = max(0.0, closed_stale_seconds)
        self.http_get = http_get
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleep = sleep
        self.calendar = calendar or ExchangeCalendar()
        self.metrics = ProviderMetrics()
        self._cache: dict[str, _CacheEntry] = {}
        self._backoff_until = 0.0

    @staticmethod
    def _symbol(symbol: str) -> str:
        normalized = str(symbol or "").strip().upper()
        if not normalized or len(normalized) > 20 or not all(ch.isalnum() or ch in ".-" for ch in normalized):
            raise ValueError("invalid ticker symbol")
        return normalized

    @staticmethod
    def _number(value: Any) -> float | None:
        if isinstance(value, bool) or value in (None, ""):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @staticmethod
    def _integer(value: Any) -> int | None:
        number = MassiveRestClient._number(value)
        return int(number) if number is not None else None

    @staticmethod
    def normalize_timestamp(value: Any) -> datetime | None:
        number = MassiveRestClient._number(value)
        if number is None or number <= 0:
            return None
        absolute = abs(number)
        if absolute >= 1e17:
            seconds = number / 1_000_000_000
        elif absolute >= 1e14:
            seconds = number / 1_000_000
        elif absolute >= 1e11:
            seconds = number / 1_000
        else:
            seconds = number
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    def _cache_get(self, key: str) -> ProviderResult | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        if time.monotonic() >= entry.expires_at:
            self._cache.pop(key, None)
            return None
        if isinstance(entry.value, ProviderError):
            raise entry.value
        result = entry.value
        return ProviderResult(
            data=result.data,
            provider=result.provider,
            endpoint=result.endpoint,
            request_id=result.request_id,
            received_timestamp=result.received_timestamp,
            latency_ms=result.latency_ms,
            cache_status="hit",
            diagnostics=result.diagnostics,
        )

    def _cache_set(self, key: str, value: ProviderResult | ProviderError, ttl: float) -> None:
        if ttl > 0:
            self._cache[key] = _CacheEntry(value=value, expires_at=time.monotonic() + ttl)

    @staticmethod
    def _retry_after(response: Any) -> float | None:
        headers = getattr(response, "headers", {}) or {}
        raw = headers.get("Retry-After")
        if raw is None:
            return None
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            try:
                parsed = parsedate_to_datetime(str(raw))
                return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return None

    def _map_http_error(self, response: Any, endpoint: str) -> ProviderError:
        status = int(getattr(response, "status_code", 0) or 0)
        message = f"Massive {endpoint} request failed with HTTP {status or 'unknown'}"
        if status == 401:
            return ProviderAuthenticationError(message, status_code=status)
        if status == 403:
            return ProviderForbiddenError(message, status_code=status)
        if status == 429:
            return ProviderRateLimitError(message, status_code=status, retry_after_seconds=self._retry_after(response))
        if status >= 500 or status == 0:
            return ProviderUnavailableError(message, status_code=status or None)
        return ProviderResponseError(message, status_code=status)

    def _request(self, endpoint: str, path: str, *, params: Mapping[str, Any] | None = None, cache_ttl: float = 0.0) -> ProviderResult:
        cache_key = f"{NORMALIZED_MARKET_DATA_SCHEMA}:{endpoint}:{path}:{sorted((params or {}).items())}"
        cached = self._cache_get(cache_key)
        if cached:
            self.metrics.record(endpoint=endpoint, latency_ms=0.0, cache_status="hit")
            return cached
        now_monotonic = time.monotonic()
        if now_monotonic < self._backoff_until:
            error = ProviderRateLimitError("Massive rate-limit backoff is active", retry_after_seconds=self._backoff_until - now_monotonic)
            self.metrics.record(endpoint=endpoint, latency_ms=0.0, cache_status="backoff", error_code=error.code)
            raise error
        if self.http_get is None:
            raise ProviderUnavailableError("Massive HTTP transport is not configured")

        query = dict(params or {})
        query["apiKey"] = self.api_key
        url = urljoin(self.base_url, path.lstrip("/"))
        started = time.perf_counter()
        last_error: ProviderError | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self.http_get(url, params=query, timeout=self.timeout_seconds)
            except Exception as exc:  # noqa: BLE001
                last_error = ProviderUnavailableError(f"Massive {endpoint} transport failed: {exc}")
            else:
                status = int(getattr(response, "status_code", 200) or 200)
                if 200 <= status < 300:
                    try:
                        payload = response.json() or {}
                    except Exception as exc:  # noqa: BLE001
                        last_error = ProviderResponseError(f"Massive {endpoint} returned invalid JSON: {exc}")
                    else:
                        latency = (time.perf_counter() - started) * 1000
                        result = ProviderResult(
                            data=payload,
                            provider=self.name,
                            endpoint=endpoint,
                            request_id=str(payload.get("request_id")) if payload.get("request_id") is not None else None,
                            received_timestamp=self.clock(),
                            latency_ms=latency,
                            diagnostics={"key_source": self.key_source, "status": payload.get("status")},
                        )
                        self._cache_set(cache_key, result, cache_ttl)
                        self.metrics.record(endpoint=endpoint, latency_ms=latency, cache_status="miss")
                        return result
                else:
                    last_error = self._map_http_error(response, endpoint)
                    if isinstance(last_error, ProviderRateLimitError):
                        delay = last_error.retry_after_seconds or max(1.0, self.retry_backoff_seconds * (2**attempt))
                        self._backoff_until = time.monotonic() + delay
            retryable = isinstance(last_error, (ProviderUnavailableError, ProviderRateLimitError))
            if attempt < self.retries and retryable:
                delay = max(0.0, self.retry_backoff_seconds * (2**attempt))
                if isinstance(last_error, ProviderRateLimitError) and last_error.retry_after_seconds is not None:
                    delay = max(delay, last_error.retry_after_seconds)
                self.sleep(delay)
                continue
            break

        assert last_error is not None
        latency = (time.perf_counter() - started) * 1000
        self._cache_set(cache_key, last_error, self.negative_cache_seconds)
        self.metrics.record(endpoint=endpoint, latency_ms=latency, cache_status="miss", error_code=last_error.code)
        raise last_error

    def _stale_threshold(self, session: str) -> float:
        if session == "regular":
            return self.regular_stale_seconds
        if session in {"pre", "after"}:
            return self.extended_stale_seconds
        return self.closed_stale_seconds

    def _normalize_snapshot(self, symbol: str, result: ProviderResult) -> NormalizedQuote:
        payload = result.data if isinstance(result.data, dict) else {}
        ticker = payload.get("ticker") if isinstance(payload.get("ticker"), dict) else {}
        quote = ticker.get("lastQuote") if isinstance(ticker.get("lastQuote"), dict) else {}
        trade = ticker.get("lastTrade") if isinstance(ticker.get("lastTrade"), dict) else {}
        minute = ticker.get("min") if isinstance(ticker.get("min"), dict) else {}
        day = ticker.get("day") if isinstance(ticker.get("day"), dict) else {}
        previous = ticker.get("prevDay") if isinstance(ticker.get("prevDay"), dict) else {}

        bid = self._number(quote.get("p"))
        ask = self._number(quote.get("P"))
        bid_size = self._number(quote.get("s"))
        ask_size = self._number(quote.get("S"))
        midpoint = (bid + ask) / 2 if bid and ask and bid > 0 and ask > 0 and ask >= bid else None
        trade_price = self._number(trade.get("p"))
        trade_size = self._number(trade.get("s") or trade.get("ds"))

        quote_ts = self.normalize_timestamp(quote.get("t") or quote.get("y") or quote.get("f"))
        trade_ts = self.normalize_timestamp(trade.get("t") or trade.get("y") or trade.get("f"))
        minute_ts = self.normalize_timestamp(minute.get("t"))
        updated_ts = self.normalize_timestamp(ticker.get("updated"))
        event_timestamp = max((stamp for stamp in (quote_ts, trade_ts, minute_ts, updated_ts) if stamp), default=None)
        received = result.received_timestamp
        age_ms = max(0, int((received - event_timestamp).total_seconds() * 1000)) if event_timestamp else None
        session = self.calendar.session_at(event_timestamp or received)
        flags: list[str] = []
        if event_timestamp is None:
            flags.append("missing_event_timestamp")
        if bid is not None and ask is not None and ask < bid:
            flags.append("crossed_market")
        if bid is None or ask is None:
            flags.append("incomplete_nbbo")

        threshold = self._stale_threshold(session)
        is_stale = event_timestamp is None or (age_ms is not None and age_ms > threshold * 1000)
        if is_stale:
            flags.append("stale")

        candidates: list[tuple[str, float | None, datetime | None, str]] = [
            ("last_trade", trade_price, trade_ts, "latest qualifying trade"),
            ("nbbo_midpoint", midpoint, quote_ts, "midpoint of the latest valid NBBO"),
            ("minute_close", self._number(minute.get("c")), minute_ts, "latest minute aggregate close"),
            ("day_close", self._number(day.get("c")), None, "current-session daily aggregate close"),
        ]
        price = None
        price_source = None
        price_reason = "No valid positive price was present in the snapshot."
        for source, candidate, timestamp, reason in candidates:
            if candidate is None or candidate <= 0:
                continue
            candidate_age = max(0.0, (received - timestamp).total_seconds()) if timestamp else None
            if source in {"last_trade", "nbbo_midpoint", "minute_close"} and timestamp is not None and candidate_age is not None:
                candidate_session = self.calendar.session_at(timestamp)
                if candidate_age > self._stale_threshold(candidate_session):
                    continue
            if source == "day_close":
                flags.append("daily_close_not_realtime")
                if session in {"pre", "regular", "after"}:
                    continue
            price, price_source, price_reason = candidate, source, reason
            break
        if price is None:
            for source, candidate, _timestamp, reason in candidates:
                if candidate is not None and candidate > 0:
                    price, price_source = candidate, source
                    price_reason = f"Stale fallback: {reason}."
                    flags.append("stale_price_fallback")
                    break

        previous_close = self._number(previous.get("c"))
        change_percent = None
        if price is not None and previous_close and previous_close > 0:
            change_percent = ((price - previous_close) / previous_close) * 100
        elif self._number(ticker.get("todaysChangePerc")) is not None:
            change_percent = self._number(ticker.get("todaysChangePerc"))

        return NormalizedQuote(
            symbol=symbol,
            bid=bid,
            ask=ask,
            bid_size=bid_size,
            ask_size=ask_size,
            midpoint=midpoint,
            last_trade_price=trade_price,
            last_trade_size=trade_size,
            price=price,
            price_source=price_source,
            price_reason=price_reason,
            event_timestamp=event_timestamp,
            received_timestamp=received,
            age_ms=age_ms,
            market_session=session,
            source=self.name,
            source_mode="rest",
            is_stale=is_stale,
            quality_flags=tuple(dict.fromkeys(flags)),
            sequence_number=self._integer(trade.get("q") or quote.get("q")),
            provider_event_id=str(trade.get("i")) if trade.get("i") is not None else None,
            request_id=result.request_id,
            previous_close=previous_close,
            change_percent=change_percent,
        )

    def get_quote(self, symbol: str) -> ProviderResult:
        symbol = self._symbol(symbol)
        raw = self._request(
            "single_ticker_snapshot",
            f"/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}",
            cache_ttl=self.quote_cache_seconds,
        )
        quote = self._normalize_snapshot(symbol, raw)
        if quote.price is None:
            error = ProviderResponseError("Massive snapshot did not contain a usable positive price")
            self.metrics.record(endpoint="normalized_quote", latency_ms=raw.latency_ms, cache_status=raw.cache_status, error_code=error.code, stale=True)
            raise error
        self.metrics.record(endpoint="normalized_quote", latency_ms=raw.latency_ms, cache_status=raw.cache_status, stale=quote.is_stale)
        return ProviderResult(
            data=quote,
            provider=self.name,
            endpoint=raw.endpoint,
            request_id=raw.request_id,
            received_timestamp=raw.received_timestamp,
            latency_ms=raw.latency_ms,
            cache_status=raw.cache_status,
            diagnostics=raw.diagnostics,
        )

    def latest_trade(self, symbol: str) -> ProviderResult:
        symbol = self._symbol(symbol)
        return self._request("latest_trade", f"/v2/last/trade/{symbol}", cache_ttl=self.quote_cache_seconds)

    def latest_quote(self, symbol: str) -> ProviderResult:
        symbol = self._symbol(symbol)
        return self._request("latest_quote", f"/v2/last/nbbo/{symbol}", cache_ttl=self.quote_cache_seconds)

    @staticmethod
    def _date_value(value: date | datetime | str) -> str:
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value)

    def get_aggregates(
        self,
        symbol: str,
        *,
        multiplier: int,
        timespan: str,
        start: date | datetime | str,
        end: date | datetime | str,
        adjusted: bool = True,
        limit: int = 5000,
    ) -> ProviderResult:
        symbol = self._symbol(symbol)
        timespan = str(timespan).lower()
        if timespan == "second":
            raise ProviderUnsupportedError("Massive REST does not provide historical per-second stock aggregates; use trades or Page 4 WebSocket second aggregates")
        if timespan not in {"minute", "hour", "day", "week", "month", "quarter", "year"}:
            raise ValueError("unsupported aggregate timespan")
        multiplier = max(1, int(multiplier))
        raw = self._request(
            f"aggregates_{timespan}",
            f"/v2/aggs/ticker/{symbol}/range/{multiplier}/{timespan}/{self._date_value(start)}/{self._date_value(end)}",
            params={"adjusted": str(bool(adjusted)).lower(), "sort": "asc", "limit": min(50000, max(1, int(limit)))},
            cache_ttl=60.0 if timespan == "minute" else 3600.0,
        )
        payload = raw.data if isinstance(raw.data, dict) else {}
        bars: list[NormalizedBar] = []
        for item in payload.get("results") or []:
            if not isinstance(item, dict):
                continue
            stamp = self.normalize_timestamp(item.get("t"))
            values = [self._number(item.get(key)) for key in ("o", "h", "l", "c", "v")]
            if stamp is None or any(value is None for value in values):
                continue
            bars.append(NormalizedBar(
                symbol=symbol,
                multiplier=multiplier,
                timespan=timespan,
                start_timestamp=stamp,
                open=float(values[0]), high=float(values[1]), low=float(values[2]), close=float(values[3]), volume=float(values[4]),
                vwap=self._number(item.get("vw")),
                transactions=self._integer(item.get("n")),
                adjusted_for_splits=bool(payload.get("adjusted", adjusted)),
            ))
        return ProviderResult(
            data=bars,
            provider=self.name,
            endpoint=raw.endpoint,
            request_id=raw.request_id,
            received_timestamp=raw.received_timestamp,
            latency_ms=raw.latency_ms,
            cache_status=raw.cache_status,
            diagnostics={**dict(raw.diagnostics), "adjusted_for_splits": bool(payload.get("adjusted", adjusted)), "bar_count": len(bars)},
        )

    def ticker_details(self, symbol: str, *, as_of: date | str | None = None) -> ProviderResult:
        symbol = self._symbol(symbol)
        params = {"date": self._date_value(as_of)} if as_of else None
        return self._request("ticker_details", f"/v3/reference/tickers/{symbol}", params=params, cache_ttl=self.reference_cache_seconds)

    def splits(self, symbol: str, *, limit: int = 100) -> ProviderResult:
        symbol = self._symbol(symbol)
        return self._request("splits", "/stocks/v1/splits", params={"ticker": symbol, "limit": min(5000, max(1, limit)), "sort": "execution_date.asc"}, cache_ttl=self.reference_cache_seconds)

    def dividends(self, symbol: str, *, limit: int = 100) -> ProviderResult:
        symbol = self._symbol(symbol)
        return self._request("dividends", "/stocks/v1/dividends", params={"ticker": symbol, "limit": min(5000, max(1, limit)), "sort": "ex_dividend_date.asc"}, cache_ttl=self.reference_cache_seconds)

    def ratios(self, symbol: str) -> ProviderResult:
        symbol = self._symbol(symbol)
        return self._request("ratios", "/stocks/financials/v1/ratios", params={"ticker": symbol, "limit": 1}, cache_ttl=self.reference_cache_seconds)


def normalized_fallback_quote(
    *,
    symbol: str,
    price: Any,
    change_percent: Any,
    source: str,
    received_timestamp: datetime | None = None,
    event_timestamp: datetime | None = None,
    quality_flags: tuple[str, ...] = ("fallback_provider",),
    calendar: ExchangeCalendar | None = None,
) -> dict[str, Any]:
    received = received_timestamp or datetime.now(timezone.utc)
    event = event_timestamp
    session = (calendar or ExchangeCalendar()).session_at(event or received)
    numeric_price = MassiveRestClient._number(price)
    numeric_change = MassiveRestClient._number(change_percent)
    age_ms = max(0, int((received - event).total_seconds() * 1000)) if event else None
    flags = list(quality_flags)
    thresholds = {"regular": 15_000, "pre": 60_000, "after": 60_000, "closed": 86_400_000}
    is_stale = event is None or (age_ms is not None and age_ms > thresholds[session])
    if event is None:
        flags.extend(("missing_event_timestamp", "freshness_unknown"))
    elif is_stale:
        flags.append("stale")
    quote = NormalizedQuote(
        symbol=symbol.upper(), bid=None, ask=None, bid_size=None, ask_size=None, midpoint=None,
        last_trade_price=None, last_trade_size=None, price=numeric_price,
        price_source=f"{source}_price" if numeric_price is not None else None,
        price_reason=f"Fallback price supplied by {source}.", event_timestamp=event,
        received_timestamp=received, age_ms=age_ms, market_session=session, source=source,
        source_mode="fallback", is_stale=is_stale, quality_flags=tuple(dict.fromkeys(flags)),
        change_percent=numeric_change,
    )
    return quote.payload()
