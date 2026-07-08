from __future__ import annotations

from statistics import mean
from typing import Any, Dict


POSITIVE_ACTIONS = {"BUY", "STRONG BUY"}
NEGATIVE_ACTIONS = {"SELL", "HOLD OFF FOR NOW"}
NEUTRAL_ACTIONS = {"HOLD"}
PAPER_PNL_HORIZONS = (1, 5, 10, 20)
PAPER_PNL_BENCHMARK_HORIZON = 20


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


def paper_exposure(action: str | None) -> int:
    if action in POSITIVE_ACTIONS:
        return 1
    if action in NEGATIVE_ACTIONS:
        return -1
    if action in NEUTRAL_ACTIONS:
        return 0
    return 0


def action_adjusted_return(action: str | None, future_return: float | None) -> float | None:
    if future_return is None:
        return None
    exposure = paper_exposure(action)
    if exposure == 0:
        return 0.0
    return round(float(future_return) * exposure, 4)


def paper_path_extremes(action: str | None, closes: list[float]) -> tuple[float | None, float | None]:
    if len(closes) < 2:
        return None, None
    start_price = float(closes[0])
    if start_price == 0.0:
        return None, None
    exposure = paper_exposure(action)
    if exposure == 0:
        return 0.0, 0.0
    path_returns = [((float(price) - start_price) / start_price) * exposure for price in closes[1:]]
    if not path_returns:
        return None, None
    return round(min(path_returns), 4), round(max(path_returns), 4)


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


def _mean_numeric(rows: list[Dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if isinstance(row.get(key), (int, float)) and not isinstance(row.get(key), bool)]
    return round(mean(values), 4) if values else None


def summarize_paper_pnl_by_action(rows: list[Dict[str, Any]]) -> Dict[str, Any]:
    actions = ["BUY", "SELL", "HOLD", "HOLD OFF FOR NOW", "STRONG BUY"]
    grouped: Dict[str, Any] = {}
    for action in actions:
        action_rows = [row for row in rows if row.get("action") == action]
        payload: Dict[str, Any] = {"rows": len(action_rows)}
        for days in PAPER_PNL_HORIZONS:
            payload[f"avg_return_{days}d"] = _mean_numeric(action_rows, f"return_{days}d")
            payload[f"avg_paper_return_{days}d"] = _mean_numeric(action_rows, f"paper_return_{days}d")
        payload["avg_max_drawdown"] = _mean_numeric(action_rows, "max_drawdown")
        payload["avg_max_favorable_excursion"] = _mean_numeric(action_rows, "max_favorable_excursion")
        payload["avg_benchmark_return_20d"] = _mean_numeric(action_rows, "benchmark_return_20d")
        payload["avg_benchmark_relative_return_20d"] = _mean_numeric(action_rows, "benchmark_relative_return_20d")
        grouped[action] = payload
    return grouped


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
        if any(_has_numeric_return(row, f"return_{days}d") for days in PAPER_PNL_HORIZONS)
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
                row.get("return_10d"),
                row.get("return_20d"),
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
    price_path_lookup=None,
    benchmark_return_lookup=None,
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
        for days in PAPER_PNL_HORIZONS:
            key = f"return_{days}d"
            try:
                row[key] = future_return_lookup(symbol, ts, days)
            except Exception:  # noqa: BLE001
                row[key] = None
            row[f"paper_return_{days}d"] = action_adjusted_return(action, row[key])
        for days in (1, 5):
            row[f"outcome_{days}d"] = classify_outcome(action, row[f"return_{days}d"])

        closes = []
        if price_path_lookup is not None:
            try:
                closes = price_path_lookup(symbol, ts, max(PAPER_PNL_HORIZONS)) or []
            except Exception:  # noqa: BLE001
                closes = []
        row["max_drawdown"], row["max_favorable_excursion"] = paper_path_extremes(action, closes)

        benchmark_return = None
        if benchmark_return_lookup is not None:
            try:
                benchmark_return = benchmark_return_lookup(ts, PAPER_PNL_BENCHMARK_HORIZON)
            except Exception:  # noqa: BLE001
                benchmark_return = None
        row["benchmark_return_20d"] = benchmark_return
        paper_return_20d = row.get("paper_return_20d")
        row["benchmark_relative_return_20d"] = (
            round(float(paper_return_20d) - float(benchmark_return), 4)
            if isinstance(paper_return_20d, (int, float)) and isinstance(benchmark_return, (int, float))
            else None
        )
        rows.append(row)
    return rows
