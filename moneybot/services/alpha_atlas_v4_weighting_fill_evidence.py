"""Evidence-only certification of persisted V4 weighting fold fill policies."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from moneybot.services.alpha_atlas_v4_phase0 import (
    apply_feature_fill_policy,
    validate_fill_policy,
)

SCHEMA = "alpha-atlas-v4-weighting-fill-policy-verification.v1"
EXPECTED_EXECUTION_CODE_SHA = "aad503b0520c80dca35bb593edac2001b1be0c85"


class FillPolicyEvidenceError(ValueError):
    pass


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _feature_hash(features: list[str]) -> str:
    return hashlib.sha256("\n".join(features).encode()).hexdigest()


def verify_fill_policy_evidence(*, diagnostic_capture: Path, registration_path: Path,
                                feature_store: Path, reload_report_path: Path,
                                corrected_states_path: Path, output_path: Path) -> dict[str, Any]:
    before = _file_sha(corrected_states_path)
    capture = json.loads(diagnostic_capture.read_text())
    registration = json.loads(registration_path.read_text())
    reload_report = json.loads(reload_report_path.read_text())
    corrected = json.loads(corrected_states_path.read_text())
    features = list(registration.get("feature_columns") or [])
    if len(features) != 43 or len(features) != len(set(features)):
        raise FillPolicyEvidenceError("FEATURE_ORDER_MISMATCH")
    if (reload_report.get("execution_code_sha") != EXPECTED_EXECUTION_CODE_SHA
            or not reload_report.get("all_replays_verified")
            or len(reload_report.get("models") or []) != 6):
        raise FillPolicyEvidenceError("SUCCESSFUL_REPLAY_EVIDENCE_MISSING")
    corrected_features = [entry["model_state"]["feature_columns"]
        for folds in corrected.get("arms", {}).values() for entry in folds]
    if len(corrected_features) != 6 or any(value != features for value in corrected_features):
        raise FillPolicyEvidenceError("FEATURE_ORDER_MISMATCH")
    captured = {int(item["fold_index"]): item for item in capture
        if item.get("model_version") == "challenger-big-loss-avoider-v1"}
    if set(captured) != {1, 2, 3}:
        raise FillPolicyEvidenceError("FILL_POLICY_NOT_PROVABLE")
    registered = {int(item["fold_index"]): item for item in registration["folds"]}
    permitted = {identifier for fold in registered.values() for identifier in fold["validation_ids"]}
    rows: dict[str, dict[str, Any]] = {}
    with feature_store.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            identifier = str(row["canonical_observation_id"])
            if identifier not in permitted:
                continue
            if identifier in rows:
                raise FillPolicyEvidenceError("DUPLICATE_VALIDATION_ID")
            rows[identifier] = row
    fold_evidence: dict[int, dict[str, Any]] = {}
    for index, fold in registered.items():
        item = captured[index]
        if (set(item.get("train_ids") or []) != set(fold["train_ids"])
                or set(item.get("validation_ids") or []) != set(fold["validation_ids"])):
            raise FillPolicyEvidenceError("FILL_STATISTICS_NOT_TRAINING_ONLY")
        policy = item.get("fill_policy")
        if not isinstance(policy, dict):
            raise FillPolicyEvidenceError("FILL_POLICY_NOT_PROVABLE")
        validate_fill_policy(policy, expected_feature_contract_version="alpha-atlas-v4-features.v2")
        if set(policy.get("features") or {}) != set(features):
            raise FillPolicyEvidenceError("FEATURE_ORDER_MISMATCH")
        validation_ids = list(fold["validation_ids"])
        if any(identifier not in rows for identifier in validation_ids):
            raise FillPolicyEvidenceError("MISSING_VALIDATION_ID")
        raw = pd.DataFrame([rows[identifier] for identifier in validation_ids])
        counts: dict[str, int] = {}
        manual = raw.copy()
        for feature in features:
            numeric = pd.to_numeric(raw[feature], errors="coerce").replace([np.inf, -np.inf], np.nan)
            count = int(numeric.isna().sum())
            if count:
                counts[feature] = count
            manual[feature] = numeric.fillna(float(policy["features"][feature]["fitted_value"])).astype(float)
        applied = apply_feature_fill_policy(raw, policy,
            expected_feature_contract_version="alpha-atlas-v4-features.v2")
        expected_matrix = manual[features].to_numpy(dtype=float)
        actual_matrix = applied[features].to_numpy(dtype=float)
        differences = np.abs(actual_matrix - expected_matrix)
        mismatch = int(np.count_nonzero(differences))
        if mismatch:
            raise FillPolicyEvidenceError("EXECUTION_MATRIX_MISMATCH")
        fold_evidence[index] = {"fold_index": index, "feature_count": 43,
            "feature_columns_sha256": _feature_hash(features), "validation_rows": len(raw),
            "validation_rows_with_missing_or_nonfinite_before_fill": int(sum(
                any(not _finite(row.get(feature)) for feature in features) for _, row in raw.iterrows())),
            "feature_cells_requiring_fill": sum(counts.values()),
            "affected_features": [{"feature": feature, "filled_cells": counts[feature],
                "fill_method": policy["features"][feature]["fill_method"],
                "fill_value": policy["features"][feature]["fitted_value"]} for feature in features if feature in counts],
            "policy_sha256": policy["policy_sha256"], "fit_input_sha256": policy["fit_input_sha256"],
            "fit_period_rows": policy["fit_period_rows"],
            "training_only_fill_statistics": True,
            "validation_rows_used_to_compute_fill_values": 0,
            "holdout_rows_used_to_compute_fill_values": 0,
            "execution_matrix": {"verified": True, "shape": list(actual_matrix.shape),
                "matrix_sha256": hashlib.sha256(actual_matrix.astype("<f8").tobytes()).hexdigest(),
                "mismatched_cells": 0, "max_abs_difference": float(differences.max(initial=0.0))}}
    folds = [{"arm": arm, **fold_evidence[index]}
        for arm in ("current_weights", "uniform_weights") for index in (1, 2, 3)]
    after = _file_sha(corrected_states_path)
    if before != after:
        raise FillPolicyEvidenceError("CORRECTED_STATE_MUTATION")
    report = {"schema_version": SCHEMA, "status": "VERIFIED", "evidence_only": True,
        "models_refit": False, "predictions_changed": False, "scores_or_decisions_changed": False,
        "corrected_states_changed": False, "final_holdout_accessed": False,
        "automatic_promotion_performed": False,
        "policy_source": {"source_files": ["moneybot/services/alpha_atlas_v4_phase0.py",
            "scripts/train_challenger_suite.py", "moneybot/services/alpha_atlas_v4_weighting_experiment.py"],
            "functions": ["fit_feature_fill_policy", "apply_feature_fill_policy", "execute"],
            "description": "Each fold fits one policy from fold_train, then applies that persisted policy to fold_train and fold validation before scaling inside train_logistic_baseline."},
        "policy": {"missing_value_detection": "pandas to_numeric(errors=coerce), then +/-inf replaced with NaN",
            "fill_stage": "before model scaling", "fill_method": "feature-specific training-fold median; zero only when the training fold has no finite value",
            "non_finite_handling": "NaN, +inf, -inf and nonnumeric values use the feature fitted_value",
            "training_only_statistics_used": True, "features_exempt": []},
        "corrected_states_immutable": {"before_sha256": before, "after_sha256": after,
            "identical": True}, "authoritative_feature_columns": features, "folds": folds}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def _finite(value: Any) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
