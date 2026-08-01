from __future__ import annotations

from typing import Any

import pandas as pd


def _event_times(df: pd.DataFrame) -> pd.Series | None:
    if "event_date" in df.columns:
        parsed = pd.to_datetime(df["event_date"], utc=True, errors="coerce")
        if parsed.notna().all():
            return parsed
    if "ts" in df.columns:
        numeric = pd.to_numeric(df["ts"], errors="coerce")
        if numeric.notna().all() and numeric.abs().median() >= 100_000_000:
            parsed = pd.to_datetime(numeric, unit="s", utc=True, errors="coerce")
        else:
            return None
        if parsed.notna().all():
            return parsed
    return None


def purged_embargoed_split(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    *,
    horizon_days: int,
    embargo_days: int = 1,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Remove overlapping labels before a boundary and embargo rows after it."""
    train = train_df.copy()
    test = test_df.copy()
    train_times = _event_times(train)
    test_times = _event_times(test)
    method = "event_time"
    if train_times is not None and test_times is not None and not test.empty:
        boundary = test_times.min()
        purge_cutoff = boundary - pd.Timedelta(days=max(0, int(horizon_days)))
        embargo_cutoff = boundary + pd.Timedelta(days=max(0, int(embargo_days)))
        train = train.loc[train_times < purge_cutoff].copy()
        test = test.loc[test_times >= embargo_cutoff].copy()
        boundary_value = boundary.isoformat()
    else:
        method = "row_fallback"
        purge_rows = min(max(0, int(horizon_days)), len(train))
        embargo_rows = min(max(0, int(embargo_days)), len(test))
        if purge_rows:
            train = train.iloc[:-purge_rows].copy()
        if embargo_rows:
            test = test.iloc[embargo_rows:].copy()
        boundary_value = None
    diagnostics = {
        "method": method,
        "horizon_days": int(horizon_days),
        "embargo_days": int(embargo_days),
        "boundary_utc": boundary_value,
        "train_rows_before": int(len(train_df)),
        "train_rows_after": int(len(train)),
        "purged_train_rows": int(len(train_df) - len(train)),
        "test_rows_before": int(len(test_df)),
        "test_rows_after": int(len(test)),
        "embargoed_test_rows": int(len(test_df) - len(test)),
    }
    return train, test, diagnostics


def purge_embargo_periods(
    periods: list[pd.DataFrame],
    *,
    horizon_days: int,
    embargo_days: int = 1,
) -> tuple[list[pd.DataFrame], list[dict[str, Any]]]:
    """Apply purging/embargo independently at every adjacent period boundary."""
    cleaned = [period.copy() for period in periods]
    boundaries: list[dict[str, Any]] = []
    for index in range(len(cleaned) - 1):
        left, right, diagnostics = purged_embargoed_split(
            cleaned[index],
            cleaned[index + 1],
            horizon_days=horizon_days,
            embargo_days=embargo_days,
        )
        diagnostics["left_period_index"] = index
        diagnostics["right_period_index"] = index + 1
        cleaned[index] = left
        cleaned[index + 1] = right
        boundaries.append(diagnostics)
    return cleaned, boundaries
