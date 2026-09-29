from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.validate_alpha_atlas_v4_acquisition_amendment import validate

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "docs" / "reports"
JSON_PATH = REPORTS / "alpha_atlas_v4_prospective_data_acquisition_amendment.v2.json"
MD_PATH = REPORTS / "alpha_atlas_v4_prospective_data_acquisition_amendment.v2.md"


def test_corrected_budget_and_cross_document_consistency():
    result = validate(JSON_PATH, MD_PATH)
    assert result["status"] == "PASS"
    assert result["expected_requests"] == 1248
    assert result["maximum_requests"] == 3789
    assert result["maximum_retained_evidence_bytes"] < 1536 * 1024 * 1024
    assert result["primary_capacity_bytes"] == 2 * 1024**3


def test_dependencies_adjustment_and_stages_are_explicit():
    report = json.loads(JSON_PATH.read_text())
    assert len(report["dependency_accounting"]) == 6
    assert [stage["stage"] for stage in report["stages"]] == [
        "A_IMPLEMENTATION_SYNTHETIC", "B_BOUNDED_OPERATIONAL_VERIFICATION", "C_TEN_SESSION_PILOT"
    ]
    assert report["adjustment_revision_policy"]["calculation_input"].startswith("Persist and calculate from provider adjusted=false")
    assert "VWAP" in report["adjustment_revision_policy"]["method"]
    assert report["request_budget"]["persistent_accounting"].startswith("Verification, bootstrap")
    assert report["storage_arrangement"]["blocker"].startswith("No such writable backup")


def test_v1_is_preserved_and_v2_records_correction():
    v1 = REPORTS / "alpha_atlas_v4_prospective_data_acquisition_amendment.v1.json"
    report = json.loads(JSON_PATH.read_text())
    assert v1.is_file()
    assert report["corrects"]["version"] == "v1"
    assert report["corrects"]["commit"] == "1c2400d"
    assert report["preserved_statuses"]["qqq_spy_manifest"] == "PROPOSED_NOT_APPROVED"


def test_exact_validator_module_execution_without_inherited_pythonpath():
    env = dict(os.environ); env.pop("PYTHONPATH", None)
    proc = subprocess.run([sys.executable, "-m", "scripts.validate_alpha_atlas_v4_acquisition_amendment"], cwd=ROOT, env=env, text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["status"] == "PASS"
