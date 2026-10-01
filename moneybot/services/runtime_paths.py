from __future__ import annotations

import logging
import os
from pathlib import Path


DEFAULT_RUNTIME_DIR = "data"


def _default_runtime_dir() -> Path:
    """Choose a best-effort durable default path when env vars are not set."""
    render_disk_root = Path("/var/data")
    if render_disk_root.exists() and render_disk_root.is_dir():
        return render_disk_root / "moneybot"
    return Path(DEFAULT_RUNTIME_DIR)


def resolve_runtime_dir() -> Path:
    """Resolve Moneybot runtime data directory from environment."""
    preferred = os.environ.get("MONEYBOT_PERSISTENT_DATA_DIR") or os.environ.get("MONEYBOT_RUNTIME_DIR")
    path = Path(preferred).expanduser() if preferred else _default_runtime_dir()
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


def prospective_snapshot_root(*, test_root: Path | None = None) -> Path:
    """Return the V4 snapshot root without ever using an implicit fallback.

    ``test_root`` is intentionally explicit and is only for synthetic tests.  A
    real pilot must use the persistent setting (not the more permissive legacy
    runtime setting).
    """
    if test_root is not None:
        root = Path(test_root)
    else:
        configured = os.environ.get("MONEYBOT_PERSISTENT_DATA_DIR")
        if not configured:
            raise RuntimeError("PERSISTENT_RUNTIME_ROOT_REQUIRED")
        root = Path(configured).expanduser()
    root = root / "alpha_atlas_v4" / "prospective_snapshots" / "v1"
    root.mkdir(parents=True, exist_ok=True)
    return root


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


def bad_symbol_cache_path() -> Path:
    return resolve_runtime_dir() / "track_b" / "bad_symbols.json"


def historical_validation_report_path() -> Path:
    return resolve_runtime_dir() / "historical_validation_report.json"
