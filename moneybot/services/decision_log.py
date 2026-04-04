from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path
from threading import Lock
from typing import Any, Dict


def read_decision_events(path: str, *, limit: int | None = None) -> list[Dict[str, Any]]:
    file_path = Path(path)
    if not file_path.exists():
        return []

    with file_path.open("r", encoding="utf-8") as fh:
        if limit is None:
            lines = fh.readlines()
        else:
            from collections import deque

            lines = list(deque(fh, maxlen=max(0, int(limit))))

    events: list[Dict[str, Any]] = []
    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            events.append(record)
    return events


def summarize_decision_events(path: str, *, limit: int = 200) -> Dict[str, Any]:
    events = read_decision_events(path, limit=limit)
    source_counts = Counter()
    endpoint_counts = Counter()
    symbol_counts = Counter()

    for event in events:
        source_counts[str(event.get("decision_source") or "unknown")] += 1
        endpoint_counts[str(event.get("endpoint") or "unknown")] += 1
        symbol = str(event.get("symbol") or "").strip().upper()
        if symbol:
            symbol_counts[symbol] += 1

    return {
        "path": path,
        "events_considered": len(events),
        "source_counts": dict(source_counts),
        "endpoint_counts": dict(endpoint_counts),
        "top_symbols": [
            {"symbol": symbol, "count": count}
            for symbol, count in symbol_counts.most_common(5)
        ],
        "latest_event": events[-1] if events else None,
    }


class DecisionLogger:
    """Lightweight decision telemetry logger (optional JSONL + in-memory counters)."""

    def __init__(self, *, enabled: bool = True, output_path: str | None = None):
        self.enabled = bool(enabled)
        base_dir = os.getenv("MONEYBOT_PERSISTENT_DATA_DIR", "data")
        os.makedirs(base_dir, exist_ok=True)
        self.output_path = output_path or os.path.join(base_dir, "decision_events.jsonl")
        self._lock = Lock()
        self._source_counts: dict[str, int] = {}
        self._endpoint_counts: dict[str, int] = {}

    def log(self, *, endpoint: str, symbol: str | None, decision_source: str | None, payload: Dict[str, Any]) -> None:
        if not self.enabled:
            return

        source = str(decision_source or "unknown")
        ep = str(endpoint or "unknown")

        with self._lock:
            self._source_counts[source] = self._source_counts.get(source, 0) + 1
            self._endpoint_counts[ep] = self._endpoint_counts.get(ep, 0) + 1

        record = {
            "ts": int(time.time()),
            "endpoint": ep,
            "symbol": symbol,
            "decision_source": source,
            "payload": payload,
        }
        try:
            path = Path(self.output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
        except Exception:
            # Logging should never break API behavior.
            return

    def health(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "output_path": self.output_path,
                "source_counts": dict(self._source_counts),
                "endpoint_counts": dict(self._endpoint_counts),
            }

    def summary(self, *, limit: int = 200) -> Dict[str, Any]:
        return summarize_decision_events(self.output_path, limit=limit)
