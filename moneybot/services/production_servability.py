from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CERTIFICATION_SCHEMA_VERSION = "moneybot-production-servability.v1"
SUPPORTED_DECISION_MODEL_FAMILIES = {"logistic_regression", "calibrated_linear"}
REQUIRED_GATES = (
    "leakage_safe",
    "feature_contract_declared",
    "feature_contract_supported",
    "all_required_features_time_safe",
    "all_required_features_serving_available",
    "no_future_features",
    "no_post_event_features",
    "no_circular_features",
    "training_serving_transform_match",
    "forecast_horizon_declared",
    "model_family_supported",
    "feature_order_match",
    "fill_policy_match",
    "representative_dry_run_passed",
    "non_degenerate_predictions",
    "decision_lane_eligible",
)


def artifact_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _feature_audit(
    feature: str, declaration: dict[str, Any], candidate_version: str
) -> dict[str, Any]:
    available = declaration.get("available_at_prediction_time") is True
    source = str(declaration.get("source") or "").strip()
    semantics = str(declaration.get("time_semantics") or "").strip().lower()
    upstream = str(declaration.get("upstream_model_version") or "").strip()
    required_fields = (
        "training_builder",
        "serving_builder",
        "transformation",
        "fill_policy",
    )
    documented = bool(source) and all(
        str(declaration.get(field) or "").strip() for field in required_fields
    )

    future = semantics in {"future", "forward", "post_prediction"} or any(
        token in semantics for token in ("t+1", "t+5", "future", "forward")
    )
    post_event = (
        semantics in {"post_event", "realized_outcome", "same_event_output"}
        or "post-event" in semantics
    )
    same_model_probability = feature == "feature_probability_up" and (
        not upstream or upstream.casefold() == candidate_version.casefold()
    )
    candidate_output = declaration.get("depends_on_candidate_output") is True
    circular = same_model_probability or candidate_output

    if future:
        classification = "NON_SERVABLE_FUTURE"
        reason = "feature uses information strictly after prediction timestamp T"
    elif post_event:
        classification = "NON_SERVABLE_POST_EVENT"
        reason = (
            "feature is derived from the event being predicted or its realized outcome"
        )
    elif circular:
        classification = "NON_SERVABLE_CIRCULAR"
        reason = "feature depends on the candidate's current output or lacks a frozen upstream model"
    elif not available or not documented:
        classification = "NON_SERVABLE_UNKNOWN"
        reason = "prediction-time provenance or matching builders/transformation/fill policy is not declared"
    elif declaration.get("required", True):
        classification = "SERVABLE"
        reason = "required feature is documented and available at or before prediction timestamp T"
    else:
        classification = "OPTIONAL_SERVABLE"
        reason = "optional feature is documented and available at or before prediction timestamp T"

    return {
        "feature_name": feature,
        "classification": classification,
        "source": source or None,
        "available_at_prediction_time": available,
        "serving_builder": declaration.get("serving_builder"),
        "training_builder": declaration.get("training_builder"),
        "transformation": declaration.get("transformation"),
        "fill_policy": declaration.get("fill_policy"),
        "time_semantics": declaration.get("time_semantics"),
        "servable": classification in {"SERVABLE", "OPTIONAL_SERVABLE"},
        "reason": reason,
    }


def certify_candidate(candidate_path: Path) -> dict[str, Any]:
    try:
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        candidate = {}
    if not isinstance(candidate, dict):
        candidate = {}

    version = str(
        candidate.get("version") or candidate.get("model_version") or ""
    ).strip()
    model_family = str(
        candidate.get("model_type") or candidate.get("model_family") or ""
    ).strip()
    lineage = (
        candidate.get("lineage") if isinstance(candidate.get("lineage"), dict) else {}
    )
    contract = candidate.get("production_feature_contract")
    contract = contract if isinstance(contract, dict) else {}
    columns = [str(item) for item in candidate.get("feature_columns") or []]
    declared_columns = [str(item) for item in contract.get("feature_columns") or []]
    required = [
        str(item) for item in contract.get("required_features") or declared_columns
    ]
    optional = [str(item) for item in contract.get("optional_features") or []]
    declarations = (
        contract.get("features") if isinstance(contract.get("features"), list) else []
    )
    by_name = {
        str(item.get("feature_name")): item
        for item in declarations
        if isinstance(item, dict) and item.get("feature_name")
    }
    audits = [
        _feature_audit(feature, by_name.get(feature, {}), version)
        for feature in columns
    ]
    blocking_audits = [
        item
        for item in audits
        if item["feature_name"] in required and not item["servable"]
    ]

    training_transform = (
        contract.get("training_transform")
        if isinstance(contract.get("training_transform"), dict)
        else {}
    )
    serving_transform = (
        contract.get("serving_transform")
        if isinstance(contract.get("serving_transform"), dict)
        else {}
    )
    fill_values = (
        contract.get("fill_values")
        if isinstance(contract.get("fill_values"), dict)
        else {}
    )
    dry_runs = (
        contract.get("representative_dry_runs")
        if isinstance(contract.get("representative_dry_runs"), list)
        else []
    )
    healthy_runs = [
        row
        for row in dry_runs
        if isinstance(row, dict)
        and row.get("feature_contract_servable") is True
        and not row.get("missing_required_features")
        and _finite(row.get("raw_probability"))
        and int(row.get("available_feature_count") or 0) >= len(required)
    ]
    probabilities = [round(float(row["raw_probability"]), 12) for row in healthy_runs]
    non_degenerate = (
        len(healthy_runs) >= 3
        and len(set(probabilities)) > 1
        and not all(
            row.get("feature_vector_is_training_mean") is True for row in healthy_runs
        )
    )
    horizon = str(
        contract.get("forecast_horizon") or candidate.get("forecast_horizon") or ""
    ).strip()
    lane = (
        str(contract.get("lane") or candidate.get("candidate_lane") or "")
        .strip()
        .lower()
    )
    recipe_hash = str(
        lineage.get("recipe_hash") or candidate.get("recipe_hash") or ""
    ).strip()
    lineage_id = str(
        lineage.get("lineage_id") or candidate.get("lineage_id") or ""
    ).strip()

    gates = {
        "leakage_safe": bool(contract.get("leakage_safe")) and not blocking_audits,
        "feature_contract_declared": bool(contract and columns and declarations),
        "feature_contract_supported": (
            str(contract.get("feature_contract_version") or "").startswith(
                "moneybot-serving-features."
            )
            and bool(lineage_id)
            and bool(recipe_hash)
        ),
        "all_required_features_time_safe": all(
            item["servable"] for item in audits if item["feature_name"] in required
        )
        and set(required) <= set(columns),
        "all_required_features_serving_available": set(required)
        <= {item["feature_name"] for item in audits if item["servable"]},
        "no_future_features": not any(
            item["classification"] == "NON_SERVABLE_FUTURE" for item in audits
        ),
        "no_post_event_features": not any(
            item["classification"] == "NON_SERVABLE_POST_EVENT" for item in audits
        ),
        "no_circular_features": not any(
            item["classification"] == "NON_SERVABLE_CIRCULAR" for item in audits
        ),
        "training_serving_transform_match": bool(training_transform)
        and training_transform == serving_transform,
        "forecast_horizon_declared": bool(horizon and horizon != "unknown"),
        "model_family_supported": model_family in SUPPORTED_DECISION_MODEL_FAMILIES,
        "feature_order_match": bool(columns) and columns == declared_columns,
        "fill_policy_match": bool(fill_values)
        and contract.get("training_fill_policy") == contract.get("serving_fill_policy"),
        "representative_dry_run_passed": len(healthy_runs) >= 3,
        "non_degenerate_predictions": non_degenerate,
        "decision_lane_eligible": lane == "decision",
    }
    blocking_reasons = [name for name, passed in gates.items() if not passed]
    blocking_features = sorted(item["feature_name"] for item in blocking_audits)
    return {
        "schema_version": CERTIFICATION_SCHEMA_VERSION,
        "passed": all(gates.values()),
        "candidate_artifact_sha256": (
            artifact_sha256(candidate_path) if candidate_path.exists() else None
        ),
        "candidate_version": version or None,
        "candidate_lineage_id": lineage_id or None,
        "candidate_recipe_hash": recipe_hash or None,
        "model_family": model_family or None,
        "lane": lane or None,
        "feature_contract_version": contract.get("feature_contract_version"),
        "forecast_horizon": horizon or "unknown",
        "feature_columns": columns,
        "required_features": required,
        "optional_features": optional,
        "fill_values": fill_values,
        "imputation_policy": contract.get("training_fill_policy"),
        "feature_audit": audits,
        "gates": gates,
        "blocking_features": blocking_features,
        "blocking_reasons": blocking_reasons,
        "warnings": list(contract.get("warnings") or []),
        "representative_dry_runs": dry_runs,
        "certified_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def validate_certification(
    candidate_path: Path, certification: dict[str, Any]
) -> list[str]:
    if not isinstance(certification, dict):
        return ["malformed_servability_certification"]
    failures: list[str] = []
    if certification.get("schema_version") != CERTIFICATION_SCHEMA_VERSION:
        failures.append("unsupported_servability_certification_schema")
    gates = certification.get("gates")
    if not isinstance(gates, dict) or any(
        gates.get(name) is not True for name in REQUIRED_GATES
    ):
        failures.append("incomplete_or_failed_servability_gates")
    if certification.get("passed") is not True:
        failures.append("failed_servability_certification")
    try:
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return sorted(set(failures + ["malformed_candidate_artifact"]))
    if certification.get("candidate_artifact_sha256") != artifact_sha256(
        candidate_path
    ):
        failures.append("candidate_artifact_hash_mismatch")
    candidate_version = str(
        candidate.get("version") or candidate.get("model_version") or ""
    ).strip()
    if certification.get("candidate_version") != candidate_version:
        failures.append("candidate_version_mismatch")
    if certification.get("feature_columns") != candidate.get("feature_columns"):
        failures.append("candidate_feature_columns_mismatch")
    lineage = (
        candidate.get("lineage") if isinstance(candidate.get("lineage"), dict) else {}
    )
    if certification.get("candidate_lineage_id") != (
        lineage.get("lineage_id") or candidate.get("lineage_id")
    ):
        failures.append("candidate_lineage_mismatch")
    if certification.get("candidate_recipe_hash") != (
        lineage.get("recipe_hash") or candidate.get("recipe_hash")
    ):
        failures.append("candidate_recipe_hash_mismatch")
    contract = (
        candidate.get("production_feature_contract")
        if isinstance(candidate.get("production_feature_contract"), dict)
        else {}
    )
    for key in ("feature_contract_version", "forecast_horizon"):
        if certification.get(key) != contract.get(key):
            failures.append(f"candidate_{key}_mismatch")
    if certification.get("model_family") != (
        candidate.get("model_type") or candidate.get("model_family")
    ):
        failures.append("candidate_model_family_mismatch")
    return sorted(set(failures))
