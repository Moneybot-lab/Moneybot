from __future__ import annotations

import logging
import os
from pathlib import Path


DEFAULT_RUNTIME_DIR = "data"


def resolve_runtime_dir() -> Path:
    """Resolve Moneybot runtime data directory from environment."""
    preferred = (
        os.environ.get("MONEYBOT_PERSISTENT_DATA_DIR")
        or os.environ.get("MONEYBOT_RUNTIME_DIR")
        or DEFAULT_RUNTIME_DIR
    )
    path = Path(preferred).expanduser()
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError as exc:
        fallback = Path(DEFAULT_RUNTIME_DIR)
        fallback.mkdir(parents=True, exist_ok=True)
        logging.warning(
            "Unable to initialize runtime dir %s (%s). Falling back to local %s (ephemeral).",
            path,
            exc,
            fallback,
        )
        return fallback


def is_durable_runtime_configured() -> bool:
    """Best-effort durability check: explicit runtime dir implies managed persistence."""
    return bool(os.environ.get("MONEYBOT_PERSISTENT_DATA_DIR") or os.environ.get("MONEYBOT_RUNTIME_DIR"))


def decision_events_log_path() -> Path:
    return resolve_runtime_dir() / "decision_events.jsonl"


def decision_outcomes_snapshot_path() -> Path:
    return resolve_runtime_dir() / "decision_outcomes_snapshot.json"


def day13_calibration_report_path() -> Path:
    return resolve_runtime_dir() / "day13_calibration_report.json"


def day13_recalibration_plan_path() -> Path:
    return resolve_runtime_dir() / "day13_recalibration_plan.json"


def day1_training_snapshot_path() -> Path:
    return resolve_runtime_dir() / "day1_training_snapshot.csv"


def day1_baseline_model_path() -> Path:
    return resolve_runtime_dir() / "day1_baseline_model.json"
