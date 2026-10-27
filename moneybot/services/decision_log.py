from __future__ import annotations

import json
import math
import os
import time
from collections import Counter
from pathlib import Path
from threading import Lock
from typing import Any, Dict

from .forecast_horizon import resolve_forecast_horizon
from .prediction_evidence import classify_event


def iter_decision_events(path: str, *, limit: int | None = None):
    """Decode one JSONL record at a time; limit counts physical lines, as before.

    Keep only byte offsets during selection, never a tail of large JSON strings.
    The open file and captured end offset give a stable append-only snapshot.
    """
    from collections import deque

    file_path = Path(path)
    if not file_path.exists():
        return
    with file_path.open("rb") as fh:
        end = fh.seek(0, 2)
        fh.seek(0)
        if limit is not None:
            offsets = deque(maxlen=max(0, int(limit)))
            while fh.tell() < end:
                offsets.append(fh.tell())
                fh.readline()
            if not offsets:
                return
            fh.seek(offsets[0])
        while fh.tell() < end:
            raw = fh.readline(end - fh.tell())
            try:
                record = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(record, dict):
                yield record


def read_decision_events(path: str, *, limit: int | None = None) -> list[Dict[str, Any]]:
    return list(iter_decision_events(path, limit=limit))


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

    def log(
        self,
        *,
        endpoint: str,
        symbol: str | None,
        decision_source: str | None,
        payload: Dict[str, Any],
        snapshot: Dict[str, Any] | None = None,
        experiment: Dict[str, Any] | None = None,
        forecast: Dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled:
            return

        source = str(decision_source or "unknown")
        ep = str(endpoint or "unknown")

        with self._lock:
            self._source_counts[source] = self._source_counts.get(source, 0) + 1
            self._endpoint_counts[ep] = self._endpoint_counts.get(ep, 0) + 1

        payload_record = dict(payload)
        snapshot_record = dict(snapshot) if isinstance(snapshot, dict) else None
        probability = payload_record.get("probability_up")
        if not isinstance(probability, (int, float)) and snapshot_record is not None:
            probability = snapshot_record.get("probability_up")
        if isinstance(probability, (int, float)) and not isinstance(probability, bool) and math.isfinite(float(probability)):
            model_version = payload_record.get("model_version") or (snapshot_record or {}).get("model_version")
            horizon = resolve_forecast_horizon(
                model_version=model_version,
                explicit_horizon=payload_record.get("forecast_horizon") or (snapshot_record or {}).get("forecast_horizon"),
            )
            payload_record["forecast_horizon"] = horizon
            if snapshot_record is not None:
                snapshot_record["forecast_horizon"] = horizon

        record = {
            "schema_version": "decision_event.v2",
            "event_category": classify_event(ep, source, forecast),
            "ts": int(time.time()),
            "endpoint": ep,
            "symbol": symbol,
            "decision_source": source,
            "payload": payload_record,
        }
        if isinstance(forecast, dict):
            record["forecast"] = dict(forecast)
        if snapshot_record is not None:
            record["snapshot"] = snapshot_record
        if isinstance(experiment, dict):
            record["experiment"] = experiment
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
