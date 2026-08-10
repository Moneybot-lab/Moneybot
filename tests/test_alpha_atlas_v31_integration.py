import json
import sys
from pathlib import Path

import pandas as pd
import pytest

from moneybot.services.alpha_atlas_v31_quantitative import (
    V31_L2_VALUES,
    V31_SCALER_RECIPES,
)
from moneybot.services.alpha_atlas_v3_features import ALPHA_ATLAS_V3_FEATURES
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
    holdout.write_text("{}\n")
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


def test_workflow_runs_v31_after_cleaning_and_uploads_directory():
    workflow = Path(".github/workflows/track-b-offline.yml").read_text()
    clean = workflow.index("Clean and quality-gate training rows")
    v31 = workflow.index("Train Alpha Atlas V3.1 human-review candidate")
    assert clean < v31
    assert "scripts/train_alpha_atlas_v31_candidate.py" in workflow
    assert "data/track_b/alpha_atlas_v31/**" in workflow
    assert "MASSIVE_API_KEY: ${{ secrets.MASSIVE_API_KEY }}" in workflow


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
