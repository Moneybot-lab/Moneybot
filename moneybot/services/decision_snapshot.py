from __future__ import annotations

from typing import Any, Dict


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list_of_str(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = _as_str(item)
        if text:
            out.append(text)
    return out


def build_decision_snapshot(
    *,
    symbol: str,
    endpoint: str,
    decision_source: str,
    recommendation: str,
    probability_up: float | None = None,
    model_version: str | None = None,
    calibration_version: str | None = None,
    quote: Dict[str, Any] | None = None,
    features: Dict[str, Any] | None = None,
    signals: Dict[str, Any] | None = None,
    explanation: Dict[str, Any] | None = None,
) -> dict[str, Any]:
    quote_raw = _as_dict(quote)
    explanation_raw = _as_dict(explanation)

    return {
        "schema_version": "decision_snapshot.v1",
        "symbol": str(symbol).strip().upper(),
        "endpoint": str(endpoint or "unknown"),
        "decision_source": str(decision_source or "unknown"),
        "recommendation": str(recommendation).strip().upper(),
        "probability_up": _as_float(probability_up),
        "model_version": _as_str(model_version),
        "calibration_version": _as_str(calibration_version),
        "quote": {
            "price": _as_float(quote_raw.get("price")),
            "change_percent": _as_float(quote_raw.get("change_percent")),
            "source": _as_str(quote_raw.get("source")),
        },
        "features": _as_dict(features),
        "signals": _as_dict(signals),
        "explanation": {
            "rationale": _as_str(explanation_raw.get("rationale")),
            "risk_notes": _as_list_of_str(explanation_raw.get("risk_notes")),
            "next_checks": _as_list_of_str(explanation_raw.get("next_checks")),
        },
    }
