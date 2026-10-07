from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import moneybot.services.alpha_atlas_v4_stage_b as stage_b
from moneybot.services.alpha_atlas_v4_prospective_snapshot import CaptureError
from moneybot.services.alpha_atlas_v4_stage_b import REQUIRED_ROOT, StageBRunner, authorization_hashes
from scripts.approve_alpha_atlas_v4_stage_b_authorization import approve
from scripts.prepare_alpha_atlas_v4_stage_b_post_deploy_authorization import prepare

REPO = Path(__file__).resolve().parents[1]
FIXTURE_NAME = "alpha_atlas_v4_stage_b_operational_verification_manifest.v1.json"
FIXTURE_PATH = REPO / "docs/reports" / FIXTURE_NAME
PROXY_PATH = REPO / "docs/reports/alpha_atlas_v4_stage_b_operational_sector_proxy_binding.accepted.v1.json"
CONFIG = json.loads((REPO / "docs/reports/alpha_atlas_v4_stage_b_runtime_config.owner-bound.v1.json").read_text())
UTC = timezone.utc
NOW = datetime(2026, 10, 8, 18, tzinfo=UTC)


def head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, check=True,
                          text=True, capture_output=True).stdout.strip()


def prepared() -> dict:
    return prepare(REPO, head(), execution_valid_from="2026-10-08T14:00:00Z",
                   execution_valid_until="2026-10-09T22:00:00Z",
                   now=datetime(2026, 10, 7, 12, tzinfo=UTC))


def test_approved_output_passes_real_live_preflight_without_clients(monkeypatch):
    draft = prepared()
    draft_pin = authorization_hashes(draft)["external_complete_canonical_sha256"]
    approved = approve(REPO, json.dumps(draft).encode(), expected_external_sha256=draft_pin,
                       now=datetime(2026, 10, 7, 13, tzinfo=UTC))
    approved_pin = authorization_hashes(approved)["external_complete_canonical_sha256"]
    fixture = json.loads(FIXTURE_PATH.read_text())
    proxy = json.loads(PROXY_PATH.read_text())
    monkeypatch.setenv("MONEYBOT_PERSISTENT_DATA_DIR", REQUIRED_ROOT)
    monkeypatch.setattr(stage_b, "storage_preflight", lambda root: {
        "root": str(root), "free_bytes": 99_999_999, "required_bytes": stage_b.COMBINED_CAP
    })
    runner = StageBRunner(
        REPO, Path(REQUIRED_ROOT), offline=False,
        approved_authorization_sha256=approved_pin,
        operational_config_sha256=CONFIG["content_sha256"], fixture_name=FIXTURE_NAME,
        fixture_file_sha256=hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
        fixture_content_sha256=fixture["content_sha256"], session=date(2026, 10, 6),
        runtime_guard=lambda phase: {"phase": phase}, now=lambda: NOW,
    )
    plan, _ = runner.preflight(approved, proxy)
    assert plan["execution_purpose"] == "OPERATIONAL_VERIFICATION_ONLY"
    assert approved["status"] == "APPROVED"
    assert approved["stage_b_execution"] == "AUTHORIZED_NOT_EXECUTED"
    assert approved["approval_scope"] == {
        "single_execution": True, "stage_b_operational_verification": True,
        "pilot": False, "training": False, "scoring": False, "trading": False,
    }


def test_approval_requires_exact_prepared_pin_scope_revision_and_unexpired_interval():
    draft = prepared()
    pin = authorization_hashes(draft)["external_complete_canonical_sha256"]
    with pytest.raises(CaptureError, match="PREPARED_AUTHORIZATION_PIN_MISMATCH"):
        approve(REPO, json.dumps(draft).encode(), expected_external_sha256="0" * 64, now=NOW)
    changed = dict(draft); changed["maximum_attempts"] = 19
    changed.pop("content_sha256")
    from moneybot.services.alpha_atlas_v4_prospective_snapshot import canonical_bytes
    changed["content_sha256"] = hashlib.sha256(canonical_bytes(changed)).hexdigest()
    changed_pin = authorization_hashes(changed)["external_complete_canonical_sha256"]
    with pytest.raises(CaptureError, match="PREPARED_AUTHORIZATION_SCOPE_MISMATCH"):
        approve(REPO, json.dumps(changed).encode(), expected_external_sha256=changed_pin, now=NOW)
    with pytest.raises(CaptureError, match="EXECUTION_VALIDITY_INVALID"):
        approve(REPO, json.dumps(draft).encode(), expected_external_sha256=pin,
                now=datetime(2026, 10, 9, 22, tzinfo=UTC))


def test_approval_cli_is_offline_and_no_clobber(tmp_path):
    draft = prepared()
    prepared_path = tmp_path / "prepared.v2.json"
    prepared_path.write_text(json.dumps(draft, indent=2, sort_keys=True) + "\n")
    pin = authorization_hashes(draft)["external_complete_canonical_sha256"]
    output = tmp_path / "approved.v1.json"
    env = os.environ.copy(); env.pop("PYTHONPATH", None)
    # Supply a deterministic clock through the function above; the CLI is checked
    # only for no-clobber here because its real-time interval is intentionally strict.
    output.write_text("preserve")
    run = subprocess.run([
        sys.executable, "-m", "scripts.approve_alpha_atlas_v4_stage_b_authorization",
        "--prepared", str(prepared_path), "--prepared-external-sha256", pin,
        "--output", str(output),
    ], cwd=REPO, env=env, text=True, capture_output=True)
    assert run.returncode != 0
    assert "AUTHORIZATION_OUTPUT_EXISTS" in run.stderr
    assert output.read_text() == "preserve"
