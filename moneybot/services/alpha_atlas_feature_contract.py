from __future__ import annotations

import math
from typing import Any

ALPHA_ATLAS_V2_FEATURES = (
    "feature_change_percent",
    "feature_endpoint_hot_momentum_buys",
    "feature_endpoint_quick_ask",
    "feature_endpoint_user_watchlist",
    "feature_macd_histogram",
    "feature_price",
    "feature_probability_up",
    "feature_rec_buy",
    "feature_rec_hold",
    "feature_rec_hold_off_for_now",
    "feature_rec_negative",
    "feature_rec_positive",
    "feature_rec_sell",
    "feature_rec_strong_buy",
    "feature_return_1d",
    "feature_return_5d",
    "feature_rsi",
    "feature_source_ai_enhanced",
    "feature_source_deterministic_model",
    "feature_source_rule_based",
    "feature_volume_ratio",
)

# Day 8 populated these from the event being labeled (and populated returns from
# post-event outcomes when snapshot values were absent). They are not a safe input
# contract for scoring that same event with v2.
NON_SERVABLE_V2_FEATURES = {
    "feature_probability_up",
    "feature_return_1d",
    "feature_return_5d",
    "feature_rec_buy",
    "feature_rec_hold",
    "feature_rec_hold_off_for_now",
    "feature_rec_negative",
    "feature_rec_positive",
    "feature_rec_sell",
    "feature_rec_strong_buy",
    "feature_source_ai_enhanced",
    "feature_source_deterministic_model",
    "feature_source_rule_based",
}


def _finite(value: Any) -> float | None:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    ):
        return float(value)
    return None


def build_alpha_atlas_event_features(
    *,
    endpoint: str,
    decision_source: str,
    recommendation: str,
    quote: dict[str, Any],
    signals: dict[str, Any],
    prior_probability: float | None = None,
) -> dict[str, float | None]:
    """Canonical Day-8 feature names; context arguments must describe the upstream event."""
    technical = (
        signals.get("technical") if isinstance(signals.get("technical"), dict) else {}
    )
    supplied = (
        signals.get("features") if isinstance(signals.get("features"), dict) else {}
    )
    endpoint_key = str(endpoint or "").strip().lower()
    source_key = str(decision_source or "").strip().lower()
    rec = str(recommendation or "").strip().upper()
    values: dict[str, float | None] = {
        "feature_price": _finite(quote.get("price")),
        "feature_change_percent": _finite(quote.get("change_percent")),
        "feature_rsi": _finite(
            supplied.get("rsi")
            if supplied.get("rsi") is not None
            else technical.get("rsi")
        ),
        "feature_macd_histogram": _finite(
            supplied.get("macd_histogram")
            if supplied.get("macd_histogram") is not None
            else technical.get("macd_histogram")
        ),
        "feature_volume_ratio": _finite(
            supplied.get("volume_ratio")
            if supplied.get("volume_ratio") is not None
            else signals.get("volume_ratio")
        ),
        "feature_return_1d": _finite(supplied.get("return_1d")),
        "feature_return_5d": _finite(supplied.get("return_5d")),
        "feature_probability_up": _finite(prior_probability),
    }
    for name in ("quick_ask", "hot_momentum_buys", "user_watchlist"):
        values[f"feature_endpoint_{name}"] = float(endpoint_key == name)
    for name, label in (
        ("buy", "BUY"),
        ("hold", "HOLD"),
        ("hold_off_for_now", "HOLD OFF FOR NOW"),
        ("sell", "SELL"),
        ("strong_buy", "STRONG BUY"),
    ):
        values[f"feature_rec_{name}"] = float(rec == label)
    values["feature_rec_positive"] = float(rec in {"BUY", "STRONG BUY"})
    values["feature_rec_negative"] = float(rec in {"SELL", "HOLD OFF FOR NOW"})
    for name in ("ai_enhanced", "deterministic_model", "rule_based"):
        values[f"feature_source_{name}"] = float(source_key == name)
    return values
