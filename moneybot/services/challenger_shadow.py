from __future__ import annotations

from typing import Any


def promising_shadow_candidates(backtest_report: dict[str, Any], *, limit: int = 3) -> list[dict[str, Any]]:
    """Return gate-cleared challengers to shadow-log, never route live."""
    ranked = list(backtest_report.get("ranked_model_versions") or [])
    by_version = {str(item.get("model_version")): item for item in backtest_report.get("challengers") or [] if isinstance(item, dict)}
    selected: list[dict[str, Any]] = []
    for version in ranked:
        item = by_version.get(str(version))
        if not item:
            continue
        gates = item.get("promotion_gates") if isinstance(item.get("promotion_gates"), dict) else {}
        if gates.get("promotion_ready") is True and item.get("routing_allowed") is False:
            selected.append(item)
        if len(selected) >= max(1, limit):
            break
    return selected


def log_challenger_shadow_decisions(
    *,
    decision_logger: Any,
    endpoint: str,
    symbol: str,
    production_payload: dict[str, Any],
    challenger_predictions: list[dict[str, Any]],
) -> int:
    """Append challenger shadow records beside production decisions without changing responses."""
    if decision_logger is None:
        return 0
    logged = 0
    for prediction in challenger_predictions:
        model_version = str(prediction.get("model_version") or "").strip()
        if not model_version:
            continue
        decision_logger.log(
            endpoint=f"{endpoint}_challenger_shadow",
            symbol=symbol,
            decision_source="offline_challenger_shadow",
            payload={
                "shadow_only": True,
                "routing_allowed": False,
                "production_recommendation": production_payload.get("recommendation"),
                "challenger_recommendation": prediction.get("recommendation"),
                "model_version": model_version,
                "probability_up": prediction.get("probability_up"),
                "backtest_report_version": prediction.get("backtest_report_version"),
            },
            experiment={
                "mode": "challenger_shadow",
                "routing_allowed": False,
                "promotion_required_before_routing": True,
            },
        )
        logged += 1
    return logged
