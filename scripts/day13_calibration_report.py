#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moneybot.services.decision_log import read_decision_events
from moneybot.services.outcome_history_provider import MassivePreferredHistoryDownload
from moneybot.services.outcome_tracking import OutcomeHistoryCache
from moneybot.services.runtime_paths import (
    day13_calibration_report_path,
    decision_events_log_path,
)

_outcome_history_download: MassivePreferredHistoryDownload | None = None
_outcome_history_cache: OutcomeHistoryCache | None = None


def _configure_outcome_history(events: list[dict]) -> None:
    global _outcome_history_download, _outcome_history_cache
    _outcome_history_download = MassivePreferredHistoryDownload()
    _outcome_history_cache = OutcomeHistoryCache(download=_outcome_history_download)
    _outcome_history_cache.preload_events(events, benchmark_symbol="SPY")


def history_eligible_events(
    events: list[dict], *, horizon_days: int, now_ts: int | None = None
) -> list[dict]:
    """Select only rows that may require a calibration outcome lookup."""
    eligible: list[dict] = []
    target_horizon = f"{int(horizon_days)}d"
    for event in events:
        if not isinstance(event, dict):
            continue
        payload = _event_payload(event)
        snapshot = _event_snapshot(event)
        if _event_forecast_horizon(payload, snapshot) != target_horizon:
            continue
        if _probability_up(payload, snapshot) is None:
            continue
        if _event_provider(event, payload, snapshot) == "portfolio_quote_only":
            continue
        symbol = str(event.get("symbol") or "").strip().upper()
        ts = event.get("ts")
        if not symbol or not isinstance(ts, int):
            continue
        if not _is_mature_event(ts, horizon_days=horizon_days, now_ts=now_ts):
            continue
        eligible.append(event)
    return eligible


def _future_return(symbol: str, start_ts: int, days: int) -> float | None:
    """Compatibility seam used by tests; production is configured Massive-first."""
    if _outcome_history_cache is None:
        return None
    return _outcome_history_cache.future_return(symbol, start_ts, days)


def _text_or_unknown(value) -> str:
    if value is None:
        return "unknown"
    text = str(value).strip()
    return text or "unknown"


def _event_payload(event: dict) -> dict:
    return event.get("payload") if isinstance(event.get("payload"), dict) else {}


def _event_snapshot(event: dict) -> dict:
    return event.get("snapshot") if isinstance(event.get("snapshot"), dict) else {}


def _probability_up(payload: dict, snapshot: dict):
    value = (
        payload.get("probability_up")
        if payload.get("probability_up") is not None
        else snapshot.get("probability_up")
    )
    return (
        value
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else None
    )


def _event_forecast_horizon(payload: dict, snapshot: dict) -> str:
    raw = payload.get("forecast_horizon") or snapshot.get("forecast_horizon")
    text = str(raw or "").strip().lower().replace(" ", "")
    aliases = {"1": "1d", "1day": "1d", "5": "5d", "5day": "5d"}
    if text in aliases:
        return aliases[text]
    if text.endswith("d") and text[:-1].isdigit():
        return f"{int(text[:-1])}d"
    return "unknown"


def _event_provider(event: dict, payload: dict, snapshot: dict) -> str:
    quote = snapshot.get("quote") if isinstance(snapshot.get("quote"), dict) else {}
    market_data = (
        snapshot.get("market_data")
        if isinstance(snapshot.get("market_data"), dict)
        else {}
    )
    diagnostics = (
        payload.get("diagnostics")
        if isinstance(payload.get("diagnostics"), dict)
        else {}
    )
    return _text_or_unknown(
        payload.get("provider")
        or event.get("provider")
        or diagnostics.get("provider")
        or quote.get("provider")
        or quote.get("quote_source")
        or market_data.get("provider")
        or market_data.get("quote_source")
    )


def _signal_completeness(payload: dict, snapshot: dict, provider: str) -> str:
    features = (
        payload.get("features")
        if isinstance(payload.get("features"), dict)
        else snapshot.get("features")
    )
    market_data = (
        snapshot.get("market_data")
        if isinstance(snapshot.get("market_data"), dict)
        else {}
    )
    if provider == "portfolio_quote_only":
        return "quote_only"

    feature_data = features if isinstance(features, dict) else {}

    def finite_value(*keys: str) -> bool:
        for source in (feature_data, payload, market_data):
            for key in keys:
                value = source.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    if math.isfinite(float(value)):
                        return True
        return False

    groups_present = sum(
        (
            finite_value("return_1d", "feature_return_1d"),
            finite_value("return_5d", "feature_return_5d"),
            finite_value("rsi", "rsi_14", "feature_rsi", "feature_rsi_14"),
            finite_value(
                "macd", "macd_hist", "macd_histogram", "feature_macd_histogram"
            ),
            finite_value("volume_ratio", "vol_ratio_20d", "feature_volume_ratio"),
        )
    )
    if groups_present >= 3:
        return "full_signal"
    if groups_present > 0:
        return "partial_signal"
    return "quote_only"


def _segment_key(row: dict, segment: str):
    if segment == "forecast_horizon":
        return row.get("forecast_horizon")
    if segment == "signal_completeness":
        return row.get("signal_completeness")
    if segment == "probability_presence":
        return row.get("probability_presence")
    return row.get(segment)


CALIBRATION_SEGMENTS = (
    "endpoint",
    "decision_source",
    "model_version",
    "forecast_horizon",
    "signal_completeness",
    "probability_presence",
    "provider",
)


def calibration_input_segments(events: list[dict], *, horizon_days: int = 5) -> dict:
    """Describe every scanned decision, including rows ineligible for calibration."""
    segments = {name: {} for name in CALIBRATION_SEGMENTS}
    for event in events:
        payload = _event_payload(event)
        snapshot = _event_snapshot(event)
        provider = _event_provider(event, payload, snapshot)
        probability_present = _probability_up(payload, snapshot) is not None
        values = {
            "endpoint": _text_or_unknown(event.get("endpoint")),
            "decision_source": _text_or_unknown(
                event.get("decision_source") or payload.get("decision_source")
            ),
            "model_version": _text_or_unknown(
                payload.get("model_version") or snapshot.get("model_version")
            ),
            "forecast_horizon": _event_forecast_horizon(payload, snapshot),
            "signal_completeness": _signal_completeness(payload, snapshot, provider),
            "probability_presence": "present" if probability_present else "null",
            "provider": provider,
        }
        eligible = (
            probability_present
            and provider != "portfolio_quote_only"
            and values["forecast_horizon"] == f"{int(horizon_days)}d"
        )
        for name, value in values.items():
            bucket = segments[name].setdefault(
                value,
                {
                    "rows_scanned": 0,
                    "model_calibration_eligible_rows": 0,
                    "excluded_rows": 0,
                    "mature_fitted_rows": 0,
                },
            )
            bucket["rows_scanned"] += 1
            bucket[
                "model_calibration_eligible_rows" if eligible else "excluded_rows"
            ] += 1
    return segments


def add_mature_segment_counts(input_segments: dict, rows: list[dict]) -> None:
    for row in rows:
        for segment in CALIBRATION_SEGMENTS:
            value = _text_or_unknown(_segment_key(row, segment))
            bucket = input_segments.get(segment, {}).get(value)
            if bucket is not None:
                bucket["mature_fitted_rows"] += 1


def _mixed_decision_warnings(
    rows: list[dict],
    scanned_profile: dict | None = None,
    input_segments: dict | None = None,
) -> list[str]:
    warnings: list[str] = []
    fields = [
        "endpoint",
        "decision_source",
        "model_version",
        "forecast_horizon",
        "signal_completeness",
        "probability_presence",
    ]
    for field in fields:
        values = set((input_segments or {}).get(field, {}))
        if not values:
            values = {str(row.get(field)) for row in rows if row.get(field) is not None}
        if len(values) > 1:
            warnings.append(f"Calibration input mixes {field}: {sorted(values)[:8]}")
    if any(row.get("provider") == "portfolio_quote_only" for row in rows) or (
        scanned_profile and scanned_profile.get("portfolio_quote_only_rows")
    ):
        warnings.append(
            "portfolio_quote_only rows are reported separately and excluded from model probability calibration"
        )
    if scanned_profile and scanned_profile.get("null_probability_rows"):
        warnings.append(
            "Rows with probability_up=null were scanned but excluded from model calibration"
        )
    if len(rows) < 30:
        warnings.append(
            f"Only {len(rows)} model-probability rows are mature; calibration is diagnostic and must not drive strong/global enforcement"
        )
    elif len(rows) < 100:
        warnings.append(
            f"Only {len(rows)} clean mature rows are available; evidence is early and limited to manually reviewed canary evaluation"
        )
    elif len(rows) < 200:
        warnings.append(
            f"{len(rows)} clean mature rows support serious canary evaluation, but not unreviewed broad calibration rollout"
        )
    mismatch = int((scanned_profile or {}).get("rows_excluded_horizon_mismatch") or 0)
    if mismatch:
        warnings.append(
            f"Excluded {mismatch} probability-event candidates because their recorded forecast horizon did not match the calibration target"
        )
    return warnings


def _is_mature_event(ts: int, *, horizon_days: int, now_ts: int | None = None) -> bool:
    now_value = int(
        now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
    )
    required_age_seconds = int((horizon_days + 2) * 86400)
    return (now_value - int(ts)) >= required_age_seconds


def calibration_rows_from_events(
    events: list[dict],
    *,
    horizon_days: int = 5,
    min_prob: float = 0.0,
    now_ts: int | None = None,
    include_quote_only: bool = False,
    eligibility_diagnostics: dict | None = None,
) -> list[dict]:
    out: list[dict] = []
    lookup_cache: dict[tuple[str, int, int], float | None] = {}
    for event in events:
        payload = _event_payload(event)
        snapshot = _event_snapshot(event)
        provider = _event_provider(event, payload, snapshot)
        signal_completeness = _signal_completeness(payload, snapshot, provider)
        recorded_horizon = _event_forecast_horizon(payload, snapshot)
        target_horizon = f"{int(horizon_days)}d"
        if recorded_horizon == "unknown":
            if eligibility_diagnostics is not None:
                eligibility_diagnostics["rows_excluded_horizon_unknown"] += 1
            continue
        if recorded_horizon != target_horizon:
            if eligibility_diagnostics is not None:
                eligibility_diagnostics["rows_excluded_horizon_mismatch"] += 1
            continue
        if eligibility_diagnostics is not None:
            eligibility_diagnostics["rows_matching_horizon"] += 1
        prob = _probability_up(payload, snapshot)
        if prob is None:
            if eligibility_diagnostics is not None:
                eligibility_diagnostics["rows_excluded_probability_null"] += 1
            continue
        if provider == "portfolio_quote_only" and not include_quote_only:
            if eligibility_diagnostics is not None:
                eligibility_diagnostics["rows_excluded_portfolio_quote_only"] += 1
            continue
        prob_f = float(prob)
        if prob_f < min_prob:
            continue
        symbol = str(event.get("symbol") or "").strip().upper()
        ts = event.get("ts")
        if not symbol or not isinstance(ts, int):
            continue
        if not _is_mature_event(ts, horizon_days=horizon_days, now_ts=now_ts):
            if eligibility_diagnostics is not None:
                eligibility_diagnostics["rows_excluded_immature"] += 1
            continue
        if eligibility_diagnostics is not None:
            eligibility_diagnostics["calibration_eligible_rows"] += 1

        cache_key = (symbol, int(ts), int(horizon_days))
        if cache_key not in lookup_cache:
            lookup_cache[cache_key] = _future_return(symbol, ts, horizon_days)
        future_ret = lookup_cache[cache_key]
        if future_ret is None:
            if eligibility_diagnostics is not None:
                eligibility_diagnostics["rows_excluded_outcome_unavailable"] += 1
            continue
        if eligibility_diagnostics is not None:
            eligibility_diagnostics["mature_fitted_rows"] += 1
        outcome_provider = (
            _outcome_history_download.provider_for_symbol(symbol)
            if _outcome_history_download is not None
            else "test_or_custom"
        )
        out.append(
            {
                "symbol": symbol,
                "ts": ts,
                "predicted": max(0.0, min(1.0, prob_f)),
                "observed": 1.0 if future_ret > 0 else 0.0,
                "endpoint": _text_or_unknown(event.get("endpoint")),
                "decision_source": _text_or_unknown(
                    event.get("decision_source") or payload.get("decision_source")
                ),
                "model_version": _text_or_unknown(
                    payload.get("model_version") or snapshot.get("model_version")
                ),
                "forecast_horizon": recorded_horizon,
                "outcome_provider": outcome_provider,
                "provider": provider,
                "signal_completeness": signal_completeness,
                "probability_presence": "present",
            }
        )
    return out


def calibration_input_profile(events: list[dict], *, horizon_days: int = 5) -> dict:
    profile = {
        "rows_scanned": len(events),
        "null_probability_rows": 0,
        "portfolio_quote_only_rows": 0,
        "full_signal_rows": 0,
        "quote_only_rows": 0,
        "partial_signal_rows": 0,
        "rows_matching_horizon": 0,
        "rows_excluded_horizon_mismatch": 0,
        "rows_excluded_horizon_unknown": 0,
    }
    for event in events:
        payload = _event_payload(event)
        snapshot = _event_snapshot(event)
        provider = _event_provider(event, payload, snapshot)
        signal = _signal_completeness(payload, snapshot, provider)
        recorded_horizon = _event_forecast_horizon(payload, snapshot)
        if recorded_horizon == "unknown":
            profile["rows_excluded_horizon_unknown"] += 1
        elif recorded_horizon == f"{int(horizon_days)}d":
            profile["rows_matching_horizon"] += 1
        else:
            profile["rows_excluded_horizon_mismatch"] += 1
        if _probability_up(payload, snapshot) is None:
            profile["null_probability_rows"] += 1
        if provider == "portfolio_quote_only":
            profile["portfolio_quote_only_rows"] += 1
        profile[f"{signal}_rows"] = int(profile.get(f"{signal}_rows", 0)) + 1
    profile["forecast_horizon"] = f"{int(horizon_days)}d"
    return profile


def segmented_calibration_summaries(rows: list[dict], *, bins: int = 10) -> dict:
    segments: dict[str, dict] = {}
    for segment in CALIBRATION_SEGMENTS:
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            grouped.setdefault(_text_or_unknown(_segment_key(row, segment)), []).append(
                row
            )
        segments[segment] = {
            key: calibration_summary(value, bins=bins)
            for key, value in sorted(grouped.items())
        }
    return segments


def _fit_platt_brier_deltas(
    rows: list[dict], *, steps: int = 600, lr: float = 0.03
) -> tuple[float, float]:
    probs = [min(max(float(r["predicted"]), 1e-6), 1.0 - 1e-6) for r in rows]
    ys = [float(r["observed"]) for r in rows]
    logits = [math.log(p / (1.0 - p)) for p in probs]

    intercept = 0.0
    slope = 1.0
    n = float(len(rows))
    for _ in range(max(100, steps)):
        grad_i = 0.0
        grad_s = 0.0
        for x, y in zip(logits, ys):
            z = intercept + slope * x
            z = max(-35.0, min(35.0, z))
            p_hat = 1.0 / (1.0 + math.exp(-z))
            # d/dz of (p_hat - y)^2 = 2*(p_hat-y)*p_hat*(1-p_hat)
            common = 2.0 * (p_hat - y) * p_hat * (1.0 - p_hat)
            grad_i += common
            grad_s += common * x
        intercept -= lr * (grad_i / n)
        slope -= lr * (grad_s / n)
        slope = max(0.25, min(3.0, slope))

    return intercept, slope - 1.0


def _sigmoid(value: float) -> float:
    clipped = max(-35.0, min(35.0, float(value)))
    return 1.0 / (1.0 + math.exp(-clipped))


def _logit(probability: float) -> float:
    p = min(max(float(probability), 1e-6), 1.0 - 1e-6)
    return math.log(p / (1.0 - p))


def _apply_platt_calibration(
    probability: float, *, intercept: float, slope_delta: float
) -> float:
    slope = max(0.25, min(3.0, 1.0 + float(slope_delta)))
    return _sigmoid(float(intercept) + (slope * _logit(probability)))


def _brier_score(
    rows: list[dict], *, prediction_key: str = "predicted"
) -> float | None:
    if not rows:
        return None
    return sum(
        (float(row[prediction_key]) - float(row["observed"])) ** 2 for row in rows
    ) / len(rows)


def calibration_summary(rows: list[dict], *, bins: int = 10) -> dict:
    if not rows:
        return {
            "rows": 0,
            "mature_fitted_rows": 0,
            "brier_score": None,
            "avg_predicted": None,
            "avg_observed": None,
            "bins": [],
            "recommended": {"intercept_delta": 0.0, "slope_delta": 0.0},
        }

    brier = float(_brier_score(rows) or 0.0)
    avg_pred = sum(r["predicted"] for r in rows) / len(rows)
    avg_obs = sum(r["observed"] for r in rows) / len(rows)

    bucket_rows: list[list[dict]] = [[] for _ in range(max(1, bins))]
    for row in rows:
        idx = min(int(row["predicted"] * bins), bins - 1)
        bucket_rows[idx].append(row)

    bucket_summary: list[dict] = []
    for idx, bucket in enumerate(bucket_rows):
        if not bucket:
            continue
        low = idx / bins
        high = (idx + 1) / bins
        bucket_summary.append(
            {
                "range": [round(low, 2), round(high, 2)],
                "count": len(bucket),
                "avg_predicted": round(
                    sum(r["predicted"] for r in bucket) / len(bucket), 4
                ),
                "avg_observed": round(
                    sum(r["observed"] for r in bucket) / len(bucket), 4
                ),
            }
        )

    base_intercept_delta = math.log(
        min(max(avg_obs, 1e-6), 1.0 - 1e-6)
        / (1.0 - min(max(avg_obs, 1e-6), 1.0 - 1e-6))
    ) - math.log(
        min(max(avg_pred, 1e-6), 1.0 - 1e-6)
        / (1.0 - min(max(avg_pred, 1e-6), 1.0 - 1e-6))
    )
    intercept_delta, slope_delta = _fit_platt_brier_deltas(rows)
    if not math.isfinite(intercept_delta):
        intercept_delta = base_intercept_delta
    if not math.isfinite(slope_delta):
        slope_delta = 0.0

    calibrated_rows = [
        {
            **row,
            "calibrated_predicted": _apply_platt_calibration(
                float(row["predicted"]),
                intercept=intercept_delta,
                slope_delta=slope_delta,
            ),
        }
        for row in rows
    ]
    calibrated_brier = float(
        _brier_score(calibrated_rows, prediction_key="calibrated_predicted") or brier
    )
    effective_brier = min(float(brier), calibrated_brier)

    return {
        "rows": len(rows),
        "mature_fitted_rows": len(rows),
        "brier_score": round(float(brier), 6),
        "brier_score_raw": round(float(brier), 6),
        "calibrated_brier_score": round(float(calibrated_brier), 6),
        "effective_brier_score": round(float(effective_brier), 6),
        "brier_improvement": round(float(brier - effective_brier), 6),
        "avg_predicted": round(float(avg_pred), 6),
        "avg_observed": round(float(avg_obs), 6),
        "bins": bucket_summary,
        "recommended": {
            "intercept_delta": round(float(intercept_delta), 6),
            "slope_delta": round(float(slope_delta), 6),
            "calibrated_brier_score": round(float(calibrated_brier), 6),
            "effective_brier_score": round(float(effective_brier), 6),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build Day-13 deterministic calibration diagnostics report."
    )
    parser.add_argument("--input", default=str(decision_events_log_path()))
    parser.add_argument("--output", default=str(day13_calibration_report_path()))
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--read-cap", type=int, default=50000)
    parser.add_argument("--horizon-days", type=int, default=5)
    parser.add_argument("--bins", type=int, default=10)
    args = parser.parse_args()

    read_cap = max(1, int(args.read_cap))
    read_limit = min(max(1, int(args.limit)), read_cap)
    events = []
    rows: list[dict] = []
    eligibility_diagnostics: dict[str, int] = {}
    while True:
        events = read_decision_events(args.input, limit=read_limit)
        _configure_outcome_history(
            history_eligible_events(events, horizon_days=max(1, args.horizon_days))
        )
        eligibility_diagnostics = {
            "rows_matching_horizon": 0,
            "rows_excluded_horizon_mismatch": 0,
            "rows_excluded_horizon_unknown": 0,
            "rows_excluded_probability_null": 0,
            "rows_excluded_portfolio_quote_only": 0,
            "rows_excluded_immature": 0,
            "rows_excluded_outcome_unavailable": 0,
            "calibration_eligible_rows": 0,
            "mature_fitted_rows": 0,
        }
        rows = calibration_rows_from_events(
            events,
            horizon_days=max(1, args.horizon_days),
            eligibility_diagnostics=eligibility_diagnostics,
        )
        if rows or read_limit >= read_cap:
            break
        read_limit = min(read_limit * 2, read_cap)
    summary = calibration_summary(rows, bins=max(2, args.bins))
    input_profile = calibration_input_profile(
        events, horizon_days=max(1, args.horizon_days)
    )
    input_segments = calibration_input_segments(
        events, horizon_days=max(1, args.horizon_days)
    )
    add_mature_segment_counts(input_segments, rows)
    warnings = _mixed_decision_warnings(rows, input_profile, input_segments)
    payload = {
        "schema_version": "calibration_report.v1",
        "computed_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": args.input,
        "rows_scanned": len(events),
        "scan_limit_used": read_limit,
        "scan_cap": read_cap,
        "horizon_days": max(1, args.horizon_days),
        "requested_calibration_horizon": f"{max(1, args.horizon_days)}d",
        "model_calibration_exclusions": {
            "probability_up_null": "excluded",
            "portfolio_quote_only": "excluded_from_model_calibration_reported_separately",
        },
        "input_profile": input_profile,
        "eligibility_diagnostics": eligibility_diagnostics,
        "input_segments": input_segments,
        "warnings": warnings,
        "segments": segmented_calibration_summaries(rows, bins=max(2, args.bins)),
        **summary,
        **(
            _outcome_history_download.diagnostics_payload()
            if _outcome_history_download is not None
            else {}
        ),
        "calibration_rows_by_outcome_provider": {
            provider: sum(1 for row in rows if row.get("outcome_provider") == provider)
            for provider in sorted({str(row.get("outcome_provider")) for row in rows})
        },
    }
    serialized = json.dumps(payload, indent=2, sort_keys=True)
    print(serialized)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(serialized, encoding="utf-8")


if __name__ == "__main__":
    main()
