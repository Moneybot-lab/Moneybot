from pathlib import Path


WORKFLOW = Path(".github/workflows/v4-development-diagnostics.yml")


def test_manual_workflow_is_read_only_bounded_and_uploads_one_artifact():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.startswith("name: V4 Development Diagnostics\n")
    assert "workflow_dispatch:" in text
    assert "source_track_b_run_id:" in text
    assert "actions: read" in text
    assert "contents: read" in text
    assert "schedule:" not in text
    assert "timeout-minutes: 45" in text
    assert "name: track-b-offline-output" in text
    assert "run-id: ${{ inputs.source_track_b_run_id }}" in text
    assert "capture_alpha_atlas_v4_development_oof.py" in text
    assert "generate_alpha_atlas_v4_development_diagnostics.py" in text
    assert "train_challenger_suite.py" not in text
    assert "backtest_challenger_suite.py" not in text
    assert "v4-development-diagnostics-${{ inputs.source_track_b_run_id }}" in text
    assert "if: always()" in text


def test_workflow_uses_existing_artifact_only_and_checks_frozen_hashes():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "flat_feature_store/all.jsonl" in text
    assert "challenger_split_plan.json" in text
    assert "challenger_suite_manifest.json" in text
    assert "validate_split_plan" in text
    assert "split_plan_sha256" in text
    assert "split_input_sha256" in text
    assert "provider_download_invoked\": False" in text
    assert "final_holdout_backtest_invoked\": False" in text
