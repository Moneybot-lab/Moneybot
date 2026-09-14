import hashlib
import json
import zipfile
from pathlib import Path

import pytest

import scripts.build_v4_weighting_certification_freeze as freeze


def _run(path: Path, run_id: int, head: str = "a") -> Path:
    path.write_text(
        json.dumps(
            {
                "id": run_id,
                "name": freeze.WORKFLOW,
                "run_attempt": 1,
                "conclusion": "success",
                "head_sha": head * 40,
            }
        )
    )
    return path


def _artifact(path: Path, run_id: int, name: str) -> Path:
    path.write_text(
        json.dumps(
            {
                "id": run_id + 1,
                "name": name,
                "expired": False,
                "workflow_run": {"id": run_id},
            }
        )
    )
    return path


def _archives(tmp_path: Path):
    corrected = b'{"immutable":"corrected states"}'
    reload_zip = tmp_path / f"{freeze.RELOAD.artifact_name}.zip"
    fill_zip = tmp_path / f"{freeze.FILL.artifact_name}.zip"
    models = [
        {
            "replay_status": "VERIFIED",
            "decision_mismatch_count": 0,
            "scores_outside_tolerance": 0,
        }
    ] * 6
    folds = [
        {
            "training_only_fill_statistics": True,
            "validation_rows_used_to_compute_fill_values": 0,
            "holdout_rows_used_to_compute_fill_values": 0,
            "execution_matrix": {"verified": True, "mismatched_cells": 0},
        }
    ] * 6
    with zipfile.ZipFile(reload_zip, "w") as output:
        output.writestr(
            "report/weighting_model_reload_verification.json",
            json.dumps(
                {
                    "all_replays_verified": True,
                    "prediction_reproducibility_status": "VERIFIED",
                    "models_fitted": False,
                    "final_holdout_accessed": False,
                    "automatic_promotion": False,
                    "models": models,
                }
            ),
        )
        output.writestr("report/corrected_weighting_model_states.json", corrected)
        output.writestr("evidence/sources.json", b'{"source":"reload"}')
    with zipfile.ZipFile(fill_zip, "w") as output:
        output.writestr(
            "report/weighting_model_fill_policy_verification.json",
            json.dumps(
                {
                    "status": "VERIFIED",
                    "evidence_only": True,
                    "models_refit": False,
                    "predictions_changed": False,
                    "scores_or_decisions_changed": False,
                    "corrected_states_changed": False,
                    "final_holdout_accessed": False,
                    "automatic_promotion_performed": False,
                    "folds": folds,
                }
            ),
        )
        output.writestr("evidence/sources.json", b'{"source":"fill"}')
    return reload_zip, fill_zip, corrected


def _build(tmp_path: Path, monkeypatch, **overrides):
    reload_zip, fill_zip, corrected = _archives(tmp_path)
    monkeypatch.setattr(
        freeze,
        "EXPECTED_CORRECTED_STATES_SHA256",
        hashlib.sha256(corrected).hexdigest(),
    )
    inputs = {
        "reload_run": _run(tmp_path / "reload_run.json", freeze.RELOAD.run_id),
        "reload_artifact": _artifact(
            tmp_path / "reload_artifact.json",
            freeze.RELOAD.run_id,
            freeze.RELOAD.artifact_name,
        ),
        "reload_zip": reload_zip,
        "fill_run": _run(tmp_path / "fill_run.json", freeze.FILL.run_id, "b"),
        "fill_artifact": _artifact(
            tmp_path / "fill_artifact.json",
            freeze.FILL.run_id,
            freeze.FILL.artifact_name,
        ),
        "fill_zip": fill_zip,
        "manifest_path": tmp_path / "V4_WEIGHTING_MODEL_CERTIFICATION_MANIFEST.json",
        "checksum_path": tmp_path / "SHA256SUMS.txt",
        "extracted_dir": tmp_path / "extracted",
    }
    inputs.update(overrides)
    return inputs, reload_zip.read_bytes(), fill_zip.read_bytes()


def test_composes_separate_sources_without_mutating_archives(tmp_path, monkeypatch):
    inputs, reload_before, fill_before = _build(tmp_path, monkeypatch)
    result = freeze.build_freeze(**inputs)
    first_manifest = inputs["manifest_path"].read_bytes()
    first_checksums = inputs["checksum_path"].read_bytes()
    assert freeze.build_freeze(**inputs) == result
    assert inputs["manifest_path"].read_bytes() == first_manifest
    assert inputs["checksum_path"].read_bytes() == first_checksums

    assert inputs["reload_zip"].read_bytes() == reload_before
    assert inputs["fill_zip"].read_bytes() == fill_before
    assert result["sources"]["reload_certification"]["run_id"] == 34876711068
    assert result["sources"]["fill_policy_certification"]["run_id"] == 34906607045
    assert result["release"]["tag_target_head_sha"] == "b" * 40
    assert (
        result["principal_evidence"]["reload_sources.json"]["source_run_id"]
        == 34876711068
    )
    assert (
        result["principal_evidence"]["fill_policy_sources.json"]["source_run_id"]
        == 34906607045
    )
    assert (
        inputs["extracted_dir"] / "reload_sources.json"
    ).read_bytes() == b'{"source":"reload"}'
    assert (
        inputs["extracted_dir"] / "fill_policy_sources.json"
    ).read_bytes() == b'{"source":"fill"}'
    checksums = inputs["checksum_path"].read_text()
    for name in (
        *[inputs[key].name for key in ("reload_zip", "fill_zip")],
        *freeze.RELOAD.required,
        "weighting_model_fill_policy_verification.json",
        "reload_sources.json",
        "fill_policy_sources.json",
        inputs["manifest_path"].name,
    ):
        assert name in checksums


@pytest.mark.parametrize(
    ("source", "missing", "reason"),
    [
        (
            freeze.RELOAD,
            "weighting_model_reload_verification.json",
            "RELOAD_MISSING_CERTIFICATION_EVIDENCE",
        ),
        (
            freeze.FILL,
            "weighting_model_fill_policy_verification.json",
            "FILL_MISSING_CERTIFICATION_EVIDENCE",
        ),
    ],
)
def test_required_evidence_is_source_specific(
    tmp_path, monkeypatch, source, missing, reason
):
    inputs, _, _ = _build(tmp_path, monkeypatch)
    archive_key = "reload_zip" if source is freeze.RELOAD else "fill_zip"
    replacement = tmp_path / "replacement.zip"
    with (
        zipfile.ZipFile(inputs[archive_key]) as original,
        zipfile.ZipFile(replacement, "w") as output,
    ):
        for item in original.infolist():
            if Path(item.filename).name != missing:
                output.writestr(item, original.read(item))
    inputs[archive_key] = replacement
    with pytest.raises(ValueError, match=reason):
        freeze.build_freeze(**inputs)


def test_corrected_state_hash_mismatch_fails(tmp_path, monkeypatch):
    inputs, _, _ = _build(tmp_path, monkeypatch)
    monkeypatch.setattr(freeze, "EXPECTED_CORRECTED_STATES_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="CORRECTED_STATES_SHA256_MISMATCH"):
        freeze.build_freeze(**inputs)


@pytest.mark.parametrize(
    "kind", ["reload_run", "fill_run", "reload_artifact", "fill_artifact"]
)
def test_either_frozen_source_mismatch_fails(tmp_path, monkeypatch, kind):
    inputs, _, _ = _build(tmp_path, monkeypatch)
    payload = json.loads(inputs[kind].read_text())
    if kind.endswith("run"):
        payload["id"] += 1
    else:
        payload["name"] += "-wrong"
    inputs[kind].write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="SOURCE_(RUN|ARTIFACT)_"):
        freeze.build_freeze(**inputs)


def test_workflow_is_exact_composite_archive_and_idempotent():
    text = Path(
        ".github/workflows/freeze-v4-weighting-model-certification.yml"
    ).read_text()
    assert text.startswith("name: Freeze V4 Weighting Model Certification\n")
    assert (
        "workflow_dispatch:" in text
        and "schedule:" not in text
        and "pull_request:" not in text
    )
    for value in (
        "34876711068",
        "34906607045",
        freeze.RELOAD.artifact_name,
        freeze.FILL.artifact_name,
    ):
        assert value in text
    assert 'gh release create "$TAG" --target "$FILL_HEAD_SHA"' in text
    assert "CERTIFICATION_ALREADY_FROZEN_AND_VERIFIED" in text
    assert "CERTIFICATION_TAG_SHA_MISMATCH" in text
    assert "EXISTING_FROZEN_EVIDENCE_MISMATCH" in text
    assert "run_alpha_atlas_v4_weighting_experiment.py" not in text
    assert "train_challenger" not in text and "track-b-offline" not in text
