from __future__ import annotations

from statistics import mean
from typing import Any, Dict


POSITIVE_ACTIONS = {"BUY", "STRONG BUY"}
NEGATIVE_ACTIONS = {"SELL", "HOLD OFF FOR NOW"}
NEUTRAL_ACTIONS = {"HOLD"}


def normalize_unix_ts(value: Any) -> int | None:
    """Normalize mixed timestamp inputs to unix seconds.

    Accepts int/float or digit-only strings and returns a positive int.
    Returns None for missing, malformed, or non-positive values.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        if value <= 0:
            return None
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.isdigit():
            parsed = int(raw)
            return parsed if parsed > 0 else None
    return None


def normalize_action(event: Dict[str, Any]) -> str | None:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    action = (
        payload.get("recommendation")
        or payload.get("advice")
        or event.get("recommendation")
        or event.get("advice")
    )
    if not isinstance(action, str):
        return None
    action = action.strip().upper()
    return action or None


def classify_outcome(action: str | None, future_return: float | None) -> str:
    if action is None or future_return is None:
        return "skipped"
    if action in NEUTRAL_ACTIONS:
        return "neutral"
    if action in POSITIVE_ACTIONS:
        return "correct" if future_return > 0 else "incorrect"
    if action in NEGATIVE_ACTIONS:
        return "correct" if future_return <= 0 else "incorrect"
    return "skipped"


def summarize_outcome_rows(rows: list[Dict[str, Any]]) -> Dict[str, Any]:
    counts = {"correct": 0, "incorrect": 0, "neutral": 0, "skipped": 0}
    returns_1d: list[float] = []
    returns_5d: list[float] = []

    for row in rows:
        counts[classify_outcome(row.get("action"), row.get("return_1d"))] += 1
        if isinstance(row.get("return_1d"), (int, float)):
            returns_1d.append(float(row["return_1d"]))
        if isinstance(row.get("return_5d"), (int, float)):
            returns_5d.append(float(row["return_5d"]))

    evaluated = counts["correct"] + counts["incorrect"]
    accuracy = round(counts["correct"] / evaluated, 4) if evaluated else None
    return {
        "rows": len(rows),
        "counts": counts,
        "evaluated_rows": evaluated,
        "accuracy": accuracy,
        "avg_return_1d": round(mean(returns_1d), 4) if returns_1d else None,
        "avg_return_5d": round(mean(returns_5d), 4) if returns_5d else None,
    }


def _has_numeric_return(row: Dict[str, Any], key: str) -> bool:
    return isinstance(row.get(key), (int, float)) and not isinstance(row.get(key), bool)


def rows_with_horizon_return(rows: list[Dict[str, Any]], horizon: str) -> list[Dict[str, Any]]:
    """Return rows that have a realized return for a specific horizon."""
    key = f"return_{horizon}"
    return [row for row in rows if _has_numeric_return(row, key)]


def rows_with_any_horizon_return(rows: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """Return rows that have at least one realized horizon return."""
    return [
        row
        for row in rows
        if _has_numeric_return(row, "return_1d") or _has_numeric_return(row, "return_5d")
    ]


def merge_recent_rows(*row_groups: list[Dict[str, Any]], limit: int) -> list[Dict[str, Any]]:
    """Merge row groups without duplicates while preserving chronological order."""
    max_rows = max(1, int(limit))
    merged: list[Dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for group in row_groups:
        for row in group:
            key = (
                row.get("ts"),
                row.get("symbol"),
                row.get("endpoint"),
                row.get("action"),
                row.get("return_1d"),
                row.get("return_5d"),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
    merged.sort(key=lambda row: int(row.get("ts") or 0))
    return merged[-max_rows:]


def close_values(history) -> list[float]:
    if history is None or getattr(history, "empty", False):
        return []
    if "Close" not in history:
        return []

    close_data = history["Close"]
    if hasattr(close_data, "columns"):
        if getattr(close_data, "empty", False):
            return []
        close_data = close_data.iloc[:, 0]

    if not hasattr(close_data, "dropna"):
        return []

    return [float(value) for value in close_data.dropna().tolist()]


def evaluate_decision_events(
    events: list[Dict[str, Any]],
    *,
    future_return_lookup,
) -> list[Dict[str, Any]]:
    rows: list[Dict[str, Any]] = []
    for event in events:
        symbol = str(event.get("symbol") or "").strip().upper()
        action = normalize_action(event)
        ts = event.get("ts")
        if not symbol or action is None or not isinstance(ts, int):
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        snapshot = event.get("snapshot") if isinstance(event.get("snapshot"), dict) else {}
        quote = snapshot.get("quote") if isinstance(snapshot.get("quote"), dict) else {}
        market_data = snapshot.get("market_data") if isinstance(snapshot.get("market_data"), dict) else {}
        personalization = snapshot.get("personalization") if isinstance(snapshot.get("personalization"), dict) else {}
        row = {
            "symbol": symbol,
            "endpoint": event.get("endpoint"),
            "decision_source": event.get("decision_source"),
            "action": action,
            "ts": ts,
            "model_version": payload.get("model_version") or snapshot.get("model_version"),
            "probability_up": payload.get("probability_up") if payload.get("probability_up") is not None else snapshot.get("probability_up"),
            "market_data": market_data,
            "personalization": personalization,
            "source_mode": quote.get("source_mode") or market_data.get("source_mode") or market_data.get("quote_source_mode"),
            "is_stale": quote.get("is_stale") if isinstance(quote.get("is_stale"), bool) else market_data.get("is_stale"),
        }
        try:
            row["return_1d"] = future_return_lookup(symbol, ts, 1)
        except Exception:  # noqa: BLE001
            row["return_1d"] = None
        try:
            row["return_5d"] = future_return_lookup(symbol, ts, 5)
        except Exception:  # noqa: BLE001
            row["return_5d"] = None
        row["outcome_1d"] = classify_outcome(action, row["return_1d"])
        row["outcome_5d"] = classify_outcome(action, row["return_5d"])
        rows.append(row)
    return rows
