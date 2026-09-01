import json
from pathlib import Path

from scripts.recover_v31_benchmark_evidence import recover


def _fixture(root: Path, model_bytes: bytes, *, version: str) -> None:
    (root / "alpha_atlas_v31").mkdir(parents=True)
    (root / "alpha_atlas_v31/candidate_model.joblib").write_bytes(model_bytes)
    (root / "alpha_atlas_v31/model_metadata.json").write_text(
        json.dumps(
            {
                "model_version": version,
                "feature_contract_version": "moneybot-serving-features.v1",
                "automatic_promotion": False,
                "ready_for_live_routing": False,
            }
        )
    )


def test_recovery_is_deterministic_and_never_invents_missing_fields(tmp_path):
    run156, run157 = tmp_path / "156", tmp_path / "157"
    _fixture(
        run156, b"same immutable model", version="candidate-alpha-atlas-v31-clean-v1"
    )
    _fixture(
        run157, b"same immutable model", version="candidate-alpha-atlas-v31-clean-v1"
    )

    first = tmp_path / "first"
    second = tmp_path / "second"
    comparison = recover(run156, run157, first)
    recover(run156, run157, second)

    assert comparison["status"] == "IMMUTABLE_MODEL_MATCH"
    for name in (
        "run_156_manifest.json",
        "run_157_manifest.json",
        "comparison.json",
        "comparison.md",
    ):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    manifest = json.loads((first / "run_156_manifest.json").read_text())
    assert manifest["actions_run_id"] == 32700956267
    assert manifest["artifact_id"] == 9510663000
    assert manifest["model_artifact_sha256"]
    assert "training_evaluation_dataset_identity" not in manifest


def test_recovery_reports_empty_artifact_without_fabrication(tmp_path):
    run156, run157 = tmp_path / "156", tmp_path / "157"
    run156.mkdir()
    run157.mkdir()
    output = tmp_path / "output"
    comparison = recover(run156, run157, output)
    manifest = json.loads((output / "run_157_manifest.json").read_text())
    assert comparison["status"] == "RECOVERED_WITHOUT_UNIQUE_MATCHING_MODEL_EVIDENCE"
    assert manifest["download_status"] == "EMPTY_ARTIFACT_DIRECTORY"
    assert manifest["model_artifact_sha256"] is None
    assert manifest["unverified_fields"] == ["all_extracted_benchmark_evidence"]


def test_manual_recovery_workflow_is_dispatch_only_and_targets_exact_runs():
    workflow = Path(".github/workflows/recover-v31-benchmark-evidence.yml").read_text()
    assert "workflow_dispatch:" in workflow
    assert "schedule:" not in workflow
    assert "actions: read" in workflow
    assert "contents: read" in workflow
    assert "run-id: '32700956267'" in workflow
    assert "run-id: '32820019463'" in workflow
    assert "github-token: ${{ github.token }}" in workflow
    assert "alpha-atlas-v31-benchmark-recovery" in workflow
