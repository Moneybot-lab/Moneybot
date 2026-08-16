from __future__ import annotations

from typing import Any

TARGET_NAME = "label_up_5d"
FORECAST_HORIZON = "5d"
HORIZON_DAYS = 5
RETURN_COLUMN = "return_5d"
TARGET_DEFINITION = "1 when close(T+5 trading bars) / close(T) - 1 > 0; otherwise 0"
POSITIVE_CLASS_SEMANTICS = "strictly positive five-trading-bar forward return"

# These buckets remain the canonical economic evaluation and tail-risk
# semantics. They do not redefine the binary decision-lane training target.
RETURN_BIN_EDGES = (-0.03, -0.005, 0.005, 0.03)
RETURN_BUCKETS = ("big_loss", "loss", "flat", "gain", "big_gain")
POSITIVE_RETURN_BUCKETS = ("gain", "big_gain")


def label_from_forward_return(value: Any) -> float | None:
    try:
        forward_return = float(value)
    except (TypeError, ValueError):
        return None
    return float(forward_return > 0.0)


def target_metadata() -> dict[str, Any]:
    return {
        "target_name": TARGET_NAME,
        "target_definition": TARGET_DEFINITION,
        "positive_class_semantics": POSITIVE_CLASS_SEMANTICS,
        "binary_positive_class": {
            "return_column": RETURN_COLUMN,
            "operator": ">",
            "value": 0.0,
        },
        "evaluation_return_buckets": {
            "names": list(RETURN_BUCKETS),
            "edges": list(RETURN_BIN_EDGES),
        },
        # Compatibility-only economic evaluation metadata; it does not define
        # label_up_5d because small positive `flat` returns are also label 1.
        "positive_return_buckets": list(POSITIVE_RETURN_BUCKETS),
        "return_bucket_edges": list(RETURN_BIN_EDGES),
        "forecast_horizon": FORECAST_HORIZON,
        "horizon_days": HORIZON_DAYS,
        "return_column": RETURN_COLUMN,
    }
