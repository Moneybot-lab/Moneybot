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

POLICY_SCHEMA_VERSION = "suitability.v1"


@dataclass(frozen=True)
class PersonalizedDecision:
    base_action: str
    policy_action: str
    action: str
    enforcement_mode: str
    cohort: str
    policy_schema_version: str
    forecast_horizon: str
    suitability: SuitabilityDecision

    def payload(self) -> dict[str, Any]:
        return {
            "base_action": self.base_action,
            "policy_action": self.policy_action,
            "action": self.action,
            "changed": self.action != self.base_action,
            "would_change": self.policy_action != self.base_action,
            "enforcement_mode": self.enforcement_mode,
            "cohort": self.cohort,
            "policy_schema_version": self.policy_schema_version,
            "forecast_horizon": self.forecast_horizon,
            "profile_version": self.suitability.profile_version,
            "profile_complete": self.suitability.profile_complete,
            "applied_rules": [dict(rule) for rule in self.suitability.applied_rules],
        }


class PersonalizationMetrics:
    def __init__(self) -> None:
        self.evaluations = 0
        self.enforced_overrides = 0
        self.shadow_overrides = 0
        self.rule_counts: dict[str, int] = {}
        self.mode_counts: dict[str, int] = {}
        self.last_actions: dict[str, str] = {}
        self.action_changes = 0

    def record(self, *, user_id: int | None, endpoint: str, symbol: str, decision: PersonalizedDecision) -> None:
        self.evaluations += 1
        self.mode_counts[decision.enforcement_mode] = self.mode_counts.get(decision.enforcement_mode, 0) + 1
        if decision.action != decision.base_action:
            self.enforced_overrides += 1
        elif decision.policy_action != decision.base_action:
            self.shadow_overrides += 1
        for rule in decision.suitability.applied_rules:
            code = str(rule.get("code") or "unknown")
            self.rule_counts[code] = self.rule_counts.get(code, 0) + 1
        key = f"{user_id if user_id is not None else 'anonymous'}:{endpoint}:{symbol.upper()}"
        previous = self.last_actions.get(key)
        if previous is not None and previous != decision.action:
            self.action_changes += 1
        self.last_actions[key] = decision.action

    def snapshot(self) -> dict[str, Any]:
        return {
            "evaluations": self.evaluations,
            "enforced_overrides": self.enforced_overrides,
            "shadow_overrides": self.shadow_overrides,
            "rule_counts": dict(self.rule_counts),
            "mode_counts": dict(self.mode_counts),
            "recommendation_churn_count": self.action_changes,
        }


class PersonalizationRuntime:
    def __init__(
        self,
        *,
        profile_enabled: bool = True,
        policy_enabled: bool = True,
        mode: str = "enforce",
        rollout_percentage: float = 100.0,
        rollout_seed: str = "moneybot-profile",
        allowlist: set[int] | None = None,
        metrics: PersonalizationMetrics | None = None,
    ) -> None:
        normalized_mode = str(mode or "enforce").strip().lower()
        self.profile_enabled = bool(profile_enabled)
        self.policy_enabled = bool(policy_enabled)
        self.mode = normalized_mode if normalized_mode in {"off", "shadow", "enforce"} else "off"
        self.rollout_percentage = max(0.0, min(100.0, float(rollout_percentage)))
        self.rollout_seed = str(rollout_seed or "moneybot-profile")
        self.allowlist = set(allowlist or set())
        self.metrics = metrics or PersonalizationMetrics()

    def cohort_for_user(self, user_id: int | None) -> str:
        if not self.profile_enabled or not self.policy_enabled or self.mode == "off" or user_id is None:
            return "off"
        if user_id in self.allowlist:
            return "allowlist"
        import hashlib

        digest = hashlib.sha256(f"{self.rollout_seed}:{user_id}".encode("utf-8")).hexdigest()
        bucket = int(digest[:8], 16) / 0xFFFFFFFF * 100.0
        return "rollout" if bucket < self.rollout_percentage else "control"

    def evaluate(
        self,
        *,
        user_id: int | None,
        context: UserDecisionContext,
        endpoint: str,
        symbol: str,
        base_action: str,
        forecast_horizon: str,
        **policy_inputs: Any,
    ) -> PersonalizedDecision:
        if self.profile_enabled and self.policy_enabled:
            suitability = apply_suitability_policy(
                base_action=base_action,
                context=context,
                symbol=symbol,
                **policy_inputs,
            )
        else:
            normalized_action = str(base_action or "HOLD").strip().upper()
            suitability = SuitabilityDecision(
                base_action=normalized_action,
                action=normalized_action,
                changed=False,
                applied_rules=(),
                profile_version=context.profile_version,
                profile_complete=context.profile_complete,
            )
        cohort = self.cohort_for_user(user_id)
        effective_mode = self.mode if cohort in {"allowlist", "rollout"} else "off"
        action = suitability.action if effective_mode == "enforce" else suitability.base_action
        decision = PersonalizedDecision(
            base_action=suitability.base_action,
            policy_action=suitability.action,
            action=action,
            enforcement_mode=effective_mode,
            cohort=cohort,
            policy_schema_version=POLICY_SCHEMA_VERSION,
            forecast_horizon=forecast_horizon,
            suitability=suitability,
        )
        self.metrics.record(user_id=user_id, endpoint=endpoint, symbol=symbol, decision=decision)
        return decision
