from __future__ import annotations

from statistics import mean
from typing import Any, Dict


POSITIVE_ACTIONS = {"BUY", "STRONG BUY"}
NEGATIVE_ACTIONS = {"SELL", "HOLD OFF FOR NOW"}
NEUTRAL_ACTIONS = {"HOLD"}


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
        row = {
            "symbol": symbol,
            "endpoint": event.get("endpoint"),
            "decision_source": event.get("decision_source"),
            "action": action,
            "ts": ts,
            "model_version": (event.get("payload") or {}).get("model_version") if isinstance(event.get("payload"), dict) else None,
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
