import json
from pathlib import Path

import pandas as pd
import pytest

from moneybot.services.alpha_atlas_v4_canonical_observations import (
    canonical_observation_id,
)
from moneybot.services.alpha_atlas_v4_phase0 import (
    FEATURE_STORE_PROVENANCE_COLUMNS,
    MODEL_FEATURES,
    apply_feature_fill_policy,
    build_temporal_safety_certification,
    feature_registry,
    fit_feature_fill_policy,
    sha256_file,
    validate_feature_registry,
    validate_temporal_safety_certification,
    verify_observation,
)
from scripts.verify_alpha_atlas_v4_reconstructability import verify_artifact


def test_fill_policy_uses_fit_only_and_is_deterministic():
    fit = pd.DataFrame(
        {
            "feature_a": [1.0, None, 3.0],
            "feature_b": [8.0, 4.0, None],
            "feature_cutoff_at": pd.date_range("2026-01-01", periods=3, tz="UTC"),
        }
    )
    future_a = pd.DataFrame({"feature_a": [None, 1e12], "feature_b": [None, -1e12]})
    future_b = pd.DataFrame({"feature_a": [None, -1e18], "feature_b": [None, 1e18]})
    policy_a = fit_feature_fill_policy(fit, ["feature_b", "feature_a"])
    policy_b = fit_feature_fill_policy(fit, ["feature_a", "feature_b"])
    assert policy_a == policy_b
    assert policy_a["features"]["feature_a"]["fitted_value"] == 2.0
    assert apply_feature_fill_policy(future_a, policy_a).iloc[0]["feature_a"] == 2.0
    assert apply_feature_fill_policy(future_b, policy_a).iloc[0]["feature_a"] == 2.0
    changed = fit_feature_fill_policy(
        fit.assign(feature_a=[10.0, None, 30.0]), ["feature_a", "feature_b"]
    )
    assert changed["features"]["feature_a"]["fitted_value"] == 20.0
    assert changed["policy_sha256"] != policy_a["policy_sha256"]


def test_each_walk_forward_fit_is_independent_of_later_fold():
    fold_one = pd.DataFrame({"feature_a": [1.0, None, 5.0]})
    first = fit_feature_fill_policy(fold_one, ["feature_a"])
    _ = fit_feature_fill_policy(
        pd.concat([fold_one, pd.DataFrame({"feature_a": [999999.0]})]), ["feature_a"]
    )
    assert fit_feature_fill_policy(fold_one, ["feature_a"]) == first


def test_feature_registry_exactly_reconciles_43_plus_5():
    registry = feature_registry()
    assert len(MODEL_FEATURES) == registry["model_input_count"] == 43
    assert len(FEATURE_STORE_PROVENANCE_COLUMNS) == registry["provenance_count"] == 5
    assert registry["feature_store_feature_columns"] == 48
    assert (
        registry["reconciliation"]
        == "48 feature-store columns = 43 model inputs + 5 provenance columns"
    )
    validate_feature_registry(
        [*MODEL_FEATURES, *FEATURE_STORE_PROVENANCE_COLUMNS], registry
    )
    assert not any(
        item["name"] == "feature_probability_up_delta_from_last_signal"
        and item["model_input"]
        for item in registry["columns"]
    )
    with pytest.raises(ValueError, match="registry mismatch"):
        validate_feature_registry([*MODEL_FEATURES, "feature_unregistered"], registry)


def _lineage_row(tmp_path: Path):
    source = tmp_path / "bars.json"
    source.write_text(json.dumps({"rows": [{"close": 10.0}]}, sort_keys=True))
    actions = tmp_path / "actions.json"
    actions.write_text(json.dumps({"actions": []}, sort_keys=True))
    base = {
        "canonical_dataset_schema_version": "massive-decision-training-rows.v4",
        "point_in_time_symbol_id": "SEC-1",
        "lane": "daily_swing",
        "universe_policy_version": "u1",
        "feature_cutoff_at": "2026-01-05T14:00:00+00:00",
        "entry_at": "2026-01-06T14:30:00+00:00",
        "exit_at": "2026-01-12T21:00:00+00:00",
        "label_horizon_sessions": 5,
        "timing_contract_version": "alpha-atlas-v4-prediction-execution-contract.v1",
        "model_feature_contract_version": "alpha-atlas-v4-features.v2",
        "execution_cost_policy_version": None,
        "exchange_calendar": "XNYS-rule-calendar.v1",
        "return_5d": 0.1,
        "corporate_action_manifest_sha256": "actions-hash",
    }
    base.update({feature: 10.0 for feature in MODEL_FEATURES})
    base["canonical_observation_id"] = canonical_observation_id(base)
    sources = [
        {
            "family": family,
            "path": source.name,
            "sha256": sha256_file(source),
            "event_at": "2026-01-02T21:00:00+00:00",
            "available_at": "2026-01-02T21:01:00+00:00",
            "staleness_status": "fresh",
        }
        for family in ("symbol", "spy", "sector", "reference")
    ]
    base["reconstruction_lineage"] = {
        "sources": sources,
        "feature_contract_version": "alpha-atlas-v4-features.v2",
        "calendar_contract_version": "XNYS-rule-calendar.v1",
        "feature_calculations": {
            feature: {
                "operator": "identity",
                "inputs": [{"family": "symbol", "row_index": 0, "field": "close"}],
            }
            for feature in MODEL_FEATURES
        },
        "execution": {
            "entry_at": base["entry_at"],
            "exit_at": base["exit_at"],
            "entry_session": "2026-01-06",
            "exit_session": "2026-01-12",
            "entry_price": 10.0,
            "exit_price": 11.0,
            "split_factor": 1.0,
        },
        "corporate_action_source": {
            "path": actions.name,
            "sha256": sha256_file(actions),
        },
        "corporate_action_manifest_sha256": "actions-hash",
    }
    return base, source


def test_exact_reconstruction_and_fail_closed_variants(tmp_path):
    row, source = _lineage_row(tmp_path)
    assert verify_observation(row, root=tmp_path)["status"] == "RECONSTRUCTABLE"
    source.write_text('{"rows": [{"close": 11.0}]}')
    assert (
        "source_hash_mismatch:symbol"
        in verify_observation(row, root=tmp_path)["failures"]
    )
    row, source = _lineage_row(tmp_path)
    row["reconstruction_lineage"]["sources"][1][
        "available_at"
    ] = "2027-01-01T00:00:00+00:00"
    assert (
        "future_source_availability:spy"
        in verify_observation(row, root=tmp_path)["failures"]
    )
    row, source = _lineage_row(tmp_path)
    row["feature_close"] = 99.0
    assert (
        "feature_mismatch:feature_close"
        in verify_observation(row, root=tmp_path)["failures"]
    )
    row, source = _lineage_row(tmp_path)
    row["return_5d"] = 0.5
    assert "target_mismatch" in verify_observation(row, root=tmp_path)["failures"]


def test_missing_context_action_and_execution_lineage_fail_closed(tmp_path):
    row, _ = _lineage_row(tmp_path)
    row["reconstruction_lineage"]["sources"] = row["reconstruction_lineage"]["sources"][
        :1
    ]
    row["reconstruction_lineage"]["corporate_action_manifest_sha256"] = "wrong"
    row["reconstruction_lineage"]["execution"].pop("entry_price")
    failures = verify_observation(row, root=tmp_path)["failures"]
    assert "missing_required_context:spy" in failures
    assert "missing_required_context:sector" in failures
    assert "corporate_action_lineage_mismatch" in failures
    assert "missing_executable_label_lineage" in failures


def test_stale_context_ticker_identity_holiday_and_early_close(tmp_path):
    row, _ = _lineage_row(tmp_path)
    row["reconstruction_lineage"]["sources"][1]["staleness_status"] = "stale"
    assert (
        "stale_or_unproven_source:spy"
        in verify_observation(row, root=tmp_path)["failures"]
    )

    row, _ = _lineage_row(tmp_path)
    row["point_in_time_symbol_id"] = "CHANGED-IDENTITY"
    assert (
        "canonical_observation_id_mismatch"
        in verify_observation(row, root=tmp_path)["failures"]
    )

    row, _ = _lineage_row(tmp_path)
    row["entry_at"] = "2026-01-01T14:30:00+00:00"
    row["reconstruction_lineage"]["execution"].update(
        {"entry_at": row["entry_at"], "entry_session": "2026-01-01"}
    )
    row["canonical_observation_id"] = canonical_observation_id(row)
    assert (
        "missing_executable_label_lineage"
        in verify_observation(row, root=tmp_path)["failures"]
    )

    row, _ = _lineage_row(tmp_path)
    row["exit_at"] = "2026-11-27T18:00:00+00:00"
    row["reconstruction_lineage"]["execution"].update(
        {"exit_at": row["exit_at"], "exit_session": "2026-11-27"}
    )
    row["canonical_observation_id"] = canonical_observation_id(row)
    assert verify_observation(row, root=tmp_path)["status"] == "RECONSTRUCTABLE"


def test_temporal_certification_is_hash_bound_and_never_trusts_legacy_boolean(tmp_path):
    artifact = tmp_path / "rows.jsonl"
    artifact.write_text('{"leakage_safe": true}\n')
    report = {
        "status": "RECONSTRUCTABLE",
        "rows_total": 1,
        "rows_checked": 1,
        "failure_count": 0,
    }
    certification = build_temporal_safety_certification(
        artifact_path=artifact,
        verification_report=report,
        timing_contract_version="timing.v1",
    )
    assert certification["status"] == "VERIFIED_FOR_THIS_ARTIFACT"
    assert certification["legacy_leakage_safe_accepted"] is False
    validate_temporal_safety_certification(
        certification, artifact_path=artifact, verification_report=report
    )
    artifact.write_text('{"leakage_safe": true, "forged": true}\n')
    with pytest.raises(ValueError, match="artifact hash mismatch"):
        validate_temporal_safety_certification(
            certification, artifact_path=artifact, verification_report=report
        )
    partial = build_temporal_safety_certification(
        artifact_path=artifact,
        verification_report={
            "status": "PARTIAL",
            "rows_total": 2,
            "rows_checked": 1,
            "failure_count": 0,
        },
        timing_contract_version="timing.v1",
    )
    assert partial["status"] == "PARTIALLY_VERIFIED"


def test_lineage_free_current_style_row_is_not_reconstructable(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_text(json.dumps({"canonical_observation_id": "missing-lineage"}) + "\n")
    report = verify_artifact(path, root=tmp_path)
    assert report["status"] == "NOT_RECONSTRUCTABLE"
    assert report["failure_reasons"] == {"missing_reconstruction_lineage": 1}


def test_v31_freeze_and_workflow_remain_non_promoting_and_separate():
    benchmark = json.loads(
        Path("docs/reports/alpha_atlas_v31_frozen_benchmark.json").read_text()
    )
    assert benchmark["status"] == "BENCHMARK_ONLY_INCOMPLETE_EXTERNAL_EVIDENCE"
    assert benchmark["automatic_promotion"] is False
    assert benchmark["ready_for_live_routing"] is False
    assert benchmark["model_artifact_evidence"] == "UNVERIFIED_EXTERNAL_ARTIFACT"
    workflow = Path(".github/workflows/track-b-offline.yml").read_text()
    assert "Evaluate V4 Phase 0 reconstructability gate" in workflow
    assert "phase0_certification_passed" in workflow
    assert "Record V3 and V3.1 compatibility boundary" in workflow
    assert '"automatic_promotion": False' in workflow
    assert '"ready_for_live_routing": False' in workflow


def test_committed_registries_and_closeout_reports_are_deterministic():
    registry_path = Path("docs/reports/alpha_atlas_v4_feature_registry.json")
    committed_registry = json.loads(registry_path.read_text())
    assert committed_registry == feature_registry()
    assert (
        registry_path.read_text()
        == json.dumps(committed_registry, indent=2, sort_keys=True) + "\n"
    )
    for name, schema in (
        (
            "alpha_atlas_v31_frozen_benchmark.json",
            "alpha-atlas-v31-frozen-benchmark.v1",
        ),
        ("alpha_atlas_v4_phase0_closeout.json", "alpha-atlas-v4-phase0-closeout.v1"),
    ):
        path = Path("docs/reports") / name
        payload = json.loads(path.read_text())
        assert payload["schema_version"] == schema
        assert path.read_text() == json.dumps(payload, indent=2, sort_keys=True) + "\n"
