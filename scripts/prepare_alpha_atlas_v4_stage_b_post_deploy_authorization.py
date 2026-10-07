"""Prepare, but never approve, Stage B authorization from an observed deployment revision."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from moneybot.services.alpha_atlas_v4_prospective_snapshot import CaptureError, canonical_bytes

FIXTURE_FILE_SHA256 = "c7d06f54aa18d4c3bd52c29e9c7423605e6919a076f29ba24dcfa7e61eecf113"
FIXTURE_CONTENT_SHA256 = "b22907c9036e3f7476ffa35e6601caaefb810e311050f6457d7d5ff4b3041885"
CONFIG_FILE_SHA256 = "0b7ef94a1a3ff023f01466a94e1b53d321608ff6c5e968e4a802d246b03c0f28"
ACCEPTED_PROXY_FILE_SHA256 = "a6cf01746642cbc956a5f6affd0c4e425536bf2e84ed89614adc121a80b1454d"
ACCEPTED_PROXY_CONTENT_SHA256 = "43f3e472f4e97ee9d6c92a227cd9b1d2eb2c661c38461bb88185a2f306d8881e"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(repo: Path, deployed_commit: str) -> dict[str, object]:
    if len(deployed_commit) != 40 or any(c not in "0123456789abcdef" for c in deployed_commit):
        raise CaptureError("DEPLOYED_COMMIT_INVALID")
    observed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()
    if observed != deployed_commit:
        raise CaptureError("DEPLOYED_COMMIT_MISMATCH", f"observed={observed}")
    reports = repo / "docs" / "reports"
    paths = {
        "fixture": reports / "alpha_atlas_v4_stage_b_verification_manifest.v3.json",
        "config": reports / "alpha_atlas_v4_stage_b_runtime_config.owner-bound.v1.json",
        "sector_context": reports / "alpha_atlas_v4_stage_b_sector_proxy_binding.accepted.v2.json",
    }
    expected = {
        "fixture": FIXTURE_FILE_SHA256,
        "config": CONFIG_FILE_SHA256,
        "sector_context": ACCEPTED_PROXY_FILE_SHA256,
    }
    for key, path in paths.items():
        if _sha256(path) != expected[key]:
            raise CaptureError("POST_DEPLOY_BOUND_FILE_HASH_MISMATCH", key)
    source_files = [
        "moneybot/services/alpha_atlas_v4_stage_b.py",
        "scripts/run_alpha_atlas_v4_stage_b_operational.py",
    ]
    value: dict[str, object] = {
        "schema_version": "alpha-atlas-v4-stage-b-post-deploy-authorization.v1",
        "status": "PREPARED_FOR_EXECUTION_APPROVAL_NOT_APPROVED",
        "mode": "LIVE",
        "execution_gate_usable": False,
        "owner_approval": "NOT_GIVEN_FOR_EXECUTION",
        "owner_accepts_sector_proxy_clarification": True,
        "stage_b_execution": "NOT_AUTHORIZED_NOT_EXECUTED",
        "session": "2026-10-20",
        "maximum_attempts": 18,
        "fixture_sha256": FIXTURE_CONTENT_SHA256,
        "sector_context_sha256": ACCEPTED_PROXY_CONTENT_SHA256,
        "operational_config_sha256": "c33217363d3be4a0cee9107f18bc4b73ca0534814d25897a7cb9702fd28d9186",
        "deployed_revision": {
            "git_commit": deployed_commit,
            "source_file_sha256s": {name: _sha256(repo / name) for name in source_files},
        },
        "bound_file_sha256s": expected,
        "budgets": {
            "expected_massive_attempts": 5,
            "maximum_massive_attempts": 18,
            "cumulative_massive_cap": 3789,
            "s3_operation_cap": 512,
            "primary_cap_bytes": 4_067_328,
            "backup_cap_bytes": 4_067_328,
            "combined_cap_bytes": 8_134_656,
        },
        "live_requests": 0,
        "instruction": "Owner must separately review and convert this record to APPROVED, then independently pin its new hash before Stage B can execute.",
    }
    value["content_sha256"] = hashlib.sha256(canonical_bytes(value)).hexdigest()
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a non-approved authorization from the deployed revision")
    parser.add_argument("--deployed-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = prepare(Path(__file__).resolve().parents[1], args.deployed_commit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": value["status"], "output": str(args.output), "content_sha256": value["content_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
