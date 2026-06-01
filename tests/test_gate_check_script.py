import json
import os
import stat
import subprocess
from pathlib import Path


def test_portfolio_gate_accepts_calibration_rows_as_5d_evidence(tmp_path: Path):
    model_payload = {
        "data": {
            "model_loaded": True,
            "model_load_error": None,
            "rollout_dry_run": False,
            "portfolio_rollout_percentage": 50,
            "decision_logging": {"enabled": True},
            "calibration_report": {
                "rows": 56,
                "effective_brier_score": 0.236441,
            },
        }
    }
    outcomes_payload = {
        "data": {
            "used_unevaluated_fallback": False,
            "lookup_errors": 0,
            "summary_5d": {"evaluated_rows": 0},
            "evaluated_rows_5d_available": 0,
        }
    }
    curl = tmp_path / "curl"
    curl.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"model = {json.dumps(model_payload)!r}\n"
        f"outcomes = {json.dumps(outcomes_payload)!r}\n"
        "url = sys.argv[-1]\n"
        "print(model if url.endswith('/api/model-health') else outcomes)\n",
        encoding="utf-8",
    )
    curl.chmod(curl.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "BASE_URL": "https://example.test"}

    result = subprocess.run(
        ["bash", "scripts/gate_check.sh", "--gate", "portfolio_50_to_75"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS  5d evidence rows >= 40" in result.stdout
