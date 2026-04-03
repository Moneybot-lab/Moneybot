#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yfinance as yf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moneybot.services.decision_log import read_decision_events
from moneybot.services.outcome_tracking import classify_outcome, close_values, normalize_action, normalize_unix_ts


def _future_return(symbol: str, start_ts: int, days: int) -> float | None:
    start_dt = datetime.fromtimestamp(int(start_ts), tz=timezone.utc)
    now_utc = datetime.now(timezone.utc)
    if start_dt >= now_utc:
        return None
    if start_dt + timedelta(days=days) > now_utc:
        return None

    end_dt = start_dt + timedelta(days=max(days + 3, 7))
    safe_end_dt = min(end_dt, now_utc + timedelta(days=1))
    try:
        history = yf.download(
            symbol,
            start=start_dt.strftime("%Y-%m-%d"),
            end=safe_end_dt.strftime("%Y-%m-%d"),
            interval="1d",
            progress=False,
            auto_adjust=False,
        )
    except Exception:  # noqa: BLE001
        return None

    closes = close_values(history)
    if len(closes) <= days:
        return None
    start_price = float(closes[0])
    end_price = float(closes[days])
    if start_price == 0:
        return None
    return round((end_price - start_price) / start_price, 4)


def _extract_probability(snapshot: dict[str, Any], payload: dict[str, Any]) -> float | None:
    value = snapshot.get("probability_up")
    if not isinstance(value, (int, float)):
        value = payload.get("probability_up")
    return float(value) if isinstance(value, (int, float)) else None


def _extract_model_version(snapshot: dict[str, Any], payload: dict[str, Any]) -> str | None:
    value = snapshot.get("model_version") if isinstance(snapshot.get("model_version"), str) else payload.get("model_version")
    return str(value) if isinstance(value, str) and value.strip() else None


def _extract_recommendation(event: dict[str, Any], snapshot: dict[str, Any]) -> str | None:
    rec = snapshot.get("recommendation") if isinstance(snapshot.get("recommendation"), str) else None
    if rec:
        return rec.strip().upper()
    return normalize_action(event)


def _extract_feature_columns(
    *,
    snapshot: dict[str, Any],
    payload: dict[str, Any],
    return_1d: float | None,
    return_5d: float | None,
) -> dict[str, float]:
    features_raw = snapshot.get("features") if isinstance(snapshot.get("features"), dict) else {}
    quote_raw = snapshot.get("quote") if isinstance(snapshot.get("quote"), dict) else {}
    out: dict[str, float] = {}

    for key, value in features_raw.items():
        if isinstance(value, (int, float)):
            clean_key = str(key)
            if not clean_key.startswith("feature_"):
                clean_key = f"feature_{clean_key}"
            out[clean_key] = float(value)

    fallback_map = {
        "feature_price": quote_raw.get("price") if isinstance(quote_raw.get("price"), (int, float)) else payload.get("price"),
        "feature_change_percent": quote_raw.get("change_percent")
        if isinstance(quote_raw.get("change_percent"), (int, float))
        else payload.get("change_percent"),
        "feature_probability_up": payload.get("probability_up"),
    }
    for key, value in fallback_map.items():
        if key not in out and isinstance(value, (int, float)):
            out[key] = float(value)

    if isinstance(return_1d, (int, float)):
        out.setdefault("feature_return_1d", float(return_1d))
    if isinstance(return_5d, (int, float)):
        out.setdefault("feature_return_5d", float(return_5d))

    return out


def build_rows(events: list[dict[str, Any]], *, horizon_days: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    now_utc = datetime.now(timezone.utc)
    mature_cutoff = now_utc - timedelta(days=max(1, horizon_days + 2))

    scanned = 0
    mature = 0
    labeled = 0
    rows: list[dict[str, Any]] = []

    for event in events:
        scanned += 1
        symbol = str(event.get("symbol") or "").strip().upper()
        ts = normalize_unix_ts(event.get("ts"))
        if not symbol or ts is None:
            continue

        event_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        if event_dt > mature_cutoff:
            continue
        mature += 1

        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        snapshot = event.get("snapshot") if isinstance(event.get("snapshot"), dict) else {}
        recommendation = _extract_recommendation(event, snapshot)
        if not recommendation:
            continue

        ret_1d = _future_return(symbol, ts, 1)
        ret_5d = _future_return(symbol, ts, max(1, horizon_days))
        if ret_1d is None and ret_5d is None:
            continue

        row: dict[str, Any] = {
            "ts": ts,
            "symbol": symbol,
            "endpoint": str(event.get("endpoint") or "unknown"),
            "decision_source": str(event.get("decision_source") or "unknown"),
            "recommendation": recommendation,
            "probability_up": _extract_probability(snapshot, payload),
            "model_version": _extract_model_version(snapshot, payload),
            "return_1d": ret_1d,
            "return_5d": ret_5d,
            "outcome_1d": classify_outcome(recommendation, ret_1d),
            "outcome_5d": classify_outcome(recommendation, ret_5d),
            "label_up_5d": int(ret_5d > 0) if isinstance(ret_5d, (int, float)) else None,
        }
        row.update(
            _extract_feature_columns(
                snapshot=snapshot,
                payload=payload,
                return_1d=ret_1d,
                return_5d=ret_5d,
            )
        )

        rows.append(row)
        labeled += 1

    return rows, {"rows_scanned": scanned, "mature_rows": mature, "labeled_rows": labeled}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build decision training dataset from historical decision events.")
    parser.add_argument("--input", default="data/decision_events.jsonl")
    parser.add_argument("--output", default="data/decision_training_snapshot.jsonl")
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--horizon-days", type=int, default=5)
    args = parser.parse_args()

    events = read_decision_events(args.input, limit=max(1, args.limit))
    rows, summary = build_rows(events, horizon_days=max(1, args.horizon_days))

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, default=str) + "\n")

    print(json.dumps({**summary, "output": str(output_path)}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
