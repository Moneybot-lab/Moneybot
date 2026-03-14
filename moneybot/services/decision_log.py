from __future__ import annotations

import json
import time
from pathlib import Path
from threading import Lock
from typing import Any, Dict


class DecisionLogger:
    """Lightweight decision telemetry logger (optional JSONL + in-memory counters)."""

    def __init__(self, *, enabled: bool = True, output_path: str = "data/decision_events.jsonl"):
        self.enabled = bool(enabled)
        self.output_path = output_path
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
