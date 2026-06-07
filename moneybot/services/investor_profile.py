from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any


PROFILE_CHOICE_FIELDS: dict[str, set[str]] = {
    "primary_goal": {"growth", "income", "preservation", "speculation", "learning"},
    "risk_tolerance": {"conservative", "moderate", "aggressive"},
    "liquidity_need": {"low", "medium", "high"},
    "experience_level": {"beginner", "intermediate", "advanced"},
    "account_type": {"taxable", "ira", "roth_ira", "paper", "other"},
    "recommendation_style": {"conservative", "balanced", "opportunity_seeking"},
}
PROFILE_INTEGER_FIELDS: dict[str, tuple[int, int]] = {
    "time_horizon_years": (1, 50),
}
PROFILE_DECIMAL_FIELDS: dict[str, tuple[Decimal, Decimal]] = {
    "loss_capacity_percent": (Decimal("1"), Decimal("100")),
    "position_size_limit_percent": (Decimal("1"), Decimal("100")),
    "sector_limit_percent": (Decimal("1"), Decimal("100")),
}
PROFILE_BOOLEAN_FIELDS = {"penny_stocks_allowed", "after_hours_alerts"}
PROFILE_LIST_FIELDS = {"excluded_sectors"}
PROFILE_MUTABLE_FIELDS = (
    set(PROFILE_CHOICE_FIELDS)
    | set(PROFILE_INTEGER_FIELDS)
    | set(PROFILE_DECIMAL_FIELDS)
    | PROFILE_BOOLEAN_FIELDS
    | PROFILE_LIST_FIELDS
)
PROFILE_REQUIRED_FIELDS = {
    "primary_goal",
    "time_horizon_years",
    "risk_tolerance",
    "loss_capacity_percent",
    "liquidity_need",
    "experience_level",
    "account_type",
}

SAFE_EFFECTIVE_DEFAULTS: dict[str, Any] = {
    "primary_goal": "preservation",
    "time_horizon_years": 1,
    "risk_tolerance": "conservative",
    "loss_capacity_percent": 10.0,
    "liquidity_need": "high",
    "experience_level": "beginner",
    "account_type": "other",
    "position_size_limit_percent": 5.0,
    "sector_limit_percent": 20.0,
    "excluded_sectors": [],
    "penny_stocks_allowed": False,
    "after_hours_alerts": False,
    "recommendation_style": "conservative",
}


class InvestorProfileValidationError(ValueError):
    def __init__(self, errors: dict[str, str]):
        super().__init__("invalid investor profile")
        self.errors = errors


def _decimal_to_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _excluded_sectors_from_csv(raw: str | None) -> list[str]:
    return [item for item in str(raw or "").split(",") if item]


def stored_profile_values(profile: Any) -> dict[str, Any]:
    return {
        "primary_goal": profile.primary_goal,
        "time_horizon_years": profile.time_horizon_years,
        "risk_tolerance": profile.risk_tolerance,
        "loss_capacity_percent": _decimal_to_float(profile.loss_capacity_percent),
        "liquidity_need": profile.liquidity_need,
        "experience_level": profile.experience_level,
        "account_type": profile.account_type,
        "position_size_limit_percent": _decimal_to_float(profile.position_size_limit_percent),
        "sector_limit_percent": _decimal_to_float(profile.sector_limit_percent),
        "excluded_sectors": _excluded_sectors_from_csv(profile.excluded_sectors_csv),
        "penny_stocks_allowed": profile.penny_stocks_allowed,
        "after_hours_alerts": profile.after_hours_alerts,
        "recommendation_style": profile.recommendation_style,
    }


def missing_profile_fields(values: dict[str, Any]) -> list[str]:
    return sorted(field for field in PROFILE_REQUIRED_FIELDS if values.get(field) is None)


def profile_payload(profile: Any) -> dict[str, Any]:
    values = stored_profile_values(profile)
    missing_fields = missing_profile_fields(values)
    effective = {
        field: values.get(field) if values.get(field) is not None else default
        for field, default in SAFE_EFFECTIVE_DEFAULTS.items()
    }
    return {
        **values,
        "profile_version": profile.profile_version,
        "profile_complete": not missing_fields,
        "missing_fields": missing_fields,
        "effective_profile": effective,
        "questionnaire_completed_at": (
            profile.questionnaire_completed_at.isoformat() if profile.questionnaire_completed_at else None
        ),
        "created_at": profile.created_at.isoformat(),
        "updated_at": profile.updated_at.isoformat(),
    }


def revision_payload(revision: Any) -> dict[str, Any]:
    return {
        "id": revision.id,
        "profile_version": revision.profile_version,
        "previous_profile": json.loads(revision.previous_profile_json),
        "new_profile": json.loads(revision.new_profile_json),
        "change_reason": revision.change_reason,
        "source": revision.source,
        "created_at": revision.created_at.isoformat(),
    }


def _normalized_nullable_string(field: str, raw: Any, errors: dict[str, str]) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        errors[field] = "must be a string or null"
        return None
    value = raw.strip().lower().replace("-", "_").replace(" ", "_")
    if value not in PROFILE_CHOICE_FIELDS[field]:
        allowed = ", ".join(sorted(PROFILE_CHOICE_FIELDS[field]))
        errors[field] = f"must be one of: {allowed}"
        return None
    return value


def validate_profile_updates(data: dict[str, Any]) -> dict[str, Any]:
    errors: dict[str, str] = {}
    updates: dict[str, Any] = {}

    unknown_fields = sorted(set(data) - PROFILE_MUTABLE_FIELDS - {"profile_version", "change_reason"})
    if unknown_fields:
        errors["unknown_fields"] = f"unsupported fields: {', '.join(unknown_fields)}"

    for field in PROFILE_CHOICE_FIELDS:
        if field in data:
            updates[field] = _normalized_nullable_string(field, data[field], errors)

    for field, (minimum, maximum) in PROFILE_INTEGER_FIELDS.items():
        if field not in data:
            continue
        raw = data[field]
        if raw is None:
            updates[field] = None
        elif isinstance(raw, bool) or not isinstance(raw, int):
            errors[field] = "must be an integer or null"
        elif not minimum <= raw <= maximum:
            errors[field] = f"must be between {minimum} and {maximum}"
        else:
            updates[field] = raw

    for field, (minimum, maximum) in PROFILE_DECIMAL_FIELDS.items():
        if field not in data:
            continue
        raw = data[field]
        if raw is None:
            updates[field] = None
            continue
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            errors[field] = "must be a number or null"
            continue
        if not value.is_finite():
            errors[field] = "must be a finite number"
        elif not minimum <= value <= maximum:
            errors[field] = f"must be between {minimum} and {maximum}"
        else:
            updates[field] = value.quantize(Decimal("0.01"))

    for field in PROFILE_BOOLEAN_FIELDS:
        if field not in data:
            continue
        raw = data[field]
        if raw is not None and not isinstance(raw, bool):
            errors[field] = "must be a boolean or null"
        else:
            updates[field] = raw

    if "excluded_sectors" in data:
        raw = data["excluded_sectors"]
        if raw is None:
            updates["excluded_sectors_csv"] = ""
        elif not isinstance(raw, list) or any(not isinstance(value, str) for value in raw):
            errors["excluded_sectors"] = "must be a list of strings or null"
        else:
            normalized = []
            for value in raw:
                sector = " ".join(value.strip().split()).lower()
                if not sector:
                    continue
                if len(sector) > 80:
                    errors["excluded_sectors"] = "each sector must be 80 characters or fewer"
                    break
                if "," in sector:
                    errors["excluded_sectors"] = "sector names cannot contain commas"
                    break
                if sector not in normalized:
                    normalized.append(sector)
            if len(normalized) > 20:
                errors["excluded_sectors"] = "cannot contain more than 20 sectors"
            elif "excluded_sectors" not in errors:
                updates["excluded_sectors_csv"] = ",".join(normalized)

    if errors:
        raise InvestorProfileValidationError(errors)
    return updates


def serialized_profile_values(values: dict[str, Any]) -> str:
    return json.dumps(values, sort_keys=True, separators=(",", ":"))


def update_completion_timestamp(profile: Any) -> None:
    values = stored_profile_values(profile)
    if missing_profile_fields(values):
        profile.questionnaire_completed_at = None
    elif profile.questionnaire_completed_at is None:
        profile.questionnaire_completed_at = datetime.utcnow()
