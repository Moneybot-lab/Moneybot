"""Concentration and score-stability analysis over an existing V4 OOF capture."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterable

from moneybot.services.alpha_atlas_v4_development_diagnostics import (
    LOG_LOSS_CLIP,
    QUANTILES,
    RELIABILITY_EDGES,
)

CONCENTRATION_SCHEMA = "alpha-atlas-v4-development-concentration.v1"
STABILITY_SCHEMA = "alpha-atlas-v4-development-score-stability.v1"
REPORTING_VIEWS = ("observation_weighted", "symbol_date_balanced", "date_balanced")


class ConcentrationDiagnosticError(ValueError):
    """Raised when capture provenance or analysis inputs fail closed."""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reporting_weights(records: list[dict[str, Any]], view: str) -> list[float]:
    """Return weights normalized to one over the supplied population."""
    if view not in REPORTING_VIEWS:
        raise ConcentrationDiagnosticError("unknown_reporting_view")
    if not records:
        return []
    if view == "observation_weighted":
        return [1.0 / len(records)] * len(records)
    keys = [
        (str(row["security"]), str(row["session"]))
        if view == "symbol_date_balanced"
        else str(row["session"])
        for row in records
    ]
    counts = Counter(keys)
    group_count = len(counts)
    return [1.0 / (group_count * counts[key]) for key in keys]


def _weighted_mean(values: Iterable[float], weights: Iterable[float]) -> float | None:
    pairs = [(float(value), float(weight)) for value, weight in zip(values, weights)]
    total = sum(weight for _, weight in pairs)
    return sum(value * weight for value, weight in pairs) / total if total else None


def _weighted_quantiles(values: list[float], weights: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(zip(values, weights), key=lambda item: item[0])
    total = sum(weight for _, weight in ordered)
    result: dict[str, float] = {}
    for quantile in QUANTILES:
        target = quantile * total
        cumulative = 0.0
        chosen = ordered[-1][0]
        for value, weight in ordered:
            cumulative += weight
            if cumulative >= target:
                chosen = value
                break
        result[f"q{int(quantile * 100):02d}"] = float(chosen)
    return result


def weighted_metrics(records: list[dict[str, Any]], view: str, threshold: float,
                     *, probability_semantics: bool) -> dict[str, Any]:
    weights = reporting_weights(records, view)
    warnings: list[str] = []
    if not records:
        return {"available": False, "warnings": ["empty_population"]}
    scores = [float(row["score"]) for row in records]
    if any(not math.isfinite(score) or (probability_semantics and not 0 <= score <= 1) for score in scores):
        return {"available": False, "warnings": ["invalid_scores"]}
    labels = [int(row["label"]) for row in records]
    if len(set(labels)) < 2:
        warnings.append("single_class_population")
    selected = [
        index for index, row in enumerate(records)
        if scores[index] >= threshold and not row.get("abstained")
        and not row.get("risk_rejected") and not row.get("rule_rejected")
    ]
    selected_weight = sum(weights[index] for index in selected)
    prevalence = _weighted_mean(labels, weights)
    precision = (
        sum(labels[index] * weights[index] for index in selected) / selected_weight
        if selected_weight else None
    )
    positive_weight = sum(weight for label, weight in zip(labels, weights) if label)
    recall = (
        sum(labels[index] * weights[index] for index in selected) / positive_weight
        if positive_weight else None
    )
    if not selected:
        warnings.append("empty_selection")
    result: dict[str, Any] = {
        "available": True,
        "reporting_view": view,
        "target_prevalence": prevalence,
        "score_mean": _weighted_mean(scores, weights),
        "score_quantiles": _weighted_quantiles(scores, weights),
        "mean_score_minus_prevalence": (
            _weighted_mean(scores, weights) - prevalence if prevalence is not None else None
        ),
        "signal_count": len(selected),
        "signal_rate_full_validation_denominator": selected_weight,
        "precision_selected_weight_renormalized": precision,
        "recall_full_validation_positive_denominator": recall,
        "warnings": warnings,
    }
    selected_returns = [
        (float(records[index]["return"]), weights[index])
        for index in selected if records[index].get("return") is not None
    ]
    result["selected_return"] = {
        "available": bool(selected_returns),
        "mean_gross_forward_return_selected_weight_renormalized": _weighted_mean(
            [value for value, _ in selected_returns], [weight for _, weight in selected_returns]
        ),
        "costs_applied": False,
        "portfolio_pnl_interpretation_allowed": False,
    }
    if probability_semantics:
        result.update(_weighted_calibration(scores, labels, weights, warnings))
    else:
        result["calibration"] = {"available": False, "reason": "score_lacks_probability_semantics"}
    return result


def _weighted_calibration(scores: list[float], labels: list[int], weights: list[float],
                          warnings: list[str]) -> dict[str, Any]:
    bins = []
    ece = 0.0
    for index in range(10):
        lower, upper = RELIABILITY_EDGES[index:index + 2]
        members = [i for i, score in enumerate(scores) if score >= lower and (score < upper or (index == 9 and score <= upper))]
        mass = sum(weights[i] for i in members)
        if members and len(members) < 10:
            warnings.append(f"sparse_bin_{index}")
        mean_score = _weighted_mean([scores[i] for i in members], [weights[i] for i in members])
        observed = _weighted_mean([labels[i] for i in members], [weights[i] for i in members])
        if mean_score is not None and observed is not None:
            ece += mass * abs(mean_score - observed)
        bins.append({"index": index, "count": len(members), "reporting_weight_mass": mass,
                     "mean_score": mean_score, "observed_frequency": observed})
    clipped = [min(1 - LOG_LOSS_CLIP, max(LOG_LOSS_CLIP, score)) for score in scores]
    return {"calibration": {"available": True,
            "brier_score": sum(weight * (score - label) ** 2 for score, label, weight in zip(scores, labels, weights)),
            "log_loss": -sum(weight * (label * math.log(score) + (1 - label) * math.log(1 - score))
                             for score, label, weight in zip(clipped, labels, weights)),
            "log_loss_diagnostic_clip": LOG_LOSS_CLIP, "ece": ece,
            "ece_definition": "10 fixed equal-width bins [0,.1),...,[.9,1] using reporting-weight mass",
            "reliability_bins": bins}}


def _concentration(records: list[dict[str, Any]], selected: list[dict[str, Any]], key_fields: tuple[str, ...]) -> dict[str, Any]:
    population_counts = Counter(tuple(str(row[field]) for field in key_fields) for row in records)
    selected_counts = Counter(tuple(str(row[field]) for field in key_fields) for row in selected)
    ordered = sorted(selected_counts.items(), key=lambda item: (-item[1], item[0]))
    total = len(selected)
    def shares(count: int) -> float | None:
        return sum(value for _, value in ordered[:count]) / total if total else None

    return {"grouping_fields": list(key_fields), "population_group_count": len(population_counts),
            "selected_group_count": len(selected_counts), "largest_selected_group_share": shares(1),
            "top_three_selected_group_share": shares(3), "top_five_selected_group_share": shares(5),
            "largest_selected_groups": [{"group": list(key), "count": count} for key, count in ordered[:5]],
            "population_group_size_distribution": _size_distribution(population_counts.values())}


def _size_distribution(values: Iterable[int]) -> dict[str, Any]:
    values = list(values)
    return {"minimum": min(values) if values else None, "median": statistics.median(values) if values else None,
            "maximum": max(values) if values else None, "groups": len(values)}


def _patterns(records: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        grouped[(str(row["security"]), str(row["session"]))].append(row)
    return {"repeated_exact_score_outcome_patterns": sum(
                count - 1 for count in Counter((float(row["score"]), int(row["label"])) for row in records).values() if count > 1),
            "mixed_label_symbol_date_groups": sum(len({int(row["label"]) for row in group}) > 1 for group in grouped.values()),
            "mixed_return_symbol_date_groups": sum(len({row.get("return") for row in group if row.get("return") is not None}) > 1 for group in grouped.values())}


def build_reports(capture: list[dict[str, Any]], *, common: dict[str, Any], selected_candidate: str) -> tuple[dict[str, Any], dict[str, Any]]:
    concentration_rows, stability_rows = [], []
    for item in capture:
        records = item["records"]
        threshold = float(item["decision_threshold"])
        selected = [row for row in records if float(row["score"]) >= threshold and not row.get("abstained") and not row.get("risk_rejected") and not row.get("rule_rejected")]
        semantics = item.get("score_semantics") == "probability"
        views = {view: weighted_metrics(records, view, threshold, probability_semantics=semantics) for view in REPORTING_VIEWS}
        base = {"candidate": item["model_version"], "fold_index": item["fold_index"], "existing_threshold": threshold,
                "observation_count": len(records), "security_count": len({row["security"] for row in records}),
                "date_count": len({row["session"] for row in records}),
                "security_date_count": len({(row["security"], row["session"]) for row in records}),
                "positive_signal_count": len(selected), "positive_signal_rate": len(selected) / len(records) if records else 0,
                "selected_security_count": len({row["security"] for row in selected}),
                "selected_date_count": len({row["session"] for row in selected}),
                "selected_security_date_count": len({(row["security"], row["session"]) for row in selected}),
                "training_prevalence_baseline": {"observation_weighted": item.get("training_prevalence"),
                    "balanced_views": None, "warning": "training group membership absent; balanced training baseline unavailable"},
                "probability_meaning_warning": (
                    "weighted classifier score has not been established as an unweighted event probability"
                    if semantics and not item.get("calibration_audit", {}).get("supports_unweighted_probability", False)
                    else None
                ),
                "reporting_views": views, "patterns": _patterns(records),
                "concentration": {"security": _concentration(records, selected, ("security",)),
                    "date": _concentration(records, selected, ("session",)),
                    "security_date": _concentration(records, selected, ("security", "session"))}}
        concentration_rows.append(base)
        stability_rows.append({"candidate": item["model_version"], "fold_index": item["fold_index"],
                               "score_semantics": item.get("score_semantics"), "reporting_views": views})
    sensitivity = _selected_sensitivity(capture, selected_candidate)
    selected_stability = [row for row in stability_rows if row["candidate"] == selected_candidate]
    return ({**common, "schema_version": CONCENTRATION_SCHEMA, "selected_candidate": selected_candidate,
             "grouping_field_meaning": {"security": "observer symbol", "date": "original event_date copied into observer session field; not asserted to be an exchange session and weekend dates are retained"},
             "reporting_weight_definitions": _weight_definitions(), "candidate_fold_results": concentration_rows,
             "selected_candidate_concentration_sensitivity": sensitivity},
            {**common, "schema_version": STABILITY_SCHEMA, "selected_candidate": selected_candidate,
             "folds_are_primary": True, "pooled_summary": None,
             "pooled_reason": "not emitted: correlated folds and no predeclared fold-weight estimand",
             "selected_candidate_fold_comparison": selected_stability,
             "causal_limitation": "fold changes are demonstrated; attributing them to weighting requires a controlled refit",
             "candidate_fold_results": stability_rows})


def _selected_sensitivity(capture: list[dict[str, Any]], candidate: str) -> list[dict[str, Any]]:
    output = []
    for item in capture:
        if item["model_version"] != candidate:
            continue
        records, threshold = item["records"], float(item["decision_threshold"])
        selected = [row for row in records if float(row["score"]) >= threshold and not row.get("abstained") and not row.get("risk_rejected") and not row.get("rule_rejected")]
        counts = Counter((str(row["security"]), str(row["session"])) for row in selected)
        ordered = [key for key, _ in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))]
        variants = []
        for count in (1, 3):
            omitted = set(ordered[:count])
            remaining = [row for row in records if (str(row["security"]), str(row["session"])) not in omitted]
            variants.append({"rule": f"omit_top_{count}_selected_count_symbol_date_groups",
                             "tie_breaking": "descending selected-observation count, then security/date lexical ascending",
                             "removed_groups": [list(key) for key in ordered[:count]],
                             "removed_observation_count": len(records) - len(remaining),
                             "removed_selected_observation_count": sum(counts[key] for key in omitted),
                             "remaining_subset_is_not_a_replacement_backtest": True,
                             "reporting_views": {view: weighted_metrics(remaining, view, threshold,
                                 probability_semantics=item.get("score_semantics") == "probability") for view in REPORTING_VIEWS}})
        output.append({"fold_index": item["fold_index"], "variants": variants})
    return output


def _weight_definitions() -> dict[str, Any]:
    return {"observation_weighted": {"formula": "w_i=1/N", "estimand": "canonical-observation average"},
            "symbol_date_balanced": {"formula": "w_i=1/(G*n_g)", "group_fields": ["security", "original_event_date"], "estimand": "equal-weight symbol/date-group average"},
            "date_balanced": {"formula": "w_i=1/(D*n_d)", "group_fields": ["original_event_date"], "estimand": "equal-weight date average"},
            "selected_metric_rule": "restrict full-population weights to selected observations and renormalize for precision/selected-return; signal rate retains full-validation weight denominator",
            "scope": "reporting_only_never_training_or_selection"}


def analyze_artifact(artifact: Path, output_dir: Path, *, expected_capture_sha256: str,
                     expected_input_sha256: str, expected_split_plan_sha256: str,
                     expected_diagnostic_code_sha: str | None = None,
                     expected_workflow_run: str | None = None,
                     expected_source_run: str | None = None,
                     selected_candidate: str = "challenger-big-loss-avoider-v1") -> dict[str, Path]:
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        if artifact.is_file() and artifact.suffix == ".zip":
            with zipfile.ZipFile(artifact) as archive:
                if any(Path(name).is_absolute() or ".." in Path(name).parts for name in archive.namelist()):
                    raise ConcentrationDiagnosticError("unsafe_zip_member")
                archive.extractall(root)
        elif artifact.is_dir():
            root = artifact
        else:
            raise ConcentrationDiagnosticError("artifact_not_found_or_unsupported")
        captures = list(root.rglob("development_walk_forward_predictions.json"))
        provenances = list(root.rglob("workflow_provenance.json"))
        basic_reports = list(root.rglob("development_signal_coverage_report.json"))
        if len(captures) != 1 or len(provenances) != 1 or len(basic_reports) != 1:
            raise ConcentrationDiagnosticError("incomplete_or_ambiguous_diagnostic_artifact")
        if sha256(captures[0]) != expected_capture_sha256:
            raise ConcentrationDiagnosticError("capture_sha256_mismatch")
        provenance, basic = json.loads(provenances[0].read_text()), json.loads(basic_reports[0].read_text())
        if provenance.get("capture_sha256") != expected_capture_sha256:
            raise ConcentrationDiagnosticError("provenance_capture_sha256_mismatch")
        for actual, expected, error in (
            (provenance.get("diagnostic_code_sha"), expected_diagnostic_code_sha, "diagnostic_code_sha_mismatch"),
            (provenance.get("diagnostic_workflow_run"), expected_workflow_run, "diagnostic_workflow_run_mismatch"),
            (str(provenance.get("source_track_b_run_id")), expected_source_run, "source_track_b_run_mismatch"),
        ):
            if expected is not None and actual != expected:
                raise ConcentrationDiagnosticError(error)
        if basic.get("canonical_input_sha256") != expected_input_sha256 or basic.get("split_plan_sha256") != expected_split_plan_sha256:
            raise ConcentrationDiagnosticError("certified_input_or_split_hash_mismatch")
        if basic.get("final_holdout_overlap_count") != 0 or basic.get("scope") != "development_oof_only":
            raise ConcentrationDiagnosticError("development_scope_not_certified")
        capture = json.loads(captures[0].read_text())
        roster = set(basic.get("candidate_roster") or [])
        pairs = {(str(item["model_version"]), int(item["fold_index"])) for item in capture}
        expected_pairs = {
            (str(item["candidate"]), int(item["fold_index"]))
            for item in basic.get("prediction_lineage") or []
        }
        captured_ids = {
            str(record["id"]) for item in capture for record in item.get("records", [])
        }
        if ({candidate for candidate, _ in pairs} != roster or len(pairs) != len(capture)
                or not expected_pairs or pairs != expected_pairs
                or len(captured_ids) != int(basic.get("diagnostic_id_count", -1))):
            raise ConcentrationDiagnosticError("capture_roster_or_fold_coverage_mismatch")
        common = {"scope": "development_oof_only", "capture_sha256": expected_capture_sha256,
                  "certified_input_sha256": expected_input_sha256, "frozen_split_plan_sha256": expected_split_plan_sha256,
                  "diagnostic_code_sha": provenance.get("diagnostic_code_sha"), "source_track_b_run_id": provenance.get("source_track_b_run_id"),
                  "candidate_roster": sorted(roster), "fold_identifiers": sorted({fold for _, fold in pairs}),
                  "canonical_observations_preserved": True, "predictions_modified": False,
                  "research_only": True, "automatic_promotion": False, "ready_for_live_routing": False}
        concentration, stability = build_reports(capture, common=common, selected_candidate=selected_candidate)
        output_dir.mkdir(parents=True, exist_ok=True)
        outputs = {"development_concentration_report.json": concentration,
                   "development_score_stability_report.json": stability}
        paths = {}
        for name, payload in outputs.items():
            path = output_dir / name
            path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            paths[name] = path
        summary = output_dir / "development_concentration_summary.md"
        summary.write_text(_summary(concentration), encoding="utf-8")
        paths[summary.name] = summary
        return paths


def _summary(report: dict[str, Any]) -> str:
    selected = [row for row in report["candidate_fold_results"] if row["candidate"] == report["selected_candidate"]]
    lines = ["# V4 development concentration and score stability", "", "Development OOF evidence only; observation-level signals are neither executed trades nor independent bets.", "",
             "## Selected candidate by frozen ranking", "", f"`{report['selected_candidate']}` (highlighted, not re-selected)."]
    for row in sorted(selected, key=lambda value: value["fold_index"]):
        group = row["concentration"]["security_date"]
        views = row["reporting_views"]
        observation = views["observation_weighted"]
        lines += ["", f"### Fold {row['fold_index']}",
                  f"- Signals: {row['positive_signal_count']} / {row['observation_count']}; selected symbol/date groups: {row['selected_security_date_count']}.",
                  f"- Largest/top-three selected symbol/date shares: {group['largest_selected_group_share']} / {group['top_three_selected_group_share']}.",
                  f"- Observation/symbol-date/date-balanced precision: {observation['precision_selected_weight_renormalized']} / {views['symbol_date_balanced']['precision_selected_weight_renormalized']} / {views['date_balanced']['precision_selected_weight_renormalized']}.",
                  f"- Observation-weighted score range/mean: {observation['score_quantiles']['q00']} to {observation['score_quantiles']['q100']} / {observation['score_mean']}.",
                  f"- Observation/symbol-date/date-balanced ECE: {observation.get('calibration', {}).get('ece')} / {views['symbol_date_balanced'].get('calibration', {}).get('ece')} / {views['date_balanced'].get('calibration', {}).get('ece')}."]
    lines += ["", "## Interpretation constraints", "", "Balanced views are reporting estimands only. Forward returns can overlap and are not portfolio P&L. Fold differences demonstrate instability or concentration, not its cause. Establishing whether weighting caused a score shift requires a pre-registered controlled development-only refit holding features, folds, objective family, purge, and embargo fixed.", "", "## One proposed next experiment", "", "Pre-register one development-only controlled objective-weighting ablation on the same frozen chronological folds, comparing current weights with uniform weights while holding every other recipe component fixed; do not inspect or rerun the final holdout.", ""]
    return "\n".join(lines)
