import hashlib
import json
import re
from pathlib import Path

import pytest

from scripts import day11_compare_candidate_vs_production as production_compare
from scripts.validate_alpha_atlas_v4_workflow_artifacts import (
    validate_canonical,
    write_failure_summary,
)

WORKFLOW = Path(".github/workflows/track-b-offline.yml")


def _workflow_steps():
    text = WORKFLOW.read_text(encoding="utf-8")
    matches = list(re.finditer(r"^      - name: (.+)$", text, re.MULTILINE))
    return {
        match.group(1): text[
            match.start() : (
                matches[index + 1].start() if index + 1 < len(matches) else len(text)
            )
        ]
        for index, match in enumerate(matches)
    }


def test_v4_workflow_orders_build_validate_canonicalize_clean_and_feature_store():
    names = list(_workflow_steps())
    assert names.index(
        "Build leakage-safe Massive decision training rows"
    ) < names.index("Validate raw V4 timing and schema")
    assert names.index("Validate raw V4 timing and schema") < names.index(
        "Canonicalize V4 economic observations"
    )
    assert names.index("Canonicalize V4 economic observations") < names.index(
        "Validate canonical V4 observations"
    )
    assert names.index("Validate canonical V4 observations") < names.index(
        "Clean and quality-gate training rows"
    )
    assert names.index("Clean and quality-gate training rows") < names.index(
        "Materialize reproducible flat feature store"
    )
    assert (
        names.index("Materialize reproducible flat feature store")
        < names.index("Assess V4 challenger temporal-split feasibility")
        < names.index("Train offline challenger suite")
    )


def test_workflow_freezes_split_plan_and_controls_insufficient_coverage():
    steps = _workflow_steps()
    preflight = steps["Assess V4 challenger temporal-split feasibility"]
    training = steps["Train offline challenger suite"]
    backtest = steps["Backtest and gate challenger suite"]
    hold = steps["Write controlled insufficient-coverage summary"]
    assert '--input "$FEATURE_STORE_DIR/all.jsonl"' in preflight
    assert "challenger_split_plan.json" in preflight
    assert "challenger_split_diagnostics.json" in preflight
    assert "--split-plan" in training
    assert "challenger_test.jsonl" in backtest
    assert "outputs.feasible == 'true'" in training
    assert "outputs.feasible == 'true'" in backtest
    assert "NO_CANDIDATE_INSUFFICIENT_TEMPORAL_COVERAGE" in hold
    assert '"training_attempted": False' in hold
    assert '"backtest_attempted": False' in hold
    assert '"automatic_promotion": False' in hold
    assert '"ready_for_live_routing": False' in hold


def test_cleaner_consumes_only_declared_canonical_output():
    steps = _workflow_steps()
    canonicalize = steps["Canonicalize V4 economic observations"]
    clean = steps["Clean and quality-gate training rows"]
    assert '--output-dir "$CANONICAL_DIR"' in canonicalize
    assert '--input "$CANONICAL_OBSERVATIONS"' in clean
    assert (
        'test "$CANONICAL_OBSERVATIONS" = "$CANONICAL_DIR/canonical_observations.jsonl"'
        in clean
    )
    assert "$RAW_V4_ROWS" not in clean
    assert "decision_training_snapshot_massive.jsonl" not in clean


def test_paths_are_run_scoped_and_do_not_reference_former_cleaner_input():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "github.run_id" in text and "github.run_attempt" in text
    assert "RAW_V4_ROWS: data/track_b/runs/" in text
    assert "CANONICAL_OBSERVATIONS: data/track_b/runs/" in text
    assert "--input data/track_b/decision_training_snapshot_massive.jsonl" not in text


def _canonical_fixture(tmp_path, **row_overrides):
    rows = tmp_path / "canonical_observations.jsonl"
    row = {
        "canonical_observation_id": "aav4obs_1",
        "model_sample_weight": 1.0,
        "canonicalization_contract_version": "alpha-atlas-v4-canonical-observation.v2",
        "model_feature_contract_version": "alpha-atlas-v4-features.v2",
    }
    row.update(row_overrides)
    content = json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
    rows.write_text(content, encoding="utf-8")
    manifest = tmp_path / "canonical_observations.jsonl.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "alpha-atlas-v4-canonical-observations.v2",
                "canonicalization_contract_version": "alpha-atlas-v4-canonical-observation.v2",
                "model_feature_contract_version": "alpha-atlas-v4-features.v2",
                "canonical_observations_sha256": hashlib.sha256(
                    content.encode()
                ).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    return rows, manifest


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("missing", "missing"),
        ("schema", "schema"),
        ("contract", "contract"),
        ("duplicate", "duplicated"),
        ("weight", "weights"),
    ],
)
def test_canonical_validation_fails_closed(tmp_path, mutation, match):
    rows, manifest = _canonical_fixture(tmp_path)
    if mutation == "missing":
        rows.unlink()
    elif mutation in {"schema", "contract"}:
        payload = json.loads(manifest.read_text())
        payload[
            (
                "schema_version"
                if mutation == "schema"
                else "canonicalization_contract_version"
            )
        ] = "wrong.v1"
        manifest.write_text(json.dumps(payload))
    elif mutation == "duplicate":
        rows.write_text(rows.read_text() * 2)
        payload = json.loads(manifest.read_text())
        payload["canonical_observations_sha256"] = hashlib.sha256(
            rows.read_bytes()
        ).hexdigest()
        manifest.write_text(json.dumps(payload))
    else:
        row = json.loads(rows.read_text())
        row["model_sample_weight"] = 2.0
        rows.write_text(json.dumps(row) + "\n")
        payload = json.loads(manifest.read_text())
        payload["canonical_observations_sha256"] = hashlib.sha256(
            rows.read_bytes()
        ).hexdigest()
        manifest.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match=match):
        validate_canonical(rows, manifest)


def test_failure_summary_is_sanitized_and_recommends_canonicalization(tmp_path):
    manifest = tmp_path / "raw.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "massive-decision-training-rows.v4",
                "dataset_manifest_hash": "abc",
            }
        )
    )
    result = write_failure_summary(
        tmp_path / "failure.json",
        failed_stage="clean",
        input_label="raw_v4_rows",
        manifest_path=manifest,
    )
    assert result["input_kind"] == "raw"
    assert result["recommended_corrective_stage"] == "canonicalize_raw_v4_rows"
    assert str(tmp_path) not in json.dumps(result)


def test_fetch_failure_summary_does_not_claim_canonicalization_defect(tmp_path):
    result = write_failure_summary(
        tmp_path / "failure.json",
        failed_stage="fetch_decision_log",
        input_label="run_scoped_track_b_artifact",
        manifest_path=None,
    )
    assert result["failed_stage"] == "fetch_decision_log"
    assert result["input_kind"] == "decision_log_export"
    assert result["expected_schema"] == "application/x-ndjson"
    assert result["recommended_corrective_stage"] == "retry_or_inspect_decision_log_export"


def test_decision_log_fetch_uses_curl_final_status_not_first_retry_header():
    fetch = _workflow_steps()["Fetch real decision log export"]
    assert "--write-out '%{http_code}'" in fetch
    assert 'HTTP_STATUS=$(timeout 150 curl "${CURL_OPTS[@]}" "$URL")' in fetch
    assert "awk 'NR==1{print $2}'" not in fetch
    assert "scripts/validate_decision_log_export.py" in fetch
    assert 'TRACK_B_FAILED_STAGE=fetch_decision_log' in fetch
    assert "--manifest-output data/decision_log_export_manifest.json" in fetch
    assert "scripts/audit_decision_log_duplicates.py" in fetch
    assert "export-decision-log-commit" in fetch
    assert "checkpoint_current" in fetch
    assert fetch.count("X-Decision-Continuity-Bootstrap: verified_seed_v1") == 2
    assert "export-decision-log?limit=" not in fetch
    build = _workflow_steps()["Build leakage-safe Massive decision training rows"]
    assert "--limit 50000" not in build


def test_upload_keeps_canonical_evidence_and_failure_summary_on_failure():
    steps = _workflow_steps()
    upload = steps["Upload Track B artifacts"]
    assert "if: always()" in upload
    for variable in (
        "RAW_V4_MANIFEST",
        "CANONICAL_DIAGNOSTICS",
        "CANONICAL_MANIFEST",
        "CLEANING_QUALITY_REPORT",
    ):
        assert variable in upload
    assert "track_b_failure_summary.json" in upload
    assert "builder_performance.json" in upload
    assert "${{ env.FEATURE_STORE_DIR }}/all.jsonl" in upload
    assert "decision_log_duplicate_audit.json" in upload
    assert "decision_log_continuity_commit.json" in upload


def test_builder_has_optimized_timeout_and_performance_telemetry():
    steps = _workflow_steps()
    build = steps["Build leakage-safe Massive decision training rows"]
    assert (
        "timeout 1800 python3 scripts/build_massive_decision_training_rows.py" in build
    )
    assert '--performance-output "$TRACK_B_RUN_DIR/builder_performance.json"' in build
    assert (
        "timeout 600 python3 scripts/build_massive_decision_training_rows.py"
        not in build
    )


def test_v2_production_boundary_and_v4_no_promotion_are_unchanged():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert (
        production_compare.CURRENT_MASSIVE_CANONICAL_SCHEMA
        == "massive-decision-training-rows.v2"
    )
    assert "day14_promote_candidate.py" not in text
    assert "/api/promote-track-b-candidate" not in text
    assert '"automatic_promotion": False' in text
    assert "ready_for_live_routing=true" not in text
    assert "V4_FEATURE_CONTRACT_VERSION: alpha-atlas-v4-features.v2" in text
    assert (
        "V4_CANONICALIZATION_CONTRACT_VERSION: alpha-atlas-v4-canonical-observation.v2"
        in text
    )
    assert "alpha-atlas-v4-canonical-observation.v1" not in text
