from __future__ import annotations

from typing import Any
import math

# These legacy versions were trained/evaluated against the repository's
# five-trading-day label contract. New artifacts should carry explicit metadata.
LEGACY_MODEL_FORECAST_HORIZONS = {
    "alpha-atlas-v1": "5d",
    "alpha-atlas-v1-fallback": "5d",
    "alpha-atlas-v2": "5d",
    "day1-logreg-v1": "5d",
}


def normalize_forecast_horizon(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return "unknown"
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            return "unknown"
        days = int(value)
        return f"{days}d" if days > 0 and float(value) == days else "unknown"
    text = str(value).strip().lower().replace(" ", "")
    aliases = {"1day": "1d", "5day": "5d", "10day": "10d"}
    text = aliases.get(text, text)
    if text.endswith("d") and text[:-1].isdigit() and int(text[:-1]) > 0:
        return f"{int(text[:-1])}d"
    return "unknown"


def _artifact_value(artifact: Any, key: str) -> Any:
    if isinstance(artifact, dict):
        return artifact.get(key)
    return getattr(artifact, key, None)


def resolve_forecast_horizon(
    *,
    artifact: Any = None,
    model_version: str | None = None,
    explicit_horizon: Any = None,
) -> str:
    """Resolve model horizon from explicit/artifact contract, then proven legacy metadata."""
    for value in (
        _artifact_value(artifact, "forecast_horizon"),
        _artifact_value(artifact, "horizon_days"),
        _artifact_value(artifact, "label_horizon"),
        explicit_horizon,
    ):
        normalized = normalize_forecast_horizon(value)
        if normalized != "unknown":
            return normalized
    lineage = _artifact_value(artifact, "lineage")
    if isinstance(lineage, dict):
        for key in ("forecast_horizon", "horizon_days", "label_horizon"):
            normalized = normalize_forecast_horizon(lineage.get(key))
            if normalized != "unknown":
                return normalized
    version = (
        str(model_version or _artifact_value(artifact, "version") or "").strip().lower()
    )
    return LEGACY_MODEL_FORECAST_HORIZONS.get(version, "unknown")
