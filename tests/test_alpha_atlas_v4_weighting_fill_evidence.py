import json

import pandas as pd
import pytest

from moneybot.services import alpha_atlas_v4_weighting_fill_evidence as evidence
from moneybot.services.alpha_atlas_v4_phase0 import fit_feature_fill_policy


def _fixture(tmp_path):
    features = [f"feature_{index}" for index in range(43)]
    policy = fit_feature_fill_policy(pd.DataFrame([{
        "canonical_observation_id": "train", "feature_cutoff_at": "2026-01-01T00:00:00Z",
        **{name: float(index) for index, name in enumerate(features)}}]), features)
    folds = [{"fold_index": index, "train_ids": [f"train-{index}"],
              "validation_ids": [f"dev-{index}"]} for index in (1, 2, 3)]
    capture = [{"model_version": "challenger-big-loss-avoider-v1", **fold,
                "fill_policy": policy} for fold in folds]
    registration = {"feature_columns": features, "folds": folds}
    state = {"feature_columns": features, "weights": [0.0] * 43,
             "means": [0.0] * 43, "stds": [1.0] * 43}
    corrected = {"arms": {arm: [{"fold_index": index, "model_state": state}
        for index in (1, 2, 3)] for arm in ("current_weights", "uniform_weights")}}
    reload = {"execution_code_sha": evidence.EXPECTED_EXECUTION_CODE_SHA,
              "all_replays_verified": True, "models": [{"replay_status": "VERIFIED"}] * 6}
    rows = [{"canonical_observation_id": f"dev-{index}",
             **{name: (None if name == "feature_0" else float(position))
                for position, name in enumerate(features)}} for index in (1, 2, 3)]
    paths = {}
    for name, payload in (("capture", capture), ("registration", registration),
                          ("corrected", corrected), ("reload", reload)):
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(payload))
    paths["features"] = tmp_path / "all.jsonl"
    paths["features"].write_text("".join(json.dumps(row) + "\n" for row in rows))
    return paths


def test_policy_serialization_training_only_matrix_identity_and_no_holdout(tmp_path):
    paths = _fixture(tmp_path)
    report = evidence.verify_fill_policy_evidence(diagnostic_capture=paths["capture"],
        registration_path=paths["registration"], feature_store=paths["features"],
        reload_report_path=paths["reload"], corrected_states_path=paths["corrected"],
        output_path=tmp_path / "evidence.json")
    assert report["status"] == "VERIFIED"
    assert report["policy"]["fill_method"].startswith("feature-specific training-fold median")
    assert report["final_holdout_accessed"] is False
    assert len(report["folds"]) == 6
    assert all(item["training_only_fill_statistics"] for item in report["folds"])
    assert all(item["validation_rows_used_to_compute_fill_values"] == 0 for item in report["folds"])
    assert all(item["execution_matrix"]["mismatched_cells"] == 0 for item in report["folds"])
    assert report["corrected_states_immutable"]["identical"] is True


def test_ambiguous_policy_fails_closed(tmp_path):
    paths = _fixture(tmp_path)
    capture = json.loads(paths["capture"].read_text())
    capture[0].pop("fill_policy")
    paths["capture"].write_text(json.dumps(capture))
    with pytest.raises(evidence.FillPolicyEvidenceError, match="FILL_POLICY_NOT_PROVABLE"):
        evidence.verify_fill_policy_evidence(diagnostic_capture=paths["capture"],
            registration_path=paths["registration"], feature_store=paths["features"],
            reload_report_path=paths["reload"], corrected_states_path=paths["corrected"],
            output_path=tmp_path / "evidence.json")


def test_corrected_state_mutation_fails(tmp_path, monkeypatch):
    paths = _fixture(tmp_path)
    hashes = iter(("before", "after"))
    monkeypatch.setattr(evidence, "_file_sha", lambda path: next(hashes))
    with pytest.raises(evidence.FillPolicyEvidenceError, match="CORRECTED_STATE_MUTATION"):
        evidence.verify_fill_policy_evidence(diagnostic_capture=paths["capture"],
            registration_path=paths["registration"], feature_store=paths["features"],
            reload_report_path=paths["reload"], corrected_states_path=paths["corrected"],
            output_path=tmp_path / "evidence.json")
