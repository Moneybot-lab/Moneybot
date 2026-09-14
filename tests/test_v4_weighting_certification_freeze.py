import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from scripts.build_v4_weighting_certification_freeze import build_freeze


def _fixture(tmp_path: Path):
    run = tmp_path / "run.json"
    artifact = tmp_path / "artifact.json"
    archive = tmp_path / "v4-weighting-model-reload-34906607045-1.zip"
    run.write_text(
        json.dumps(
            {
                "id": 34906607045,
                "name": "V4 Verify Weighting Model Reload",
                "run_attempt": 1,
                "conclusion": "success",
                "head_sha": "a" * 40,
            }
        )
    )
    artifact.write_text(
        json.dumps(
            {
                "id": 77,
                "name": "v4-weighting-model-reload-34906607045-1",
                "expired": False,
                "size_in_bytes": 100,
                "workflow_run": {"id": 34906607045},
            }
        )
    )
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(
            "report/weighting_model_reload_verification.json",
            json.dumps(
                {
                    "all_replays_verified": True,
                    "final_holdout_accessed": False,
                    "automatic_promotion": False,
                }
            ),
        )
        output.writestr(
            "report/weighting_model_fill_policy_verification.json",
            json.dumps(
                {
                    "status": "VERIFIED",
                    "models_refit": False,
                    "final_holdout_accessed": False,
                    "automatic_promotion_performed": False,
                }
            ),
        )
        output.writestr("evidence/sources.json", "{}")
        output.writestr("report/corrected_weighting_model_states.json", "{}")
    return run, artifact, archive


def test_builds_manifest_without_changing_source_archive(tmp_path):
    run, artifact, archive = _fixture(tmp_path)
    before = archive.read_bytes()
    manifest_path, checksums = tmp_path / "manifest.json", tmp_path / "SHA256SUMS"
    result = build_freeze(
        run, artifact, archive, manifest_path, checksums, tmp_path / "extracted"
    )

    assert archive.read_bytes() == before
    assert result["original_artifact"]["sha256"] == hashlib.sha256(before).hexdigest()
    assert result["original_artifact"]["bytes_modified"] is False
    assert result["archive_only_no_model_computation"] is True
    assert result["source"]["head_sha"] == "a" * 40
    assert (
        "weighting_model_reload_verification.json" in checksums.read_text()
        or (tmp_path / "extracted/weighting_model_reload_verification.json").exists()
    )


def test_fails_closed_on_wrong_run_or_unverified_evidence(tmp_path):
    run, artifact, archive = _fixture(tmp_path)
    payload = json.loads(run.read_text())
    payload["id"] = 34906607046
    run.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="FROZEN_SOURCE_RUN_MISMATCH"):
        build_freeze(
            run,
            artifact,
            archive,
            tmp_path / "manifest",
            tmp_path / "sums",
            tmp_path / "out",
        )


def test_workflow_is_manual_exact_and_archival_only():
    text = Path(
        ".github/workflows/freeze-v4-weighting-model-certification.yml"
    ).read_text()
    assert text.startswith("name: Freeze V4 Weighting Model Certification\n")
    assert (
        "workflow_dispatch:" in text
        and "schedule:" not in text
        and "pull_request:" not in text
    )
    assert 'SOURCE_RUN_ID: "34906607045"' in text
    assert "SOURCE_ARTIFACT: v4-weighting-model-reload-34906607045-1" in text
    assert '--target "$SOURCE_HEAD_SHA"' in text
    assert 'gh release create "$TAG"' in text
    assert "run_alpha_atlas_v4_weighting_experiment.py" not in text
    assert "train_challenger" not in text and "track-b-offline" not in text
