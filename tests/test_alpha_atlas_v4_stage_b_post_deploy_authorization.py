from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from moneybot.services.alpha_atlas_v4_prospective_snapshot import CaptureError
from scripts.prepare_alpha_atlas_v4_stage_b_post_deploy_authorization import prepare

REPO = Path(__file__).resolve().parents[1]


def test_post_deploy_authorization_binds_observed_revision_but_stays_unapproved():
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, text=True, capture_output=True
    ).stdout.strip()
    value = prepare(REPO, head)
    assert value["deployed_revision"]["git_commit"] == head
    assert value["status"] == "PREPARED_FOR_EXECUTION_APPROVAL_NOT_APPROVED"
    assert value["execution_gate_usable"] is False
    assert value["owner_accepts_sector_proxy_clarification"] is True
    assert value["owner_approval"] == "NOT_GIVEN_FOR_EXECUTION"
    assert value["live_requests"] == 0
    assert value["budgets"]["maximum_massive_attempts"] == 18


def test_post_deploy_authorization_rejects_unobserved_revision():
    with pytest.raises(CaptureError, match="DEPLOYED_COMMIT_MISMATCH"):
        prepare(REPO, "0" * 40)


def test_post_deploy_cli_exact_module(tmp_path):
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, text=True, capture_output=True
    ).stdout.strip()
    output = tmp_path / "authorization.json"
    env = os.environ.copy()
    for name in ("PYTHONPATH", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                 "MONEYBOT_STAGE_B_AWS_ACCESS_KEY_ID", "MONEYBOT_STAGE_B_AWS_SECRET_ACCESS_KEY"):
        env.pop(name, None)
    run = subprocess.run(
        [
            sys.executable, "-m", "scripts.prepare_alpha_atlas_v4_stage_b_post_deploy_authorization",
            "--deployed-commit", head, "--output", str(output),
        ],
        cwd=REPO,
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )
    result = json.loads(output.read_text())
    assert result["status"] == "PREPARED_FOR_EXECUTION_APPROVAL_NOT_APPROVED"
    assert result["execution_gate_usable"] is False
    assert "APPROVED" not in result["status"].removesuffix("_NOT_APPROVED")
    assert json.loads(run.stdout)["content_sha256"] == result["content_sha256"]
