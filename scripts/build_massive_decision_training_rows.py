#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import json
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from moneybot.services.decision_log import read_decision_events
from moneybot.services.outcome_tracking import normalize_action, normalize_unix_ts

SCHEMA_VERSION = "massive-decision-training-rows.v1"


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
    symbol = str(row.get("ticker") or row.get("symbol") or row.get("T") or "").strip().upper()
    day = _market_date(row.get("date") or row.get("day") or row.get("window_start") or row.get("timestamp") or row.get("t"))
    close = _coerce_float(row.get("close") or row.get("c") or row.get("Close"))
    if not symbol or not day or close is None:
        return None
    return {
        "symbol": symbol,
        "date": day,
        "open": _coerce_float(row.get("open") or row.get("o") or row.get("Open")),
        "high": _coerce_float(row.get("high") or row.get("h") or row.get("High")),
        "low": _coerce_float(row.get("low") or row.get("l") or row.get("Low")),
        "close": close,
        "volume": _coerce_float(row.get("volume") or row.get("v") or row.get("Volume")),
    }


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
) -> dict[str, list[dict[str, Any]]]:
    wanted = {str(symbol).strip().upper() for symbol in symbols or set() if str(symbol).strip()}
    by_symbol: dict[str, dict[str, dict[str, Any]]] = {}
    for path in sorted(raw_root.rglob("*")):
        if not path.is_file() or path.name.startswith("_"):
            continue
        if not (path.name.endswith(".csv") or path.name.endswith(".csv.gz") or path.name.endswith(".jsonl") or path.name.endswith(".jsonl.gz")):
            continue
        for row in _read_market_file(path):
            symbol = str(row["symbol"]).upper()
            day = str(row["date"])
            if wanted and symbol not in wanted:
                continue
            if start_date and day < start_date:
                continue
            if end_date and day > end_date:
                continue
            by_symbol.setdefault(symbol, {})[day] = row
    return {symbol: [rows[day] for day in sorted(rows)] for symbol, rows in by_symbol.items()}


def _market_load_window(events: list[dict[str, Any]], *, horizon_days: int, history_lag_days: int = 70) -> tuple[set[str], str | None, str | None]:
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
    if not event_days:
        return symbols, None, None
    start = min(event_days) - timedelta(days=max(0, history_lag_days))
    end = max(event_days) + timedelta(days=max(1, horizon_days) + 3)
    return symbols, start.isoformat(), end.isoformat()


def _event_day(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()


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


def _rolling_close_mean(rows: list[dict[str, Any]], idx: int, window: int) -> float | None:
    if idx + 1 < window:
        return None
    closes = [_coerce_float(row.get("close")) for row in rows[idx - window + 1 : idx + 1]]
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


def _macd_components_at(rows: list[dict[str, Any]], idx: int) -> tuple[float | None, float | None, float | None]:
    macd_line = _macd_line_at(rows, idx)
    macd_values = [_macd_line_at(rows, pos) for pos in range(idx + 1)]
    clean = [float(value) for value in macd_values if value is not None]
    signal = _ema_values(clean, 9)
    hist = (macd_line - signal) if macd_line is not None and signal is not None else None
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
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    atr = _mean(true_ranges)
    return round(atr, 6) if atr is not None else None


def _lagged_return(rows: list[dict[str, Any]] | None, idx: int | None, days: int) -> float | None:
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
    if benchmark_rows is None or benchmark_idx is None or symbol_idx < window or benchmark_idx < window:
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
    benchmark_variance = sum((ret - benchmark_mean) ** 2 for ret in benchmark_returns) / window
    if benchmark_variance == 0.0:
        return None
    covariance = (
        sum(
            (symbol_return - symbol_mean) * (benchmark_return - benchmark_mean)
            for symbol_return, benchmark_return in zip(symbol_returns, benchmark_returns)
        )
        / window
    )
    return round(covariance / benchmark_variance, 6)


def _rolling_vwap(rows: list[dict[str, Any]], idx: int, window: int = 20) -> float | None:
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


def _vwap_slope(rows: list[dict[str, Any]], idx: int, window: int = 10, vwap_window: int = 20) -> float | None:
    if idx + 1 < window + vwap_window - 1:
        return None
    values = [_rolling_vwap(rows, pos, vwap_window) for pos in range(idx - window + 1, idx + 1)]
    if any(value is None for value in values):
        return None
    y = [float(value) for value in values]
    x_mean = (window - 1) / 2.0
    y_mean = sum(y) / window
    denom = sum((pos - x_mean) ** 2 for pos in range(window))
    if denom == 0.0 or y[0] == 0.0:
        return None
    slope = sum((pos - x_mean) * (value - y_mean) for pos, value in enumerate(y)) / denom
    return round(slope / y[0], 6)


def _rolling_numeric_mean(rows: list[dict[str, Any]], idx: int, window: int, column: str) -> float | None:
    if idx + 1 < window:
        return None
    values = [_coerce_float(row.get(column)) for row in rows[idx - window + 1 : idx + 1]]
    if any(value is None for value in values):
        return None
    return round(sum(float(value) for value in values) / window, 6)


def _rolling_zscore(rows: list[dict[str, Any]], idx: int, window: int, column: str) -> float | None:
    current = _coerce_float(rows[idx].get(column)) if idx < len(rows) else None
    if current is None or idx + 1 < window:
        return None
    values = [_coerce_float(row.get(column)) for row in rows[idx - window + 1 : idx + 1]]
    if any(value is None for value in values):
        return None
    clean = [float(value) for value in values]
    avg = sum(clean) / window
    variance = sum((value - avg) ** 2 for value in clean) / window
    std = variance ** 0.5
    if std == 0.0:
        return 0.0
    return round((float(current) - avg) / std, 6)


def _rolling_extreme(rows: list[dict[str, Any]], idx: int, window: int, column: str, *, high: bool) -> float | None:
    if idx + 1 < window:
        return None
    values = [_coerce_float(row.get(column)) for row in rows[idx - window + 1 : idx + 1]]
    if any(value is None for value in values):
        return None
    return round(max(values) if high else min(values), 6)


def _return_volatility(rows: list[dict[str, Any]], idx: int, window: int) -> float | None:
    if idx < window:
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
    return round(variance ** 0.5, 6)


def _trend_slope(rows: list[dict[str, Any]], idx: int, window: int) -> float | None:
    if idx + 1 < window:
        return None
    closes = [_coerce_float(row.get("close")) for row in rows[idx - window + 1 : idx + 1]]
    if any(value is None for value in closes):
        return None
    y = [float(value) for value in closes]
    x_mean = (window - 1) / 2.0
    y_mean = sum(y) / window
    denom = sum((pos - x_mean) ** 2 for pos in range(window))
    if denom == 0.0 or y[0] == 0.0:
        return None
    slope = sum((pos - x_mean) * (value - y_mean) for pos, value in enumerate(y)) / denom
    return round(slope / y[0], 6)


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return round(float(numerator) / float(denominator), 6)


def _pct(newer: float, older: float | None) -> float | None:
    if older in {None, 0}:
        return None
    return round((newer / float(older)) - 1.0, 6)


def build_training_rows_from_raw_market(events: list[dict[str, Any]], market: dict[str, list[dict[str, Any]]], *, horizon_days: int = 5) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    summary = {"events_scanned": 0, "rows_joined": 0, "missing_symbol_history": 0, "insufficient_history": 0, "insufficient_forward_window": 0}
    for event in events:
        summary["events_scanned"] += 1
        symbol = str(event.get("symbol") or "").strip().upper()
        ts = normalize_unix_ts(event.get("ts"))
        if not symbol or ts is None or symbol not in market:
            summary["missing_symbol_history"] += 1
            continue
        event_day = _event_day(ts)
        history = market[symbol]
        spy_history = market.get("SPY")
        spy_idx = _row_before_or_on(spy_history, event_day) if spy_history else None
        idx = _row_before_or_on(history, event_day)
        if idx is None or idx < 5:
            summary["insufficient_history"] += 1
            continue
        label_idx = idx + max(1, horizon_days)
        if label_idx >= len(history):
            summary["insufficient_forward_window"] += 1
            continue

        asof = history[idx]
        prev1 = history[idx - 1]
        prev5 = history[idx - 5]
        prev10 = history[idx - 10] if idx >= 10 else {}
        prev20 = history[idx - 20] if idx >= 20 else {}
        future = history[label_idx]
        close = float(asof["close"])
        return_fwd = _pct(float(future["close"]), close)
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        snapshot = event.get("snapshot") if isinstance(event.get("snapshot"), dict) else {}
        sma_10 = _rolling_close_mean(history, idx, 10)
        sma_20 = _rolling_close_mean(history, idx, 20)
        sma_50 = _rolling_close_mean(history, idx, 50)
        high_20 = _rolling_extreme(history, idx, 20, "high", high=True)
        low_20 = _rolling_extreme(history, idx, 20, "low", high=False)
        macd_line, macd_signal, macd_hist = _macd_components_at(history, idx)
        volume = _coerce_float(asof.get("volume"))
        volume_avg_5 = _rolling_numeric_mean(history, idx, 5, "volume")
        volume_avg_20 = _rolling_numeric_mean(history, idx, 20, "volume")
        vwap = _rolling_vwap(history, idx, 20)
        open_price = _coerce_float(asof.get("open"))
        return_5d_lagged = _pct(close, prev5.get("close"))
        return_20d_lagged = _pct(close, prev20.get("close"))
        spy_return_5d = _lagged_return(spy_history, spy_idx, 5)
        row = {
            "ts": ts,
            "event_date": event_day,
            "market_asof_date": asof["date"],
            "label_asof_date": future["date"],
            "symbol": symbol,
            "endpoint": str(event.get("endpoint") or "unknown"),
            "decision_source": str(event.get("decision_source") or "unknown"),
            "recommendation": normalize_action(event),
            "probability_up": snapshot.get("probability_up", payload.get("probability_up")),
            "model_version": snapshot.get("model_version", payload.get("model_version")),
            "feature_close": close,
            "feature_sma_10": sma_10,
            "feature_sma_20": sma_20,
            "feature_sma_50": sma_50,
            "feature_sma_10_over_20": _ratio(sma_10, sma_20),
            "feature_sma_20_over_50": _ratio(sma_20, sma_50),
            "feature_trend_slope_10d": _trend_slope(history, idx, 10),
            "feature_trend_slope_20d": _trend_slope(history, idx, 20),
            "feature_volatility_5d": _return_volatility(history, idx, 5),
            "feature_volatility_20d": _return_volatility(history, idx, 20),
            "feature_drawdown_from_20d_high": _pct(close, high_20),
            "feature_distance_from_20d_low": _pct(close, low_20),
            "feature_gap_percent": _pct(open_price, prev1.get("close")),
            "feature_ema_10": _ema_at(history, idx, 10),
            "feature_ema_20": _ema_at(history, idx, 20),
            "feature_price_vs_sma_20": _pct(close, sma_20),
            "feature_price_vs_sma_50": _pct(close, sma_50),
            "feature_rsi_14": _rsi_at(history, idx, 14),
            "feature_macd": macd_line,
            "feature_macd_signal": macd_signal,
            "feature_macd_hist": macd_hist,
            "feature_atr_14": _atr_at(history, idx, 14),
            "feature_spy_return_1d": _lagged_return(spy_history, spy_idx, 1),
            "feature_spy_return_5d": spy_return_5d,
            "feature_symbol_minus_spy_5d": (
                round(return_5d_lagged - spy_return_5d, 6)
                if return_5d_lagged is not None and spy_return_5d is not None
                else None
            ),
            "feature_symbol_beta_20d": _beta_to_benchmark(history, spy_history, idx, spy_idx, 20),
            "feature_return_1d_lagged": _pct(close, prev1.get("close")),
            "feature_return_5d_lagged": return_5d_lagged,
            "feature_return_10d_lagged": _pct(close, prev10.get("close")),
            "feature_return_20d_lagged": return_20d_lagged,
            "feature_momentum_5d_vs_20d": (
                round(return_5d_lagged - return_20d_lagged, 6)
                if return_5d_lagged is not None and return_20d_lagged is not None
                else None
            ),
            "feature_volume": asof.get("volume"),
            "feature_volume_ratio_20d": _ratio(volume, volume_avg_20),
            "feature_relative_volume_5d": _ratio(volume, volume_avg_5),
            "feature_volume_zscore_20d": _rolling_zscore(history, idx, 20, "volume"),
            "feature_vwap": vwap,
            "feature_price_vs_vwap": _pct(close, vwap),
            "feature_vwap_slope": _vwap_slope(history, idx, 10, 20),
            "feature_above_vwap": int(close > vwap) if vwap is not None else None,
            "feature_dollar_volume": round(close * volume, 6) if volume is not None else None,
            f"return_{horizon_days}d": return_fwd,
            f"label_up_{horizon_days}d": int(return_fwd is not None and return_fwd > 0.0),
            "leakage_guard": "features_asof_market_close_on_or_before_decision_date_labels_after_decision_date",
        }
        rows.append(row)
        summary["rows_joined"] += 1
    return rows, summary


def write_rows(path: Path, rows: list[dict[str, Any]], summary: dict[str, int], *, raw_root: Path, decision_log: Path, horizon_days: int) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "raw_market_root": str(raw_root),
        "decision_log": str(decision_log),
        "output_path": str(path),
        "horizon_days": horizon_days,
        "leakage_safe": True,
        "join_policy": "last_market_row_on_or_before_decision_date; labels strictly after that row",
        **summary,
    }
    manifest_path = path.with_suffix(path.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Join immutable Massive raw market files with MoneyBot decision logs into leakage-safe training rows.")
    parser.add_argument("--raw-root", default="data/raw/massive_flatfiles")
    parser.add_argument("--decision-log", default="data/decision_events.jsonl")
    parser.add_argument("--output", default="data/decision_training_snapshot.jsonl")
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--horizon-days", type=int, default=5)
    args = parser.parse_args()
    decision_log = Path(args.decision_log)
    events = read_decision_events(decision_log, limit=max(1, args.limit))
    raw_root = Path(args.raw_root)
    horizon_days = max(1, args.horizon_days)
    symbols, start_date, end_date = _market_load_window(events, horizon_days=horizon_days)
    market = load_market_history(raw_root, symbols=symbols, start_date=start_date, end_date=end_date)
    rows, summary = build_training_rows_from_raw_market(events, market, horizon_days=horizon_days)
    summary.update(
        {
            "market_symbols_requested": len(symbols),
            "market_symbols_loaded": len(market),
            "market_start_date": start_date,
            "market_end_date": end_date,
        }
    )
    manifest = write_rows(Path(args.output), rows, summary, raw_root=raw_root, decision_log=decision_log, horizon_days=horizon_days)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
