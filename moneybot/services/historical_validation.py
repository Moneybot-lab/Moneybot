from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = "historical_validation.v1"
MANIFEST_SCHEMA_VERSION = "dataset_manifest.v1"


@dataclass(frozen=True)
class DatasetManifest:
    dataset_id: str
    source: str
    rows: int
    schema_version: str = MANIFEST_SCHEMA_VERSION
    data_schema_version: str = "market-data.v1"
    created_at_utc: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    adjustment_method: str = "split_dividend_adjusted"
    point_in_time: bool = True
    includes_delisted: bool = False
    checksum: str | None = None
    notes: tuple[str, ...] = ()

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        data["notes"] = list(self.notes)
        return data


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _action(row: Mapping[str, Any]) -> str:
    return str(row.get("action") or row.get("recommendation") or row.get("advice") or "").strip().upper()


def _is_positive_action(action: str) -> bool:
    return action in {"BUY", "STRONG BUY"}


def _is_negative_action(action: str) -> bool:
    return action in {"SELL", "HOLD OFF FOR NOW"}


def _return_for(row: Mapping[str, Any], horizon: str) -> float | None:
    return _num(row.get(f"return_{horizon}"))


def _observed_label(action: str, realized_return: float | None) -> int | None:
    if realized_return is None:
        return None
    if _is_positive_action(action):
        return 1 if realized_return > 0 else 0
    if _is_negative_action(action):
        return 1 if realized_return <= 0 else 0
    return None


def _probability(row: Mapping[str, Any]) -> float | None:
    from .prediction_evidence import probability
    # No percent conversion, clamping, or fallback across prediction contracts.
    return probability(row.get("probability_up"))


def _brier(pairs: Iterable[tuple[float, int]]) -> float | None:
    values = [(prob - observed) ** 2 for prob, observed in pairs]
    return round(mean(values), 6) if values else None


def _ece(pairs: list[tuple[float, int]], *, buckets: int = 10) -> float | None:
    if not pairs:
        return None
    bucketed: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for prob, observed in pairs:
        bucket = min(buckets - 1, int(prob * buckets))
        bucketed[bucket].append((prob, observed))
    total = len(pairs)
    error = 0.0
    for values in bucketed.values():
        confidence = mean(prob for prob, _ in values)
        accuracy = mean(observed for _, observed in values)
        error += (len(values) / total) * abs(confidence - accuracy)
    return round(error, 6)


def build_dataset_manifest(
    *,
    dataset_id: str,
    source: str,
    rows: Iterable[Mapping[str, Any]],
    start_date: str | None = None,
    end_date: str | None = None,
    adjustment_method: str = "split_dividend_adjusted",
    point_in_time: bool = True,
    includes_delisted: bool = False,
    notes: Iterable[str] = (),
) -> DatasetManifest:
    materialized = [dict(row) for row in rows]
    canonical = json.dumps(materialized, sort_keys=True, separators=(",", ":"), default=str)
    checksum = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return DatasetManifest(
        dataset_id=dataset_id,
        source=source,
        rows=len(materialized),
        created_at_utc=_utc_now_iso(),
        start_date=start_date,
        end_date=end_date,
        adjustment_method=adjustment_method,
        point_in_time=point_in_time,
        includes_delisted=includes_delisted,
        checksum=checksum,
        notes=tuple(str(note) for note in notes),
    )


def summarize_validation_rows(rows: Iterable[Mapping[str, Any]], *, horizon: str = "5d") -> dict[str, Any]:
    """Streaming metrics with on-disk per-symbol churn state and fixed ECE bins."""
    import sqlite3
    import tempfile
    from pathlib import Path

    count = Counter()
    sums = Counter()
    bins = [[0, 0.0, 0.0] for _ in range(10)]
    by_profile, by_source = Counter(), Counter()
    worst_return = worst_adverse = None
    with tempfile.TemporaryDirectory(prefix="moneybot-validation-") as directory:
        db = sqlite3.connect(Path(directory) / "churn.sqlite")
        db.execute("PRAGMA cache_size=-1024")
        db.execute("CREATE TABLE last_action (symbol TEXT PRIMARY KEY, action TEXT)")
        try:
            for row in rows:
                count["input"] += 1
                action, realized = _action(row), _return_for(row, horizon)
                if realized is None or action not in {"BUY", "STRONG BUY", "SELL", "HOLD OFF FOR NOW", "HOLD"}:
                    continue
                if row.get("forecast_horizon", horizon) != horizon:
                    continue
                count["evaluated"] += 1
                exposure = -1 if _is_negative_action(action) else (0 if action == "HOLD" else 1)
                directional = realized * exposure
                sums["underlying"] += realized
                sums["return"] += directional
                cost = _num(row.get("transaction_cost_bps"))
                if cost is None:
                    cost = _num(row.get("estimated_friction_bps")) or 0.0
                sums["net"] += directional - cost / 10000
                worst_return = directional if worst_return is None else min(worst_return, directional)
                mae = _num(row.get("max_adverse_excursion"))
                if mae is not None:
                    count["adverse"] += 1
                    sums["adverse"] += mae
                    worst_adverse = mae if worst_adverse is None else min(worst_adverse, mae)
                p = _probability(row)
                label = int(realized > 0)
                if p is not None:
                    count["calibration"] += 1
                    sums["brier"] += (p - label) ** 2
                    bucket = bins[min(9, int(p * 10))]
                    bucket[0] += 1
                    bucket[1] += p
                    bucket[2] += label
                if _is_positive_action(action):
                    count["positive"] += 1
                    count["positive_correct"] += label
                if _is_negative_action(action):
                    count["negative"] += 1
                    count["negative_correct"] += 1 - label
                count["positive_true"] += label
                count["negative_true"] += 1 - label
                market = row.get("market_data") if isinstance(row.get("market_data"), Mapping) else {}
                mode = str(row.get("source_mode") or market.get("source_mode") or market.get("quote_source_mode") or "unknown")
                by_source[mode if mode in {"rest", "websocket", "streaming", "rest_fallback", "fallback"} else "unknown"] += 1
                stale = [row.get("is_stale"), market.get("is_stale"), market.get("quote_is_stale"), market.get("quote_stale")]
                count["stale"] += any(value is True for value in stale)
                count["unknown_freshness"] += not any(isinstance(value, bool) for value in stale)
                count["delayed"] += row.get("is_delayed") is True
                count["fallback"] += "fallback" in mode.lower() or row.get("is_degraded") is True or market.get("is_degraded") is True
                personalization = row.get("personalization") if isinstance(row.get("personalization"), Mapping) else {}
                base = str(row.get("base_action") or personalization.get("base_action") or "").upper()
                final = str(personalization.get("action") or row.get("action") or "").upper()
                count["override"] += bool(base and final and base != final)
                profile = str(row.get("risk_tolerance") or personalization.get("risk_tolerance") or personalization.get("profile_bucket") or "unknown")
                by_profile[profile if profile in {"conservative", "moderate", "aggressive"} else "unknown"] += 1
                symbol = str(row.get("symbol") or "").upper()
                if symbol:
                    previous = db.execute("SELECT action FROM last_action WHERE symbol=?", (symbol,)).fetchone()
                    if previous:
                        count["transitions"] += 1
                        count["changes"] += previous[0] != action
                    db.execute("INSERT OR REPLACE INTO last_action VALUES (?, ?)", (symbol, action))
        finally:
            db.close()
    def ratio(value, denominator, default=None):
        return round(value / denominator, 6) if denominator else default
    n = count["evaluated"]
    c = count["calibration"]
    return {
        "schema_version": "historical_validation_metrics.v1", "input_rows": count["input"],
        "evaluated_rows": n, "horizon": horizon,
        "brier_score": ratio(sums["brier"], c),
        "calibration_error": ratio(sum(abs(b[1] - b[2]) for b in bins), c),
        "calibration_rows": c, "calibration_status": "available" if c else "insufficient_evidence",
        "buy_precision": ratio(count["positive_correct"], count["positive"]),
        "buy_recall": ratio(count["positive_correct"], count["positive_true"]),
        "sell_precision": ratio(count["negative_correct"], count["negative"]),
        "sell_recall": ratio(count["negative_correct"], count["negative_true"]),
        "directional_accuracy": ratio(count["positive_correct"] + count["negative_correct"], count["positive"] + count["negative"]),
        "avg_return": ratio(sums["return"], n), "avg_underlying_return": ratio(sums["underlying"], n),
        "avg_net_return": ratio(sums["net"], n), "worst_return": round(worst_return, 6) if worst_return is not None else None,
        "avg_max_adverse_excursion": ratio(sums["adverse"], count["adverse"]), "worst_max_adverse_excursion": worst_adverse,
        "recommendation_churn_rate": ratio(count["changes"], count["transitions"], 0.0),
        "profile_override_rate": ratio(count["override"], n, 0.0), "stale_data_rate": ratio(count["stale"], n, 0.0),
        "unknown_freshness_rows": count["unknown_freshness"], "delayed_rows": count["delayed"],
        "fallback_rate": ratio(count["fallback"], n, 0.0), "by_profile": dict(by_profile), "by_source_mode": dict(by_source),
    }


def evaluate_promotion_gates(
    metrics: Mapping[str, Any],
    *,
    baseline_metrics: Mapping[str, Any] | None = None,
    min_rows: int = 30,
    max_brier_score: float = 0.26,
    max_brier_regression: float = 0.01,
    min_avg_net_return: float = 0.0,
    max_churn_rate: float = 0.35,
    max_stale_data_rate: float = 0.05,
    max_fallback_rate: float = 0.20,
    max_adverse_excursion_regression: float = 0.02,
    licensing_review_complete: bool = False,
    privacy_review_complete: bool = False,
) -> dict[str, Any]:
    baseline = baseline_metrics or {}
    gates: list[dict[str, Any]] = []

    def add(name: str, passed: bool, *, actual: Any, limit: Any, severity: str = "blocker", detail: str = "") -> None:
        gates.append({"name": name, "passed": bool(passed), "actual": actual, "limit": limit, "severity": severity, "detail": detail})

    rows = int(metrics.get("evaluated_rows") or 0)
    add("minimum_evaluated_rows", rows >= min_rows, actual=rows, limit=min_rows)

    brier = _num(metrics.get("brier_score"))
    baseline_brier = _num(baseline.get("brier_score"))
    brier_ok = brier is not None and brier <= max_brier_score
    if baseline_brier is not None and brier is not None:
        brier_ok = brier_ok and brier <= baseline_brier + max_brier_regression
    add("calibration_brier", brier_ok, actual=brier, limit={"absolute": max_brier_score, "baseline_regression": max_brier_regression})

    net_return = _num(metrics.get("avg_net_return"))
    add("average_net_return", net_return is not None and net_return >= min_avg_net_return, actual=net_return, limit=min_avg_net_return)

    churn = _num(metrics.get("recommendation_churn_rate")) or 0.0
    add("recommendation_churn", churn <= max_churn_rate, actual=churn, limit=max_churn_rate)

    stale = _num(metrics.get("stale_data_rate")) or 0.0
    add("stale_data_rate", stale <= max_stale_data_rate, actual=stale, limit=max_stale_data_rate)

    fallback = _num(metrics.get("fallback_rate")) or 0.0
    add("fallback_rate", fallback <= max_fallback_rate, actual=fallback, limit=max_fallback_rate)

    adverse = _num(metrics.get("worst_max_adverse_excursion"))
    baseline_adverse = _num(baseline.get("worst_max_adverse_excursion"))
    if adverse is not None and baseline_adverse is not None:
        adverse_ok = adverse >= baseline_adverse - max_adverse_excursion_regression
        add("adverse_excursion_regression", adverse_ok, actual=adverse, limit={"baseline": baseline_adverse, "allowed_regression": max_adverse_excursion_regression})
    else:
        add("adverse_excursion_regression", True, actual=adverse, limit="baseline unavailable", severity="warning", detail="Skipped because baseline or candidate MAE is unavailable.")

    add("licensing_review", licensing_review_complete, actual=licensing_review_complete, limit=True)
    add("privacy_review", privacy_review_complete, actual=privacy_review_complete, limit=True)

    blockers = [gate for gate in gates if not gate["passed"] and gate.get("severity") == "blocker"]
    warnings = [gate for gate in gates if not gate["passed"] and gate.get("severity") != "blocker"]
    return {
        "schema_version": "promotion_gates.v1",
        "promotion_ready": not blockers,
        "status": "pass" if not blockers else "blocked",
        "failed_blockers": len(blockers),
        "warnings": len(warnings),
        "gates": gates,
    }


def build_historical_validation_report(
    *,
    rows: Iterable[Mapping[str, Any]],
    dataset_manifest: DatasetManifest | Mapping[str, Any] | None = None,
    baseline_metrics: Mapping[str, Any] | None = None,
    calibration_report: Mapping[str, Any] | None = None,
    stream_health: Mapping[str, Any] | None = None,
    provider_health: Mapping[str, Any] | None = None,
    profile_metrics: Mapping[str, Any] | None = None,
    gate_options: Mapping[str, Any] | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    metrics = summarize_validation_rows(rows)
    if calibration_report:
        report_brier = _num(calibration_report.get("effective_brier_score") or calibration_report.get("calibrated_brier_score") or calibration_report.get("brier_score"))
        if report_brier is not None:
            metrics["external_calibration_brier_score"] = report_brier
        if calibration_report.get("rows") is not None:
            metrics["external_calibration_rows"] = calibration_report.get("rows")
    gates = evaluate_promotion_gates(metrics, baseline_metrics=baseline_metrics, **dict(gate_options or {}))
    manifest_payload = dataset_manifest.payload() if isinstance(dataset_manifest, DatasetManifest) else (dict(dataset_manifest) if isinstance(dataset_manifest, Mapping) else None)
    computed_at = generated_at_utc or _utc_now_iso()
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": computed_at,
        "computed_at_utc": computed_at,
        "dataset_manifest": manifest_payload,
        "metrics": metrics,
        "baseline_metrics": dict(baseline_metrics or {}),
        "promotion_gates": gates,
        "rollout_recommendation": "promote" if gates["promotion_ready"] else "hold_shadow",
        "stream_health": dict(stream_health or {}),
        "provider_health": dict(provider_health or {}),
        "profile_metrics": dict(profile_metrics or {}),
        "required_next_steps": [] if gates["promotion_ready"] else [gate["name"] for gate in gates["gates"] if not gate["passed"] and gate.get("severity") == "blocker"],
    }
