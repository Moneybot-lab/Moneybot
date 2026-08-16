import json
import os
import stat
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_gate_with_payloads(tmp_path: Path, *, gate: str, model_payload: dict, outcomes_payload: dict) -> subprocess.CompletedProcess[str]:
    curl = tmp_path / "curl"
    curl.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"model = {json.dumps(model_payload)!r}\n"
        f"outcomes = {json.dumps(outcomes_payload)!r}\n"
        "url = sys.argv[-1]\n"
        "print(model if url.endswith('/api/model-health') else outcomes)\n",
        encoding="utf-8",
    )
    curl.chmod(curl.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "BASE_URL": "https://example.test"}
    return subprocess.run(
        ["bash", "scripts/gate_check.sh", "--gate", gate],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _base_model_payload(*, calibration_rows: int, brier: float = 0.236441, rollout: int = 75, portfolio_rollout: int = 50) -> dict:
    return {
        "data": {
            "model_loaded": True,
            "model_load_error": None,
            "rollout_dry_run": False,
            "rollout_percentage": rollout,
            "portfolio_rollout_percentage": portfolio_rollout,
            "decision_logging": {"enabled": True},
            "calibration_report": {
                "rows": calibration_rows,
                "effective_brier_score": brier,
            },
        }
    }


def _base_outcomes_payload(*, evaluated_available: int = 120, accuracy_1d: float = 0.6389, rows_5d: int = 0) -> dict:
    return {
        "data": {
            "used_unevaluated_fallback": False,
            "lookup_errors": 0,
            "evaluated_rows_available": evaluated_available,
            "evaluated_rows_5d_available": rows_5d,
            "summary_1d": {"accuracy": accuracy_1d, "evaluated_rows": 36},
            "summary_5d": {"evaluated_rows": rows_5d, "accuracy": None},
        }
    }


def test_portfolio_gate_accepts_calibration_rows_as_5d_evidence(tmp_path: Path):
    result = _run_gate_with_payloads(
        tmp_path,
        gate="portfolio_50_to_75",
        model_payload=_base_model_payload(calibration_rows=56),
        outcomes_payload=_base_outcomes_payload(evaluated_available=64, rows_5d=0),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS  5d evidence rows >= 40" in result.stdout


def test_quick_75_to_100_gate_waits_for_60_5d_evidence_rows(tmp_path: Path):
    result = _run_gate_with_payloads(
        tmp_path,
        gate="75_to_100",
        model_payload=_base_model_payload(calibration_rows=56),
        outcomes_payload=_base_outcomes_payload(rows_5d=0),
    )

    assert result.returncode == 1
    assert "FAIL  5d evidence rows >= 60" in result.stdout
    assert "calibration_report.rows >= 100" not in result.stdout
    assert "summary_5d.accuracy" not in result.stdout


def test_quick_75_to_100_gate_accepts_calibration_rows_as_5d_evidence(tmp_path: Path):
    result = _run_gate_with_payloads(
        tmp_path,
        gate="75_to_100",
        model_payload=_base_model_payload(calibration_rows=60),
        outcomes_payload=_base_outcomes_payload(rows_5d=0),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS  5d evidence rows >= 60" in result.stdout


def test_quick_10_to_25_gate_requires_current_10_percent(tmp_path: Path):
    result = _run_gate_with_payloads(
        tmp_path,
        gate="10_to_25",
        model_payload=_base_model_payload(calibration_rows=30, rollout=25, portfolio_rollout=10),
        outcomes_payload=_base_outcomes_payload(evaluated_available=24, rows_5d=10, accuracy_1d=0.50),
    )

    assert result.returncode == 1
    assert "FAIL  rollout_percentage == 10" in result.stdout
    assert "PASS  5d evidence rows >= 10" in result.stdout


def test_quick_25_to_50_gate_passes_with_current_25_percent(tmp_path: Path):
    result = _run_gate_with_payloads(
        tmp_path,
        gate="25_to_50",
        model_payload=_base_model_payload(calibration_rows=30, rollout=25, portfolio_rollout=10),
        outcomes_payload=_base_outcomes_payload(evaluated_available=44, rows_5d=20, accuracy_1d=0.50),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS  rollout_percentage == 25" in result.stdout
    assert "PASS  5d evidence rows >= 20" in result.stdout


def test_portfolio_10_to_25_gate_passes_with_current_10_percent(tmp_path: Path):
    result = _run_gate_with_payloads(
        tmp_path,
        gate="portfolio_10_to_25",
        model_payload=_base_model_payload(calibration_rows=30, rollout=10, portfolio_rollout=10),
        outcomes_payload=_base_outcomes_payload(evaluated_available=24, rows_5d=10, accuracy_1d=0.50),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS  portfolio_rollout_percentage == 10" in result.stdout
    assert "PASS  5d evidence rows >= 10" in result.stdout


def test_portfolio_25_to_50_gate_requires_current_25_percent(tmp_path: Path):
    result = _run_gate_with_payloads(
        tmp_path,
        gate="portfolio_25_to_50",
        model_payload=_base_model_payload(calibration_rows=30, rollout=10, portfolio_rollout=10),
        outcomes_payload=_base_outcomes_payload(evaluated_available=44, rows_5d=20, accuracy_1d=0.50),
    )

    assert result.returncode == 1
    assert "FAIL  portfolio_rollout_percentage == 25" in result.stdout
    assert "PASS  5d evidence rows >= 20" in result.stdout
