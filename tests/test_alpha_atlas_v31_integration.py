import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from moneybot.services.alpha_atlas_v31_quantitative import (
    V31_L2_VALUES,
    V31_SCALER_RECIPES,
)
from moneybot.services.alpha_atlas_v3_features import ALPHA_ATLAS_V3_FEATURES
from moneybot.services.deterministic_model import BaselineModelArtifact, save_artifact
from scripts import train_alpha_atlas_v31_candidate as trainer
from scripts.run_track_b_offline import alpha_atlas_v31_summary


def test_trainer_refuses_missing_canonical_inputs(tmp_path):
    with pytest.raises(FileNotFoundError, match="Canonical Massive cleaned input"):
        trainer.train_v31(
            tmp_path / "train.jsonl",
            tmp_path / "test.jsonl",
            tmp_path / "all.jsonl",
            tmp_path / "out",
            object(),
        )


def test_declared_matrix_is_bounded_and_safe():
    assert len(V31_SCALER_RECIPES) == 5
    assert V31_L2_VALUES == (1e-4, 1e-3, 1e-2, 1e-1)
    assert {item["scaler_type"] for item in V31_SCALER_RECIPES} == {
        "weighted_standard",
        "robust_iqr",
    }
    for _, features in trainer.ABLATIONS:
        assert set(features) <= set(ALPHA_ATLAS_V3_FEATURES)


def test_recipe_selection_has_no_holdout_argument():
    assert list(__import__("inspect").signature(trainer.select_recipe).parameters) == [
        "train"
    ]


def test_holdout_cannot_open_before_recipe_freeze(tmp_path):
    holdout = tmp_path / "cleaned_test.jsonl"
    holdout.write_text(
        json.dumps(
            {
                "canonical_dataset_schema_version": "massive-decision-training-rows.v2",
                "split_metadata_hash": "fixture-split-hash",
                "price_adjustment_policy": "event_time_split_adjusted",
            }
        )
        + "\n"
    )
    with pytest.raises(RuntimeError, match="before the V3.1 recipe is frozen"):
        trainer._load_holdout_after_freeze(holdout, tmp_path / "missing.json")
    frozen = tmp_path / "frozen.json"
    frozen.write_text("{}")
    assert len(trainer._load_holdout_after_freeze(holdout, frozen)) == 1


def test_missing_massive_key_generates_no_candidate(tmp_path, monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "train_alpha_atlas_v31_candidate.py",
            "--train",
            str(tmp_path / "train"),
            "--test",
            str(tmp_path / "test"),
            "--all-cleaned",
            str(tmp_path / "all"),
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )
    with pytest.raises(SystemExit, match="MASSIVE_API_KEY is required"):
        trainer.main()
    assert not (tmp_path / "out").exists()


def test_track_b_summary_keeps_v31_separate_and_never_promotes(tmp_path):
    directory = tmp_path / "alpha_atlas_v31"
    directory.mkdir()
    (directory / "candidate_alpha_atlas_v31_clean.json").write_text(
        json.dumps(
            {
                "version": "candidate-alpha-atlas-v31-clean-v1",
                "feature_columns": ["feature_return_1d_lagged"],
            }
        )
    )
    (directory / "alpha_atlas_v31_frozen_recipe.json").write_text(
        json.dumps(
            {
                "recipe_frozen_before_holdout": True,
                "scaler_type": "weighted_standard",
                "l2": 0.01,
                "automatic_promotion": False,
            }
        )
    )
    summary = alpha_atlas_v31_summary(tmp_path)
    assert summary["candidate_source_version"] == "candidate-alpha-atlas-v31-clean-v1"
    assert summary["recipe_frozen_before_holdout"] is True
    assert summary["automatic_promotion"] is False


def test_workflow_keeps_v31_out_of_incompatible_v4_lineage():
    workflow = Path(".github/workflows/track-b-offline.yml").read_text()
    clean = workflow.index("Clean and quality-gate training rows")
    v31 = workflow.index("Record V3 and V3.1 compatibility boundary")
    assert clean < v31
    assert "scripts/train_alpha_atlas_v31_candidate.py" not in workflow
    assert "${{ env.TRACK_B_ARTIFACT_DIR }}/alpha_atlas_v31/**" in workflow
    assert "skipped_incompatible_v4_executable_label_lineage" in workflow


def _canonical_rows(start, dates, symbols):
    rows = []
    for day in range(dates):
        event_date = (pd.Timestamp(start) + pd.Timedelta(days=day)).date().isoformat()
        for symbol_index, symbol in enumerate(symbols):
            scale = (day + 1) * (symbol_index + 1)
            row = {
                "symbol": symbol,
                "event_date": event_date,
                "endpoint": "quick_ask",
                "decision_source": "deterministic_model",
                "label_up_5d": float((day + symbol_index) % 3 != 0),
                "return_5d": (-0.04 if day % 7 == 0 else 0.01 * ((day % 5) - 1)),
                "canonical_dataset_schema_version": "massive-decision-training-rows.v2",
                "split_metadata_hash": "fixture-split-hash",
                "price_adjustment_policy": "event_time_split_adjusted",
            }
            for feature_index, feature in enumerate(ALPHA_ATLAS_V3_FEATURES):
                row[feature] = scale * (feature_index + 1) / 1000.0
            rows.append(row)
    return rows


def _jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


class _MassiveHistory:
    def get_price_history_data(self, symbol, days=90):
        factor = 1.0 + (sum(ord(char) for char in symbol) % 9) / 100.0
        bars = [
            {
                "date": (pd.Timestamp("2025-01-01") + pd.Timedelta(days=index))
                .date()
                .isoformat(),
                "close": factor * (50 + index + ((index % 5) - 2)),
                "volume": 100_000 + (index * 1_000) + (factor * 100),
            }
            for index in range(90)
        ]
        return {"source": "massive", "bars": bars}


def test_end_to_end_freezes_before_holdout_and_binds_certification(
    tmp_path, monkeypatch
):
    train_path, test_path, all_path = (
        tmp_path / name for name in ("train.jsonl", "test.jsonl", "all.jsonl")
    )
    train_rows = _canonical_rows("2024-01-01", 80, ["A", "B"])
    test_rows = _canonical_rows("2025-01-01", 8, ["A", "B"])
    _jsonl(train_path, train_rows)
    _jsonl(test_path, test_rows)
    _jsonl(all_path, train_rows + test_rows)
    monkeypatch.setenv("MASSIVE_API_KEY", "test-key")
    output = tmp_path / "alpha_atlas_v31"
    v3_path = tmp_path / "alpha_atlas_v3" / "candidate_alpha_atlas_v3_clean.json"
    v3_path.parent.mkdir()
    v3_feature = ALPHA_ATLAS_V3_FEATURES[0]
    save_artifact(
        BaselineModelArtifact(
            version="candidate-alpha-atlas-v3-clean-v1",
            feature_columns=[v3_feature],
            means=[0.0],
            stds=[1.0],
            weights=[0.0],
            bias=-10.0,
            decision_threshold=0.55,
        ),
        v3_path,
    )
    v3_payload = json.loads(v3_path.read_text())
    v3_payload["feature_fill_values"] = {v3_feature: 0.0}
    v3_path.write_text(json.dumps(v3_payload))
    report = trainer.train_v31(
        train_path, test_path, all_path, output, _MassiveHistory()
    )
    frozen = json.loads((output / "alpha_atlas_v31_frozen_recipe.json").read_text())
    certification = json.loads(
        (output / "production_servability_certification.json").read_text()
    )
    candidate = json.loads(
        (output / "candidate_alpha_atlas_v31_clean.json").read_text()
    )
    assert report["recipe_frozen_before_holdout"] is True
    assert frozen["holdout_input"] is None
    assert frozen["clip_lower"] == candidate["clip_lower"]
    assert frozen["recipe_hash"] == candidate["lineage"]["recipe_hash"]
    assert certification["candidate_artifact_sha256"]
    assert certification["passed"] is True
    assert candidate["automatic_promotion"] is False
    comparison_path = output / "alpha_atlas_v31_candidate_iteration_comparison.json"
    assert comparison_path.is_file()
    comparison = json.loads(comparison_path.read_text())
    assert comparison["bootstrap"]["metrics"]["brier_score"]["available"] is True
    assert (
        comparison["bootstrap"]["metrics"]["avg_selected_return"]["available"] is False
    )
    assert report["automatic_promotion"] is False


def test_bootstrap_reports_metric_specific_unavailable_evidence(monkeypatch):
    frame = pd.DataFrame(
        {
            "event_date": ["2026-01-01", "2026-01-02", "2026-01-03"],
            "symbol": ["AAA", "BBB", "CCC"],
            "return_5d": [0.01, -0.01, 0.02],
            "label_up_5d": [1.0, 0.0, 1.0],
        }
    )

    def metric_specific_score(sampled, probabilities, threshold, weights):
        is_v31 = float(np.mean(probabilities)) > 0.5
        return {
            "brier_score": 0.10 if is_v31 else 0.20,
            # Both candidates legitimately make zero positive selections.
            "avg_selected_return": None,
            # Non-finite paired deltas must also be treated as unavailable.
            "big_loss_false_positive_rate": np.inf if is_v31 else 0.0,
        }

    monkeypatch.setattr(trainer, "_score", metric_specific_score)
    result = trainer._date_block_bootstrap(
        frame,
        np.full(len(frame), 0.1),
        np.full(len(frame), 0.9),
        0.55,
        0.55,
        samples=20,
    )

    assert result["available"] is True
    assert result["metrics"]["brier_score"] == {
        "available": True,
        "sample_count": 20,
        "mean_delta": pytest.approx(-0.1),
        "confidence_interval_95": pytest.approx([-0.1, -0.1]),
        "probability_v31_improves": 1.0,
    }
    assert result["metrics"]["avg_selected_return"] == {
        "available": False,
        "sample_count": 0,
        "reason": "no paired finite bootstrap observations",
        "mean_delta": None,
        "confidence_interval_95": None,
        "probability_v31_improves": None,
    }
    assert result["metrics"]["big_loss_false_positive_rate"]["available"] is False


def test_bootstrap_normal_case_preserves_all_metric_results(monkeypatch):
    frame = pd.DataFrame(
        {
            "event_date": ["2026-01-01", "2026-01-02", "2026-01-03"],
            "symbol": ["AAA", "BBB", "CCC"],
        }
    )

    def complete_score(sampled, probabilities, threshold, weights):
        is_v31 = float(np.mean(probabilities)) > 0.5
        return {
            "brier_score": 0.15 if is_v31 else 0.20,
            "avg_selected_return": 0.03 if is_v31 else 0.01,
            "big_loss_false_positive_rate": 0.01 if is_v31 else 0.02,
        }

    monkeypatch.setattr(trainer, "_score", complete_score)
    result = trainer._date_block_bootstrap(
        frame,
        np.full(len(frame), 0.1),
        np.full(len(frame), 0.9),
        0.55,
        0.55,
        samples=12,
    )

    assert result["available"] is True
    for metric in (
        "brier_score",
        "avg_selected_return",
        "big_loss_false_positive_rate",
    ):
        assert result["metrics"][metric]["available"] is True
        assert result["metrics"][metric]["sample_count"] == 12
        assert result["metrics"][metric]["confidence_interval_95"] is not None
