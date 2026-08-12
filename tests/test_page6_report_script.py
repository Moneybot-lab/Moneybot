import json
import subprocess
import sys
from pathlib import Path


def test_page6_report_script_writes_manifest_metrics_and_blocked_gates(tmp_path):
    outcomes = tmp_path / "outcomes.json"
    output = tmp_path / "report.json"
    outcomes.write_text(json.dumps({"data": {"rows_5d": [
        {"symbol": "AAPL", "action": "BUY", "probability_up": 0.7, "return_5d": 0.02}
    ]}}), encoding="utf-8")

    completed = subprocess.run([
        sys.executable, "scripts/page6_historical_validation_report.py",
        "--outcomes", str(outcomes), "--output", str(output), "--min-rows", "2",
    ], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=False)

    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["dataset_manifest"]["checksum"]
    assert report["metrics"]["evaluated_rows"] == 1
    assert report["promotion_gates"]["promotion_ready"] is False
    assert report["rollout_recommendation"] == "hold_shadow"
