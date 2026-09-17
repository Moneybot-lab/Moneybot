from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

import scripts.build_v4_hosted_portfolio_verification_freeze as freeze


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value))
    return path


def _fixture(tmp_path: Path, monkeypatch):
    track_zip, verify_zip = tmp_path / "track.zip", tmp_path / "verify.zip"
    verification = {
        "status": "VERIFIED", "failures": [], "evidence_only": True,
        "model_fitting_performed": False, "holdout_access_performed": False,
        "automatic_promotion": False, "automatic_promotion_performed": False,
        "ready_for_live_routing": False, "live_routing_performed": False,
        "economic_gates_preserved": True,
        "hosted_source": {"environment": "github_actions", "workflow": freeze.TRACK_B_WORKFLOW, "run_id": freeze.TRACK_B_RUN_ID, "run_attempt": 1, "head_sha": freeze.TRACK_B_HEAD_SHA},
        "phase0": {"core_failure_count": 0, "reconstructable_rows": 34446, "rows_checked": 34446},
        "valuation_scope_verification": {"selected_portfolio_required_observations": 25, "selected_portfolio_verified_observations": 25, "selected_portfolio_incomplete_observations": 0, "selected_portfolio_independent_adjustments_verified": True, "diagnostic_failure_selected_portfolio_intersection": 0},
        "equity_reconciliation": {"mismatched_sessions": 0, "maximum_absolute_difference": 0.0},
        "ledger_summary": {"duplicate_violations": 0},
    }
    evidence = {
        "v4_hosted_portfolio_verification.json": json.dumps(verification).encode(),
        "v4_hosted_portfolio_source_manifest.json": b'{"source":"exact"}',
        "source_artifact_SHA256SUMS.txt": b"source hash\n",
    }
    with zipfile.ZipFile(verify_zip, "w") as archive:
        for name, data in evidence.items(): archive.writestr(name, data)
    with zipfile.ZipFile(track_zip, "w") as archive:
        for name in freeze.REVIEW_FILES: archive.writestr(f"run/path/{name}", name.encode())
    monkeypatch.setattr(freeze, "TRACK_B_ARCHIVE_SHA256", hashlib.sha256(track_zip.read_bytes()).hexdigest())
    monkeypatch.setattr(freeze, "VERIFY_ARCHIVE_SHA256", hashlib.sha256(verify_zip.read_bytes()).hexdigest())
    monkeypatch.setattr(freeze, "EVIDENCE_HASHES", {name: hashlib.sha256(data).hexdigest() for name, data in evidence.items()})
    inputs = {
        "track_b_run": _write(tmp_path / "track_run.json", {"id": freeze.TRACK_B_RUN_ID, "name": freeze.TRACK_B_WORKFLOW, "run_attempt": 1, "conclusion": "failure", "head_sha": freeze.TRACK_B_HEAD_SHA}),
        "track_b_artifact": _write(tmp_path / "track_artifact.json", {"name": freeze.TRACK_B_ARTIFACT, "expired": False, "workflow_run": {"id": freeze.TRACK_B_RUN_ID}}),
        "track_b_zip": track_zip,
        "verifier_run": _write(tmp_path / "verify_run.json", {"id": freeze.VERIFY_RUN_ID, "name": freeze.VERIFY_WORKFLOW, "run_attempt": 1, "conclusion": "success", "head_sha": "b" * 40}),
        "verifier_artifact": _write(tmp_path / "verify_artifact.json", {"name": freeze.VERIFY_ARTIFACT, "expired": False, "workflow_run": {"id": freeze.VERIFY_RUN_ID}}),
        "verifier_zip": verify_zip,
        "manifest_path": tmp_path / "V4_HOSTED_PORTFOLIO_VERIFICATION_MANIFEST.json",
        "checksum_path": tmp_path / "SHA256SUMS.txt",
        "extracted_dir": tmp_path / "extracted",
    }
    return inputs


def test_builds_exact_composite_freeze_idempotently(tmp_path, monkeypatch):
    inputs = _fixture(tmp_path, monkeypatch)
    track_before, verify_before = inputs["track_b_zip"].read_bytes(), inputs["verifier_zip"].read_bytes()
    result = freeze.build_freeze(**inputs)
    manifest_before, sums_before = inputs["manifest_path"].read_bytes(), inputs["checksum_path"].read_bytes()
    assert freeze.build_freeze(**inputs) == result
    assert inputs["manifest_path"].read_bytes() == manifest_before
    assert inputs["checksum_path"].read_bytes() == sums_before
    assert inputs["track_b_zip"].read_bytes() == track_before
    assert inputs["verifier_zip"].read_bytes() == verify_before
    assert result["sources"]["hosted_track_b"]["historical_workflow_conclusion"] == "failure"
    assert result["release"]["tag_target_head_sha"] == "b" * 40


@pytest.mark.parametrize("key", ["track_b_zip", "verifier_zip"])
def test_archive_hash_mismatch_fails(tmp_path, monkeypatch, key):
    inputs = _fixture(tmp_path, monkeypatch); inputs[key].write_bytes(b"changed")
    with pytest.raises(ValueError, match="ARCHIVE_SHA256_MISMATCH"): freeze.build_freeze(**inputs)


def test_verification_json_hash_mismatch_fails(tmp_path, monkeypatch):
    inputs = _fixture(tmp_path, monkeypatch)
    monkeypatch.setitem(freeze.EVIDENCE_HASHES, "v4_hosted_portfolio_verification.json", "0" * 64)
    with pytest.raises(ValueError, match="PRINCIPAL_EVIDENCE_SHA256_MISMATCH"): freeze.build_freeze(**inputs)


def test_non_verified_result_fails(tmp_path, monkeypatch):
    inputs = _fixture(tmp_path, monkeypatch)
    with zipfile.ZipFile(inputs["verifier_zip"], "a") as archive:
        pass
    verification = json.loads(zipfile.ZipFile(inputs["verifier_zip"]).read("v4_hosted_portfolio_verification.json"))
    verification["status"] = "FAILED"
    replacement = tmp_path / "replacement.zip"
    with zipfile.ZipFile(inputs["verifier_zip"]) as source, zipfile.ZipFile(replacement, "w") as output:
        for item in source.infolist(): output.writestr(item, json.dumps(verification) if item.filename.endswith("v4_hosted_portfolio_verification.json") else source.read(item))
    inputs["verifier_zip"] = replacement
    monkeypatch.setattr(freeze, "VERIFY_ARCHIVE_SHA256", hashlib.sha256(replacement.read_bytes()).hexdigest())
    monkeypatch.setitem(freeze.EVIDENCE_HASHES, "v4_hosted_portfolio_verification.json", hashlib.sha256(json.dumps(verification).encode()).hexdigest())
    with pytest.raises(ValueError, match="VERIFICATION_RESULT_NOT_ACCEPTED"): freeze.build_freeze(**inputs)


@pytest.mark.parametrize("source", ["track_b_run", "verifier_run", "track_b_artifact", "verifier_artifact"])
def test_exact_source_binding(tmp_path, monkeypatch, source):
    inputs = _fixture(tmp_path, monkeypatch); value = json.loads(inputs[source].read_text())
    value["name"] = "wrong"; inputs[source].write_text(json.dumps(value))
    with pytest.raises(ValueError, match="SOURCE_(RUN|ARTIFACT)_MISMATCH"): freeze.build_freeze(**inputs)


def test_historical_track_b_failure_is_required(tmp_path, monkeypatch):
    inputs = _fixture(tmp_path, monkeypatch); value = json.loads(inputs["track_b_run"].read_text())
    value["conclusion"] = "success"; inputs["track_b_run"].write_text(json.dumps(value))
    with pytest.raises(ValueError, match="TRACK_B_HISTORICAL_CONCLUSION_MISMATCH"): freeze.build_freeze(**inputs)


def test_freeze_workflow_is_archival_idempotent_and_targets_verifier_sha():
    text = Path(".github/workflows/freeze-v4-hosted-portfolio-verification.yml").read_text()
    assert text.startswith("name: Freeze V4 Hosted Portfolio Verification\n")
    assert "workflow_dispatch:" in text and "schedule:" not in text and "push:" not in text
    for value in ("35002276286", "35040195995", freeze.TRACK_B_ARCHIVE_SHA256, freeze.VERIFY_ARCHIVE_SHA256): assert value in text
    assert 'gh release create "$TAG" --target "$VERIFIER_HEAD_SHA"' in text
    assert "HOSTED_PORTFOLIO_VERIFICATION_ALREADY_FROZEN" in text
    assert "EXISTING_HOSTED_PORTFOLIO_FREEZE_MISMATCH" in text
    for forbidden in ("train_challenger", "backtest_challenger", "fit_feature", "track-b-offline.yml", "Massive"):
        assert forbidden not in text
