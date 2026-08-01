import json

import pandas as pd

from moneybot.services.deterministic_model import BaselineModelArtifact, save_artifact
from scripts import day11_compare_candidate_vs_production as compare


def _artifact(path, *, weight, threshold=0.55, feature="feature_signal"):
    save_artifact(
        BaselineModelArtifact(
            version=path.stem,
            feature_columns=[feature],
            means=[0.0],
            stds=[1.0],
            weights=[weight],
            bias=0.0,
            decision_threshold=threshold,
        ),
        path,
    )


def test_future_feature_leakage_audit_rejects_label_and_realized_features(tmp_path):
    safe = tmp_path / "safe.json"
    unsafe = tmp_path / "unsafe.json"
    _artifact(safe, weight=1.0)
    _artifact(unsafe, weight=1.0, feature="feature_forward_return_5d")

    audit = compare._future_feature_leakage_audit(str(safe), str(unsafe))

    assert audit["passed"] is False
    assert audit["violations"] == [{"artifact_path": str(unsafe), "feature": "feature_forward_return_5d"}]


def test_phase_1_certification_reports_all_required_checks(tmp_path):
    candidate_path = tmp_path / "candidate.json"
    production_path = tmp_path / "production.json"
    _artifact(candidate_path, weight=2.0, threshold=0.60)
    _artifact(production_path, weight=-1.0, threshold=0.55)
    boundaries = [
        {"date_overlap_count": 0, "symbol_date_overlap_count": 0, "label_horizon_gap_passed": True},
        {"date_overlap_count": 0, "symbol_date_overlap_count": 0, "label_horizon_gap_passed": True},
        {"date_overlap_count": 0, "symbol_date_overlap_count": 0, "label_horizon_gap_passed": True},
    ]
    metadata = {
        "metrics": {
            "recipe_reproduction": {"passed": True, "maximum_parameter_delta": 0.0},
            "training_periods": {
                "purged_embargoed": True,
                "windows": {
                    "fit": {"rows": 20, "start": "2026-01-01", "end": "2026-01-20"},
                    "calibration": {"rows": 5, "start": "2026-01-22", "end": "2026-01-26"},
                    "threshold_selection": {"rows": 5, "start": "2026-01-28", "end": "2026-02-01"},
                    "final_test": {"rows": 12, "start": "2026-02-03", "end": "2026-02-14"},
                },
                "purge_embargo_boundaries": boundaries,
            }
        }
    }
    candidate_path.with_suffix(".json.meta.json").write_text(json.dumps(metadata), encoding="utf-8")
    frame = pd.DataFrame(
        {
            "symbol": [f"S{idx % 4}" for idx in range(12)],
            "event_date": pd.date_range("2026-02-03", periods=12, freq="D").astype(str),
            "feature_signal": [1.0, -1.0] * 6,
            "return_5d": [0.04, -0.04] * 6,
            "return_bin_5d": ["big_gain", "big_loss"] * 6,
        }
    )
    candidate_metrics = compare._evaluate(str(candidate_path), frame.copy())
    candidate_metrics["feature_risk_audit"] = compare._feature_risk_audit(str(candidate_path), frame.copy())
    production_metrics = compare._evaluate(str(production_path), frame.copy())
    clone = compare._clone_detection(str(candidate_path), str(production_path), frame.copy())
    decision_win, _ = compare._decide(candidate_metrics, production_metrics, min_rows=1)
    ranking_win, _, _ = compare._ranking_lane_decide(candidate_metrics, production_metrics)
    walk_forward = compare._walk_forward_validation(str(candidate_path), str(production_path), frame.copy(), min_rows=1)
    promotion = compare._promotion_decision(decision_win and ranking_win and bool(walk_forward["consistent"]), False, decision_win, ranking_win, bool(walk_forward["consistent"]))
    threshold = compare._threshold_optimizer_report(str(candidate_path), candidate_metrics, frame.copy(), min_rows=1)
    examples = compare._prediction_error_examples(str(candidate_path), str(production_path), frame.copy())

    certification = compare._phase_1_certification(
        candidate_model_path=str(candidate_path),
        production_model_path=str(production_path),
        test_df=frame,
        min_rows=1,
        candidate_metrics=candidate_metrics,
        clone_detection=clone,
        walk_forward=walk_forward,
        threshold_optimizer=threshold,
        promotion_decision=promotion,
        report_examples=examples,
    )

    required = {
        "phase_1_certified",
        "reproducible",
        "recipe_reproduction_passed",
        "split_hygiene_passed",
        "purge_embargo_passed",
        "future_feature_leakage_passed",
        "artifact_scored_mistake_mining_passed",
        "cmi_regression_test_passed",
        "clone_detection_passed",
        "threshold_walk_forward_guardrails_passed",
        "symbol_date_concentration_passed",
        "blocking_issues",
    }
    assert required.issubset(certification)
    assert certification["reproducible"] is True
    assert certification["split_hygiene_passed"] is True
    assert certification["purge_embargo_passed"] is True
    assert certification["future_feature_leakage_passed"] is True
    assert certification["artifact_scored_mistake_mining_passed"] is True
