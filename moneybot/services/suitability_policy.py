from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .investor_profile import profile_payload


@dataclass(frozen=True)
class UserDecisionContext:
    profile_version: int
    profile_complete: bool
    primary_goal: str
    time_horizon_years: int
    risk_tolerance: str
    loss_capacity_percent: float
    liquidity_need: str
    experience_level: str
    account_type: str
    position_size_limit_percent: float
    sector_limit_percent: float
    excluded_sectors: tuple[str, ...]
    penny_stocks_allowed: bool
    after_hours_alerts: bool
    recommendation_style: str

    @classmethod
    def from_profile(cls, profile: Any) -> "UserDecisionContext":
        payload = profile_payload(profile)
        effective = payload["effective_profile"]
        return cls(
            profile_version=int(payload["profile_version"]),
            profile_complete=bool(payload["profile_complete"]),
            primary_goal=str(effective["primary_goal"]),
            time_horizon_years=int(effective["time_horizon_years"]),
            risk_tolerance=str(effective["risk_tolerance"]),
            loss_capacity_percent=float(effective["loss_capacity_percent"]),
            liquidity_need=str(effective["liquidity_need"]),
            experience_level=str(effective["experience_level"]),
            account_type=str(effective["account_type"]),
            position_size_limit_percent=float(effective["position_size_limit_percent"]),
            sector_limit_percent=float(effective["sector_limit_percent"]),
            excluded_sectors=tuple(str(item).lower() for item in effective["excluded_sectors"]),
            penny_stocks_allowed=bool(effective["penny_stocks_allowed"]),
            after_hours_alerts=bool(effective["after_hours_alerts"]),
            recommendation_style=str(effective["recommendation_style"]),
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "profile_version": self.profile_version,
            "profile_complete": self.profile_complete,
            "primary_goal": self.primary_goal,
            "time_horizon_years": self.time_horizon_years,
            "risk_tolerance": self.risk_tolerance,
            "loss_capacity_percent": self.loss_capacity_percent,
            "liquidity_need": self.liquidity_need,
            "experience_level": self.experience_level,
            "account_type": self.account_type,
            "position_size_limit_percent": self.position_size_limit_percent,
            "sector_limit_percent": self.sector_limit_percent,
            "excluded_sectors": list(self.excluded_sectors),
            "penny_stocks_allowed": self.penny_stocks_allowed,
            "after_hours_alerts": self.after_hours_alerts,
            "recommendation_style": self.recommendation_style,
        }


@dataclass(frozen=True)
class SuitabilityDecision:
    base_action: str
    action: str
    changed: bool
    applied_rules: tuple[dict[str, Any], ...]
    profile_version: int
    profile_complete: bool

    def payload(self) -> dict[str, Any]:
        return {
            "base_action": self.base_action,
            "action": self.action,
            "changed": self.changed,
            "applied_rules": [dict(rule) for rule in self.applied_rules],
            "profile_version": self.profile_version,
            "profile_complete": self.profile_complete,
        }


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _rule(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details}


def apply_suitability_policy(
    *,
    base_action: str,
    context: UserDecisionContext,
    symbol: str,
    current_price: float | None = None,
    probability_up: float | None = None,
    confidence: float | None = None,
    position_weight_percent: float | None = None,
    sector: str | None = None,
    sector_weight_percent: float | None = None,
    security_attributes: Mapping[str, Any] | None = None,
) -> SuitabilityDecision:
    """Translate an objective market action into a profile-suitable action.

    This policy intentionally only softens BUY to HOLD. It does not create a BUY or
    SELL, and it never changes the underlying forecast score or probability.
    """

    base = str(base_action or "HOLD").strip().upper()
    if base not in {"BUY", "HOLD", "SELL"}:
        base = "HOLD"
    action = base
    rules: list[dict[str, Any]] = []

    if base != "BUY":
        return SuitabilityDecision(
            base_action=base,
            action=action,
            changed=False,
            applied_rules=tuple(rules),
            profile_version=context.profile_version,
            profile_complete=context.profile_complete,
        )

    attributes = dict(security_attributes or {})
    price = _number(current_price)
    probability = _number(probability_up)
    confidence_value = _number(confidence)
    if confidence_value is not None and confidence_value > 1:
        confidence_value /= 100.0
    forecast_confidence = probability if probability is not None else confidence_value

    penny_stock = bool(attributes.get("penny_stock")) or (price is not None and 0 < price < 5)
    if penny_stock and not context.penny_stocks_allowed:
        action = "HOLD"
        rules.append(_rule(
            "penny_stocks_disabled",
            f"{symbol.upper()} is below the profile's penny-stock guardrail, so BUY was softened to HOLD.",
            current_price=price,
        ))

    normalized_sector = str(sector or "").strip().lower()
    if normalized_sector and normalized_sector in context.excluded_sectors:
        action = "HOLD"
        rules.append(_rule(
            "excluded_sector",
            f"The {normalized_sector} sector is excluded by this investor profile.",
            sector=normalized_sector,
        ))

    position_weight = _number(position_weight_percent)
    if position_weight is not None and position_weight >= context.position_size_limit_percent:
        action = "HOLD"
        rules.append(_rule(
            "position_limit_reached",
            "The position is already at or above the profile's single-position limit.",
            position_weight_percent=round(position_weight, 4),
            limit_percent=context.position_size_limit_percent,
        ))

    sector_weight = _number(sector_weight_percent)
    if sector_weight is not None and sector_weight >= context.sector_limit_percent:
        action = "HOLD"
        rules.append(_rule(
            "sector_limit_reached",
            "The sector is already at or above the profile's concentration limit.",
            sector_weight_percent=round(sector_weight, 4),
            limit_percent=context.sector_limit_percent,
        ))

    required_confidence = 0.0
    confidence_reasons: list[str] = []
    if context.risk_tolerance == "conservative":
        required_confidence = max(required_confidence, 0.70)
        confidence_reasons.append("conservative risk tolerance")
    if context.liquidity_need == "high" and context.time_horizon_years <= 3:
        required_confidence = max(required_confidence, 0.75)
        confidence_reasons.append("high liquidity need and short horizon")
    if context.recommendation_style == "conservative":
        required_confidence = max(required_confidence, 0.70)
        confidence_reasons.append("conservative recommendation style")
    if context.experience_level == "beginner":
        required_confidence = max(required_confidence, 0.68)
        confidence_reasons.append("beginner experience level")
    if context.primary_goal == "preservation":
        required_confidence = max(required_confidence, 0.72)
        confidence_reasons.append("capital-preservation goal")
    if context.loss_capacity_percent <= 10:
        required_confidence = max(required_confidence, 0.75)
        confidence_reasons.append("low loss capacity")

    if required_confidence and (forecast_confidence is None or forecast_confidence < required_confidence):
        action = "HOLD"
        rules.append(_rule(
            "confidence_below_profile_threshold",
            "The forecast confidence does not clear this profile's BUY threshold.",
            forecast_confidence=forecast_confidence,
            required_confidence=required_confidence,
            reasons=confidence_reasons,
        ))

    return SuitabilityDecision(
        base_action=base,
        action=action,
        changed=action != base,
        applied_rules=tuple(rules),
        profile_version=context.profile_version,
        profile_complete=context.profile_complete,
    )
