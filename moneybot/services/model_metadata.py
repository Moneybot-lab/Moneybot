from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


def metadata_path_for_model(model_path: str) -> Path:
    path = Path(model_path)
    return path.with_suffix(path.suffix + ".meta.json")


def history_path_for_model(model_path: str) -> Path:
    path = Path(model_path)
    return path.with_suffix(path.suffix + ".history.json")


def build_artifact_metadata(
    *,
    model_path: str,
    model_version: str,
    input_path: str,
    train_rows: int,
    test_rows: int,
    metrics: Dict[str, Any],
    train_ratio: float,
    horizon_days: int,
    target_return: float,
) -> Dict[str, Any]:
    return {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_path": model_path,
        "model_version": model_version,
        "input_path": input_path,
        "train_rows": int(train_rows),
        "test_rows": int(test_rows),
        "metrics": dict(metrics),
        "train_ratio": float(train_ratio),
        "horizon_days": int(horizon_days),
        "target_return": float(target_return),
    }


def save_artifact_metadata(model_path: str, metadata: Dict[str, Any]) -> Path:
    path = metadata_path_for_model(model_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    return path


def append_artifact_history(model_path: str, metadata: Dict[str, Any], *, max_entries: int = 25) -> Path:
    path = history_path_for_model(model_path)
    history = load_artifact_history(model_path)
    history.append(dict(metadata))
    trimmed = history[-max(1, int(max_entries)) :]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(trimmed, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _load_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def load_artifact_metadata(model_path: str) -> Dict[str, Any] | None:
    data = _load_json(metadata_path_for_model(model_path), default=None)
    return data if isinstance(data, dict) else None


def load_artifact_history(model_path: str) -> list[Dict[str, Any]]:
    data = _load_json(history_path_for_model(model_path), default=[])
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
