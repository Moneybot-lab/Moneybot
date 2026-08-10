from __future__ import annotations

from typing import Any, Iterable

import numpy as np
import pandas as pd

from moneybot.services.alpha_atlas_v3_features import ALPHA_ATLAS_V3_FEATURES

V31_CANDIDATE_VERSION = "candidate-alpha-atlas-v31-clean-v1"
V31_SCALER_RECIPES = (
    {
        "name": "weighted_standard",
        "scaler_type": "weighted_standard",
        "winsor_quantiles": None,
    },
    {
        "name": "winsor_0_1",
        "scaler_type": "weighted_standard",
        "winsor_quantiles": (0.001, 0.999),
    },
    {
        "name": "winsor_0_5",
        "scaler_type": "weighted_standard",
        "winsor_quantiles": (0.005, 0.995),
    },
    {
        "name": "winsor_1",
        "scaler_type": "weighted_standard",
        "winsor_quantiles": (0.01, 0.99),
    },
    {"name": "robust_iqr", "scaler_type": "robust_iqr", "winsor_quantiles": None},
)
V31_L2_VALUES = (1e-4, 1e-3, 1e-2, 1e-1)
AUDIT_PERCENTILES = (
    0.0001,
    0.001,
    0.005,
    0.01,
    0.05,
    0.25,
    0.5,
    0.75,
    0.95,
    0.99,
    0.995,
    0.999,
    0.9999,
)


def feature_distribution_audit(
    fit: pd.DataFrame,
    *,
    features: Iterable[str] = ALPHA_ATLAS_V3_FEATURES,
) -> dict[str, Any]:
    """Describe the fit period without filtering or mutating any observation."""
    report: dict[str, Any] = {
        "scope": "fit_period_only",
        "rows": int(len(fit)),
        "rows_deleted": 0,
        "features": {},
    }
    for feature in features:
        values = pd.to_numeric(fit.get(feature), errors="coerce").replace(
            [np.inf, -np.inf], np.nan
        )
        clean = values.dropna()
        median = float(clean.median()) if len(clean) else None
        quantiles = (
            clean.quantile(AUDIT_PERCENTILES) if len(clean) else pd.Series(dtype=float)
        )
        report["features"][feature] = {
            "count": int(len(clean)),
            "missing_count": int(values.isna().sum()),
            "mean": float(clean.mean()) if len(clean) else None,
            "median": median,
            "std": float(clean.std(ddof=0)) if len(clean) else None,
            "mad": (
                float(np.median(np.abs(clean.to_numpy() - median)))
                if len(clean)
                else None
            ),
            "min": float(clean.min()) if len(clean) else None,
            "max": float(clean.max()) if len(clean) else None,
            "percentiles": {
                f"p{percentile * 100:g}": float(quantiles.loc[percentile])
                for percentile in AUDIT_PERCENTILES
            },
        }
    return report


def extreme_return_audit(
    fit: pd.DataFrame,
    *,
    features: Iterable[str] = (
        "feature_return_1d_lagged",
        "feature_return_5d_lagged",
        "feature_return_20d_lagged",
    ),
    tail_fraction: float = 0.01,
) -> dict[str, Any]:
    if not 0.0 < tail_fraction <= 0.5:
        raise ValueError("tail_fraction must be in (0, 0.5]")
    group_columns = ["symbol", "event_date", "endpoint", "decision_source"]
    keys = [column for column in group_columns if column in fit.columns]
    duplicate_counts = (
        fit.groupby(keys, dropna=False)[keys[0]].transform("size")
        if keys
        else pd.Series(1, index=fit.index)
    )
    output: dict[str, Any] = {
        "scope": "fit_period_only",
        "tail_fraction": tail_fraction,
        "rows_deleted": 0,
        "features": {},
    }
    for feature in features:
        numeric = pd.to_numeric(fit.get(feature), errors="coerce")
        valid = numeric.dropna()
        if valid.empty:
            output["features"][feature] = []
            continue
        lower, upper = valid.quantile([tail_fraction, 1.0 - tail_fraction])
        indices = valid[(valid <= lower) | (valid >= upper)].index
        rows = []
        for index in indices:
            source = fit.loc[index]
            rows.append(
                {
                    "symbol": source.get("symbol"),
                    "event_date": str(source.get("event_date") or "")[:10],
                    "endpoint": source.get("endpoint"),
                    "decision_source": source.get("decision_source"),
                    "raw_feature_value": float(numeric.loc[index]),
                    "duplicate_decision_rows": int(duplicate_counts.loc[index]),
                    "close_values_used": source.get(f"{feature}_close_values"),
                    "massive_bar_dates": source.get(f"{feature}_bar_dates"),
                    "classification": source.get("corporate_action_classification")
                    or "unknown",
                }
            )
        output["features"][feature] = rows
    return output


def calibration_is_stable(
    calibration_delta_brier: float,
    fold_delta_briers: Iterable[float],
    *,
    maximum_fold_regression: float = 0.0025,
) -> bool:
    """Accept calibration only when its block improves and no fold materially regresses."""
    deltas = [float(value) for value in fold_delta_briers]
    return (
        calibration_delta_brier < 0.0
        and bool(deltas)
        and max(deltas) <= maximum_fold_regression
    )
