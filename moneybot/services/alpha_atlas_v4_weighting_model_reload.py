"""Metadata-only recovery and no-fit replay of saved V4 weighting model states."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import numpy as np
import pandas as pd

from moneybot.services.alpha_atlas_v4_phase0 import apply_feature_fill_policy
from moneybot.services.deterministic_model import load_artifact, predict_proba

SCHEMA = "alpha-atlas-v4-weighting-model-reload-verification.v1"
TOLERANCE = 1e-12
EXPECTED_COUNTS = {1: 4464, 2: 2479, 3: 3330}


class ModelReloadError(ValueError):
    pass


def _sha_bytes(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def repair_and_verify(*, execution_predictions: Path, paired_comparison: Path,
                      registration_path: Path, diagnostic_capture: Path,
                      feature_store: Path, output_dir: Path,
                      execution_code_sha: str) -> dict[str, Any]:
    predictions = json.loads(execution_predictions.read_text())
    original_predictions = copy.deepcopy(predictions)
    registration = json.loads(registration_path.read_text())
    capture = json.loads(diagnostic_capture.read_text())
    authoritative = list(registration["feature_columns"])
    if len(authoritative) != 43 or len(authoritative) != len(set(authoritative)):
        raise ModelReloadError("authoritative_feature_mapping_invalid")
    permitted_ids = {identifier for fold in registration["folds"] for identifier in fold["validation_ids"]}
    rows = {}
    with feature_store.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            identifier = str(row["canonical_observation_id"])
            if identifier not in permitted_ids:
                continue
            if identifier in rows:
                raise ModelReloadError("duplicate_canonical_input_id")
            rows[identifier] = row
    reports, corrected = [], {"schema_version": "alpha-atlas-v4-weighting-model-state-metadata-repair.v1", "arms": {}}
    captured_folds = {
        int(item["fold_index"]): item
        for item in capture
        if item.get("model_version") == "challenger-big-loss-avoider-v1"
    }
    if set(captured_folds) != {1, 2, 3}:
        raise ModelReloadError("selected_candidate_fill_policy_capture_incomplete")
    for arm, folds in predictions["arms"].items():
        corrected["arms"][arm] = []
        for fold in folds:
            index = int(fold["fold_index"])
            records, state = fold["records"], fold["model_state"]
            registered_fold = next(item for item in registration["folds"] if int(item["fold_index"]) == index)
            captured_fold = captured_folds[index]
            if (set(captured_fold["train_ids"]) != set(registered_fold["train_ids"])
                    or set(captured_fold["validation_ids"]) != set(registered_fold["validation_ids"])
                    or [record["id"] for record in records] != [record["id"] for record in captured_fold["records"]]
                    or captured_fold.get("fill_policy") is None):
                raise ModelReloadError("captured_fill_policy_provenance_mismatch")
            if len(records) != EXPECTED_COUNTS[index] or len({record["id"] for record in records}) != len(records):
                raise ModelReloadError("validation_count_or_id_integrity_mismatch")
            dimensions = {name: len(state[name]) for name in ("feature_columns", "weights", "means", "stds")}
            if dimensions != {"feature_columns": 10, "weights": 43, "means": 43, "stds": 43}:
                raise ModelReloadError("unexpected_saved_state_defect_shape")
            repaired = copy.deepcopy(state)
            repaired["feature_columns"] = authoritative
            changes = [key for key in state if state.get(key) != repaired.get(key)]
            if changes != ["feature_columns"]:
                raise ModelReloadError("repair_changed_unauthorized_state")
            corrected["arms"][arm].append({"fold_index": index, "model_state": repaired,
                "original_state_canonical_sha256": _sha_bytes(state),
                "corrected_state_canonical_sha256": _sha_bytes(repaired),
                "authorized_changed_fields": changes})
            missing_ids = [record["id"] for record in records if record["id"] not in rows]
            missing_values = sum(any(
                rows.get(record["id"], {}).get(feature) is None
                or not math.isfinite(float(rows.get(record["id"], {}).get(feature, float("nan"))))
                for feature in authoritative
            ) for record in records)
            result = {"arm": arm, "fold_index": index, "expected_validation_count": EXPECTED_COUNTS[index],
                "checked_validation_count": len(records), "authoritative_feature_columns": authoritative,
                "dimensions": {"features": 43, "coefficients": 43, "means": 43, "stds": 43},
                "missing_ids": missing_ids, "duplicate_prediction_id_count": len(records) - len({record["id"] for record in records}),
                "fixed_tolerance": TOLERANCE, "threshold": float(repaired["decision_threshold"]),
                "calibration_slope": float(repaired.get("calibration_slope", 1)),
                "calibration_intercept": float(repaired.get("calibration_intercept", 0))}
            if missing_ids:
                result.update({"replay_status": "BLOCKED_MISSING_PERSISTED_FOLD_FILL_POLICY",
                    "failure_reason": f"missing_ids={len(missing_ids)}",
                    "maximum_absolute_score_difference": None, "scores_outside_tolerance": None,
                    "decision_mismatch_count": None})
            else:
                validation = pd.DataFrame([rows[record["id"]] for record in records])
                if missing_values:
                    validation = apply_feature_fill_policy(
                        validation,
                        captured_fold["fill_policy"],
                        expected_feature_contract_version="alpha-atlas-v4-features.v2",
                    )
                matrix = validation[authoritative].to_numpy(dtype=float)
                with TemporaryDirectory() as temporary:
                    path = Path(temporary) / "model.json"
                    path.write_text(json.dumps(repaired))
                    loaded = load_artifact(path)
                replay = predict_proba(loaded, matrix)
                expected = np.asarray([float(record["score"]) for record in records])
                differences = np.abs(replay - expected)
                result.update({"replay_status": "VERIFIED" if bool(np.all(differences <= TOLERANCE)) else "FAILED_SCORE_MISMATCH",
                    "failure_reason": None if bool(np.all(differences <= TOLERANCE)) else "reloaded scores exceed fixed tolerance",
                    "maximum_absolute_score_difference": float(differences.max()),
                    "scores_outside_tolerance": int((differences > TOLERANCE).sum()),
                    "decision_mismatch_count": int(((replay >= 0.60) != (expected >= 0.60)).sum())})
            reports.append(result)
    if predictions != original_predictions:
        raise ModelReloadError("original_predictions_modified")
    paired = json.loads(paired_comparison.read_text())
    if not math.isclose(float(paired["primary_aggregate_equal_fold_mean"]), 0.006319421301219023, abs_tol=1e-15):
        raise ModelReloadError("original_paired_result_mismatch")
    all_replays_verified = all(item["replay_status"] == "VERIFIED" for item in reports)
    report = {"schema_version": SCHEMA, "execution_code_sha": execution_code_sha,
        "registration_sha256": registration["registration_sha256"], "authoritative_mapping_evidence":
        "registration feature_columns and execution source matrix train[features]/validation[features] use identical order",
        "metadata_correction_status": "MAPPING_CORRECTION_PROVEN_FROM_REGISTRATION_AND_SOURCE_PATH",
        "prediction_reproducibility_status": "VERIFIED" if all_replays_verified else "UNRESOLVED",
        "classification": ("A_correct_numerical_fitting_and_prediction_incorrect_saved_metadata"
                           if all_replays_verified else "INSUFFICIENT_REPLAY_EVIDENCE_TO_CONFIRM_METADATA_ONLY"),
        "original_primary_uniform_minus_current": float(paired["primary_aggregate_equal_fold_mean"]),
        "saved_result_arithmetic_status": "VERIFIED_AGGREGATE_UNCHANGED",
        "original_result_preserved": True, "models": reports,
        "all_replays_verified": all_replays_verified,
        "final_holdout_accessed": False, "models_fitted": False,
        "automatic_promotion": False, "ready_for_live_routing": False}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "weighting_model_reload_verification.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    (output_dir / "corrected_weighting_model_states.json").write_text(json.dumps(corrected, indent=2, sort_keys=True) + "\n")
    return report
