from pathlib import Path


def test_reload_workflow_is_manual_read_only_and_never_fits():
    text = Path(".github/workflows/v4-verify-weighting-model-reload.yml").read_text()
    assert text.startswith("name: V4 Verify Weighting Model Reload\n")
    assert "workflow_dispatch:" in text and "schedule:" not in text
    assert "contents: read" in text and "actions: read" in text
    assert "timeout-minutes: 20" in text
    assert "34797622678" in text and "aad503b0520c80dca35bb593edac2001b1be0c85" in text
    assert "run_alpha_atlas_v4_weighting_experiment.py" not in text
    assert "train_challenger" not in text and "track-b-offline.yml" not in text
    assert "if: failure()" in text and "if: always()" in text
