from pathlib import Path


WORKFLOW = Path(".github/workflows/v4-execute-weighting-experiment.yml")


def test_execute_workflow_is_manual_read_only_serialized_and_bounded():
    text = WORKFLOW.read_text()
    assert text.startswith("name: V4 Execute Weighting Experiment\n")
    assert "workflow_dispatch:" in text
    assert "schedule:" not in text and "pull_request:" not in text and "push:" not in text
    assert "contents: read" in text and "actions: read" in text
    assert "timeout-minutes: 45" in text
    assert "cancel-in-progress: false" in text
    assert "v4-weighting-experiment-alpha-atlas-v4-big-loss-weight-ablation-v1" in text


def test_execute_workflow_is_bound_to_exact_reviewed_evidence():
    text = WORKFLOW.read_text()
    for value in (
        "34689216730", "34769717178", "34796621288",
        "v4-weighting-registration-34796621288-1",
        "4112f445d8ef079550f02185e4592c05aff9c0321ee4bfa615b632360af4c5c3",
        "ea4e55f9faa848219945d7e03c92c7a541645cd4d6df8aa3cfbd0d1334872f15",
        "506073994052be852a5a00fc38e239b85e72a50494c3559640ea980fb6a52d9e",
        "bd60055adc6cb1b3f8b143c6c61031bced01c9151fcb3b67c33f75430106e8e8",
        "454febde2e14ca8a916222d86a6872db1c5e790a29429f2db6393d828e87e434",
    ):
        assert value in text
    assert "expected exactly one non-expired" in text
    assert "run_attempt" in text


def test_workflow_executes_only_existing_registration_and_keeps_failures_red():
    text = WORKFLOW.read_text()
    assert "run_alpha_atlas_v4_weighting_experiment.py execute" in text
    assert "run_alpha_atlas_v4_weighting_experiment.py register" not in text
    assert "train_challenger" not in text
    assert "capture_alpha_atlas" not in text
    assert "backtest" not in text.lower()
    assert "validate_v4_weighting_execution_sources.py" in text
    assert "if: failure()" in text and "if: always()" in text
    assert 'rm -rf "$OUTPUT_ROOT/results"' in text
    assert "v4-weighting-execution-${{ github.run_id }}-${{ github.run_attempt }}" in text
    assert "final_holdout_evaluated\":false" in text
