"""Pre-registered two-arm development-only training-weight experiment."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from moneybot.services.alpha_atlas_v4_concentration_diagnostics import REPORTING_VIEWS, weighted_metrics
from moneybot.services.alpha_atlas_v4_phase0 import apply_feature_fill_policy, fit_feature_fill_policy
from moneybot.services.alpha_atlas_v4_temporal_split import canonical_json_hash, file_sha256, validate_split_plan
from moneybot.services.deterministic_model import predict_proba, train_logistic_baseline
from scripts.train_challenger_suite import (_chronologically_order_rows, _load_jsonl,
    _prepare_frame, _return_column, _specialized_sample_weight)

EXPERIMENT_ID = "alpha-atlas-v4-big-loss-weight-ablation.v1"
SELECTED = "challenger-big-loss-avoider-v1"
ARMS = ("current_weights", "uniform_weights")


class WeightingExperimentError(ValueError):
    pass


def _id_hash(ids: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()


def build_registration(input_path: Path, plan_path: Path, manifest_path: Path,
                       capture_path: Path, *, source_run: str, expected_input_hash: str,
                       expected_plan_hash: str, expected_manifest_hash: str,
                       expected_capture_hash: str,
                       original_capture_code_sha: str) -> dict[str, Any]:
    if file_sha256(input_path) != expected_input_hash or file_sha256(manifest_path) != expected_manifest_hash or file_sha256(capture_path) != expected_capture_hash:
        raise WeightingExperimentError("expected_artifact_hash_mismatch")
    plan = json.loads(plan_path.read_text())
    development, holdout = validate_split_plan(plan, input_path=input_path)
    if plan.get("plan_sha256") != expected_plan_hash:
        raise WeightingExperimentError("expected_split_plan_hash_mismatch")
    manifest, capture = json.loads(manifest_path.read_text()), json.loads(capture_path.read_text())
    candidates = [item for item in manifest.get("challengers", []) if item.get("model_version") == SELECTED]
    captured = [item for item in capture if item.get("model_version") == SELECTED]
    if len(candidates) != 1 or len(captured) != 3:
        raise WeightingExperimentError("frozen_candidate_or_three_fold_capture_missing")
    candidate = candidates[0]
    if candidate.get("model_type") != "logistic_regression" or candidate.get("spec", {}).get("family") != "big_loss_avoider":
        raise WeightingExperimentError("unexpected_frozen_candidate_recipe")
    manifest_folds = {int(fold["fold_index"]): fold for fold in manifest.get("walk_forward_windows", [])}
    folds = []
    for item in sorted(captured, key=lambda value: int(value["fold_index"])):
        train_ids, validation_ids = list(map(str, item["train_ids"])), list(map(str, item["validation_ids"]))
        frozen_fold = manifest_folds.get(int(item["fold_index"]))
        planned_train = set(map(str, (frozen_fold or {}).get("train_canonical_observation_ids", [])))
        planned_validation = set(map(str, (frozen_fold or {}).get("validation_canonical_observation_ids", [])))
        if (len(train_ids) != len(set(train_ids)) or len(validation_ids) != len(set(validation_ids))
                or set(train_ids) != planned_train or set(validation_ids) != planned_validation
                or not frozen_fold.get("timing_boundary_passed") or frozen_fold.get("final_holdout_overlap_count") != 0
                or set(train_ids) & set(validation_ids) or not (set(train_ids) | set(validation_ids)) <= development or set(train_ids) & holdout):
            raise WeightingExperimentError("capture_membership_violation")
        folds.append({"fold_index": int(item["fold_index"]), "train_ids": sorted(train_ids),
                      "validation_ids": sorted(validation_ids), "train_ids_sha256": _id_hash(train_ids),
                      "validation_ids_sha256": _id_hash(validation_ids)})
    core = {"schema_version": "alpha-atlas-v4-weighting-experiment-registration.v1",
            "experiment_id": EXPERIMENT_ID, "status": "REGISTERED_PRE_EXECUTION",
            "previously_inspected_development_research": True, "source_track_b_run": source_run,
            "selected_candidate": SELECTED, "input_sha256": expected_input_hash,
            "split_plan_sha256": expected_plan_hash, "manifest_sha256": expected_manifest_hash,
            "capture_sha256": expected_capture_hash, "feature_columns": manifest.get("feature_columns"),
            "original_capture_code_sha": original_capture_code_sha,
            "target_definition": manifest.get("decision_target"), "candidate_spec": candidate.get("spec"),
            "candidate_lineage": candidate.get("lineage"), "folds": folds,
            "arms": {"current_weights": {"construction": "1.0 normally; 2.0 loss; 6.0 big_loss from registered return bucket",
                    "fit_entry_point": "train_logistic_baseline(sample_weight=...)", "class_weight": "none"},
                "uniform_weights": {"construction": "constant=sum(current_raw_weights)/N for every training row",
                    "normalization": "preserves raw total; fitter normalizes either arm to mean one", "class_weight": "none"}},
            "fixed": ["observations", "labels", "fold membership and ordering", "features", "missing-value policy",
                "model family", "learning rate", "l2", "threshold", "epochs", "deterministic zero initialization",
                "calibration method (none for this recipe)", "purge", "embargo", "abstention/risk/execution rules"],
            "primary_endpoint": {"metric": "symbol_date_balanced_brier", "paired_difference": "uniform-current",
                "aggregate": "equal-weight mean across exactly three folds", "favorable_rule": "aggregate < 0 and at least two fold differences < 0",
                "interpretation": "descriptive squared-error score performance; not proof of calibrated event probabilities"},
            "secondary_views": list(REPORTING_VIEWS), "constant_reference": "unweighted training-fold target prevalence",
            "failure_conditions": ["hash mismatch", "registration mismatch", "missing/duplicate fold or ID", "timing/membership violation",
                "invalid score", "baseline reproduction mismatch", "arm validation mismatch"],
            "research_only": True, "automatic_promotion": False, "ready_for_live_routing": False,
            "no_threshold_or_candidate_selection": True}
    return {**core, "registration_sha256": canonical_json_hash(core)}


def validate_registration(registration: dict[str, Any]) -> None:
    core = {key: value for key, value in registration.items() if key != "registration_sha256"}
    if registration.get("experiment_id") != EXPERIMENT_ID or registration.get("registration_sha256") != canonical_json_hash(core):
        raise WeightingExperimentError("registration_hash_or_id_mismatch")


def execute(registration: dict[str, Any], input_path: Path, plan_path: Path,
            manifest_path: Path, capture_path: Path, output_dir: Path, *, code_sha: str) -> dict[str, Path]:
    validate_registration(registration)
    rebuilt = build_registration(input_path, plan_path, manifest_path, capture_path,
        source_run=registration["source_track_b_run"], expected_input_hash=registration["input_sha256"],
        expected_plan_hash=registration["split_plan_sha256"], expected_manifest_hash=registration["manifest_sha256"],
        expected_capture_hash=registration["capture_sha256"],
        original_capture_code_sha=registration["original_capture_code_sha"])
    if rebuilt != registration:
        raise WeightingExperimentError("registration_does_not_match_artifacts")
    plan = json.loads(plan_path.read_text())
    development_ids, holdout_ids = validate_split_plan(plan, input_path=input_path)
    frame = _load_jsonl(input_path)
    frame = frame.loc[frame["canonical_observation_id"].astype(str).isin(development_ids)].copy()
    if set(frame["canonical_observation_id"].astype(str)) & holdout_ids:
        raise WeightingExperimentError("holdout_entered_experiment")
    frame = _chronologically_order_rows(_prepare_frame(frame))
    target = str((registration.get("target_definition") or {}).get("target_name") or "label_up_5d")
    if target not in frame:
        target = "label_up_5d"
    features, spec = registration["feature_columns"], registration["candidate_spec"]
    return_col = _return_column(frame, 5)
    baseline_capture = {int(item["fold_index"]): item for item in json.loads(capture_path.read_text()) if item["model_version"] == SELECTED}
    predictions, weights_out, comparisons = {arm: [] for arm in ARMS}, [], []
    for fold in registration["folds"]:
        ids = frame["canonical_observation_id"].astype(str)
        train = frame.loc[ids.isin(fold["train_ids"])].copy()
        validation = frame.loc[ids.isin(fold["validation_ids"])].copy()
        if list(sorted(train["canonical_observation_id"].astype(str))) != fold["train_ids"] or list(sorted(validation["canonical_observation_id"].astype(str))) != fold["validation_ids"]:
            raise WeightingExperimentError("exact_fold_membership_mismatch")
        policy = fit_feature_fill_policy(train, features)
        train = apply_feature_fill_policy(train, policy, expected_feature_contract_version="alpha-atlas-v4-features.v2")
        validation = apply_feature_fill_policy(validation, policy, expected_feature_contract_version="alpha-atlas-v4-features.v2")
        labels = train[target].to_numpy(dtype=float)
        current = _specialized_sample_weight(train, return_col, "big_loss_avoider")
        uniform = np.full(len(train), float(current.sum()) / len(train))
        fold_arm = {}
        for arm, arm_weights in zip(ARMS, (current, uniform)):
            model = train_logistic_baseline(train[features].to_numpy(dtype=float), labels,
                learning_rate=float(spec["lr"]), l2=float(spec["l2"]), decision_threshold=float(spec["threshold"]),
                epochs=int(spec["epochs"]), sample_weight=arm_weights)
            scores = predict_proba(model, validation[features].to_numpy(dtype=float))
            if not np.isfinite(scores).all():
                raise WeightingExperimentError("invalid_arm_scores")
            records = [{"id": str(row["canonical_observation_id"]), "security": str(row["symbol"]),
                "session": str(row["event_date"]), "label": int(row[target]), "score": float(scores[position]),
                "return": float(row[return_col]) if return_col and pd.notna(row[return_col]) else None,
                "abstained": False, "risk_rejected": False, "rule_rejected": False}
                for position, (_, row) in enumerate(validation.iterrows())]
            fold_arm[arm] = records
            predictions[arm].append({"fold_index": fold["fold_index"], "records": records, "model_state": model.to_dict()})
            weights_out.append({"fold_index": fold["fold_index"], "arm": arm, "count": len(arm_weights),
                "raw_sum": float(arm_weights.sum()), "raw_min": float(arm_weights.min()), "raw_max": float(arm_weights.max()),
                "normalized_sum": float(len(arm_weights)), "unique_raw_weights": sorted(set(map(float, arm_weights)))})
        existing = baseline_capture[fold["fold_index"]]
        existing_by_id = {record["id"]: float(record["score"]) for record in existing["records"]}
        if any(not math.isclose(existing_by_id[row["id"]], row["score"], abs_tol=1e-12) for row in fold_arm["current_weights"]):
            raise WeightingExperimentError("baseline_reproduction_mismatch")
        if [row["id"] for row in fold_arm["current_weights"]] != [row["id"] for row in fold_arm["uniform_weights"]]:
            raise WeightingExperimentError("arm_validation_ids_mismatch")
        arm_metrics = {}
        for arm in ARMS:
            selected_rows = [row for row in fold_arm[arm] if row["score"] >= float(spec["threshold"])]
            group_counts: dict[tuple[str, str], int] = {}
            for row in selected_rows:
                key = (row["security"], row["session"])
                group_counts[key] = group_counts.get(key, 0) + 1
            ordered_counts = sorted(group_counts.values(), reverse=True)
            arm_metrics[arm] = {
                "reporting_views": {view: weighted_metrics(fold_arm[arm], view, float(spec["threshold"]), probability_semantics=True)
                    for view in REPORTING_VIEWS},
                "selected_distinct_securities": len({row["security"] for row in selected_rows}),
                "selected_distinct_dates": len({row["session"] for row in selected_rows}),
                "selected_distinct_security_date_groups": len(group_counts),
                "largest_selected_group_share": (ordered_counts[0] / len(selected_rows) if selected_rows else None),
                "top_three_selected_group_share": (sum(ordered_counts[:3]) / len(selected_rows) if selected_rows else None),
            }
        constant = float(labels.mean())
        reference = {view: weighted_metrics([{**row, "score": constant} for row in fold_arm["current_weights"]], view,
            float(spec["threshold"]), probability_semantics=True) for view in REPORTING_VIEWS}
        difference = arm_metrics["uniform_weights"]["reporting_views"]["symbol_date_balanced"]["calibration"]["brier_score"] - arm_metrics["current_weights"]["reporting_views"]["symbol_date_balanced"]["calibration"]["brier_score"]
        comparisons.append({"fold_index": fold["fold_index"], "arms": arm_metrics, "constant_score_reference": {"value": constant, "metrics": reference},
                            "primary_uniform_minus_current": difference})
    differences = [row["primary_uniform_minus_current"] for row in comparisons]
    aggregate = sum(differences) / 3
    favorable_folds = sum(value < 0 for value in differences)
    classification = "favorable_research_evidence" if aggregate < 0 and favorable_folds >= 2 else ("unfavorable" if aggregate > 0 and favorable_folds <= 1 else "mixed_or_unchanged")
    common = {"schema_version": "alpha-atlas-v4-weighting-experiment-execution.v1", "experiment_id": EXPERIMENT_ID,
        "registration_sha256": registration["registration_sha256"], "execution_code_sha": code_sha,
        "original_capture_code_sha": registration["original_capture_code_sha"], "final_holdout_overlap_count": 0, "final_holdout_evaluated": False,
        "automatic_promotion": False, "ready_for_live_routing": False}
    output_dir.mkdir(parents=True, exist_ok=True)
    payloads = {"weighting_experiment_execution_provenance.json": common,
        "weighting_experiment_predictions.json": {**common, "arms": predictions},
        "weighting_experiment_effective_weights.json": {**common, "folds": weights_out},
        "weighting_experiment_paired_comparison.json": {**common, "folds": comparisons,
            "primary_aggregate_equal_fold_mean": aggregate, "improved_fold_count": favorable_folds,
            "registered_interpretation": classification, "promotion_or_threshold_change_allowed": False}}
    paths = {}
    for name, payload in payloads.items():
        path = output_dir / name
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        paths[name] = path
    summary = output_dir / "weighting_experiment_summary.md"
    summary.write_text(f"# {EXPERIMENT_ID}\n\nDevelopment-only, previously inspected research evidence. Primary uniform-minus-current mean: `{aggregate}`; result: `{classification}`. Three folds are not statistical significance or independent bets. No promotion or threshold change is authorized.\n")
    paths[summary.name] = summary
    return paths
