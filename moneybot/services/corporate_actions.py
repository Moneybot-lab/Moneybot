from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

CORPORATE_ACTION_SCHEMA_VERSION = "moneybot-corporate-actions.v1"
# Massive represents stock dividends in the splits feed with the same explicit
# split_from/split_to share-basis ratio. They are safe to normalize when (and
# only when) both positive ratio fields are present, exactly like other splits.
SUPPORTED_ADJUSTMENT_TYPES = {"forward_split", "reverse_split", "stock_dividend"}


def normalize_split(raw: dict[str, Any]) -> dict[str, Any] | None:
    ticker = str(raw.get("ticker") or "").strip().upper()
    execution_date = str(raw.get("execution_date") or "")[:10]
    adjustment_type = str(raw.get("adjustment_type") or "").strip().lower()
    try:
        split_from = float(raw.get("split_from"))
        split_to = float(raw.get("split_to"))
    except (TypeError, ValueError):
        return None
    if not ticker or len(execution_date) != 10 or split_from <= 0 or split_to <= 0:
        return None
    if adjustment_type not in SUPPORTED_ADJUSTMENT_TYPES:
        return None
    normalized = {
        "ticker": ticker,
        "execution_date": execution_date,
        "adjustment_type": adjustment_type,
        "split_from": split_from,
        "split_to": split_to,
        "price_adjustment_factor": split_from / split_to,
        "historical_adjustment_factor": raw.get("historical_adjustment_factor"),
        "id": str(raw.get("id") or ""),
    }
    available_at = raw.get("available_at") or raw.get("published_at")
    if available_at not in (None, ""):
        normalized["available_at"] = available_at
    return normalized


def canonical_splits(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = [item for raw in records if (item := normalize_split(raw)) is not None]
    unique = {
        (
            item["ticker"],
            item["execution_date"],
            item["id"],
            item["split_from"],
            item["split_to"],
        ): item
        for item in normalized
    }
    return sorted(
        unique.values(),
        key=lambda item: (item["ticker"], item["execution_date"], item["id"]),
    )


def split_source_hash(records: Iterable[dict[str, Any]]) -> str:
    payload = "".join(
        json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n"
        for item in canonical_splits(records)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_split_cache(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(
            f"Required Massive split metadata cache does not exist: {path}"
        )
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Corrupt Massive split metadata at line {number}"
            ) from exc
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid Massive split metadata at line {number}")
        records.append(raw)
    normalized = canonical_splits(records)
    if len(normalized) != len(records):
        raise ValueError(
            "Massive split metadata contains unsupported, duplicate, or malformed records"
        )
    return normalized


def index_splits(records: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for item in canonical_splits(records):
        result.setdefault(item["ticker"], []).append(item)
    return result


def price_factor_between(
    events: Iterable[dict[str, Any]], start_date: str, end_date: str
) -> float:
    factor = 1.0
    for event in events:
        if start_date < event["execution_date"] <= end_date:
            factor *= float(event["split_from"]) / float(event["split_to"])
    return factor


def adjust_bars_to_asof(
    bars: Iterable[dict[str, Any]],
    events: Iterable[dict[str, Any]],
    asof_date: str,
    *,
    audit: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Put bars on the share basis effective at asof_date using no future split."""
    split_events = list(events)
    adjusted = []
    for raw in bars:
        bar = dict(raw)
        day = str(bar.get("date") or "")[:10]
        factor = price_factor_between(split_events, day, asof_date)
        applicable = [
            str(item.get("id") or "")
            for item in split_events
            if day < item["execution_date"] <= asof_date
        ]
        if factor != 1.0:
            for field in ("open", "high", "low", "close"):
                value = bar.get(field)
                if value is not None:
                    bar[field] = float(value) * factor
                    if audit is not None:
                        audit["price_values_adjusted"] = (
                            audit.get("price_values_adjusted", 0) + 1
                        )
            if bar.get("volume") is not None:
                bar["volume"] = float(bar["volume"]) / factor
                if audit is not None:
                    audit["volume_values_adjusted"] = (
                        audit.get("volume_values_adjusted", 0) + 1
                    )
            if audit is not None:
                audit["bars_adjusted"] = audit.get("bars_adjusted", 0) + 1
        bar["_split_adjustment_factor"] = factor
        bar["_applicable_split_ids"] = applicable
        adjusted.append(bar)
    return adjusted


def split_adjusted_forward_return(
    asof_close: float,
    future_close: float,
    asof_date: str,
    future_date: str,
    events: Iterable[dict[str, Any]],
) -> float:
    factor = price_factor_between(events, asof_date, future_date)
    denominator = float(asof_close) * factor
    if not math.isfinite(denominator) or denominator == 0:
        raise ValueError(
            "Cannot calculate split-adjusted return from invalid as-of close"
        )
    return (float(future_close) / denominator) - 1.0
