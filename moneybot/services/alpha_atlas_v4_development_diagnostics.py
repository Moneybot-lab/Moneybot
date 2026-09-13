"""Development-only diagnostics for frozen Alpha Atlas V4 OOF predictions.

This module deliberately does no fitting and never reads portfolio valuations.  It
turns predictions captured by the existing purged walk-forward observer into
descriptive evidence without changing a model, threshold, or ranking.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from moneybot.services.alpha_atlas_v4_temporal_split import validate_split_plan

SCHEMA_VERSION = "alpha-atlas-v4-development-diagnostics.v1"
THRESHOLD_GRID = tuple(round(0.30 + 0.05 * i, 2) for i in range(11))
RELIABILITY_EDGES = tuple(i / 10 for i in range(11))
QUANTILES = (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0)
LOG_LOSS_CLIP = 1e-15


class DevelopmentDiagnosticError(ValueError):
    """Fail-closed error for contaminated or incomplete diagnostic evidence."""


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _quantiles(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    result = {}
    for q in QUANTILES:
        position = q * (len(ordered) - 1)
        low = int(position)
        high = min(low + 1, len(ordered) - 1)
        value = ordered[low] + (ordered[high] - ordered[low]) * (position - low)
        result[f"q{int(q * 100):02d}"] = round(value, 8)
    return result


def calibration_metrics(probabilities: list[float], labels: list[int]) -> dict[str, Any]:
    """Fixed-width reliability diagnostics; invalid scores are reported, not hidden."""
    if len(probabilities) != len(labels):
        raise DevelopmentDiagnosticError("score_label_length_mismatch")
    warnings: list[str] = []
    if len(set(labels)) < 2 and labels:
        warnings.append("single_class_validation")
    invalid = sum(not math.isfinite(p) or p < 0.0 or p > 1.0 for p in probabilities)
    if invalid:
        warnings.append("invalid_probability_values")
        return {"available": False, "invalid_probability_count": invalid, "warnings": warnings}
    if not labels:
        return {"available": False, "invalid_probability_count": 0, "warnings": ["empty_validation"]}
    bins = []
    weighted_error = 0.0
    for index in range(10):
        lower, upper = RELIABILITY_EDGES[index], RELIABILITY_EDGES[index + 1]
        members = [i for i, p in enumerate(probabilities) if p >= lower and (p < upper or (index == 9 and p <= upper))]
        count = len(members)
        mean_p = sum(probabilities[i] for i in members) / count if count else None
        observed = sum(labels[i] for i in members) / count if count else None
        if count and count < 10:
            warnings.append(f"sparse_bin_{index}")
        if count:
            weighted_error += count * abs(float(mean_p) - float(observed))
        bins.append({"index": index, "lower_inclusive": lower, "upper_inclusive": index == 9,
                     "upper": upper, "count": count, "mean_probability": mean_p,
                     "observed_frequency": observed})
    brier = sum((p - y) ** 2 for p, y in zip(probabilities, labels)) / len(labels)
    clipped = [min(1.0 - LOG_LOSS_CLIP, max(LOG_LOSS_CLIP, p)) for p in probabilities]
    log_loss = -sum(y * math.log(p) + (1 - y) * math.log(1 - p) for p, y in zip(clipped, labels)) / len(labels)
    return {"available": True, "brier_score": brier, "log_loss": log_loss,
            "log_loss_diagnostic_clip": LOG_LOSS_CLIP, "ece": weighted_error / len(labels),
            "ece_definition": "10 fixed equal-width bins [0,.1),...,[.9,1]",
            "reliability_bins": bins, "invalid_probability_count": 0,
            "warnings": sorted(set(warnings))}


def _threshold_result(records: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    selected = [row for row in records if float(row["score"]) >= threshold and not row.get("abstained", False) and not row.get("risk_rejected", False) and not row.get("rule_rejected", False)]
    positives = sum(int(row["label"]) for row in selected)
    total_positive = sum(int(row["label"]) for row in records)
    returns = [float(row["return"]) for row in selected if row.get("return") is not None]
    return {"threshold": threshold, "positive_signal_count": len(selected),
            "positive_signal_rate": len(selected) / len(records) if records else 0.0,
            "distinct_signal_dates": len({str(row["session"]) for row in selected}),
            "distinct_signal_securities": len({str(row["security"]) for row in selected}),
            "precision": positives / len(selected) if selected else None,
            "recall": positives / total_positive if total_positive else None,
            "average_selected_return": sum(returns) / len(returns) if returns else None,
            "warnings": (["empty_selection"] if not selected else []) + (["insufficient_signal_support"] if 0 < len(selected) < 10 else [])}


def generate_development_diagnostics(*, canonical_input: Path, split_plan_path: Path,
                                     manifest_path: Path, predictions_path: Path,
                                     output_dir: Path, baseline_sha: str) -> dict[str, Path]:
    plan = _load_json(split_plan_path)
    development_ids, holdout_ids = validate_split_plan(plan, input_path=canonical_input)
    development_ids, holdout_ids = set(development_ids), set(holdout_ids)
    rows = _load_json(predictions_path)
    if not isinstance(rows, list):
        raise DevelopmentDiagnosticError("predictions_must_be_a_json_array")
    manifest = _load_json(manifest_path)
    roster = {str(item["model_version"]): item for item in manifest.get("challengers", [])}
    seen = {str(item.get("model_version")) for item in rows}
    if seen != set(roster):
        raise DevelopmentDiagnosticError("prediction_roster_does_not_match_frozen_manifest")
    fold_plan = {int(fold["fold_index"]): fold for fold in manifest.get("walk_forward_windows", [])}
    coverage, calibration, sensitivity, lineage = [], [], [], []
    pooled_records: dict[str, list[dict[str, Any]]] = {candidate: [] for candidate in roster}
    pooled_semantics: dict[str, str] = {}
    diagnostic_ids: set[str] = set()
    for item in rows:
        candidate = str(item["model_version"])
        fold_index = int(item["fold_index"])
        fold = fold_plan.get(fold_index)
        if not fold or not fold.get("usable"):
            raise DevelopmentDiagnosticError("prediction_references_unknown_or_unusable_fold")
        train_ids = set(map(str, item["train_ids"]))
        validation_ids = list(map(str, item["validation_ids"]))
        if train_ids != set(map(str, fold["train_ids"])) or set(validation_ids) != set(map(str, fold["validation_ids"])):
            raise DevelopmentDiagnosticError("prediction_fold_provenance_mismatch")
        if train_ids & set(validation_ids) or not (train_ids | set(validation_ids)) <= development_ids:
            raise DevelopmentDiagnosticError("nondevelopment_or_in_sample_prediction")
        records = item.get("records", [])
        record_ids = [str(record.get("id")) for record in records]
        if len(record_ids) != len(set(record_ids)) or set(record_ids) != set(validation_ids):
            raise DevelopmentDiagnosticError("prediction_record_membership_mismatch")
        diagnostic_ids.update(validation_ids)
        pooled_records[candidate].extend(records)
        pooled_semantics[candidate] = str(item.get("score_semantics", "probability"))
        spec = roster[candidate].get("spec", {})
        lane = roster[candidate].get("candidate_lane") or spec.get("candidate_lane", "decision")
        semantics = item.get("score_semantics", "probability")
        scores = [float(record["score"]) for record in records]
        labels = [int(record["label"]) for record in records]
        threshold = float(item["decision_threshold"])
        above = [record for record in records if math.isfinite(float(record["score"])) and float(record["score"]) >= threshold]
        abstained = sum(bool(record.get("abstained")) for record in above)
        risk_rejected = sum(bool(record.get("risk_rejected")) for record in above if not record.get("abstained"))
        other_rejected = sum(bool(record.get("rule_rejected")) for record in above if not record.get("abstained") and not record.get("risk_rejected"))
        final = len(above) - abstained - risk_rejected - other_rejected
        warnings = []
        if semantics != "probability":
            warnings.append("score_has_no_event_probability_semantics")
        if spec.get("sample_weight_policy") not in (None, "uniform", "none") and not item.get("calibration_audit", {}).get("supports_unweighted_probability", False):
            warnings.append("weighted_classifier_score_not_established_as_unweighted_event_probability")
        coverage.append({"candidate": candidate, "candidate_lane": lane, "fold_index": fold_index,
                         "validation_observations": len(records), "distinct_securities": len({str(r["security"]) for r in records}),
                         "distinct_sessions": len({str(r["session"]) for r in records}),
                         "distinct_economic_units": len({str(r.get("economic_unit", r["id"])) for r in records}),
                         "target_definition": item.get("target_definition"), "class_prevalence": sum(labels) / len(labels) if labels else None,
                         "training_prevalence_baseline": item.get("training_prevalence"),
                         "effective_training_weight": item.get("effective_training_weight"), "sample_weight_policy": spec.get("sample_weight_policy"),
                         "score_semantics": semantics, "score_quantiles": _quantiles([s for s in scores if math.isfinite(s)]),
                         "configured_threshold": threshold, "above_threshold": len(above), "removed_by_abstention": abstained,
                         "removed_by_risk_filter": risk_rejected, "removed_by_other_signal_rules": other_rejected,
                         "final_positive_signals": final, "funnel_reconciles": len(above) == abstained + risk_rejected + other_rejected + final,
                         "execution": item.get("execution", {"available": False, "reason": "no_oof_execution_evidence"}), "warnings": warnings})
        metrics = calibration_metrics(scores, labels) if semantics == "probability" else {"available": False, "warnings": warnings}
        calibration.append({"candidate": candidate, "candidate_lane": lane, "fold_index": fold_index,
                            "training_prevalence_baseline": item.get("training_prevalence"),
                            "calibration_fitting_membership": item.get("calibration_audit", {}), **metrics})
        grid = sorted(set(THRESHOLD_GRID + (threshold,)))
        sensitivity.append({"candidate": candidate, "candidate_lane": lane, "fold_index": fold_index,
                            "score_semantics": semantics, "other_signal_rules_held_fixed": True,
                            "results": [_threshold_result(records, value) for value in grid]})
        lineage.append({"candidate": candidate, "fold_index": fold_index,
                        "train_ids_sha256": hashlib.sha256("\n".join(sorted(train_ids)).encode()).hexdigest(),
                        "validation_ids_sha256": hashlib.sha256("\n".join(validation_ids).encode()).hexdigest(),
                        "latest_train_label_completion_at": fold.get("latest_train_label_completion_at"),
                        "earliest_validation_decision_at": fold.get("earliest_validation_decision_at"),
                        "embargo_sessions": fold.get("embargo_sessions"), "calibration_audit": item.get("calibration_audit", {})})
    for candidate in sorted(roster):
        records = pooled_records[candidate]
        semantics = pooled_semantics[candidate]
        lane = roster[candidate].get("candidate_lane") or roster[candidate].get("spec", {}).get("candidate_lane", "decision")
        if semantics == "probability":
            calibration.append({"candidate": candidate, "candidate_lane": lane, "summary": "pooled_oof_descriptive_not_independent",
                                **calibration_metrics([float(row["score"]) for row in records], [int(row["label"]) for row in records])})
        configured = [float(item["decision_threshold"]) for item in rows if str(item["model_version"]) == candidate]
        grid = sorted(set(THRESHOLD_GRID + tuple(configured)))
        sensitivity.append({"candidate": candidate, "candidate_lane": lane, "summary": "pooled_oof_descriptive_not_independent",
                            "score_semantics": semantics, "other_signal_rules_held_fixed": True,
                            "temporal_fold_count": len({int(item["fold_index"]) for item in rows if str(item["model_version"]) == candidate}),
                            "results": [_threshold_result(records, value) for value in grid]})
    if diagnostic_ids & holdout_ids:
        raise DevelopmentDiagnosticError("final_holdout_overlap")
    common = {"schema_version": SCHEMA_VERSION, "scope": "development_oof_only", "baseline_code_sha": baseline_sha,
              "research_only": True, "automatic_promotion": False, "ready_for_live_routing": False,
              "canonical_input_sha256": _hash(canonical_input), "split_plan_file_sha256": _hash(split_plan_path),
              "split_plan_sha256": plan.get("plan_sha256"), "manifest_sha256": _hash(manifest_path),
              "predictions_sha256": _hash(predictions_path), "development_id_count": len(development_ids),
              "diagnostic_id_count": len(diagnostic_ids), "final_holdout_overlap_count": 0,
              "candidate_roster": sorted(roster), "prediction_lineage": lineage}
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {}
    for name, key, data in (("development_signal_coverage_report.json", "coverage", coverage),
                            ("development_calibration_report.json", "calibration", calibration),
                            ("development_threshold_sensitivity_report.json", "threshold_sensitivity", sensitivity)):
        path = output_dir / name
        path.write_text(json.dumps({**common, key: data}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        outputs[name] = path
    summary = output_dir / "development_diagnostics_summary.md"
    summary.write_text(f"""# Alpha Atlas V4 development diagnostics\n\n- Scope: **development out-of-fold only**; final holdout overlap: **0**.\n- Baseline SHA: `{baseline_sha}`.\n- Candidates/folds: {len(roster)}/{len(fold_plan)}.\n- Safety: `research_only=true`, `automatic_promotion=false`, `ready_for_live_routing=false`.\n- Threshold grid: fixed 0.30–0.80 by 0.05 plus configured thresholds; descriptive, not selection.\n- Calibration: ten fixed equal-width bins; log loss clips to `{LOG_LOSS_CLIP}` diagnostically only.\n- Execution diagnostics: unavailable unless supplied as existing OOF execution evidence; valuation never filters observations.\n\n## Limitations and next experiment\n\nThese correlated chronological folds are limited development history, not independent trading samples. Weighted scores are not called unweighted event probabilities without supporting calibration evidence. If warranted by these reports, pre-register a temporally valid inner-development calibration or chronological cross-fit experiment with the same purge and exchange-session embargo; do not use pooled validation labels or final holdout outcomes.\n""", encoding="utf-8")
    outputs[summary.name] = summary
    return outputs
