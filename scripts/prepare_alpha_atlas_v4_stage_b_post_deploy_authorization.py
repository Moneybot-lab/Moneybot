"""Prepare, but never approve, Stage B authorization from an observed deployment revision."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from moneybot.services.alpha_atlas_v4_prospective_snapshot import CaptureError, canonical_bytes
from moneybot.services.alpha_atlas_v4_stage_b import (
    REGISTERED_BUDGETS, SETUP_PACKAGE_FILE_SHA256, authorization_hashes,
    parse_utc_timestamp,
)

FIXTURE_FILE_SHA256 = "de6c1b13f978421947eb93ed64439fd50780ca20faa1741a10af4786b99dbb4e"
FIXTURE_CONTENT_SHA256 = "579013c85367c75f8a821aa30c75fe9cd261080a9c8060cfcd94af3d958499e9"
CONFIG_FILE_SHA256 = "0b7ef94a1a3ff023f01466a94e1b53d321608ff6c5e968e4a802d246b03c0f28"
ACCEPTED_PROXY_FILE_SHA256 = "9613f67239d0297cc148fbeb1260eed28bfd1b625ccc58c74061b156df5b84bf"
ACCEPTED_PROXY_CONTENT_SHA256 = "ba59fd773cd8be78816280e3a16a02bb3b83fcff8b3a92e5299eb78ceae28fc4"
PURPOSE_CLARIFICATION_FILE_SHA256 = "6bb84d9acfae889ee2be6ec3c9d202e079256b361f0d05f24dff5d92399361ef"
PROXY_ACCEPTANCE_FILE_SHA256 = "9bef7795c582e40ce17f48963fd513a5a46b2ea0a6d25af656fb3da00aaee2e1"
UTC = timezone.utc


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def prepare(repo: Path, deployed_commit: str, *, execution_valid_from: str,
            execution_valid_until: str, now: datetime | None=None) -> dict[str, object]:
    if len(deployed_commit) != 40 or any(c not in "0123456789abcdef" for c in deployed_commit):
        raise CaptureError("DEPLOYED_COMMIT_INVALID")
    observed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()
    if observed != deployed_commit:
        raise CaptureError("DEPLOYED_COMMIT_MISMATCH", f"observed={observed}")
    reports = repo / "docs" / "reports"
    paths = {
        "setup_package": reports / "alpha_atlas_v4_stage_b_setup_package.v1.json",
        "fixture": reports / "alpha_atlas_v4_stage_b_operational_verification_manifest.v1.json",
        "config": reports / "alpha_atlas_v4_stage_b_runtime_config.owner-bound.v1.json",
        "sector_context": reports / "alpha_atlas_v4_stage_b_operational_sector_proxy_binding.accepted.v1.json",
        "purpose_clarification": reports / "alpha_atlas_v4_stage_b_operational_purpose_clarification.v1.json",
        "proxy_acceptance": reports / "alpha_atlas_v4_stage_b_operational_proxy_acceptance.v1.json",
    }
    expected = {
        "setup_package": SETUP_PACKAGE_FILE_SHA256,
        "fixture": FIXTURE_FILE_SHA256,
        "config": CONFIG_FILE_SHA256,
        "sector_context": ACCEPTED_PROXY_FILE_SHA256,
        "purpose_clarification": PURPOSE_CLARIFICATION_FILE_SHA256,
        "proxy_acceptance": PROXY_ACCEPTANCE_FILE_SHA256,
    }
    for key, path in paths.items():
        if _sha256(path) != expected[key]:
            raise CaptureError("POST_DEPLOY_BOUND_FILE_HASH_MISMATCH", key)
    valid_from=parse_utc_timestamp(execution_valid_from,code="PREPARATION_INTERVAL_INVALID")
    valid_until=parse_utc_timestamp(execution_valid_until,code="PREPARATION_INTERVAL_INVALID")
    prepared_at=parse_utc_timestamp((now or datetime.now(UTC)).isoformat(),code="PREPARATION_TIME_INVALID")
    if valid_from>=valid_until: raise CaptureError("PREPARATION_INTERVAL_INVALID")
    if valid_until<=prepared_at: raise CaptureError("PREPARATION_INTERVAL_EXPIRED")
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
        "execution_purpose": "OPERATIONAL_VERIFICATION_ONLY",
        "historical_data_as_of": "2026-10-06",
        "session": "2026-10-06",
        "execution_valid_from": _utc_text(valid_from),
        "execution_valid_until": _utc_text(valid_until),
        "hard_runtime_minutes": 55,
        "premarket_timing_readiness": "NOT_TESTED",
        "prospective_snapshot_eligibility": "NOT_TESTED",
        "maximum_attempts": 18,
        "fixture_sha256": FIXTURE_CONTENT_SHA256,
        "setup_package_sha256": SETUP_PACKAGE_FILE_SHA256,
        "sector_context_sha256": ACCEPTED_PROXY_CONTENT_SHA256,
        "operational_config_sha256": "c33217363d3be4a0cee9107f18bc4b73ca0534814d25897a7cb9702fd28d9186",
        "deployed_revision": {
            "git_commit": deployed_commit,
            "source_file_sha256s": {name: _sha256(repo / name) for name in source_files},
        },
        "bound_file_sha256s": expected,
        "budgets": dict(REGISTERED_BUDGETS),
        "hash_semantics": {
            "internal_content_sha256": "SHA-256 of canonical JSON after removing content_sha256.",
            "external_complete_canonical_sha256": "SHA-256 of complete canonical authorization including content_sha256; this is the runner pin.",
            "file_byte_sha256": "SHA-256 of exact saved file bytes; formatting-sensitive and not the runner pin.",
        },
        "live_requests": 0,
        "instruction": "Owner must separately review and convert this record to APPROVED, then independently pin its new hash before Stage B can execute.",
    }
    value["content_sha256"] = hashlib.sha256(canonical_bytes(value)).hexdigest()
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a non-approved authorization from the deployed revision")
    parser.add_argument("--deployed-commit", required=True)
    parser.add_argument("--execution-valid-from", required=True)
    parser.add_argument("--execution-valid-until", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): raise CaptureError("AUTHORIZATION_OUTPUT_EXISTS",str(args.output))
    value = prepare(Path(__file__).resolve().parents[1], args.deployed_commit,
                    execution_valid_from=args.execution_valid_from,
                    execution_valid_until=args.execution_valid_until)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    file_bytes=(json.dumps(value,indent=2,sort_keys=True)+"\n").encode()
    with args.output.open("xb") as handle: handle.write(file_bytes)
    hashes=authorization_hashes(value,file_bytes=file_bytes)
    print(json.dumps({"status":value["status"],"output":str(args.output),**hashes},sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
