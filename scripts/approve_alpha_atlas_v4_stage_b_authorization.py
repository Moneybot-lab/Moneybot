"""Convert one independently pinned Stage B draft into an approved, unexecuted record.

This command is deliberately local-only.  It does not read credentials, construct
clients, reserve attempts, or run Stage B.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from moneybot.services.alpha_atlas_v4_prospective_snapshot import CaptureError, canonical_bytes
from moneybot.services.alpha_atlas_v4_stage_b import (
    LIVE_OWNER_APPROVAL,
    REGISTERED_BUDGETS,
    SETUP_PACKAGE_FILE_SHA256,
    authorization_hashes,
    parse_utc_timestamp,
)
from scripts.prepare_alpha_atlas_v4_stage_b_post_deploy_authorization import (
    ACCEPTED_PROXY_FILE_SHA256,
    ACCEPTED_PROXY_CONTENT_SHA256,
    CONFIG_FILE_SHA256,
    FIXTURE_CONTENT_SHA256,
    FIXTURE_FILE_SHA256,
    PROXY_ACCEPTANCE_FILE_SHA256,
    PURPOSE_CLARIFICATION_FILE_SHA256,
)

UTC = timezone.utc
EXPECTED_PURPOSE = "OPERATIONAL_VERIFICATION_ONLY"
PREPARED_STATUS = "PREPARED_FOR_EXECUTION_APPROVAL_NOT_APPROVED"


def _validate_prepared(repo: Path, value: Mapping[str, Any], *, expected_external_sha256: str,
                       now: datetime) -> None:
    hashes = authorization_hashes(value)
    if not hashes["internal_content_valid"]:
        raise CaptureError("AUTHORIZATION_HASH_MISMATCH")
    if hashes["external_complete_canonical_sha256"] != expected_external_sha256:
        raise CaptureError("PREPARED_AUTHORIZATION_PIN_MISMATCH")
    exact = {
        "status": PREPARED_STATUS,
        "mode": "LIVE",
        "execution_gate_usable": False,
        "owner_approval": "NOT_GIVEN_FOR_EXECUTION",
        "owner_accepts_sector_proxy_clarification": True,
        "stage_b_execution": "NOT_AUTHORIZED_NOT_EXECUTED",
        "execution_purpose": EXPECTED_PURPOSE,
        "historical_data_as_of": "2026-10-06",
        "session": "2026-10-06",
        "hard_runtime_minutes": 55,
        "maximum_attempts": 18,
        "fixture_sha256": FIXTURE_CONTENT_SHA256,
        "setup_package_sha256": SETUP_PACKAGE_FILE_SHA256,
        "sector_context_sha256": ACCEPTED_PROXY_CONTENT_SHA256,
        "operational_config_sha256": "c33217363d3be4a0cee9107f18bc4b73ca0534814d25897a7cb9702fd28d9186",
        "premarket_timing_readiness": "NOT_TESTED",
        "prospective_snapshot_eligibility": "NOT_TESTED",
    }
    if any(value.get(key) != wanted for key, wanted in exact.items()):
        raise CaptureError("PREPARED_AUTHORIZATION_SCOPE_MISMATCH")
    if value.get("budgets") != REGISTERED_BUDGETS:
        raise CaptureError("AUTHORIZATION_BUDGET_MISMATCH")
    bound = value.get("bound_file_sha256s", {})
    expected_bound = {
        "setup_package": SETUP_PACKAGE_FILE_SHA256,
        "fixture": FIXTURE_FILE_SHA256,
        "config": CONFIG_FILE_SHA256,
        "sector_context": ACCEPTED_PROXY_FILE_SHA256,
        "purpose_clarification": PURPOSE_CLARIFICATION_FILE_SHA256,
        "proxy_acceptance": PROXY_ACCEPTANCE_FILE_SHA256,
    }
    if bound != expected_bound:
        raise CaptureError("PREPARED_AUTHORIZATION_SCOPE_MISMATCH", "bound files")
    revision = value.get("deployed_revision", {})
    observed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()
    if revision.get("git_commit") != observed:
        raise CaptureError("DEPLOYED_REVISION_MISMATCH")
    for name, wanted in revision.get("source_file_sha256s", {}).items():
        path = (repo / str(name)).resolve()
        if not path.is_relative_to(repo.resolve()) or not path.is_file():
            raise CaptureError("DEPLOYED_REVISION_MISMATCH", str(name))
        import hashlib
        if hashlib.sha256(path.read_bytes()).hexdigest() != wanted:
            raise CaptureError("DEPLOYED_REVISION_MISMATCH", str(name))
    valid_from = parse_utc_timestamp(value.get("execution_valid_from"))
    valid_until = parse_utc_timestamp(value.get("execution_valid_until"))
    instant = now.astimezone(UTC)
    if valid_from >= valid_until or instant >= valid_until:
        raise CaptureError("EXECUTION_VALIDITY_INVALID")


def approve(repo: Path, prepared_bytes: bytes, *, expected_external_sha256: str,
            now: datetime | None = None) -> dict[str, Any]:
    try:
        prepared = json.loads(prepared_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaptureError("AUTHORIZATION_NOT_JSON") from exc
    if not isinstance(prepared, dict):
        raise CaptureError("AUTHORIZATION_NOT_OBJECT")
    _validate_prepared(repo, prepared, expected_external_sha256=expected_external_sha256,
                       now=now or datetime.now(UTC))
    approved = dict(prepared)
    approved.pop("content_sha256", None)
    approved.update({
        "status": "APPROVED",
        "execution_gate_usable": True,
        "owner_approval": LIVE_OWNER_APPROVAL,
        "stage_b_execution": "AUTHORIZED_NOT_EXECUTED",
        "approval_scope": {
            "single_execution": True,
            "stage_b_operational_verification": True,
            "pilot": False,
            "training": False,
            "scoring": False,
            "trading": False,
        },
        "approved_from_prepared_external_complete_canonical_sha256": expected_external_sha256,
    })
    import hashlib
    approved["content_sha256"] = hashlib.sha256(canonical_bytes(approved)).hexdigest()
    return approved


def main() -> int:
    parser = argparse.ArgumentParser(description="Approve one pinned Stage B draft without executing it")
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--prepared-external-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise CaptureError("AUTHORIZATION_OUTPUT_EXISTS", str(args.output))
    repo = Path(__file__).resolve().parents[1]
    approved = approve(repo, args.prepared.read_bytes(),
                       expected_external_sha256=args.prepared_external_sha256)
    output_bytes = (json.dumps(approved, indent=2, sort_keys=True) + "\n").encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as handle:
        handle.write(output_bytes)
    print(json.dumps({"status": approved["status"], "stage_b_execution": approved["stage_b_execution"],
                      "output": str(args.output), **authorization_hashes(approved, file_bytes=output_bytes)},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
