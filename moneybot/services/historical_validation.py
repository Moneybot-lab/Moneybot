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
    value = _num(row.get("probability_up"))
    if value is None:
        payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
        value = _num(payload.get("probability_up"))
    if value is None:
        return None
    if value > 1:
        value = value / 100
    return max(0.0, min(1.0, value))


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
    materialized = [dict(row) for row in rows]
    evaluated: list[dict[str, Any]] = []
    calibration_pairs: list[tuple[float, int]] = []
    positive_predictions = 0
    positive_true = 0
    positive_correct = 0
    negative_predictions = 0
    negative_true = 0
    negative_correct = 0
    returns: list[float] = []
    underlying_returns: list[float] = []
    net_returns: list[float] = []
    adverse: list[float] = []
    stale_count = 0
    fallback_count = 0
    override_count = 0
    by_profile: Counter[str] = Counter()
    by_source: Counter[str] = Counter()
    symbol_actions: dict[str, list[str]] = defaultdict(list)

    for row in materialized:
        action = _action(row)
        realized = _return_for(row, horizon)
        observed = _observed_label(action, realized)
        if observed is None:
            continue
        evaluated.append(row)
        underlying_return = float(realized or 0.0)
        directional_return = -underlying_return if _is_negative_action(action) else underlying_return
        underlying_returns.append(underlying_return)
        returns.append(directional_return)
        cost_bps = _num(row.get("transaction_cost_bps")) or _num(row.get("estimated_friction_bps")) or 0.0
        net_returns.append(directional_return - (cost_bps / 10_000))
        mae = _num(row.get("max_adverse_excursion"))
        if mae is not None:
            adverse.append(mae)
        probability = _probability(row)
        if probability is not None:
            calibration_pairs.append((probability, 1 if underlying_return > 0 else 0))
        if _is_positive_action(action):
            positive_predictions += 1
            positive_correct += observed
        if _is_negative_action(action):
            negative_predictions += 1
            negative_correct += observed
        if realized is not None and realized > 0:
            positive_true += 1
        if realized is not None and realized <= 0:
            negative_true += 1
        market_data = row.get("market_data") if isinstance(row.get("market_data"), Mapping) else {}
        source_mode = str(row.get("source_mode") or market_data.get("source_mode") or market_data.get("quote_source_mode") or "unknown")
        by_source[source_mode] += 1
        if bool(row.get("is_stale") or market_data.get("is_stale") or market_data.get("quote_stale")):
            stale_count += 1
        if "fallback" in source_mode.lower() or bool(row.get("is_degraded") or market_data.get("is_degraded")):
            fallback_count += 1
        personalization = row.get("personalization") if isinstance(row.get("personalization"), Mapping) else {}
        base_action = str(row.get("base_action") or personalization.get("base_action") or "").upper()
        final_action = str(personalization.get("action") or row.get("action") or "").upper()
        if base_action and final_action and base_action != final_action:
            override_count += 1
        profile_bucket = str(row.get("risk_tolerance") or personalization.get("risk_tolerance") or personalization.get("profile_bucket") or "unknown")
        by_profile[profile_bucket] += 1
        symbol = str(row.get("symbol") or "").upper()
        if symbol and action:
            symbol_actions[symbol].append(action)

    churn_transitions = 0
    churn_changes = 0
    for actions in symbol_actions.values():
        for previous, current in zip(actions, actions[1:]):
            churn_transitions += 1
            if previous != current:
                churn_changes += 1

    evaluated_count = len(evaluated)
    return {
        "schema_version": "historical_validation_metrics.v1",
        "input_rows": len(materialized),
        "evaluated_rows": evaluated_count,
        "horizon": horizon,
        "brier_score": _brier(calibration_pairs),
        "calibration_error": _ece(calibration_pairs),
        "calibration_rows": len(calibration_pairs),
        "buy_precision": round(positive_correct / positive_predictions, 6) if positive_predictions else None,
        "buy_recall": round(positive_correct / positive_true, 6) if positive_true else None,
        "sell_precision": round(negative_correct / negative_predictions, 6) if negative_predictions else None,
        "sell_recall": round(negative_correct / negative_true, 6) if negative_true else None,
        "avg_return": round(mean(returns), 6) if returns else None,
        "avg_underlying_return": round(mean(underlying_returns), 6) if underlying_returns else None,
        "avg_net_return": round(mean(net_returns), 6) if net_returns else None,
        "worst_return": round(min(returns), 6) if returns else None,
        "avg_max_adverse_excursion": round(mean(adverse), 6) if adverse else None,
        "worst_max_adverse_excursion": round(min(adverse), 6) if adverse else None,
        "recommendation_churn_rate": round(churn_changes / churn_transitions, 6) if churn_transitions else 0.0,
        "profile_override_rate": round(override_count / evaluated_count, 6) if evaluated_count else 0.0,
        "stale_data_rate": round(stale_count / evaluated_count, 6) if evaluated_count else 0.0,
        "fallback_rate": round(fallback_count / evaluated_count, 6) if evaluated_count else 0.0,
        "by_profile": dict(by_profile),
        "by_source_mode": dict(by_source),
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
