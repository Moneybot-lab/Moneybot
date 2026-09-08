from pathlib import Path

WORKFLOW = Path(".github/workflows/alpha-atlas-v4-phase1-preflight.yml")


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_preflight_workflow_is_manual_bounded_and_serialized():
    text = _text()
    assert "on:\n  workflow_dispatch:\n" in text
    assert "schedule:" not in text
    assert "permissions:\n  contents: read\n" in text
    assert "cancel-in-progress: false" in text
    assert "timeout-minutes: 15" in text
    assert "--execute-probes" in text
    assert "MAX_PROBES" not in text  # limits stay owned by the tested service contract


def test_preflight_workflow_maps_existing_secret_without_logging_it():
    text = _text()
    assert "MASSIVE_API_KEY: ${{ secrets.MASSIVE_API_KEY }}" in text
    assert "POLYGON_API_KEY" not in text
    assert "api_key=" not in text.lower()
    assert 'echo "$MASSIVE_API_KEY"' not in text
    assert "Missing required repository secret: MASSIVE_API_KEY" in text


def test_preflight_workflow_never_backfills_trains_or_deploys():
    text = _text().lower()
    run_lines = "\n".join(
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith(("python ", "python3 ", "./"))
    )
    assert "audit_alpha_atlas_v4_phase1_readiness.py" in run_lines
    assert not any(
        forbidden in run_lines
        for forbidden in ("backfill", "train", "deploy", "promote", "route")
    )


def test_preflight_workflow_always_summarizes_and_uploads_safe_reports():
    text = _text()
    assert text.count("if: always()") >= 3
    assert "alpha_atlas_v4_phase1_workflow_summary.md" in text
    assert "actions/upload-artifact@v4" in text
    assert "artifacts/alpha-atlas-v4-phase1-preflight" in text
    assert "if-no-files-found: warn" in text
    assert "data/raw" not in text
    assert "cache" not in text.lower()
