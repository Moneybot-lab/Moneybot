import json
import os
import subprocess
import sys

from scripts.validate_alpha_atlas_v4_stage_b_setup import validate


def test_stage_b_package_arithmetic_and_manifest():
    result = validate()
    assert result["status"] == "PASS"
    assert result["maximum_attempts"] == 18
    assert result["live_provider_requests"] == 0


def test_stage_b_validator_exact_module_without_pythonpath():
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    run = subprocess.run(
        [sys.executable, "-m", "scripts.validate_alpha_atlas_v4_stage_b_setup"],
        cwd=os.path.dirname(os.path.dirname(__file__)), env=env,
        check=True, capture_output=True, text=True,
    )
    assert json.loads(run.stdout)["status"] == "PASS"
