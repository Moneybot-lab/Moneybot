#!/usr/bin/env python3
from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import hashlib
import json
import os
import re
import shutil
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from moneybot.services.decision_log import read_decision_events
from moneybot.services.outcome_tracking import normalize_action, normalize_unix_ts
from moneybot.services.alpha_atlas_v3_features import build_alpha_atlas_v3_features
from moneybot.services.alpha_atlas_v4_timing_contract import (
    ALPHA_ATLAS_V4_TIMING_CONTRACT_VERSION,
    AlphaAtlasV4TimingRecord,
)
from moneybot.services.market_data_providers import ExchangeCalendar
from moneybot.services.decision_target import (
    TARGET_NAME,
    label_from_forward_return,
    target_metadata,
)
from moneybot.services.corporate_actions import (
    CORPORATE_ACTION_SCHEMA_VERSION,
    adjust_bars_to_asof,
    index_splits,
    load_split_cache,
    price_factor_between,
    split_adjusted_forward_return,
    split_source_hash,
)

SCHEMA_VERSION = "massive-decision-training-rows.v4"
EXCHANGE_CALENDAR = ExchangeCalendar()
DEFAULT_MAX_STALENESS_SESSIONS = 3
V4_FEATURE_CONTRACT_VERSION = "alpha-atlas-v4-features.v2"
RECONSTRUCTION_LINEAGE_VERSION = "alpha-atlas-v4-reconstruction-lineage.v1"
MARKET_AVAILABILITY_POLICY_VERSION = "xnys-completed-daily-bar.v1"
CALCULATION_ENGINE_VERSION = "massive-v4-feature-replay.v1"
SECTOR_BENCHMARK_SYMBOLS = {
    "communication services": "XLC",
    "consumer discretionary": "XLY",
    "consumer staples": "XLP",
    "energy": "XLE",
    "financials": "XLF",
    "health care": "XLV",
    "healthcare": "XLV",
    "industrials": "XLI",
    "materials": "XLB",
    "real estate": "XLRE",
    "technology": "XLK",
    "utilities": "XLU",
}


class BuildTelemetry:
    """Operational timings/counters excluded from all deterministic identities."""

    def __init__(self, output: Path | None = None):
        self.started = time.perf_counter()
        self.output = output
        self.stages: dict[str, float] = {}
        self.counters: dict[str, int] = {}

    def add(self, stage: str, duration: float) -> None:
        self.stages[stage] = self.stages.get(stage, 0.0) + duration

    def count(self, name: str, amount: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + amount

    def progress(self, phase: str, **values: Any) -> None:
        fields = " ".join(f"{key}={value}" for key, value in sorted(values.items()))
        print(
            f"phase={phase} {fields} elapsed={time.perf_counter() - self.started:.3f}",
            file=sys.stderr,
            flush=True,
        )
        self.flush(status="RUNNING", phase=phase)

    def flush(self, *, status: str, phase: str) -> None:
        if self.output is None:
            return
        payload = {
            "schema_version": "alpha-atlas-v4-builder-performance.v1",
            "status": status,
            "phase": phase,
            "elapsed_seconds": round(time.perf_counter() - self.started, 6),
            "stage_seconds": {
                key: round(value, 6) for key, value in sorted(self.stages.items())
            },
            "counters": dict(sorted(self.counters.items())),
        }
        self.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.output.with_suffix(self.output.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        temporary.replace(self.output)


def _iter_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open("r", encoding="utf-8", newline="")


def _coerce_float(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _market_date(raw: Any) -> str | None:
    if raw in {None, ""}:
        return None
    text = str(raw)
    if text.isdigit():
        value = int(text)
        # Massive flat files can encode window_start in nanoseconds,
        # microseconds, milliseconds, or seconds depending on export.
        # Normalize by magnitude before converting to a Python timestamp.
        if value > 100_000_000_000_000_000:
            value = value / 1_000_000_000
        elif value > 100_000_000_000_000:
            value = value / 1_000_000
        elif value > 100_000_000_000:
            value = value / 1_000
        return datetime.fromtimestamp(value, tz=timezone.utc).date().isoformat()
    return text[:10]


def _normalize_market_row(row: dict[str, Any]) -> dict[str, Any] | None:
    symbol = (
        str(row.get("ticker") or row.get("symbol") or row.get("T") or "")
        .strip()
        .upper()
    )
    day = _market_date(
        row.get("date")
        or row.get("day")
        or row.get("window_start")
        or row.get("timestamp")
        or row.get("t")
    )
    close = _coerce_float(row.get("close") or row.get("c") or row.get("Close"))
    if not symbol or not day or close is None:
        return None
    available_at = _parse_utc_datetime(
        row.get("available_at")
        or row.get("provider_available_at")
        or row.get("received_timestamp")
    )
    return {
        "symbol": symbol,
        "date": day,
        "open": _coerce_float(row.get("open") or row.get("o") or row.get("Open")),
        "high": _coerce_float(row.get("high") or row.get("h") or row.get("High")),
        "low": _coerce_float(row.get("low") or row.get("l") or row.get("Low")),
        "close": close,
        "volume": _coerce_float(row.get("volume") or row.get("v") or row.get("Volume")),
        "available_at": available_at.isoformat() if available_at else None,
    }


def _parse_utc_datetime(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


_PATH_DATE_RE = re.compile(r"(20\d{2})[-/](\d{2})[-/](\d{2})")


def _path_market_date(path: Path) -> str | None:
    text = path.as_posix()
    matches = list(_PATH_DATE_RE.finditer(text))
    if not matches:
        return None
    if len(matches) == 1 and not _PATH_DATE_RE.search(path.name):
        return None
    match = matches[-1]
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_market_file(path: Path) -> Iterable[dict[str, Any]]:
    with _iter_text(path) as fh:
        if path.name.endswith(".jsonl") or path.name.endswith(".jsonl.gz"):
            for line in fh:
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(raw, dict):
                    row = _normalize_market_row(raw)
                    if row:
                        yield row
        else:
            reader = csv.DictReader(fh)
            for raw in reader:
                row = _normalize_market_row(dict(raw))
                if row:
                    yield row


def load_market_history(
    raw_root: Path,
    *,
    symbols: set[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    telemetry: BuildTelemetry | None = None,
) -> dict[str, list[dict[str, Any]]]:
    wanted = {
        str(symbol).strip().upper()
        for symbol in symbols or set()
        if str(symbol).strip()
    }
    by_symbol: dict[str, dict[str, dict[str, Any]]] = {}
    discovery_started = time.perf_counter()
    paths = sorted(raw_root.rglob("*"))
    if telemetry:
        telemetry.add("source_file_discovery", time.perf_counter() - discovery_started)
    for path in paths:
        if not path.is_file() or path.name.startswith("_"):
            continue
        if not (
            path.name.endswith(".csv")
            or path.name.endswith(".csv.gz")
            or path.name.endswith(".jsonl")
            or path.name.endswith(".jsonl.gz")
        ):
            continue
        path_day = _path_market_date(path)
        if path_day and start_date and path_day < start_date:
            continue
        if path_day and end_date and path_day > end_date:
            continue
        hashing_started = time.perf_counter()
        object_hash = _sha256_file(path)
        if telemetry:
            telemetry.add(
                "source_object_hashing", time.perf_counter() - hashing_started
            )
            telemetry.count("source_objects_hashed")
        object_size = path.stat().st_size
        relative_path = path.relative_to(raw_root).as_posix()
        object_id = (
            "source_object_"
            + hashlib.sha256(
                f"massive\0{relative_path}\0{object_hash}".encode()
            ).hexdigest()
        )
        parsing_started = time.perf_counter()
        for ordinal, row in enumerate(_read_market_file(path)):
            symbol = str(row["symbol"]).upper()
            day = str(row["date"])
            if wanted and symbol not in wanted:
                continue
            if start_date and day < start_date:
                continue
            if end_date and day > end_date:
                continue
            row_content = {
                key: row.get(key)
                for key in (
                    "symbol",
                    "date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "available_at",
                )
            }
            row["_source_object_id"] = object_id
            row["_source_object_sha256"] = object_hash
            row["_source_relative_path"] = relative_path
            row["_source_object_size"] = object_size
            row["_source_row_ordinal"] = ordinal
            row["_source_raw_row_sha256"] = hashlib.sha256(
                json.dumps(row_content, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            by_symbol.setdefault(symbol, {})[day] = row
        if telemetry:
            telemetry.add(
                "massive_daily_file_parsing", time.perf_counter() - parsing_started
            )
            telemetry.count("source_files_parsed")
    if telemetry:
        telemetry.progress(
            "market_load",
            source_objects=telemetry.counters.get("source_objects_hashed", 0),
        )
    return {
        symbol: [rows[day] for day in sorted(rows)]
        for symbol, rows in by_symbol.items()
    }


def _market_load_window(
    events: list[dict[str, Any]], *, horizon_days: int, history_lag_days: int = 120
) -> tuple[set[str], str | None, str | None]:
    symbols: set[str] = set()
    event_days = []
    for event in events:
        symbol = str(event.get("symbol") or "").strip().upper()
        if symbol:
            symbols.add(symbol)
        ts = normalize_unix_ts(event.get("ts"))
        if ts is not None:
            event_days.append(datetime.fromtimestamp(ts, tz=timezone.utc).date())
    if event_days:
        symbols.add("SPY")
        symbols.update(SECTOR_BENCHMARK_SYMBOLS.values())
    if not event_days:
        return symbols, None, None
    start = min(event_days) - timedelta(days=max(0, history_lag_days))
    end = max(event_days) + timedelta(days=max(1, horizon_days) + 3)
    return symbols, start.isoformat(), end.isoformat()


def _symbol_signal_counts(
    events: list[dict[str, Any]], symbol: str, ts: int, *, window_days: int = 7
) -> dict[str, int]:
    window_start = ts - (max(1, window_days) * 86_400)
    counts = {"signals": 0, "buys": 0, "sells": 0}
    for event in events:
        event_symbol = str(event.get("symbol") or "").strip().upper()
        event_ts = normalize_unix_ts(event.get("ts"))
        if (
            event_symbol != symbol
            or event_ts is None
            or not (window_start <= event_ts < ts)
        ):
            continue
        action = normalize_action(event)
        counts["signals"] += 1
        if action in {"BUY", "STRONG BUY"}:
            counts["buys"] += 1
        elif action == "SELL":
            counts["sells"] += 1
    return counts


def _event_probability_up(event: dict[str, Any]) -> float | None:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    snapshot = event.get("snapshot") if isinstance(event.get("snapshot"), dict) else {}
    for source in (snapshot, payload, event):
        value = _coerce_float(source.get("probability_up"))
        if value is not None:
            return value
    return None


def _previous_symbol_signal(
    events: list[dict[str, Any]], symbol: str, ts: int
) -> dict[str, Any] | None:
    previous: dict[str, Any] | None = None
    previous_ts: int | None = None
    for event in events:
        event_symbol = str(event.get("symbol") or "").strip().upper()
        event_ts = normalize_unix_ts(event.get("ts"))
        if event_symbol != symbol or event_ts is None or event_ts >= ts:
            continue
        if previous_ts is None or event_ts > previous_ts:
            previous = event
            previous_ts = event_ts
    return previous


def _signal_history_index(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[tuple[int, str, float | None, dict[str, Any]]]] = {}
    for event in events:
        symbol = str(event.get("symbol") or "").strip().upper()
        event_ts = normalize_unix_ts(event.get("ts"))
        if not symbol or event_ts is None:
            continue
        grouped.setdefault(symbol, []).append(
            (event_ts, normalize_action(event), _event_probability_up(event), event)
        )
    signal_index: dict[str, dict[str, Any]] = {}
    for symbol, entries in grouped.items():
        entries.sort(key=lambda item: item[0])
        signal_prefix = [0]
        buy_prefix = [0]
        sell_prefix = [0]
        for _, action, _, _ in entries:
            signal_prefix.append(signal_prefix[-1] + 1)
            buy_prefix.append(buy_prefix[-1] + int(action in {"BUY", "STRONG BUY"}))
            sell_prefix.append(sell_prefix[-1] + int(action == "SELL"))
        signal_index[symbol] = {
            "entries": entries,
            "timestamps": [entry[0] for entry in entries],
            "signal_prefix": signal_prefix,
            "buy_prefix": buy_prefix,
            "sell_prefix": sell_prefix,
        }
    return signal_index


def _indexed_signal_context(
    signal_index: dict[str, dict[str, Any]],
    symbol: str,
    ts: int,
    *,
    window_days: int = 7,
) -> tuple[dict[str, int], dict[str, Any] | None, int | None, str | None, float | None]:
    symbol_index = signal_index.get(symbol, {})
    entries = symbol_index.get("entries", [])
    event_ts_values = symbol_index.get("timestamps", [])
    current_pos = bisect.bisect_left(event_ts_values, ts)
    window_start = ts - (max(1, window_days) * 86_400)
    window_pos = bisect.bisect_left(event_ts_values, window_start)
    signal_prefix = symbol_index.get("signal_prefix", [0])
    buy_prefix = symbol_index.get("buy_prefix", [0])
    sell_prefix = symbol_index.get("sell_prefix", [0])
    counts = {
        "signals": signal_prefix[current_pos] - signal_prefix[window_pos],
        "buys": buy_prefix[current_pos] - buy_prefix[window_pos],
        "sells": sell_prefix[current_pos] - sell_prefix[window_pos],
    }
    if current_pos <= 0:
        return counts, None, None, None, None
    previous_ts, previous_action, previous_probability, previous_event = entries[
        current_pos - 1
    ]
    return counts, previous_event, previous_ts, previous_action, previous_probability


def _market_date_index(market: dict[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
    return {
        symbol: [str(row["date"]) for row in rows] for symbol, rows in market.items()
    }


def _row_before_or_on_indexed(
    date_index: dict[str, list[str]], symbol: str, day: str
) -> int | None:
    dates = date_index.get(symbol)
    if not dates:
        return None
    pos = bisect.bisect_right(dates, day) - 1
    return pos if pos >= 0 else None


def _event_day(ts: int) -> str:
    instant = datetime.fromtimestamp(ts, tz=timezone.utc)
    return EXCHANGE_CALENDAR.local_date(instant).isoformat()


def _row_before_or_on(rows: list[dict[str, Any]], day: str) -> int | None:
    idx = None
    for pos, row in enumerate(rows):
        if row["date"] <= day:
            idx = pos
        else:
            break
    return idx


def _mean(values: list[float]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _rolling_close_mean(
    rows: list[dict[str, Any]], idx: int, window: int
) -> float | None:
    if idx + 1 < window:
        return None
    closes = [
        _coerce_float(row.get("close")) for row in rows[idx - window + 1 : idx + 1]
    ]
    if any(value is None for value in closes):
        return None
    return round(float(sum(closes)) / window, 6)


def _ema_at(rows: list[dict[str, Any]], idx: int, span: int) -> float | None:
    closes = [_coerce_float(row.get("close")) for row in rows[: idx + 1]]
    if len(closes) < span or any(value is None for value in closes):
        return None
    alpha = 2.0 / (span + 1.0)
    ema = float(closes[0])
    for close in closes[1:]:
        ema = (float(close) * alpha) + (ema * (1.0 - alpha))
    return round(ema, 6)


def _rsi_at(rows: list[dict[str, Any]], idx: int, window: int = 14) -> float | None:
    if idx < window:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for pos in range(idx - window + 1, idx + 1):
        close = _coerce_float(rows[pos].get("close"))
        prev = _coerce_float(rows[pos - 1].get("close"))
        if close is None or prev is None:
            return None
        delta = close - prev
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = _mean(gains)
    avg_loss = _mean(losses)
    if avg_gain is None or avg_loss is None:
        return None
    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 6)


def _macd_line_at(rows: list[dict[str, Any]], idx: int) -> float | None:
    ema12 = _ema_at(rows, idx, 12)
    ema26 = _ema_at(rows, idx, 26)
    if ema12 is None or ema26 is None:
        return None
    return ema12 - ema26


def _ema_values(values: list[float], span: int) -> float | None:
    if len(values) < span:
        return None
    alpha = 2.0 / (span + 1.0)
    ema = float(values[0])
    for value in values[1:]:
        ema = (float(value) * alpha) + (ema * (1.0 - alpha))
    return ema


def _macd_components_at(
    rows: list[dict[str, Any]], idx: int
) -> tuple[float | None, float | None, float | None]:
    macd_line = _macd_line_at(rows, idx)
    macd_values = [_macd_line_at(rows, pos) for pos in range(idx + 1)]
    clean = [float(value) for value in macd_values if value is not None]
    signal = _ema_values(clean, 9)
    hist = (
        (macd_line - signal) if macd_line is not None and signal is not None else None
    )
    return (
        round(macd_line, 6) if macd_line is not None else None,
        round(signal, 6) if signal is not None else None,
        round(hist, 6) if hist is not None else None,
    )


def _atr_at(rows: list[dict[str, Any]], idx: int, window: int = 14) -> float | None:
    if idx < window:
        return None
    true_ranges: list[float] = []
    for pos in range(idx - window + 1, idx + 1):
        high = _coerce_float(rows[pos].get("high"))
        low = _coerce_float(rows[pos].get("low"))
        prev_close = _coerce_float(rows[pos - 1].get("close"))
        if high is None or low is None or prev_close is None:
            return None
        true_ranges.append(
            max(high - low, abs(high - prev_close), abs(low - prev_close))
        )
    atr = _mean(true_ranges)
    return round(atr, 6) if atr is not None else None


def _lagged_return(
    rows: list[dict[str, Any]] | None, idx: int | None, days: int
) -> float | None:
    if rows is None or idx is None or idx < days:
        return None
    close = _coerce_float(rows[idx].get("close"))
    previous = _coerce_float(rows[idx - days].get("close"))
    if close is None:
        return None
    return _pct(close, previous)


def _beta_to_benchmark(
    symbol_rows: list[dict[str, Any]],
    benchmark_rows: list[dict[str, Any]] | None,
    symbol_idx: int,
    benchmark_idx: int | None,
    window: int = 20,
) -> float | None:
    if (
        benchmark_rows is None
        or benchmark_idx is None
        or symbol_idx < window
        or benchmark_idx < window
    ):
        return None
    symbol_returns: list[float] = []
    benchmark_returns: list[float] = []
    for offset in range(window - 1, -1, -1):
        symbol_pos = symbol_idx - offset
        benchmark_pos = benchmark_idx - offset
        symbol_return = _lagged_return(symbol_rows, symbol_pos, 1)
        benchmark_return = _lagged_return(benchmark_rows, benchmark_pos, 1)
        if symbol_return is None or benchmark_return is None:
            return None
        symbol_returns.append(symbol_return)
        benchmark_returns.append(benchmark_return)
    symbol_mean = sum(symbol_returns) / window
    benchmark_mean = sum(benchmark_returns) / window
    benchmark_variance = (
        sum((ret - benchmark_mean) ** 2 for ret in benchmark_returns) / window
    )
    if benchmark_variance == 0.0:
        return None
    covariance = (
        sum(
            (symbol_return - symbol_mean) * (benchmark_return - benchmark_mean)
            for symbol_return, benchmark_return in zip(
                symbol_returns, benchmark_returns
            )
        )
        / window
    )
    return round(covariance / benchmark_variance, 6)


def _sector_benchmark_symbol(
    event: dict[str, Any], payload: dict[str, Any], snapshot: dict[str, Any]
) -> str:
    for source in (snapshot, payload, event):
        for key in ("sector_etf", "sector_benchmark", "sector_benchmark_symbol"):
            value = str(source.get(key) or "").strip().upper()
            if value:
                return value
        sector = str(source.get("sector") or "").strip().lower()
        if sector in SECTOR_BENCHMARK_SYMBOLS:
            return SECTOR_BENCHMARK_SYMBOLS[sector]
    return "SPY"


def _market_regime_risk_on(
    spy_history: list[dict[str, Any]] | None, spy_idx: int | None
) -> int | None:
    if spy_history is None or spy_idx is None:
        return None
    spy_return_5d = _lagged_return(spy_history, spy_idx, 5)
    spy_close = _coerce_float(spy_history[spy_idx].get("close"))
    spy_sma_20 = _rolling_close_mean(spy_history, spy_idx, 20)
    if spy_return_5d is None or spy_close is None or spy_sma_20 is None:
        return None
    return int(spy_return_5d > 0.0 and spy_close >= spy_sma_20)


def _rolling_vwap(
    rows: list[dict[str, Any]], idx: int, window: int = 20
) -> float | None:
    if idx + 1 < window:
        return None
    total_dollar_volume = 0.0
    total_volume = 0.0
    for row in rows[idx - window + 1 : idx + 1]:
        close = _coerce_float(row.get("close"))
        volume = _coerce_float(row.get("volume"))
        if close is None or volume is None:
            return None
        total_dollar_volume += close * volume
        total_volume += volume
    if total_volume == 0.0:
        return None
    return round(total_dollar_volume / total_volume, 6)


def _vwap_slope(
    rows: list[dict[str, Any]], idx: int, window: int = 10, vwap_window: int = 20
) -> float | None:
    if idx + 1 < window + vwap_window - 1:
        return None
    values = [
        _rolling_vwap(rows, pos, vwap_window)
        for pos in range(idx - window + 1, idx + 1)
    ]
    if any(value is None for value in values):
        return None
    y = [float(value) for value in values]
    x_mean = (window - 1) / 2.0
    y_mean = sum(y) / window
    denom = sum((pos - x_mean) ** 2 for pos in range(window))
    if denom == 0.0 or y[0] == 0.0:
        return None
    slope = (
        sum((pos - x_mean) * (value - y_mean) for pos, value in enumerate(y)) / denom
    )
    return round(slope / y[0], 6)


def _rolling_numeric_mean(
    rows: list[dict[str, Any]], idx: int, window: int, column: str
) -> float | None:
    if idx + 1 < window:
        return None
    values = [
        _coerce_float(row.get(column)) for row in rows[idx - window + 1 : idx + 1]
    ]
    if any(value is None for value in values):
        return None
    return round(sum(float(value) for value in values) / window, 6)


def _rolling_zscore(
    rows: list[dict[str, Any]], idx: int, window: int, column: str
) -> float | None:
    current = _coerce_float(rows[idx].get(column)) if idx < len(rows) else None
    if current is None or idx + 1 < window:
        return None
    values = [
        _coerce_float(row.get(column)) for row in rows[idx - window + 1 : idx + 1]
    ]
    if any(value is None for value in values):
        return None
    clean = [float(value) for value in values]
    avg = sum(clean) / window
    variance = sum((value - avg) ** 2 for value in clean) / window
    std = variance**0.5
    if std == 0.0:
        return 0.0
    return round((float(current) - avg) / std, 6)


def _rolling_extreme(
    rows: list[dict[str, Any]], idx: int, window: int, column: str, *, high: bool
) -> float | None:
    if idx + 1 < window:
        return None
    values = [
        _coerce_float(row.get(column)) for row in rows[idx - window + 1 : idx + 1]
    ]
    if any(value is None for value in values):
        return None
    return round(max(values) if high else min(values), 6)


def _return_volatility(
    rows: list[dict[str, Any]] | None, idx: int | None, window: int
) -> float | None:
    if rows is None or idx is None or idx < window:
        return None
    returns: list[float] = []
    for pos in range(idx - window + 1, idx + 1):
        close = _coerce_float(rows[pos].get("close"))
        prev_close = _coerce_float(rows[pos - 1].get("close"))
        ret = _pct(float(close), prev_close) if close is not None else None
        if ret is None:
            return None
        returns.append(ret)
    avg = sum(returns) / len(returns)
    variance = sum((ret - avg) ** 2 for ret in returns) / len(returns)
    return round(variance**0.5, 6)


def _trend_slope(rows: list[dict[str, Any]], idx: int, window: int) -> float | None:
    if idx + 1 < window:
        return None
    closes = [
        _coerce_float(row.get("close")) for row in rows[idx - window + 1 : idx + 1]
    ]
    if any(value is None for value in closes):
        return None
    y = [float(value) for value in closes]
    x_mean = (window - 1) / 2.0
    y_mean = sum(y) / window
    denom = sum((pos - x_mean) ** 2 for pos in range(window))
    if denom == 0.0 or y[0] == 0.0:
        return None
    slope = (
        sum((pos - x_mean) * (value - y_mean) for pos, value in enumerate(y)) / denom
    )
    return round(slope / y[0], 6)


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return round(float(numerator) / float(denominator), 6)


def _pct(newer: float, older: float | None) -> float | None:
    if older in {None, 0}:
        return None
    return round((newer / float(older)) - 1.0, 6)


def _session_distance(older: date, newer: date) -> int:
    count = 0
    candidate = older
    while candidate < newer:
        candidate += timedelta(days=1)
        if EXCHANGE_CALENDAR.is_trading_day(candidate):
            count += 1
    return count


def _bar_availability(row: dict[str, Any]) -> datetime | None:
    return _parse_utc_datetime(row.get("available_at"))


def _feature_index_for_decision(
    rows: list[dict[str, Any]],
    dates: list[str],
    decision_at: datetime,
    *,
    max_staleness_sessions: int,
) -> tuple[int | None, datetime | None, str | None]:
    local_day = EXCHANGE_CALENDAR.local_date(decision_at)
    current_idx = _row_before_or_on_indexed({"_": dates}, "_", local_day.isoformat())
    current_row = rows[current_idx] if current_idx is not None else None
    current_is_today = bool(
        current_row and str(current_row.get("date")) == local_day.isoformat()
    )
    after_close = bool(
        EXCHANGE_CALENDAR.is_trading_day(local_day)
        and decision_at >= EXCHANGE_CALENDAR.session_close(local_day)
    )
    if current_is_today and after_close:
        available_at = _bar_availability(current_row)
        if available_at is None or available_at > decision_at:
            return None, None, "current_session_provider_availability_unproven"
        idx = current_idx
        source_at = EXCHANGE_CALENDAR.session_close(local_day)
        availability_at = available_at
    else:
        permitted_day = EXCHANGE_CALENDAR.previous_session(local_day)
        idx = _row_before_or_on_indexed({"_": dates}, "_", permitted_day.isoformat())
        if idx is None:
            return None, None, "missing_completed_daily_bar"
        source_day = date.fromisoformat(str(rows[idx]["date"]))
        source_at = EXCHANGE_CALENDAR.session_close(source_day)
        availability_at = _bar_availability(rows[idx]) or source_at
        if availability_at > decision_at:
            return None, None, "daily_bar_available_after_feature_cutoff"
    source_day = date.fromisoformat(str(rows[idx]["date"]))
    reference_day = (
        local_day
        if EXCHANGE_CALENDAR.is_trading_day(local_day)
        else EXCHANGE_CALENDAR.previous_session(local_day, include_current=True)
    )
    if _session_distance(source_day, reference_day) > max_staleness_sessions:
        return None, None, "stale_daily_bar"
    return idx, availability_at, None


def _exact_row_index(dates: list[str], day: date) -> int | None:
    pos = bisect.bisect_left(dates, day.isoformat())
    return pos if pos < len(dates) and dates[pos] == day.isoformat() else None


def _advance_session(day: date, count: int) -> date:
    result = day
    for _ in range(max(0, count)):
        result = EXCHANGE_CALENDAR.next_session(result)
    return result


def _feature_safe_splits(
    events: list[dict[str, Any]], decision_at: datetime, market_session: date
) -> list[dict[str, Any]]:
    safe = []
    for event in events:
        execution_day = date.fromisoformat(str(event["execution_date"]))
        available_at = _parse_utc_datetime(event.get("available_at"))
        if available_at is not None and available_at <= decision_at:
            safe.append(event)
        elif execution_day < market_session:
            safe.append(event)
    return safe


def build_training_rows_from_raw_market(
    events: list[dict[str, Any]],
    market: dict[str, list[dict[str, Any]]],
    *,
    horizon_days: int = 5,
    split_events: list[dict[str, Any]] | None = None,
    max_staleness_sessions: int = DEFAULT_MAX_STALENESS_SESSIONS,
    telemetry: BuildTelemetry | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    label_name = (
        TARGET_NAME
        if horizon_days == target_metadata()["horizon_days"]
        else f"label_up_{horizon_days}d"
    )
    rows: list[dict[str, Any]] = []
    if split_events is None:
        raise ValueError("Canonical Massive rows require normalized split metadata")
    splits_by_symbol = index_splits(split_events)
    metadata_hash = split_source_hash(split_events)
    summary = {
        "events_scanned": 0,
        "rows_joined": 0,
        "missing_symbol_history": 0,
        "insufficient_history": 0,
        "insufficient_forward_window": 0,
        "rejected_unproven_availability": 0,
        "rejected_stale_feature_family": 0,
        "rejected_missing_context": 0,
        "rejected_missing_entry_price": 0,
        "split_events_loaded": len(split_events),
        "training_rows_affected": 0,
        "feature_windows_crossing_splits": 0,
        "label_windows_crossing_splits": 0,
        "bars_adjusted": 0,
        "price_values_adjusted": 0,
        "volume_values_adjusted": 0,
        "affected_feature_rows": 0,
        "affected_label_rows": 0,
        "split_provenance": [],
    }
    indexing_started = time.perf_counter()
    signal_index = _signal_history_index(events)
    if telemetry:
        telemetry.add("decision_log_indexing", time.perf_counter() - indexing_started)
    indexing_started = time.perf_counter()
    date_index = _market_date_index(market)
    if telemetry:
        telemetry.add("market_history_indexing", time.perf_counter() - indexing_started)
    feature_cache: dict[tuple[str, int], dict[str, Any]] = {}
    adjusted_history_cache: dict[
        tuple[str, str, tuple[str, ...]], list[dict[str, Any]]
    ] = {}
    event_identity_counts: dict[str, int] = {}

    def adjusted_history(
        symbol: str,
        raw: list[dict[str, Any]],
        safe_splits: list[dict[str, Any]],
        asof_day: str,
    ) -> list[dict[str, Any]]:
        key = (
            symbol,
            asof_day,
            tuple(sorted(str(item.get("id") or "") for item in safe_splits)),
        )
        cached = adjusted_history_cache.get(key)
        if cached is None:
            started = time.perf_counter()
            cached = adjust_bars_to_asof(raw, safe_splits, asof_day, audit=summary)
            adjusted_history_cache[key] = cached
            if telemetry:
                telemetry.add(
                    "split_adjusted_history_preparation", time.perf_counter() - started
                )
                telemetry.count("split_adjusted_history_cache_misses")
        elif telemetry:
            telemetry.count("split_adjusted_history_cache_hits")
        return cached

    observations_started = time.perf_counter()
    for event in events:
        summary["events_scanned"] += 1
        if telemetry and summary["events_scanned"] % 5000 == 0:
            telemetry.progress(
                "build",
                observations_processed=summary["events_scanned"],
                observations_emitted=summary["rows_joined"],
            )
        symbol = str(event.get("symbol") or "").strip().upper()
        ts = normalize_unix_ts(event.get("ts"))
        if not symbol or ts is None or symbol not in market:
            summary["missing_symbol_history"] += 1
            continue
        (
            signal_counts_7d,
            previous_signal,
            previous_signal_ts,
            previous_action,
            previous_probability,
        ) = _indexed_signal_context(
            signal_index,
            symbol,
            ts,
            window_days=7,
        )
        decision_at = datetime.fromtimestamp(ts, tz=timezone.utc)
        event_day = _event_day(ts)
        market_session = date.fromisoformat(event_day)
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        snapshot = (
            event.get("snapshot") if isinstance(event.get("snapshot"), dict) else {}
        )
        sector_benchmark_symbol = _sector_benchmark_symbol(event, payload, snapshot)
        raw_history = market[symbol]
        if len(raw_history) < 6:
            summary["insufficient_history"] += 1
            continue
        idx, symbol_available_at, symbol_rejection = _feature_index_for_decision(
            raw_history,
            date_index.get(symbol, []),
            decision_at,
            max_staleness_sessions=max_staleness_sessions,
        )
        if idx is None:
            if symbol_rejection == "stale_daily_bar":
                summary["rejected_stale_feature_family"] += 1
            else:
                summary["rejected_unproven_availability"] += 1
            continue
        feature_day = str(raw_history[idx]["date"])
        feature_splits = _feature_safe_splits(
            splits_by_symbol.get(symbol, []), decision_at, market_session
        )
        history = adjusted_history(symbol, raw_history, feature_splits, feature_day)
        raw_spy_history = market.get("SPY")
        context_required = True
        if context_required and raw_spy_history is None:
            summary["rejected_missing_context"] += 1
            continue
        spy_idx = None
        spy_available_at = None
        if raw_spy_history:
            spy_idx, spy_available_at, spy_rejection = _feature_index_for_decision(
                raw_spy_history,
                date_index.get("SPY", []),
                decision_at,
                max_staleness_sessions=max_staleness_sessions,
            )
            if spy_idx is None:
                if spy_rejection == "stale_daily_bar":
                    summary["rejected_stale_feature_family"] += 1
                else:
                    summary["rejected_unproven_availability"] += 1
                continue
        spy_history = (
            adjusted_history(
                "SPY",
                raw_spy_history,
                _feature_safe_splits(
                    splits_by_symbol.get("SPY", []), decision_at, market_session
                ),
                str(raw_spy_history[spy_idx]["date"]),
            )
            if raw_spy_history
            else None
        )
        if idx is None or idx < 5:
            summary["insufficient_history"] += 1
            continue

        entry_session = EXCHANGE_CALENDAR.entry_session_after(decision_at)
        exit_session = _advance_session(entry_session, max(1, horizon_days) - 1)
        entry_idx = _exact_row_index(date_index.get(symbol, []), entry_session)
        label_idx = _exact_row_index(date_index.get(symbol, []), exit_session)
        if (
            entry_idx is None
            or _coerce_float(raw_history[entry_idx].get("open")) is None
        ):
            summary["rejected_missing_entry_price"] += 1
            continue
        if (
            label_idx is None
            or _coerce_float(raw_history[label_idx].get("close")) is None
        ):
            summary["insufficient_forward_window"] += 1
            continue

        asof = history[idx]
        prev1 = history[idx - 1]
        prev5 = history[idx - 5]
        prev10 = history[idx - 10] if idx >= 10 else {}
        prev20 = history[idx - 20] if idx >= 20 else {}
        entry = raw_history[entry_idx]
        future = raw_history[label_idx]
        close = float(asof["close"])
        label_factor = price_factor_between(
            splits_by_symbol.get(symbol, []), str(entry["date"]), str(future["date"])
        )
        return_fwd = round(
            split_adjusted_forward_return(
                float(entry["open"]),
                float(future["close"]),
                str(entry["date"]),
                str(future["date"]),
                splits_by_symbol.get(symbol, []),
            ),
            6,
        )
        feature_split_ids = sorted(
            {
                split_id
                for bar in history[: idx + 1]
                for split_id in bar.get("_applicable_split_ids", [])
                if split_id
            }
        )
        label_split_ids = [
            str(item.get("id") or "")
            for item in splits_by_symbol.get(symbol, [])
            if str(entry["date"]) < item["execution_date"] <= str(future["date"])
        ]
        if feature_split_ids:
            summary["feature_windows_crossing_splits"] += 1
            summary["affected_feature_rows"] += 1
        if label_factor != 1.0:
            summary["label_windows_crossing_splits"] += 1
            summary["affected_label_rows"] += 1
        if feature_split_ids or label_factor != 1.0:
            summary["training_rows_affected"] += 1
            source_start = max(0, idx - 20)
            summary["split_provenance"].append(
                {
                    "symbol": symbol,
                    "event_date": event_day,
                    "source_bar_dates": [
                        bar["date"] for bar in raw_history[source_start : idx + 1]
                    ],
                    "raw_closes": [
                        bar["close"] for bar in raw_history[source_start : idx + 1]
                    ],
                    "adjusted_closes": [
                        bar["close"] for bar in history[source_start : idx + 1]
                    ],
                    "applicable_split_ids": feature_split_ids,
                    "label_split_ids": label_split_ids,
                    "label_cumulative_adjustment_factor": label_factor,
                }
            )
        current_action = normalize_action(event)
        current_probability = _event_probability_up(event)
        cache_key = (symbol, idx, event_day)
        cached_features = feature_cache.get(cache_key)
        if cached_features is None:
            feature_started = time.perf_counter()
            sma_10_cached = _rolling_close_mean(history, idx, 10)
            sma_20_cached = _rolling_close_mean(history, idx, 20)
            sma_50_cached = _rolling_close_mean(history, idx, 50)
            high_20_cached = _rolling_extreme(history, idx, 20, "high", high=True)
            low_20_cached = _rolling_extreme(history, idx, 20, "low", high=False)
            macd_line_cached, macd_signal_cached, macd_hist_cached = (
                _macd_components_at(history, idx)
            )
            volume_cached = _coerce_float(asof.get("volume"))
            volume_avg_5_cached = _rolling_numeric_mean(history, idx, 5, "volume")
            volume_avg_20_cached = _rolling_numeric_mean(history, idx, 20, "volume")
            vwap_cached = _rolling_vwap(history, idx, 20)
            cached_features = {
                "sma_10": sma_10_cached,
                "sma_20": sma_20_cached,
                "sma_50": sma_50_cached,
                "high_20": high_20_cached,
                "low_20": low_20_cached,
                "macd_line": macd_line_cached,
                "macd_signal": macd_signal_cached,
                "macd_hist": macd_hist_cached,
                "volume": volume_cached,
                "volume_avg_5": volume_avg_5_cached,
                "volume_avg_20": volume_avg_20_cached,
                "vwap": vwap_cached,
                "open_price": _coerce_float(asof.get("open")),
                "return_5d_lagged": _pct(close, prev5.get("close")),
                "return_20d_lagged": _pct(close, prev20.get("close")),
                "trend_slope_10d": _trend_slope(history, idx, 10),
                "trend_slope_20d": _trend_slope(history, idx, 20),
                "volatility_5d": _return_volatility(history, idx, 5),
                "volatility_20d": _return_volatility(history, idx, 20),
                "ema_10": _ema_at(history, idx, 10),
                "ema_20": _ema_at(history, idx, 20),
                "rsi_14": _rsi_at(history, idx, 14),
                "atr_14": _atr_at(history, idx, 14),
                "return_1d_lagged": _pct(close, prev1.get("close")),
                "return_10d_lagged": _pct(close, prev10.get("close")),
                "volume_zscore_20d": _rolling_zscore(history, idx, 20, "volume"),
                "vwap_slope": _vwap_slope(history, idx, 10, 20),
            }
            feature_cache[cache_key] = cached_features
            if telemetry:
                telemetry.add(
                    "feature_construction", time.perf_counter() - feature_started
                )
                telemetry.count("feature_cache_misses")
        elif telemetry:
            telemetry.count("feature_cache_hits")
        sma_10 = cached_features["sma_10"]
        sma_20 = cached_features["sma_20"]
        sma_50 = cached_features["sma_50"]
        high_20 = cached_features["high_20"]
        low_20 = cached_features["low_20"]
        macd_line = cached_features["macd_line"]
        macd_signal = cached_features["macd_signal"]
        macd_hist = cached_features["macd_hist"]
        volume = cached_features["volume"]
        volume_avg_5 = cached_features["volume_avg_5"]
        volume_avg_20 = cached_features["volume_avg_20"]
        vwap = cached_features["vwap"]
        open_price = cached_features["open_price"]
        return_5d_lagged = cached_features["return_5d_lagged"]
        return_20d_lagged = cached_features["return_20d_lagged"]
        shared_v3_features = build_alpha_atlas_v3_features(
            symbol_bars=history[: idx + 1],
            spy_bars=(
                spy_history[: spy_idx + 1]
                if spy_history and spy_idx is not None
                else []
            ),
            asof_date=event_day,
        )
        spy_return_5d = _lagged_return(spy_history, spy_idx, 5)
        raw_sector_history = market.get(sector_benchmark_symbol)
        sector_idx = None
        sector_available_at = None
        if sector_benchmark_symbol != "SPY" and context_required:
            if raw_sector_history is None:
                summary["rejected_missing_context"] += 1
                continue
            (
                sector_idx,
                sector_available_at,
                sector_rejection,
            ) = _feature_index_for_decision(
                raw_sector_history,
                date_index.get(sector_benchmark_symbol, []),
                decision_at,
                max_staleness_sessions=max_staleness_sessions,
            )
            if sector_idx is None:
                if sector_rejection == "stale_daily_bar":
                    summary["rejected_stale_feature_family"] += 1
                else:
                    summary["rejected_unproven_availability"] += 1
                continue
        elif sector_benchmark_symbol == "SPY":
            sector_idx = spy_idx
            sector_available_at = spy_available_at
        sector_history = (
            adjusted_history(
                sector_benchmark_symbol,
                raw_sector_history,
                _feature_safe_splits(
                    splits_by_symbol.get(sector_benchmark_symbol, []),
                    decision_at,
                    market_session,
                ),
                str(raw_sector_history[sector_idx]["date"]),
            )
            if raw_sector_history
            else None
        )
        sector_return_5d = _lagged_return(sector_history, sector_idx, 5)
        event_fingerprint = hashlib.sha256(
            json.dumps(
                event, sort_keys=True, default=str, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        event_occurrence = event_identity_counts.get(event_fingerprint, 0)
        event_identity_counts[event_fingerprint] = event_occurrence + 1
        fallback_decision_id = f"event_{event_fingerprint}_{event_occurrence}"
        entry_at = EXCHANGE_CALENDAR.session_open(entry_session)
        exit_at = EXCHANGE_CALENDAR.session_close(exit_session)
        feature_source_at = {
            "symbol_daily": EXCHANGE_CALENDAR.session_close(
                date.fromisoformat(str(asof["date"]))
            )
        }
        feature_availability_at = {
            "symbol_daily": (
                symbol_available_at.isoformat() if symbol_available_at else None
            )
        }
        if spy_history and spy_idx is not None:
            feature_source_at["spy_daily"] = EXCHANGE_CALENDAR.session_close(
                date.fromisoformat(str(spy_history[spy_idx]["date"]))
            )
            feature_availability_at["spy_daily"] = (
                spy_available_at.isoformat() if spy_available_at else None
            )
            feature_source_at["market_regime"] = feature_source_at["spy_daily"]
            feature_availability_at["market_regime"] = feature_availability_at[
                "spy_daily"
            ]
            feature_source_at["volatility_proxy"] = feature_source_at["spy_daily"]
            feature_availability_at["volatility_proxy"] = feature_availability_at[
                "spy_daily"
            ]
        if sector_history and sector_idx is not None:
            feature_source_at["sector_daily"] = EXCHANGE_CALENDAR.session_close(
                date.fromisoformat(str(sector_history[sector_idx]["date"]))
            )
            feature_availability_at["sector_daily"] = (
                sector_available_at.isoformat() if sector_available_at else None
            )
        timing = AlphaAtlasV4TimingRecord(
            decision_id=str(event.get("decision_id") or fallback_decision_id),
            symbol=symbol,
            point_in_time_symbol_id=str(
                event.get("point_in_time_symbol_id") or f"{symbol}:{event_day}"
            ),
            exchange=str(event.get("exchange") or "XNAS"),
            trading_calendar=EXCHANGE_CALENDAR.identifier,
            model_feature_contract_version=V4_FEATURE_CONTRACT_VERSION,
            decision_at=decision_at,
            feature_cutoff_at=decision_at,
            latest_source_bar_at=feature_source_at,
            entry_at=entry_at,
            label_start_at=entry_at,
            exit_at=exit_at,
            entry_price_source="official_regular_session_open",
            exit_price_source="official_regular_session_close",
            data_provider_id=str(event.get("data_provider_id") or "massive-flatfile"),
            corporate_action_adjustment_ids=tuple(
                sorted(set(feature_split_ids + label_split_ids))
            ),
            staleness_status="fresh",
            rejection_reason=None,
            code_commit=str(event.get("code_commit") or "unrecorded-research-commit"),
            dataset_manifest_hash=str(
                event.get("dataset_manifest_hash") or "pending-write-manifest"
            ),
            transaction_cost_bps=None,
            entry_slippage_bps=None,
            exit_slippage_bps=None,
        )
        row = {
            "ts": ts,
            "decision_id": timing.decision_id,
            "event_date": event_day,
            "market_asof_date": asof["date"],
            "feature_market_asof_date": asof["date"],
            "label_asof_date": future["date"],
            "timing_contract_version": ALPHA_ATLAS_V4_TIMING_CONTRACT_VERSION,
            "model_feature_contract_version": timing.model_feature_contract_version,
            "label_horizon_sessions": max(1, horizon_days),
            "lane": str(event.get("lane") or "track_b_research"),
            "universe_policy_version": str(
                event.get("universe_policy_version") or "track-b-us-equities.v1"
            ),
            "execution_cost_policy_version": event.get("execution_cost_policy_version"),
            "decision_at": timing.decision_at.isoformat(),
            "feature_cutoff_at": timing.feature_cutoff_at.isoformat(),
            "entry_at": timing.entry_at.isoformat(),
            "entry_session_date": entry_session,
            "label_start_at": timing.label_start_at.isoformat(),
            "exit_at": timing.exit_at.isoformat(),
            "exit_session_date": exit_session,
            "market_session_date": event_day,
            "feature_family_source_at": {
                key: value.isoformat() for key, value in feature_source_at.items()
            },
            "feature_family_available_at": feature_availability_at,
            "entry_price_source": timing.entry_price_source,
            "exit_price_source": timing.exit_price_source,
            "exchange_calendar": timing.trading_calendar,
            "staleness_status": timing.staleness_status,
            "staleness_tolerance_sessions": max_staleness_sessions,
            "rejection_reason": None,
            "point_in_time_symbol_id": timing.point_in_time_symbol_id,
            "corporate_action_availability_policy": "feature_actions_known_by_cutoff;label_actions_realized_in_horizon",
            "code_commit": timing.code_commit,
            "dataset_manifest_hash": timing.dataset_manifest_hash,
            "corporate_action_schema_version": CORPORATE_ACTION_SCHEMA_VERSION,
            "canonical_dataset_schema_version": SCHEMA_VERSION,
            "split_metadata_hash": metadata_hash,
            "price_adjustment_policy": "event_time_split_adjusted",
            "volume_adjustment_policy": "inverse_split_factor",
            "feature_split_ids": feature_split_ids,
            "label_split_ids": label_split_ids,
            "label_split_adjustment_factor": label_factor,
            "raw_entry_price": float(entry["open"]),
            "adjusted_entry_price": float(entry["open"]) * label_factor,
            "raw_exit_price": float(future["close"]),
            "adjusted_exit_price": float(future["close"]),
            "symbol": symbol,
            "sector_benchmark_symbol": sector_benchmark_symbol,
            "endpoint": str(event.get("endpoint") or "unknown"),
            "decision_source": str(event.get("decision_source") or "unknown"),
            "recommendation": normalize_action(event),
            "probability_up": snapshot.get(
                "probability_up", payload.get("probability_up")
            ),
            "model_version": snapshot.get(
                "model_version", payload.get("model_version")
            ),
            "feature_close": close,
            "request_prior_state": {
                "symbol_signal_count_7d": signal_counts_7d["signals"],
                "symbol_buy_count_7d": signal_counts_7d["buys"],
                "symbol_sell_count_7d": signal_counts_7d["sells"],
                "days_since_last_signal": (
                    round((ts - previous_signal_ts) / 86_400, 6)
                    if previous_signal_ts is not None
                    else None
                ),
                "previous_recommendation_buy": (
                    int(previous_action in {"BUY", "STRONG BUY"})
                    if previous_action is not None
                    else 0
                ),
                "recommendation_changed": (
                    int(previous_action != current_action)
                    if previous_action is not None
                    else 0
                ),
                "probability_up_delta_from_last_signal": (
                    round(current_probability - previous_probability, 6)
                    if current_probability is not None
                    and previous_probability is not None
                    else None
                ),
                "prior_signal_at": (
                    datetime.fromtimestamp(
                        previous_signal_ts, tz=timezone.utc
                    ).isoformat()
                    if previous_signal_ts is not None
                    else None
                ),
                "prior_signal_source_identifier": (
                    str(
                        (previous_signal.get("snapshot") or {}).get("model_version")
                        or (previous_signal.get("payload") or {}).get("model_version")
                        or previous_signal.get("decision_source")
                        or "unknown"
                    )
                    if isinstance(previous_signal, dict)
                    else None
                ),
                "classification": "request_level_prior_model_and_recommendation_history",
            },
            "feature_sma_10": sma_10,
            "feature_sma_20": sma_20,
            "feature_sma_50": sma_50,
            "feature_sma_10_over_20": _ratio(sma_10, sma_20),
            "feature_sma_20_over_50": _ratio(sma_20, sma_50),
            "feature_trend_slope_10d": cached_features["trend_slope_10d"],
            "feature_trend_slope_20d": cached_features["trend_slope_20d"],
            "feature_volatility_5d": cached_features["volatility_5d"],
            "feature_volatility_20d": cached_features["volatility_20d"],
            "feature_drawdown_from_20d_high": _pct(close, high_20),
            "feature_distance_from_20d_low": _pct(close, low_20),
            "feature_gap_percent": _pct(open_price, prev1.get("close")),
            "feature_ema_10": cached_features["ema_10"],
            "feature_ema_20": cached_features["ema_20"],
            "feature_price_vs_sma_20": _pct(close, sma_20),
            "feature_price_vs_sma_50": _pct(close, sma_50),
            "feature_rsi_14": cached_features["rsi_14"],
            "feature_macd": macd_line,
            "feature_macd_signal": macd_signal,
            "feature_macd_hist": macd_hist,
            "feature_atr_14": cached_features["atr_14"],
            "feature_spy_return_1d": _lagged_return(spy_history, spy_idx, 1),
            "feature_spy_return_5d": spy_return_5d,
            "feature_symbol_minus_spy_5d": (
                round(return_5d_lagged - spy_return_5d, 6)
                if return_5d_lagged is not None and spy_return_5d is not None
                else None
            ),
            "feature_symbol_beta_20d": _beta_to_benchmark(
                history, spy_history, idx, spy_idx, 20
            ),
            "feature_sector_relative_return_5d": (
                round(return_5d_lagged - sector_return_5d, 6)
                if return_5d_lagged is not None and sector_return_5d is not None
                else None
            ),
            "feature_market_regime_risk_on": _market_regime_risk_on(
                spy_history, spy_idx
            ),
            "feature_market_volatility_proxy": _return_volatility(
                spy_history, spy_idx, 20
            ),
            "feature_return_1d_lagged": cached_features["return_1d_lagged"],
            "feature_return_5d_lagged": return_5d_lagged,
            "feature_return_10d_lagged": cached_features["return_10d_lagged"],
            "feature_return_20d_lagged": return_20d_lagged,
            "feature_momentum_5d_vs_20d": (
                round(return_5d_lagged - return_20d_lagged, 6)
                if return_5d_lagged is not None and return_20d_lagged is not None
                else None
            ),
            "feature_volume": asof.get("volume"),
            "feature_volume_ratio_20d": _ratio(volume, volume_avg_20),
            "feature_relative_volume_5d": _ratio(volume, volume_avg_5),
            "feature_volume_zscore_20d": cached_features["volume_zscore_20d"],
            "feature_vwap": vwap,
            "feature_price_vs_vwap": _pct(close, vwap),
            "feature_vwap_slope": cached_features["vwap_slope"],
            "feature_above_vwap": int(close > vwap) if vwap is not None else None,
            "feature_dollar_volume": (
                round(close * volume, 6) if volume is not None else None
            ),
            f"return_{horizon_days}d": return_fwd,
            label_name: label_from_forward_return(return_fwd),
            "leakage_guard": "v4_features_at_or_before_cutoff_executable_open_entry_s0_close_exit",
        }
        # The V3 allowlist is always materialized by the shared train/serve
        # engine so production cannot drift from these training definitions.
        row.update(shared_v3_features)
        rows.append(row)
        summary["rows_joined"] += 1
    if telemetry:
        telemetry.add(
            "observation_and_feature_construction",
            time.perf_counter() - observations_started,
        )
        telemetry.count("observations_processed", summary["events_scanned"])
        telemetry.count("observations_emitted", summary["rows_joined"])
    return rows, summary


def _json_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def _write_jsonl_records(path: Path, records: Iterable[dict[str, Any]]) -> str:
    payload = "".join(
        json.dumps(record, sort_keys=True, default=str, separators=(",", ":")) + "\n"
        for record in records
    )
    path.write_text(payload, encoding="utf-8")
    return hashlib.sha256(payload.encode()).hexdigest()


def _write_partitioned_jsonl_records(
    directory: Path,
    *,
    section: str,
    records: list[dict[str, Any]],
    max_uncompressed_bytes: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Write stable gzip partitions without truncating a single evidence record."""
    partitions: list[dict[str, Any]] = []
    payloads: list[bytes] = []
    current: list[bytes] = []
    current_bytes = 0
    for record in records:
        encoded = (
            json.dumps(record, sort_keys=True, default=str, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        if current and current_bytes + len(encoded) > max_uncompressed_bytes:
            payloads.append(b"".join(current))
            current, current_bytes = [], 0
        current.append(encoded)
        current_bytes += len(encoded)
    if current or not records:
        payloads.append(b"".join(current))

    record_offset = 0
    compressed_total = 0
    uncompressed_total = 0
    for index, payload in enumerate(payloads):
        name = f"{section}.part-{index:05d}.jsonl.gz"
        path = directory / name
        compressed = gzip.compress(payload, compresslevel=6, mtime=0)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(compressed)
        temporary.replace(path)
        count = payload.count(b"\n")
        entry = {
            "path": name,
            "sha256": hashlib.sha256(compressed).hexdigest(),
            "compressed_bytes": len(compressed),
            "uncompressed_bytes": len(payload),
            "records": count,
            "record_offset_start": record_offset,
            "record_offset_end_exclusive": record_offset + count,
            "compression": "gzip",
        }
        partitions.append(entry)
        record_offset += count
        compressed_total += len(compressed)
        uncompressed_total += len(payload)
    return partitions, {
        "records": len(records),
        "partitions": len(partitions),
        "compressed_bytes": compressed_total,
        "uncompressed_bytes": uncompressed_total,
    }


def _evidence_market_record(bar: dict[str, Any], *, basis: str) -> dict[str, Any]:
    values = {
        key: bar.get(key)
        for key in ("symbol", "date", "open", "high", "low", "close", "volume")
    }
    adjustment = {
        "basis": basis,
        "split_adjustment_factor": float(bar.get("_split_adjustment_factor", 1.0)),
        "applicable_split_ids": sorted(bar.get("_applicable_split_ids") or []),
    }
    source_row_id = "source_row_" + _json_sha256(
        {
            "source_object_id": bar.get("_source_object_id"),
            "source_raw_row_sha256": bar.get("_source_raw_row_sha256"),
            "values": values,
            "adjustment": adjustment,
        }
    )
    session = date.fromisoformat(str(bar["date"]))
    explicit = _parse_utc_datetime(bar.get("available_at"))
    availability = (
        {
            "kind": "EXPLICIT_PROVIDER_AVAILABILITY",
            "available_at": explicit.isoformat(),
        }
        if explicit
        else {
            "kind": "OFFICIAL_SESSION_CLOSE_POLICY",
            "policy_version": MARKET_AVAILABILITY_POLICY_VERSION,
            "calendar_contract_version": EXCHANGE_CALENDAR.identifier,
            "session_date": session.isoformat(),
        }
    )
    return {
        "source_row_id": source_row_id,
        "source_object_id": bar.get("_source_object_id"),
        "source_raw_row_sha256": bar.get("_source_raw_row_sha256"),
        **values,
        "adjustment": adjustment,
        "source_event_timestamp": EXCHANGE_CALENDAR.session_close(session).isoformat(),
        "source_event_timestamp_semantics": "official_regular_session_close",
        "availability_evidence": availability,
        "row_content_sha256": _json_sha256(
            {"values": values, "adjustment": adjustment}
        ),
    }


def emit_phase0_evidence_bundle(
    *,
    rows: list[dict[str, Any]],
    market: dict[str, list[dict[str, Any]]],
    split_events: list[dict[str, Any]],
    split_cache_path: Path,
    raw_root: Path,
    evidence_dir: Path,
    max_selected_rows: int,
    max_bundle_bytes: int,
    max_partition_bytes: int = 16_777_216,
    max_total_evidence_bytes: int = 4_294_967_296,
    telemetry: BuildTelemetry | None = None,
) -> dict[str, Any]:
    """Persist compact, deduplicated evidence used by the production V4 builder."""
    from moneybot.services.alpha_atlas_v4_canonical_observations import (
        CANONICAL_OBSERVATION_CONTRACT_VERSION,
        canonical_observation_id,
    )
    from moneybot.services.alpha_atlas_v4_phase0 import feature_registry

    evidence_dir.mkdir(parents=True, exist_ok=True)
    source_objects: dict[str, dict[str, Any]] = {}
    selected: dict[str, dict[str, Any]] = {}
    identities: dict[str, dict[str, Any]] = {}
    actions: dict[str, dict[str, Any]] = {}
    lineages: dict[str, dict[str, Any]] = {}
    splits_by_symbol = index_splits(split_events)
    date_indices = _market_date_index(market)
    positions = {
        symbol: {day: index for index, day in enumerate(days)}
        for symbol, days in date_indices.items()
    }
    split_cache_hash = _sha256_file(split_cache_path)
    adjusted_cache: dict[tuple[str, str, tuple[str, ...]], list[dict[str, Any]]] = {}
    window_cache: dict[tuple[str, int, tuple[str, ...]], list[str]] = {}
    market_record_cache: dict[tuple[str, float, tuple[str, ...]], dict[str, Any]] = {}
    canonical_lineage_refs: dict[str, dict[str, Any]] = {}
    registry_hash = feature_registry()["registry_sha256"]

    def adjusted(
        symbol: str,
        raw: list[dict[str, Any]],
        safe_splits: list[dict[str, Any]],
        asof_day: str,
    ) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
        split_ids = tuple(sorted(str(item.get("id") or "") for item in safe_splits))
        key = (symbol, asof_day, split_ids)
        history = adjusted_cache.get(key)
        if history is None:
            started = time.perf_counter()
            history = adjust_bars_to_asof(raw, safe_splits, asof_day)
            adjusted_cache[key] = history
            if telemetry:
                telemetry.add(
                    "lineage_split_adjusted_history_preparation",
                    time.perf_counter() - started,
                )
                telemetry.count("lineage_adjusted_history_cache_misses")
        elif telemetry:
            telemetry.count("lineage_adjusted_history_cache_hits")
        return history, split_ids

    def add_window(bars: list[dict[str, Any]]) -> list[str]:
        ids = []
        for bar in bars:
            object_id = str(bar.get("_source_object_id") or "")
            if not object_id:
                raise ValueError("market row lacks immutable source-object provenance")
            source_objects.setdefault(
                object_id,
                {
                    "source_object_id": object_id,
                    "provider": "Massive",
                    "dataset_family": "stocks_daily_aggregates",
                    "relative_source_path": bar["_source_relative_path"],
                    "sha256": bar["_source_object_sha256"],
                    "size_bytes": int(bar["_source_object_size"]),
                    "session_partition": str(bar["date"]),
                    "schema_type": "normalized_daily_aggregate",
                },
            )
            record_key = (
                str(bar.get("_source_raw_row_sha256") or ""),
                float(bar.get("_split_adjustment_factor", 1.0)),
                tuple(sorted(bar.get("_applicable_split_ids") or [])),
            )
            record = market_record_cache.get(record_key)
            if record is None:
                identity_started = time.perf_counter()
                record = _evidence_market_record(bar, basis="event_time_split_adjusted")
                market_record_cache[record_key] = record
                if telemetry:
                    telemetry.add(
                        "source_row_identity_generation",
                        time.perf_counter() - identity_started,
                    )
                    telemetry.count("selected_row_identities_computed")
            elif telemetry:
                telemetry.count("selected_row_identity_cache_hits")
            selected.setdefault(record["source_row_id"], record)
            ids.append(record["source_row_id"])
            if len(selected) > max_selected_rows:
                raise ValueError("PHASE0_EVIDENCE_BUNDLE_TOO_LARGE:selected_row_limit")
        return ids

    lineage_started = time.perf_counter()
    for row_number, row in enumerate(rows, 1):
        canonical_id = canonical_observation_id(row)
        reused = canonical_lineage_refs.get(canonical_id)
        if reused is not None:
            row["reconstruction_lineage"] = dict(reused)
            if telemetry:
                telemetry.count("duplicate_observation_lineage_reused")
            continue
        if telemetry and row_number % 5000 == 0:
            telemetry.progress(
                "lineage",
                observations_processed=row_number,
                selected_rows=len(selected),
            )
        symbol = str(row["symbol"])
        cutoff = datetime.fromisoformat(str(row["feature_cutoff_at"]))
        market_session = date.fromisoformat(str(row["market_session_date"]))
        feature_day = str(row["feature_market_asof_date"])
        symbol_raw = market[symbol]
        symbol_idx = positions[symbol][feature_day]
        symbol_history, symbol_split_ids = adjusted(
            symbol,
            symbol_raw,
            _feature_safe_splits(
                splits_by_symbol.get(symbol, []), cutoff, market_session
            ),
            feature_day,
        )
        spy_raw = market["SPY"]
        spy_idx = bisect.bisect_right(date_indices["SPY"], feature_day) - 1
        spy_history, spy_split_ids = adjusted(
            "SPY",
            spy_raw,
            _feature_safe_splits(
                splits_by_symbol.get("SPY", []), cutoff, market_session
            ),
            str(spy_raw[spy_idx]["date"]),
        )
        sector_symbol = str(row.get("sector_benchmark_symbol") or "SPY")
        sector_raw = market[sector_symbol]
        sector_idx = bisect.bisect_right(date_indices[sector_symbol], feature_day) - 1
        sector_history, sector_split_ids = adjusted(
            sector_symbol,
            sector_raw,
            _feature_safe_splits(
                splits_by_symbol.get(sector_symbol, []), cutoff, market_session
            ),
            str(sector_raw[sector_idx]["date"]),
        )
        entry_idx = positions[symbol][str(row["entry_session_date"])]
        exit_idx = positions[symbol][str(row["exit_session_date"])]

        def window_ids(
            key: tuple[str, int, tuple[str, ...]],
            history: list[dict[str, Any]],
            idx: int,
        ) -> list[str]:
            cached_ids = window_cache.get(key)
            if cached_ids is None:
                started = time.perf_counter()
                cached_ids = add_window(history[: idx + 1])
                window_cache[key] = cached_ids
                if telemetry:
                    telemetry.add(
                        "source_window_selection", time.perf_counter() - started
                    )
                    telemetry.count("source_window_cache_misses")
            elif telemetry:
                telemetry.count("source_window_cache_hits")
            return cached_ids

        symbol_ids = window_ids(
            (symbol, symbol_idx, symbol_split_ids), symbol_history, symbol_idx
        )
        spy_ids = window_ids(("SPY", spy_idx, spy_split_ids), spy_history, spy_idx)
        sector_ids = window_ids(
            (sector_symbol, sector_idx, sector_split_ids), sector_history, sector_idx
        )
        entry_id = add_window([symbol_raw[entry_idx]])[0]
        exit_id = add_window([symbol_raw[exit_idx]])[0]

        identity_started = time.perf_counter()
        identity_id = "identity_" + _json_sha256(
            {
                "ticker": symbol,
                "decision_at": row["decision_at"],
                "decision_id": row["decision_id"],
                "point_in_time_symbol_id": row["point_in_time_symbol_id"],
            }
        )
        identities.setdefault(
            identity_id,
            {
                "security_identity_evidence_id": identity_id,
                "evidence_class": "REQUEST_EVENT_IDENTITY",
                "ticker": symbol,
                "decision_at": row["decision_at"],
                "source": "immutable_decision_event",
                "source_record_sha256": _json_sha256(
                    {
                        "decision_id": row["decision_id"],
                        "ticker": symbol,
                        "decision_at": row["decision_at"],
                    }
                ),
                "point_in_time_symbol_id": row["point_in_time_symbol_id"],
                "historical_universe_certification_eligible": False,
            },
        )
        if telemetry:
            telemetry.add(
                "security_identity_evidence", time.perf_counter() - identity_started
            )
        relevant_ids = sorted(
            set(
                (row.get("feature_split_ids") or [])
                + (row.get("label_split_ids") or [])
            )
        )
        relevant = [
            item
            for item in splits_by_symbol.get(symbol, [])
            if str(item.get("id")) in relevant_ids
        ]
        action_started = time.perf_counter()
        action_id = "corporate_actions_" + _json_sha256(
            {
                "symbol": symbol,
                "source_sha256": split_cache_hash,
                "action_ids": relevant_ids,
            }
        )
        actions.setdefault(
            action_id,
            {
                "corporate_action_evidence_id": action_id,
                "symbol": symbol,
                "inspected_source_label": split_cache_path.name,
                "inspected_source_sha256": split_cache_hash,
                "disposition": (
                    "RELEVANT_ACTIONS_RECORDED"
                    if relevant
                    else "NO_RELEVANT_ACTION_IN_INSPECTED_SOURCE"
                ),
                "actions": relevant,
            },
        )
        if telemetry:
            telemetry.add(
                "corporate_action_evidence", time.perf_counter() - action_started
            )
        lineage_id = "lineage_" + _json_sha256(
            {
                "canonical_observation_id": canonical_id,
                "symbol_row_ids": symbol_ids,
                "entry_row_id": entry_id,
                "exit_row_id": exit_id,
            }
        )
        lineage_record = {
            "lineage_id": lineage_id,
            "canonical_observation_id": canonical_id,
            "security_identity_evidence_id": identity_id,
            "symbol": symbol,
            "symbol_row_ids": symbol_ids,
            "symbol_source_index": len(symbol_ids) - 1,
            "spy_row_ids": spy_ids,
            "spy_source_index": len(spy_ids) - 1,
            "sector_symbol": sector_symbol,
            "sector_row_ids": sector_ids,
            "sector_source_index": len(sector_ids) - 1,
            "entry_row_id": entry_id,
            "exit_5_row_id": (
                exit_id if int(row["label_horizon_sessions"]) == 5 else None
            ),
            "exit_10_row_id": (
                exit_id if int(row["label_horizon_sessions"]) == 10 else None
            ),
            "exit_row_id": exit_id,
            "corporate_action_evidence_id": action_id,
            "feature_contract_version": V4_FEATURE_CONTRACT_VERSION,
            "timing_contract_version": ALPHA_ATLAS_V4_TIMING_CONTRACT_VERSION,
            "canonicalization_contract_version": CANONICAL_OBSERVATION_CONTRACT_VERSION,
            "calendar_contract_version": EXCHANGE_CALENDAR.identifier,
            "calculation_engine_version": CALCULATION_ENGINE_VERSION,
            "feature_registry_sha256": registry_hash,
        }
        lineages.setdefault(lineage_id, lineage_record)
        row["reconstruction_lineage"] = {
            "schema_version": RECONSTRUCTION_LINEAGE_VERSION,
            "evidence_manifest_path": "source_evidence_manifest.json",
            "lineage_id": lineage_id,
        }
        canonical_lineage_refs[canonical_id] = dict(row["reconstruction_lineage"])

    if telemetry:
        telemetry.add(
            "lineage_accumulation_and_deduplication",
            time.perf_counter() - lineage_started,
        )
        telemetry.count("selected_source_rows", len(selected))
        telemetry.count("canonical_lineages", len(lineages))

    sections = {
        "source_objects": sorted(
            source_objects.values(), key=lambda item: item["source_object_id"]
        ),
        "selected_market_rows": sorted(
            selected.values(), key=lambda item: item["source_row_id"]
        ),
        "corporate_action_evidence": sorted(
            actions.values(), key=lambda item: item["corporate_action_evidence_id"]
        ),
        "security_identity_evidence": sorted(
            identities.values(), key=lambda item: item["security_identity_evidence_id"]
        ),
        "observation_lineage": sorted(
            lineages.values(), key=lambda item: item["lineage_id"]
        ),
    }
    serialization_started = time.perf_counter()
    section_index = {}
    section_sizes = {}
    for name, records in sections.items():
        partitions, sizes = _write_partitioned_jsonl_records(
            evidence_dir,
            section=name,
            records=records,
            max_uncompressed_bytes=max(1, max_partition_bytes),
        )
        section_index[name] = partitions
        section_sizes[name] = sizes
    manifest = {
        "schema_version": RECONSTRUCTION_LINEAGE_VERSION,
        "availability_policy_version": MARKET_AVAILABILITY_POLICY_VERSION,
        "feature_contract_version": V4_FEATURE_CONTRACT_VERSION,
        "timing_contract_version": ALPHA_ATLAS_V4_TIMING_CONTRACT_VERSION,
        "canonicalization_contract_version": CANONICAL_OBSERVATION_CONTRACT_VERSION,
        "calendar_contract_version": EXCHANGE_CALENDAR.identifier,
        "calculation_engine_version": CALCULATION_ENGINE_VERSION,
        "configuration": {
            "label_horizon_sessions": sorted(
                {int(row["label_horizon_sessions"]) for row in rows}
            ),
            "price_adjustment_policy": "event_time_split_adjusted",
            "volume_adjustment_policy": "inverse_split_factor",
            "primary_manifest_byte_limit": max_bundle_bytes,
            "partition_uncompressed_byte_limit": max(1, max_partition_bytes),
            "total_compressed_evidence_byte_limit": max_total_evidence_bytes,
        },
        "source_root_relative_path": os.path.relpath(
            raw_root.resolve(), evidence_dir.resolve()
        ),
        "corporate_action_source_relative_path": os.path.relpath(
            split_cache_path.resolve(), evidence_dir.resolve()
        ),
        "corporate_action_source_sha256": split_cache_hash,
        "partition_contract_version": "alpha-atlas-v4-evidence-partitions.v1",
        "partition_max_uncompressed_bytes": max(1, max_partition_bytes),
        "sections": section_index,
        "metrics": {
            "unique_source_objects": len(source_objects),
            "selected_source_rows": len(selected),
            "raw_request_observations": len(rows),
            "canonical_economic_observations": len(lineages),
            "partition_count": sum(len(items) for items in section_index.values()),
            "compressed_evidence_bytes": sum(
                item["compressed_bytes"] for item in section_sizes.values()
            ),
            "uncompressed_evidence_bytes": sum(
                item["uncompressed_bytes"] for item in section_sizes.values()
            ),
        },
        "lineage_summary": {
            "all_canonical_lineages_indexed": len(lineages),
            "all_selected_rows_indexed": len(selected),
            "source_object_count": len(source_objects),
            "security_identity_count": len(identities),
            "corporate_action_evidence_count": len(actions),
            "evidence_truncated": False,
        },
    }
    manifest["metrics"]["evidence_bundle_bytes"] = manifest["metrics"][
        "compressed_evidence_bytes"
    ]
    manifest["metrics"]["average_bytes_per_observation"] = round(
        manifest["metrics"]["compressed_evidence_bytes"] / max(1, len(rows)), 3
    )
    manifest_path = evidence_dir / "source_evidence_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    primary_manifest_bytes = manifest_path.stat().st_size
    total_evidence_bytes = (
        primary_manifest_bytes + manifest["metrics"]["compressed_evidence_bytes"]
    )
    diagnostics = {
        "schema_version": "alpha-atlas-v4-evidence-bundle-diagnostics.v1",
        "configured_primary_manifest_byte_limit": max_bundle_bytes,
        "configured_partition_uncompressed_byte_limit": max(1, max_partition_bytes),
        "configured_total_evidence_byte_limit": max_total_evidence_bytes,
        "primary_manifest_bytes": primary_manifest_bytes,
        "total_serialized_evidence_bytes": total_evidence_bytes,
        "selected_row_count": len(selected),
        "section_sizes": section_sizes,
        "largest_sections": sorted(
            ({"section": name, **sizes} for name, sizes in section_sizes.items()),
            key=lambda item: (-item["compressed_bytes"], item["section"]),
        ),
    }
    diagnostics_path = evidence_dir / "evidence_bundle_diagnostics.json"
    diagnostics_path.write_text(
        json.dumps(diagnostics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if telemetry:
        telemetry.progress(
            "evidence_serialized",
            partitions=manifest["metrics"]["partition_count"],
            primary_manifest_bytes=primary_manifest_bytes,
            selected_rows=len(selected),
            total_evidence_bytes=total_evidence_bytes,
        )
    if primary_manifest_bytes > max_bundle_bytes:
        raise ValueError("PHASE0_EVIDENCE_PRIMARY_MANIFEST_TOO_LARGE:byte_limit")
    if total_evidence_bytes > max_total_evidence_bytes:
        raise ValueError("PHASE0_EVIDENCE_BUNDLE_TOO_LARGE:total_byte_limit")
    final_hash = _sha256_file(manifest_path)
    for row in rows:
        row["reconstruction_lineage"]["source_evidence_manifest_sha256"] = final_hash
    manifest["manifest_sha256"] = final_hash
    if telemetry:
        telemetry.add(
            "evidence_serialization", time.perf_counter() - serialization_started
        )
        telemetry.progress(
            "lineage_complete", selected_rows=len(selected), lineages=len(lineages)
        )
    return manifest


def write_rows(
    path: Path,
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    *,
    raw_root: Path,
    decision_log: Path,
    horizon_days: int,
    split_metadata_hash: str,
    output_path_label: Path | None = None,
    telemetry: BuildTelemetry | None = None,
) -> dict[str, Any]:
    hashing_started = time.perf_counter()
    path.parent.mkdir(parents=True, exist_ok=True)
    dataset_digest = hashlib.sha256()
    for row in rows:
        dataset_digest.update(
            (
                json.dumps(row, sort_keys=True, default=str, separators=(",", ":"))
                + "\n"
            ).encode("utf-8")
        )
    dataset_manifest_hash = dataset_digest.hexdigest()
    if telemetry:
        telemetry.add("manifest_hashing", time.perf_counter() - hashing_started)
    serialization_started = time.perf_counter()
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            if (
                row.get("timing_contract_version")
                == ALPHA_ATLAS_V4_TIMING_CONTRACT_VERSION
            ):
                row["dataset_manifest_hash"] = dataset_manifest_hash
            fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    if telemetry:
        telemetry.add(
            "raw_observation_serialization", time.perf_counter() - serialization_started
        )
    finalization_started = time.perf_counter()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "raw_market_root": str(raw_root),
        "decision_log": str(decision_log),
        "output_path": str(output_path_label or path),
        "horizon_days": horizon_days,
        "temporal_safety": {
            "schema_version": "alpha-atlas-v4-temporal-safety-certification.v1",
            "status": "NOT_EVALUATED",
            "reason": "builder output has not yet been independently reconstructed and hash-certified",
            "legacy_leakage_safe_accepted": False,
        },
        "corporate_action_normalization_required": True,
        "corporate_action_normalization_passed": True,
        "price_adjustment_policy": "event_time_split_adjusted",
        "volume_adjustment_policy": "inverse_split_factor",
        "split_source": "massive",
        "split_metadata_hash": split_metadata_hash,
        "dataset_manifest_hash": dataset_manifest_hash,
        "corporate_action_schema_version": CORPORATE_ACTION_SCHEMA_VERSION,
        "decision_target": (
            target_metadata()
            if horizon_days == target_metadata()["horizon_days"]
            else {
                "target_name": f"label_up_{horizon_days}d",
                "forecast_horizon": f"{horizon_days}d",
                "positive_class_semantics": "strictly positive forward return",
            }
        ),
        "timing_contract_version": ALPHA_ATLAS_V4_TIMING_CONTRACT_VERSION,
        "model_feature_contract_version": V4_FEATURE_CONTRACT_VERSION,
        "exchange_calendar": EXCHANGE_CALENDAR.identifier,
        "staleness_tolerance_sessions": DEFAULT_MAX_STALENESS_SESSIONS,
        "join_policy": "point-in-time completed daily bars; executable open entry; S0-based official close exit",
        **{key: value for key, value in summary.items() if key != "split_provenance"},
    }
    manifest_path = path.with_suffix(path.suffix + ".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    if telemetry:
        telemetry.add(
            "manifest_finalization", time.perf_counter() - finalization_started
        )
    return manifest


def _distribution(rows: list[dict[str, Any]], fields: list[str]) -> dict[str, Any]:
    report = {}
    for field in fields:
        values = sorted(
            float(row[field])
            for row in rows
            if _coerce_float(row.get(field)) is not None
        )
        if not values:
            report[field] = {}
            continue

        def percentile(q: float) -> float:
            return values[min(len(values) - 1, int((len(values) - 1) * q))]

        report[field] = {
            "count": len(values),
            "min": values[0],
            "max": values[-1],
            "mean": sum(values) / len(values),
            "median": percentile(0.5),
            "p99": percentile(0.99),
            "p99.5": percentile(0.995),
            "p99.9": percentile(0.999),
        }
    return report


def _suspicious_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    thresholds = {
        "feature_return_1d_lagged": 1.0,
        "feature_return_5d_lagged": 3.0,
        "feature_return_20d_lagged": 5.0,
    }
    output = []
    for row in rows:
        for field, threshold in thresholds.items():
            value = _coerce_float(row.get(field))
            if value is not None and abs(value) > threshold:
                output.append(
                    {
                        "symbol": row.get("symbol"),
                        "date": row.get("event_date"),
                        "field": field,
                        "adjusted_return": value,
                        "known_split_in_window": bool(row.get("feature_split_ids")),
                        "split_ids": row.get("feature_split_ids") or [],
                    }
                )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Join immutable Massive raw market files with MoneyBot decision logs into leakage-safe training rows."
    )
    parser.add_argument("--raw-root", default="data/raw/massive_flatfiles")
    parser.add_argument("--decision-log", default="data/decision_events.jsonl")
    parser.add_argument("--output", default="data/decision_training_snapshot.jsonl")
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--horizon-days", type=int, default=5)
    parser.add_argument(
        "--split-cache", default="data/track_b/corporate_actions/massive_splits.jsonl"
    )
    parser.add_argument("--phase0-evidence-dir")
    parser.add_argument("--phase0-max-selected-rows", type=int, default=2_000_000)
    parser.add_argument("--phase0-max-evidence-bytes", type=int, default=1_048_576)
    parser.add_argument("--phase0-max-partition-bytes", type=int, default=16_777_216)
    parser.add_argument(
        "--phase0-max-total-evidence-bytes", type=int, default=4_294_967_296
    )
    parser.add_argument("--performance-output")
    args = parser.parse_args()
    telemetry = BuildTelemetry(
        Path(args.performance_output) if args.performance_output else None
    )
    output_path = Path(args.output)
    output_path.unlink(missing_ok=True)
    output_path.with_suffix(output_path.suffix + ".manifest.json").unlink(
        missing_ok=True
    )
    if args.phase0_evidence_dir:
        final_evidence = Path(args.phase0_evidence_dir)
        if final_evidence.exists():
            shutil.rmtree(final_evidence)
    decision_log = Path(args.decision_log)
    started = time.perf_counter()
    events = read_decision_events(decision_log, limit=max(1, args.limit))
    telemetry.add("decision_log_loading", time.perf_counter() - started)
    telemetry.count("decision_events_loaded", len(events))
    telemetry.progress("decision_log_loaded", decision_events=len(events))
    raw_root = Path(args.raw_root)
    horizon_days = max(1, args.horizon_days)
    symbols, start_date, end_date = _market_load_window(
        events, horizon_days=horizon_days
    )
    market = load_market_history(
        raw_root,
        symbols=symbols,
        start_date=start_date,
        end_date=end_date,
        telemetry=telemetry,
    )
    actions_started = time.perf_counter()
    split_events = load_split_cache(Path(args.split_cache))
    split_manifest_path = (
        Path(args.split_cache).parent / "split_adjustment_manifest.json"
    )
    if not split_manifest_path.is_file():
        raise SystemExit("Canonical split metadata manifest is required")
    try:
        split_manifest = json.loads(split_manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit("Canonical split metadata manifest is corrupt") from exc
    if (
        split_manifest.get("schema_version") != CORPORATE_ACTION_SCHEMA_VERSION
        or split_manifest.get("source_hash") != split_source_hash(split_events)
        or split_manifest.get("corporate_action_normalization_passed") is not True
    ):
        raise SystemExit(
            "Canonical split metadata cache does not match its manifest/hash"
        )
    telemetry.add(
        "corporate_action_loading_and_indexing", time.perf_counter() - actions_started
    )
    telemetry.count("corporate_actions_loaded", len(split_events))
    rows, summary = build_training_rows_from_raw_market(
        events,
        market,
        horizon_days=horizon_days,
        split_events=split_events,
        telemetry=telemetry,
    )
    summary.update(
        {
            "market_symbols_requested": len(symbols),
            "market_symbols_loaded": len(market),
            "market_start_date": start_date,
            "market_end_date": end_date,
        }
    )
    metadata_hash = split_source_hash(split_events)
    output_temp = output_path.with_suffix(output_path.suffix + ".tmp")
    output_temp_manifest = output_temp.with_suffix(
        output_temp.suffix + ".manifest.json"
    )
    for stale in (output_temp, output_temp_manifest):
        stale.unlink(missing_ok=True)
    if args.phase0_evidence_dir:
        evidence_dir = Path(args.phase0_evidence_dir)
        evidence_staging = evidence_dir.with_name(evidence_dir.name + ".tmp")
        if evidence_staging.exists():
            shutil.rmtree(evidence_staging)
        evidence_manifest = emit_phase0_evidence_bundle(
            rows=rows,
            market=market,
            split_events=split_events,
            split_cache_path=Path(args.split_cache),
            raw_root=raw_root,
            evidence_dir=evidence_staging,
            max_selected_rows=max(1, args.phase0_max_selected_rows),
            max_bundle_bytes=max(1, args.phase0_max_evidence_bytes),
            max_partition_bytes=max(1, args.phase0_max_partition_bytes),
            max_total_evidence_bytes=max(1, args.phase0_max_total_evidence_bytes),
            telemetry=telemetry,
        )
        summary["phase0_evidence"] = evidence_manifest["metrics"]
        summary["reconstruction_lineage_contract_version"] = (
            RECONSTRUCTION_LINEAGE_VERSION
        )
        if evidence_dir.exists():
            shutil.rmtree(evidence_dir)
        evidence_staging.replace(evidence_dir)
    manifest = write_rows(
        output_temp,
        rows,
        summary,
        raw_root=raw_root,
        decision_log=decision_log,
        horizon_days=horizon_days,
        split_metadata_hash=metadata_hash,
        output_path_label=output_path,
        telemetry=telemetry,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_temp.replace(output_path)
    output_temp_manifest.replace(
        output_path.with_suffix(output_path.suffix + ".manifest.json")
    )
    quality_dir = output_path.parent / "training_quality"
    quality_dir.mkdir(parents=True, exist_ok=True)
    provenance = summary.get("split_provenance", [])
    audited_events = []
    for event in split_events:
        split_id = str(event.get("id") or "")
        audited_events.append(
            {
                **event,
                "affected_feature_rows": sum(
                    split_id in item.get("applicable_split_ids", [])
                    for item in provenance
                ),
                "affected_label_rows": sum(
                    split_id in item.get("label_split_ids", []) for item in provenance
                ),
            }
        )
    audit = {
        "schema_version": CORPORATE_ACTION_SCHEMA_VERSION,
        "split_metadata_hash": metadata_hash,
        "split_events_loaded": len(split_events),
        "symbols_with_splits": len({item["ticker"] for item in split_events}),
        **{
            key: summary.get(key, 0)
            for key in (
                "training_rows_affected",
                "feature_windows_crossing_splits",
                "label_windows_crossing_splits",
                "bars_adjusted",
                "price_values_adjusted",
                "volume_values_adjusted",
                "affected_feature_rows",
                "affected_label_rows",
            )
        },
        "events": audited_events,
        "split_provenance": provenance,
    }
    (quality_dir / "corporate_action_audit.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    fields = [
        "feature_return_1d_lagged",
        "feature_return_5d_lagged",
        "feature_return_20d_lagged",
        f"return_{horizon_days}d",
        "feature_volatility_20d",
        "feature_price_vs_sma_20",
        "feature_macd_hist",
    ]
    split_symbols = {str(item.get("ticker") or "").upper() for item in split_events}
    affected_events = [
        event
        for event in events
        if str(event.get("symbol") or "").upper() in split_symbols
    ]
    if affected_events:
        raw_rows, _ = build_training_rows_from_raw_market(
            affected_events, market, horizon_days=horizon_days, split_events=[]
        )
        adjusted_audit_rows = [
            row for row in rows if row.get("symbol") in split_symbols
        ]
    else:
        raw_rows = rows
        adjusted_audit_rows = rows
    suspicious_before = _suspicious_rows(raw_rows)
    suspicious_after = _suspicious_rows(adjusted_audit_rows)
    before_after = {
        "schema_version": CORPORATE_ACTION_SCHEMA_VERSION,
        "split_metadata_hash": metadata_hash,
        "before": _distribution(raw_rows, fields),
        "after": _distribution(adjusted_audit_rows, fields),
        "suspicious_rows_before": len(suspicious_before),
        "suspicious_rows_after": len(suspicious_after),
        "suspicious_before": suspicious_before,
        "suspicious_after": suspicious_after,
    }
    (quality_dir / "split_adjustment_before_after_report.json").write_text(
        json.dumps(before_after, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    telemetry.progress(
        "complete",
        observations=len(rows),
        selected_rows=(summary.get("phase0_evidence") or {}).get(
            "selected_source_rows", 0
        ),
    )
    telemetry.flush(status="COMPLETED", phase="complete")
    print(
        json.dumps(
            {
                "builder_performance": {
                    "stage_seconds": telemetry.stages,
                    "counters": telemetry.counters,
                }
            },
            sort_keys=True,
        ),
        file=sys.stderr,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
