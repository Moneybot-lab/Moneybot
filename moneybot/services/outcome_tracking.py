from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any, Callable, Dict

import pandas as pd


POSITIVE_ACTIONS = {"BUY", "STRONG BUY"}
NEGATIVE_ACTIONS = {"SELL", "HOLD OFF FOR NOW"}
NEUTRAL_ACTIONS = {"HOLD"}
PAPER_PNL_HORIZONS = (1, 5, 10, 20)
PAPER_PNL_BENCHMARK_HORIZON = 20


MAX_HISTORY_HORIZON_DAYS = max(PAPER_PNL_HORIZONS)
DEFAULT_HISTORY_CALENDAR_WINDOW_DAYS = max(14, MAX_HISTORY_HORIZON_DAYS * 3 + 7)


@dataclass
class OutcomeHistoryDiagnostics:
    history_cache_hits: int = 0
    history_cache_misses: int = 0
    history_download_errors: int = 0
    insufficient_history_1d: int = 0
    insufficient_history_5d: int = 0
    insufficient_history_10d: int = 0
    insufficient_history_20d: int = 0

    def increment_insufficient(self, days: int) -> None:
        attr = f"insufficient_history_{days}d"
        if hasattr(self, attr):
            setattr(self, attr, int(getattr(self, attr)) + 1)

    def as_dict(self) -> dict[str, int]:
        return {
            "history_cache_hits": self.history_cache_hits,
            "history_cache_misses": self.history_cache_misses,
            "history_download_errors": self.history_download_errors,
            "insufficient_history_1d": self.insufficient_history_1d,
            "insufficient_history_5d": self.insufficient_history_5d,
            "insufficient_history_10d": self.insufficient_history_10d,
            "insufficient_history_20d": self.insufficient_history_20d,
        }


def event_market_date(ts: int) -> datetime.date:
    """Use the UTC event date as the daily-bar entry date convention.

    Paper P&L uses the close for the event's UTC date when Yahoo has that daily
    bar. If the event date is not a market session, the next available completed
    daily close in the downloaded history becomes the entry close. Returns are
    never produced unless enough completed close bars are present.
    """
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).date()


def dated_close_values(history) -> list[tuple[datetime.date, float]]:
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

    close_data = close_data.dropna()
    dated: list[tuple[datetime.date, float]] = []
    for idx, value in close_data.items():
        try:
            date_value = pd.Timestamp(idx).date()
        except Exception:  # noqa: BLE001
            continue
        dated.append((date_value, float(value)))
    return dated


@dataclass
class OutcomeHistoryCache:
    download: Callable[..., Any]
    now: datetime | None = None
    calendar_window_days: int = DEFAULT_HISTORY_CALENDAR_WINDOW_DAYS
    diagnostics: OutcomeHistoryDiagnostics = field(default_factory=OutcomeHistoryDiagnostics)
    _cache: dict[tuple[str, str], list[tuple[datetime.date, float]]] = field(default_factory=dict)
    _symbol_cache: dict[str, list[tuple[datetime.date, float]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.now is None:
            self.now = datetime.now(timezone.utc)
        elif self.now.tzinfo is None:
            self.now = self.now.replace(tzinfo=timezone.utc)
        else:
            self.now = self.now.astimezone(timezone.utc)

    @property
    def cache_size(self) -> int:
        return len(self._cache) + len(self._symbol_cache)

    def _download_symbol_range(self, symbol: str, start_date: datetime.date) -> list[tuple[datetime.date, float]]:
        assert self.now is not None
        safe_end_date = self.now.date() + timedelta(days=1)
        if safe_end_date <= start_date:
            return []
        try:
            history = self.download(
                str(symbol).upper(),
                start=start_date.isoformat(),
                end=safe_end_date.isoformat(),
                interval="1d",
                progress=False,
                auto_adjust=False,
                threads=False,
            )
        except Exception:  # noqa: BLE001
            self.diagnostics.history_download_errors += 1
            return []
        return [(date_value, close) for date_value, close in dated_close_values(history) if date_value >= start_date]

    def preload_events(self, events: list[Dict[str, Any]], *, benchmark_symbol: str = "SPY") -> None:
        symbol_dates: dict[str, list[datetime.date]] = {}
        assert self.now is not None
        for event in events:
            if not isinstance(event, dict):
                continue
            symbol = str(event.get("symbol") or "").strip().upper()
            ts = event.get("ts")
            if not symbol or not isinstance(ts, int):
                continue
            event_date = event_market_date(ts)
            if event_date >= self.now.date():
                continue
            symbol_dates.setdefault(symbol, []).append(event_date)
        if symbol_dates:
            earliest = min(min(dates) for dates in symbol_dates.values())
            symbol_dates.setdefault(benchmark_symbol.upper(), []).append(earliest)
        for symbol, dates in symbol_dates.items():
            if symbol in self._symbol_cache:
                self.diagnostics.history_cache_hits += 1
                continue
            self.diagnostics.history_cache_misses += 1
            self._symbol_cache[symbol] = self._download_symbol_range(symbol, min(dates))

    def diagnostics_payload(self) -> dict[str, int]:
        payload = self.diagnostics.as_dict()
        payload["history_cache_size"] = self.cache_size
        return payload

    def closes_for_event(self, symbol: str, ts: int) -> list[float]:
        event_date = event_market_date(ts)
        symbol_key = str(symbol).upper()
        if self.now is not None and event_date >= self.now.date():
            return []
        if symbol_key in self._symbol_cache:
            self.diagnostics.history_cache_hits += 1
            return [close for date_value, close in self._symbol_cache[symbol_key] if date_value >= event_date]
        key = (symbol_key, event_date.isoformat())
        if key in self._cache:
            self.diagnostics.history_cache_hits += 1
            return [close for _, close in self._cache[key]]

        self.diagnostics.history_cache_misses += 1
        start_date = event_date
        end_date = start_date + timedelta(days=max(14, int(self.calendar_window_days)))
        assert self.now is not None
        safe_end_date = min(end_date, self.now.date() + timedelta(days=1))
        if safe_end_date <= start_date:
            self._cache[key] = []
            return []
        try:
            history = self.download(
                symbol_key,
                start=start_date.isoformat(),
                end=safe_end_date.isoformat(),
                interval="1d",
                progress=False,
                auto_adjust=False,
                threads=False,
            )
        except Exception:  # noqa: BLE001
            self.diagnostics.history_download_errors += 1
            self._cache[key] = []
            return []

        dated = [(date_value, close) for date_value, close in dated_close_values(history) if date_value >= event_date]
        self._cache[key] = dated
        return [close for _, close in dated]

    def future_return(self, symbol: str, ts: int, days: int) -> float | None:
        closes = self.closes_for_event(symbol, ts)
        if len(closes) <= days:
            self.diagnostics.increment_insufficient(days)
            return None
        start_price = float(closes[0])
        if start_price == 0.0:
            return None
        return round((float(closes[days]) - start_price) / start_price, 4)

    def price_path(self, symbol: str, ts: int, days: int) -> list[float]:
        closes = self.closes_for_event(symbol, ts)
        return closes[: max(0, int(days)) + 1]

    def benchmark_return(self, ts: int, days: int) -> float | None:
        return self.future_return("SPY", ts, days)


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
    path_returns = [
        0.0,
        *[((float(price) - start_price) / start_price) * exposure for price in closes[1:]],
    ]
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
            payload[f"evaluated_rows_{days}d"] = sum(
                1
                for row in action_rows
                if _has_numeric_return(row, f"return_{days}d")
            )
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


def rows_with_horizon_accuracy_outcome(rows: list[Dict[str, Any]], horizon: str) -> list[Dict[str, Any]]:
    """Return rows with a realized actionable correct/incorrect outcome for a horizon.

    HOLD rows can have realized returns, but they are classified as neutral and do
    not contribute to accuracy. Accuracy cards should prefer these actionable rows
    so a recent block of HOLD decisions does not mask older BUY/SELL 5D outcomes.
    """
    outcome_key = f"outcome_{horizon}"
    return [
        row
        for row in rows_with_horizon_return(rows, horizon)
        if row.get(outcome_key) in {"correct", "incorrect"}
    ]


def rows_with_any_horizon_return(rows: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """Return rows that have at least one realized horizon return."""
    return [
        row
        for row in rows
        if any(_has_numeric_return(row, f"return_{days}d") for days in PAPER_PNL_HORIZONS)
    ]


def _visible_row_identity(row: Dict[str, Any], horizon: str | None) -> tuple[Any, ...]:
    ts = normalize_unix_ts(row.get("ts"))
    market_date = event_market_date(ts).isoformat() if ts is not None else None
    return_key = f"return_{horizon}" if horizon else None
    outcome_key = f"outcome_{horizon}" if horizon else None
    return (
        market_date,
        row.get("symbol"),
        row.get("endpoint"),
        row.get("decision_source"),
        row.get("action"),
        row.get("model_version"),
        row.get(return_key) if return_key else tuple(row.get(f"return_{days}d") for days in PAPER_PNL_HORIZONS),
        row.get(outcome_key) if outcome_key else None,
    )


def select_recent_unique_rows(rows: list[Dict[str, Any]], *, limit: int, horizon: str | None = None) -> list[Dict[str, Any]]:
    """Return recent rows without repeating identical same-day visible decisions.

    Decision logs can contain many repeated same-symbol checks during a day. The
    visible UI tables should not spend all rows on identical symbol/action/return
    entries, while aggregate P&L continues to use every logged decision.
    """
    max_rows = max(1, int(limit))
    selected: list[Dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in reversed(rows):
        key = _visible_row_identity(row, horizon)
        if key in seen:
            continue
        seen.add(key)
        selected.append(row)
        if len(selected) >= max_rows:
            break
    selected.reverse()
    return selected


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
        drawdown_to_date, favorable_to_date = paper_path_extremes(action, closes)
        row["max_drawdown_to_date"] = drawdown_to_date
        row["max_favorable_excursion_to_date"] = favorable_to_date
        path_complete = len(closes) > max(PAPER_PNL_HORIZONS)
        row["paper_path_complete_20d"] = path_complete
        row["max_drawdown"] = drawdown_to_date if path_complete else None
        row["max_favorable_excursion"] = favorable_to_date if path_complete else None

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
