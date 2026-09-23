from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.resolve_v4_baseline_registration_supplements import ResolutionError, resolve


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _layout(tmp_path: Path, *, intended_identity: bool = True):
    prior = tmp_path / "prior"; identity = tmp_path / "identity"; track = tmp_path / "track"; output = tmp_path / "output"
    for root in (prior, identity, track): root.mkdir()
    (prior / "reports").mkdir(); (prior / "reports/registration.json").write_text(json.dumps({"registration_sha256": "a" * 64}))
    (prior / "nested").mkdir(); (prior / "nested/registration.json").write_text(json.dumps({"registration_sha256": "wrong"}))
    # The artifact contains a misleading root-level basename, but the pinned
    # selected-portfolio policy is nested under the source run layout.
    (track / "execution_policy.json").write_text(json.dumps({"policy_version": "wrong-root"}))
    policy = (json.dumps({"policy_version": "other-scope", "transaction_cost_bps": 5}) + "\n").encode()
    policy_path = track / "track_b/runs/34689216730-1/challenger_suite/portfolio_path/execution_policy.json"
    policy_path.parent.mkdir(parents=True); policy_path.write_bytes(policy)
    report = {"schema_version": "alpha-atlas-v4-historical-coverage-diagnostics.v2", "research_only": True,
              "ready_for_live_routing": False, "status": "VERIFIED_DIAGNOSTIC_EXECUTION"}
    intended = (json.dumps(report, sort_keys=True) + "\n").encode()
    if intended_identity: (identity / "alpha_atlas_v4_historical_coverage_diagnostics.json").write_bytes(intended)
    for relative, marker in (("source/run-35178703375/reports", "old"), ("source/run-35183625727/reports", "transition")):
        folder = identity / relative; folder.mkdir(parents=True)
        (folder / "alpha_atlas_v4_historical_coverage_diagnostics.json").write_text(json.dumps({**report, "marker": marker}))
    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps({key: {"source_run": run, "source_attempt": 1, "artifact_id": run,
        "artifact_name": key, "artifact_digest": "sha256:" + key[0] * 64} for key, run in (("track", 1), ("prior", 35659754050), ("identity", 35233428008))}))
    return prior, identity, track, output, metadata, intended, policy


def test_explicit_identity_path_wins_over_two_different_nested_copies(tmp_path):
    prior, identity, track, output, metadata, intended, policy = _layout(tmp_path)
    result = resolve(prior_root=prior, identity_root=identity, track_root=track, output_dir=output,
                     validated_metadata=metadata, expected_identity_hash=_sha(intended), expected_cost_hash=_sha(policy),
                     expected_cost_hash_provenance="RECORDED_BY_FAILED_HOSTED_RESOLVER")
    evidence = result["sources"]["identity_evidence"]
    assert result["status"] == "RESOLVED"
    assert result["sources"]["prior_registration"]["artifact_relative_path"] == "reports/registration.json"
    cost = result["sources"]["cost_policy_evidence"]
    assert cost["artifact_relative_path"] == "track_b/runs/34689216730-1/challenger_suite/portfolio_path/execution_policy.json"
    assert (output / "other-scope-execution-policy.json").read_bytes() == policy
    assert cost["expected_hash_provenance"] == "RECORDED_BY_FAILED_HOSTED_RESOLVER"
    assert evidence["artifact_relative_path"] == "alpha_atlas_v4_historical_coverage_diagnostics.json"
    assert [x["artifact_relative_path"] for x in evidence["basename_candidate_inventory"]] == [
        "alpha_atlas_v4_historical_coverage_diagnostics.json",
        "source/run-35178703375/reports/alpha_atlas_v4_historical_coverage_diagnostics.json",
        "source/run-35183625727/reports/alpha_atlas_v4_historical_coverage_diagnostics.json",
    ]
    assert (output / "identity-evidence.json").read_bytes() == intended
    assert evidence["sha256_computed"] == _sha(intended) and evidence["bytes"] == len(intended)
    env_paths = dict(line.split("=", 1) for line in (output / "supplemental-paths.env").read_text().splitlines())
    assert all(Path(path).is_file() for path in env_paths.values())


def test_missing_intended_identity_fails_even_when_nested_basename_matches(tmp_path):
    prior, identity, track, output, metadata, _, _ = _layout(tmp_path, intended_identity=False)
    with pytest.raises(ResolutionError, match="IDENTITY_EVIDENCE_INTENDED_PATH_MISSING"):
        resolve(prior_root=prior, identity_root=identity, track_root=track, output_dir=output, validated_metadata=metadata)
    report = json.loads((output / "supplemental-resolution.json").read_text())
    assert report["status"] == "FAILED"
    assert len(report["sources"]["identity_evidence"]["basename_candidate_inventory"]) == 2


def test_expected_identity_hash_mismatch_is_actionable(tmp_path):
    prior, identity, track, output, metadata, _, _ = _layout(tmp_path)
    with pytest.raises(ResolutionError, match="IDENTITY_EVIDENCE_EXPECTED_HASH_MISMATCH"):
        resolve(prior_root=prior, identity_root=identity, track_root=track, output_dir=output,
                validated_metadata=metadata, expected_identity_hash="0" * 64)
    assert "IDENTITY_EVIDENCE_EXPECTED_HASH_MISMATCH" in (output / "supplemental-resolution.md").read_text()


def test_actual_workflow_entrypoint_copies_exact_bytes_without_pythonpath(tmp_path):
    prior, identity, track, output, metadata, intended, policy = _layout(tmp_path)
    command = [sys.executable, "-m", "scripts.resolve_v4_baseline_registration_supplements",
               "--prior-root", str(prior), "--identity-root", str(identity), "--track-root", str(track),
               "--output-dir", str(output), "--validated-metadata", str(metadata),
               "--expected-identity-hash", _sha(intended), "--expected-cost-hash", _sha(policy),
               "--expected-cost-hash-provenance", "RECORDED_BY_FAILED_HOSTED_RESOLVER"]
    env = os.environ.copy(); env.pop("PYTHONPATH", None)
    completed = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], env=env, text=True, capture_output=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (output / "identity-evidence.json").read_bytes() == intended
    assert (output / "other-scope-execution-policy.json").read_bytes() == policy
    assert json.loads((output / "supplemental-resolution.json").read_text())["status"] == "RESOLVED"


def test_missing_or_mismatched_pinned_cost_path_preserves_actionable_inventory(tmp_path):
    prior, identity, track, output, metadata, _, policy = _layout(tmp_path)
    pinned = track / "track_b/runs/34689216730-1/challenger_suite/portfolio_path/execution_policy.json"
    pinned.unlink()
    with pytest.raises(ResolutionError, match="COST_POLICY_EVIDENCE_INTENDED_PATH_MISSING"):
        resolve(prior_root=prior, identity_root=identity, track_root=track, output_dir=output,
                validated_metadata=metadata, expected_cost_hash=_sha(policy))
    failure = json.loads((output / "supplemental-resolution.json").read_text())
    assert failure["sources"]["cost_policy_evidence"]["basename_candidate_inventory"][0]["artifact_relative_path"] == "execution_policy.json"

    pinned.parent.mkdir(parents=True, exist_ok=True)
    pinned.write_text(json.dumps({"policy_version": "other-scope", "transaction_cost_bps": 6}) + "\n")
    with pytest.raises(ResolutionError, match="COST_POLICY_EVIDENCE_EXPECTED_HASH_MISMATCH"):
        resolve(prior_root=prior, identity_root=identity, track_root=track, output_dir=output,
                validated_metadata=metadata, expected_cost_hash=_sha(policy))
