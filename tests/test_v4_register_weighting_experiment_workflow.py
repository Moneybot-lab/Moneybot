from pathlib import Path


WORKFLOW = Path(".github/workflows/v4-register-weighting-experiment.yml")


def test_workflow_is_manual_read_only_and_bounded():
    text = WORKFLOW.read_text()
    assert text.startswith("name: V4 Register Weighting Experiment\n")
    assert "workflow_dispatch:" in text
    assert "schedule:" not in text and "pull_request:" not in text and "push:" not in text
    assert "contents: read" in text and "actions: read" in text
    assert "timeout-minutes: 15" in text
    assert 'default: "34689216730"' in text
    assert 'default: "34769717178"' in text


def test_workflow_retrieves_exact_sources_and_never_falls_back_or_executes():
    text = WORKFLOW.read_text()
    assert "name: track-b-offline-output" in text
    assert "name: v4-development-diagnostics-34689216730" in text
    assert "run-id: ${{ inputs.source_track_b_run_id }}" in text
    assert "run-id: ${{ inputs.source_diagnostics_run_id }}" in text
    assert "latest successful" not in text.lower()
    assert "list-workflow-runs" not in text.lower()
    assert "run_alpha_atlas_v4_weighting_experiment.py register" in text
    assert "run_alpha_atlas_v4_weighting_experiment.py execute" not in text
    assert "capture_alpha_atlas" not in text
    assert "train_challenger" not in text
    assert "provider" not in text.lower()


def test_workflow_validates_and_packages_registration_but_retains_failure():
    text = WORKFLOW.read_text()
    assert "validate_v4_weighting_registration_sources.py" in text
    assert "validate_registration(registration)" in text
    assert "len(registration['folds'])==3" in text
    assert "rm -rf \"$OUTPUT_ROOT/registration\"" in text
    assert "if: failure()" in text
    assert "if: always()" in text
    assert "failure_diagnostics.json" in text
    assert "v4-weighting-registration-${{ github.run_id }}-${{ github.run_attempt }}" in text
    assert "Download and review the registration before authorizing execution." in text
