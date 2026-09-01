import json
import sys
from pathlib import Path

import pytest

from moneybot.services.decision_target import (
    FORECAST_HORIZON,
    POSITIVE_CLASS_SEMANTICS,
    TARGET_NAME,
    label_from_forward_return,
    target_metadata,
)
from scripts import train_alpha_atlas_v3_candidate as v3_train
from scripts.backtest_challenger_suite import HORIZON_DAYS as BACKTEST_HORIZON
from scripts.run_track_b_offline import alpha_atlas_v3_summary
from scripts.train_challenger_suite import _target
from scripts.train_massive_baseline_model import TARGET_COLUMN


def test_one_canonical_decision_target_is_used_by_track_b_and_v3():
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame({TARGET_NAME: [0.0, 1.0]})

    assert TARGET_NAME == "label_up_5d"
    assert TARGET_COLUMN == TARGET_NAME
    assert _target(frame, 5) == TARGET_NAME
    assert BACKTEST_HORIZON == 5
    assert FORECAST_HORIZON == "5d"
    assert (
        POSITIVE_CLASS_SEMANTICS == "strictly positive five-trading-bar forward return"
    )
    assert label_from_forward_return(-0.01) == 0.0
    assert label_from_forward_return(0.0) == 0.0
    assert label_from_forward_return(0.01) == 1.0
    assert v3_train.TARGET_NAME == TARGET_NAME


def test_v3_contract_persists_authoritative_target_metadata(tmp_path):
    candidate = tmp_path / "candidate.json"
    candidate.write_text(
        json.dumps(
            {
                "version": v3_train.CANDIDATE_VERSION,
                "feature_columns": list(v3_train.ALPHA_ATLAS_V3_FEATURES),
                "feature_fill_values": {
                    feature: 0.0 for feature in v3_train.ALPHA_ATLAS_V3_FEATURES
                },
            }
        ),
        encoding="utf-8",
    )
    v3_train.attach_v3_contract(candidate, [])
    payload = json.loads(candidate.read_text(encoding="utf-8"))

    for key, value in target_metadata().items():
        assert payload[key] == value
    assert payload["production_feature_contract"]["forecast_horizon"] == "5d"


def test_missing_massive_api_key_fails_before_generating_candidate(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "train_alpha_atlas_v3_candidate.py",
            "--output-dir",
            str(tmp_path / "alpha_atlas_v3"),
        ],
    )

    with pytest.raises(SystemExit, match="MASSIVE_API_KEY is required"):
        v3_train.main()
    assert not (tmp_path / "alpha_atlas_v3").exists()


def test_dry_run_rejects_non_massive_history(tmp_path):
    from moneybot.services.deterministic_model import (
        BaselineModelArtifact,
        save_artifact,
    )

    candidate = tmp_path / "candidate.json"
    artifact = BaselineModelArtifact(
        version=v3_train.CANDIDATE_VERSION,
        feature_columns=list(v3_train.ALPHA_ATLAS_V3_FEATURES),
        means=[0.0] * len(v3_train.ALPHA_ATLAS_V3_FEATURES),
        stds=[1.0] * len(v3_train.ALPHA_ATLAS_V3_FEATURES),
        weights=[0.1] * len(v3_train.ALPHA_ATLAS_V3_FEATURES),
        bias=0.0,
        decision_threshold=0.55,
    )
    save_artifact(artifact, str(candidate))
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    payload["feature_fill_values"] = {
        feature: 0.0 for feature in v3_train.ALPHA_ATLAS_V3_FEATURES
    }
    candidate.write_text(json.dumps(payload), encoding="utf-8")

    bars = [
        {
            "date": (
                f"2026-01-{index + 1:02d}"
                if index < 31
                else f"2026-02-{index - 30:02d}"
            ),
            "close": 20.0 + index * 0.1,
            "volume": 1_000_000 + index * 1_000,
        }
        for index in range(50)
    ]

    class Service:
        def get_price_history_data(self, symbol, days=90):
            return {"bars": bars, "source": "yfinance"}

    runs = v3_train.build_serving_dry_runs(
        artifact_path=candidate, market_service=Service(), symbols=("AAPL",)
    )

    assert runs[0]["history_source"] == "yfinance"
    assert runs[0]["massive_history_required"] is True
    assert runs[0]["feature_contract_servable"] is False


def test_track_b_workflow_keeps_v3_out_of_incompatible_v4_lineage():
    workflow = Path(".github/workflows/track-b-offline.yml").read_text(encoding="utf-8")
    clean_position = workflow.index("Clean and quality-gate training rows")
    backtest_position = workflow.index("Backtest and gate challenger suite")
    v3_position = workflow.index("Record V3 and V3.1 compatibility boundary")
    upload_position = workflow.index("Upload Track B artifacts")

    assert clean_position < backtest_position < v3_position < upload_position
    assert "skipped_incompatible_v4_executable_label_lineage" in workflow
    assert '"v4_input_consumed": False' in workflow
    assert "v3_generation_status.json" in workflow
    assert "scripts/train_alpha_atlas_v3_candidate.py" not in workflow
    assert "data/track_b/**" in workflow
    assert "v3_generation_status.json" in workflow
    assert "day14_promote_candidate.py" not in workflow
    assert "/api/promote-track-b-candidate" not in workflow


def test_track_b_summary_surfaces_real_v3_review_metrics(tmp_path):
    output = tmp_path / "track_b"
    v3_dir = output / "alpha_atlas_v3"
    v3_dir.mkdir(parents=True)
    (v3_dir / "candidate_alpha_atlas_v3_clean.json").write_text(
        json.dumps(
            {
                "version": v3_train.CANDIDATE_VERSION,
                "target_name": TARGET_NAME,
                "forecast_horizon": "5d",
            }
        ),
        encoding="utf-8",
    )
    (v3_dir / "alpha_atlas_v3_model_report.json").write_text(
        json.dumps(
            {
                "target_column": TARGET_NAME,
                "row_counts": {"train": 900, "test": 100},
                "duplicate_weighted_metrics": {
                    "brier_score": 0.19,
                    "avg_selected_return": 0.021,
                    "big_loss_false_positive_rate": 0.04,
                    "big_gain_capture_rate": 0.31,
                },
                "threshold_selection": {"selected_metrics": {"utility_score": 0.12}},
            }
        ),
        encoding="utf-8",
    )
    (v3_dir / "production_servability_certification.json").write_text(
        json.dumps({"passed": True}), encoding="utf-8"
    )
    (v3_dir / "alpha_atlas_v3_recovery_rebaseline_report.json").write_text(
        json.dumps(
            {"comparison_mode": "recovery_rebaseline", "automatic_promotion": False}
        ),
        encoding="utf-8",
    )

    summary = alpha_atlas_v3_summary(output)

    assert summary == {
        "candidate_generated": True,
        "candidate_source_version": v3_train.CANDIDATE_VERSION,
        "target_name": TARGET_NAME,
        "forecast_horizon": "5d",
        "training_rows": 900,
        "final_test_rows": 100,
        "brier_score": 0.19,
        "average_return": 0.021,
        "utility_score": 0.12,
        "big_loss_rate": 0.04,
        "big_gain_capture_rate": 0.31,
        "servability_certification_passed": True,
        "comparison_mode": "recovery_rebaseline",
        "generation_status": "generated",
        "blocking_reason": None,
        "automatic_promotion": False,
    }


def test_track_b_summary_reports_missing_key_skip_without_fake_candidate(tmp_path):
    output = tmp_path / "track_b"
    v3_dir = output / "alpha_atlas_v3"
    v3_dir.mkdir(parents=True)
    (v3_dir / "v3_generation_status.json").write_text(
        json.dumps(
            {
                "status": "skipped_missing_massive_api_key",
                "blocking_reason": "MASSIVE_API_KEY is not configured for real serving dry runs",
                "target_name": TARGET_NAME,
                "forecast_horizon": "5d",
                "comparison_mode": "recovery_rebaseline",
                "automatic_promotion": False,
            }
        ),
        encoding="utf-8",
    )

    summary = alpha_atlas_v3_summary(output)

    assert summary["candidate_generated"] is False
    assert summary["servability_certification_passed"] is False
    assert summary["generation_status"] == "skipped_missing_massive_api_key"
    assert summary["blocking_reason"].startswith("MASSIVE_API_KEY")
    assert summary["automatic_promotion"] is False
