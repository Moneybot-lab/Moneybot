import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from moneybot.services.deterministic_model import BaselineModelArtifact, load_artifact, save_artifact
from scripts.day11_compare_candidate_vs_production import (
    _comparison_promotion_evidence,
    _comparison_scope_report,
    _resolve_massive_comparator,
)
from scripts.train_massive_baseline_model import (
    MODEL_ECHO_FEATURES,
    _duplicate_weights,
    _market_feature_columns,
    train_massive_baseline,
)


def _rows(start: str, dates: int, rows_per_date: int = 2):
    output = []
    for date_index, date in enumerate(pd.date_range(start, periods=dates, freq="D")):
        for duplicate in range(rows_per_date):
            up = (date_index + duplicate) % 2
            output.append({
                "event_date": date.strftime("%Y-%m-%d"),
                "symbol": f"SYM{date_index % 10}",
                "endpoint": "user_watchlist",
                "decision_source": "deterministic_model",
                "label_up_5d": up,
                "return_5d": 0.04 if up else -0.04,
                "label_asof_date": (date + pd.Timedelta(days=5)).strftime("%Y-%m-%d"),
                "feature_return_5d_lagged": 0.02 if up else -0.02,
                "feature_rsi_14": 60.0 if up else 40.0,
                "feature_relative_volume_20d": 1.5 if up else 0.7,
                "feature_spy_return_5d": 0.01,
                "feature_sector_relative_return_5d": 0.005,
                "feature_probability_up_delta_from_last_signal": 0.9,
                "feature_previous_recommendation_buy": 1.0,
                "feature_endpoint_user_watchlist": 1.0,
                "probability_up": 0.95,
                "recommendation": "BUY",
                "model_version": "legacy",
            })
    return output


def _write(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_massive_baseline_trains_from_cleaned_inputs_and_excludes_unsafe_features(tmp_path):
    quality = tmp_path / "data" / "track_b" / "training_quality"
    train_path = quality / "cleaned_train.jsonl"
    test_path = quality / "cleaned_test.jsonl"
    all_path = quality / "cleaned_all.jsonl"
    train_rows = _rows("2026-01-01", 60)
    test_rows = _rows("2026-03-10", 12)
    _write(train_path, train_rows)
    _write(test_path, test_rows)
    _write(all_path, train_rows + test_rows)
    output = tmp_path / "data" / "track_b" / "massive_baseline_model_v1.json"

    report = train_massive_baseline(train_path, test_path, all_path, output)
    artifact = load_artifact(output)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert artifact.version == "massive_baseline_model_v1"
    assert artifact.lineage["target_column"] == "label_up_5d"
    assert artifact.lineage["evaluation_return_column"] == "return_5d"
    assert artifact.lineage["sample_weight_policy"] == "1 / count(symbol, event_date, endpoint, decision_source)"
    assert payload["model_version"] == "massive_baseline_model_v1"
    assert payload["target_column"] == "label_up_5d"
    assert payload["evaluation_return_column"] == "return_5d"
    assert payload["horizon_days"] == 5
    assert payload["target_name"] == "label_up_5d"
    assert payload["target_definition"] == "1 when close(T+5 trading bars) / close(T) - 1 > 0; otherwise 0"
    assert payload["decision_target"] == report["decision_target"]
    assert payload["duplicate_weighting_applied"] is True
    assert payload["selected_threshold"] == artifact.decision_threshold
    assert isinstance(payload["threshold_selection_sufficient"], bool)
    assert payload["calibration"] == report["calibration"]
    assert report["training_inputs"] == {"train": str(train_path), "test": str(test_path), "all_cleaned": str(all_path)}
    assert report["temporal_validation"]["cleaned_test_untouched_for_final_holdout"] is True
    assert report["duplicate_weighting_applied"] is True
    assert "feature_return_5d_lagged" in artifact.feature_columns
    assert "feature_spy_return_5d" in artifact.feature_columns
    assert not ({"return_5d", "label_up_5d", "label_asof_date", "feature_return_5d"} & set(artifact.feature_columns))
    assert not (MODEL_ECHO_FEATURES & set(artifact.feature_columns))
    assert (output.parent / "massive_baseline_model_report.json").exists()
    assert (output.parent / "massive_baseline_feature_coverage_report.json").exists()
    assert (output.parent / "massive_baseline_backtest_report.json").exists()


def test_duplicate_weights_are_inverse_group_counts():
    frame = pd.DataFrame([
        {"symbol": "A", "event_date": "2026-01-01", "endpoint": "watch", "decision_source": "model"},
        {"symbol": "A", "event_date": "2026-01-01", "endpoint": "watch", "decision_source": "model"},
        {"symbol": "B", "event_date": "2026-01-01", "endpoint": "watch", "decision_source": "model"},
    ])

    assert np.allclose(_duplicate_weights(frame), [0.5, 0.5, 1.0])


def test_day10_cleaned_mode_builds_candidate_market_no_echo_v1(tmp_path):
    quality = tmp_path / "training_quality"
    train_path = quality / "cleaned_train.jsonl"
    test_path = quality / "cleaned_test.jsonl"
    all_path = quality / "cleaned_all.jsonl"
    train_rows = _rows("2026-01-01", 60)
    test_rows = _rows("2026-03-10", 12)
    _write(train_path, train_rows)
    _write(test_path, test_rows)
    _write(all_path, train_rows + test_rows)
    output = tmp_path / "candidate_market_no_echo_v1.json"

    completed = subprocess.run([
        sys.executable,
        "scripts/day10_train_candidate_model.py",
        "--cleaned-train", str(train_path),
        "--cleaned-test", str(test_path),
        "--cleaned-all", str(all_path),
        "--model-version", "candidate_market_no_echo_v1",
        "--output-model", str(output),
    ], capture_output=True, text=True, check=False)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["model_version"] == "candidate_market_no_echo_v1"
    assert payload["target_column"] == "label_up_5d"
    assert payload["evaluation_return_column"] == "return_5d"
    assert payload["horizon_days"] == 5
    assert payload["sample_weight_policy"] == "1 / count(symbol, event_date, endpoint, decision_source)"
    assert payload["duplicate_weighting_applied"] is True
    assert not (MODEL_ECHO_FEATURES & set(payload["feature_columns"]))


def test_feature_policy_rejects_future_echo_and_endpoint_fields():
    frame = pd.DataFrame({
        "feature_return_5d_lagged": [0.1],
        "feature_rsi_14": [50.0],
        "feature_return_5d": [0.3],
        "feature_future_return_5d": [0.3],
        "feature_probability_up_delta_from_last_signal": [0.4],
        "feature_endpoint_user_watchlist": [1.0],
    })

    assert _market_feature_columns(frame) == ["feature_return_5d_lagged", "feature_rsi_14"]


def test_massive_comparator_is_selected_but_legacy_default_fill_remains_diagnostic(tmp_path):
    baseline = tmp_path / "massive_baseline_model_v1.json"
    save_artifact(BaselineModelArtifact(
        version="massive_baseline_model_v1",
        feature_columns=["feature_rsi_14"], means=[50.0], stds=[10.0], weights=[1.0], bias=0.0,
        decision_threshold=0.55, lineage={"target_column": "label_up_5d"},
    ), baseline)

    resolved = _resolve_massive_comparator("data/track_b/decision_training_snapshot_massive.jsonl", "legacy.json", str(baseline))
    assert resolved["comparator_kind"] == "massive_baseline_model_v1"
    assert resolved["production_scoring_mode"] == "native_or_valid_adapter"

    frame = pd.DataFrame({
        "event_date": pd.date_range("2026-01-01", periods=1000).astype(str),
        "symbol": [f"S{i % 20}" for i in range(1000)],
        "endpoint": ["watch"] * 1000,
    })
    candidate_coverage = {"features": {"feature_rsi_14": {"availability_rate": 1.0}}}
    baseline_coverage = {"features": {"feature_rsi_14": {"availability_rate": 1.0}}}
    valid = _comparison_scope_report(
        "data/track_b/decision_training_snapshot_massive.jsonl", frame, candidate_coverage, baseline_coverage,
        target_columns_match=True, duplicate_weighting_policy_match=True,
    )
    invalid = _comparison_scope_report(
        "data/track_b/decision_training_snapshot_massive.jsonl", frame, candidate_coverage,
        {"features": {"legacy": {"availability_rate": 0.0}}},
        target_columns_match=True, duplicate_weighting_policy_match=True,
    )

    assert valid["comparison_valid"] is True
    assert valid["apples_to_apples_scoring"] is True
    assert valid["promotion_eligible_evidence"] is True
    assert invalid["comparison_valid"] is False
    assert invalid["allowed_uses"] == ["diagnostic_only"]

    eligible = _comparison_promotion_evidence(valid, leakage_passed=True, threshold_support_passed=True, concentration_passed=True)
    threshold_blocked = _comparison_promotion_evidence(valid, leakage_passed=True, threshold_support_passed=False, concentration_passed=True)
    assert eligible["promotion_eligible_evidence"] is True
    assert threshold_blocked["promotion_eligible_evidence"] is False
