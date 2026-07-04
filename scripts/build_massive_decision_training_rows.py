#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import json
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from moneybot.services.decision_log import read_decision_events
from moneybot.services.outcome_tracking import normalize_action, normalize_unix_ts

SCHEMA_VERSION = "massive-decision-training-rows.v1"
MARKET_TIMEZONE = ZoneInfo("America/New_York")
REGULAR_MARKET_CLOSE = time(16, 0)


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


def load_market_history(raw_root: Path) -> dict[str, list[dict[str, Any]]]:
    by_symbol: dict[str, dict[str, dict[str, Any]]] = {}
    for path in sorted(raw_root.rglob("*")):
        if not path.is_file() or path.name.startswith("_"):
            continue
        if not (path.name.endswith(".csv") or path.name.endswith(".csv.gz") or path.name.endswith(".jsonl") or path.name.endswith(".jsonl.gz")):
            continue
        for row in _read_market_file(path):
            by_symbol.setdefault(row["symbol"], {})[row["date"]] = row
    return {symbol: [rows[day] for day in sorted(rows)] for symbol, rows in by_symbol.items()}


def _event_day(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()


def _market_close_ts(day: str) -> int:
    close_dt = datetime.combine(datetime.fromisoformat(day).date(), REGULAR_MARKET_CLOSE, tzinfo=MARKET_TIMEZONE)
    return int(close_dt.astimezone(timezone.utc).timestamp())


def _row_before_or_on(rows: list[dict[str, Any]], day: str) -> int | None:
    idx = None
    for pos, row in enumerate(rows):
        if row["date"] <= day:
            idx = pos
        else:
            break
    return idx


def _row_completed_for_decision(rows: list[dict[str, Any]], ts: int) -> int | None:
    """Return the latest daily bar completed before the decision timestamp.

    Massive daily aggregate rows contain the final close/volume for a market
    date. For intraday decisions, the same-date aggregate is not known yet, so
    it must be excluded until the regular market close for that date has passed.
    """
    day = _event_day(ts)
    idx = _row_before_or_on(rows, day)
    if idx is None:
        return None
    if rows[idx]["date"] == day and ts < _market_close_ts(day):
        idx -= 1
    return idx if idx >= 0 else None


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
        feature_idx = _row_completed_for_decision(history, ts)
        label_anchor_idx = _row_before_or_on(history, event_day)
        if feature_idx is None or feature_idx < 5:
            summary["insufficient_history"] += 1
            continue
        if label_anchor_idx is None:
            summary["insufficient_forward_window"] += 1
            continue
        label_idx = label_anchor_idx + max(1, horizon_days)
        if label_idx >= len(history):
            summary["insufficient_forward_window"] += 1
            continue

        asof = history[feature_idx]
        prev1 = history[feature_idx - 1]
        prev5 = history[feature_idx - 5]
        future = history[label_idx]
        close = float(asof["close"])
        return_fwd = _pct(float(future["close"]), close)
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        snapshot = event.get("snapshot") if isinstance(event.get("snapshot"), dict) else {}
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
            "feature_return_1d_lagged": _pct(close, prev1.get("close")),
            "feature_return_5d_lagged": _pct(close, prev5.get("close")),
            "feature_volume": asof.get("volume"),
            f"return_{horizon_days}d": return_fwd,
            f"label_up_{horizon_days}d": int(return_fwd is not None and return_fwd > 0.0),
            "leakage_guard": "features_asof_previous_completed_market_close_unless_decision_after_close_labels_anchored_to_decision_date",
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
        "join_policy": "features use last completed market row before decision timestamp; same-date daily bars only after regular market close; labels anchor to the decision date row before applying the horizon",
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
    market = load_market_history(raw_root)
    rows, summary = build_training_rows_from_raw_market(events, market, horizon_days=max(1, args.horizon_days))
    manifest = write_rows(Path(args.output), rows, summary, raw_root=raw_root, decision_log=decision_log, horizon_days=max(1, args.horizon_days))
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
