import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


def _ts(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())


def _run(command: list[str], *, cwd: Path) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(cwd)
    completed = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, {
        "command": command,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def test_massive_offline_training_pipeline_smoke(tmp_path):
    """End-to-end smoke test for raw market joins, feature materialization, training, backtesting, and promotion prep."""
    repo = Path(__file__).resolve().parents[1]
    raw_dir = tmp_path / "raw" / "2026-07-03" / "us_stocks_sip" / "day_aggs_v1"
    raw_dir.mkdir(parents=True)

    for symbol, base in {"AAPL": 100.0, "MSFT": 200.0}.items():
        rows = ["ticker,date,open,high,low,close,volume"]
        start = date(2026, 1, 1)
        for idx in range(1, 91):
            day = (start + timedelta(days=idx - 1)).isoformat()
            close = base + (idx * 0.75) + ((idx % 7) * 0.2)
            rows.append(f"{symbol},{day},{close},{close + 1},{close - 1},{close},{1000000 + idx}")
        (raw_dir / f"{symbol}.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")

    decision_log = tmp_path / "decision_events.jsonl"
    events = []
    start = date(2026, 1, 1)
    for idx in range(7, 75):
        day = (start + timedelta(days=idx - 1)).isoformat()
        for symbol in ("AAPL", "MSFT"):
            events.append(
                {
                    "ts": _ts(day),
                    "endpoint": "quick_ask",
                    "symbol": symbol,
                    "decision_source": "deterministic_model",
                    "payload": {"recommendation": "BUY" if idx % 3 else "HOLD", "probability_up": 0.55},
                    "snapshot": {"model_version": "smoke-production-v1", "probability_up": 0.55},
                }
            )
    decision_log.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")

    training_rows = tmp_path / "track_b" / "decision_training_snapshot_massive.jsonl"
    flat_dir = tmp_path / "track_b" / "flat_feature_store"
    suite_dir = tmp_path / "track_b" / "challenger_suite"
    backtest_report = suite_dir / "backtest_report.json"
    promotion_dir = tmp_path / "track_b"

    _run(
        [
            sys.executable,
            "scripts/build_massive_decision_training_rows.py",
            "--raw-root",
            str(tmp_path / "raw"),
            "--decision-log",
            str(decision_log),
            "--output",
            str(training_rows),
            "--limit",
            "1000",
            "--horizon-days",
            "5",
        ],
        cwd=repo,
    )
    _run([sys.executable, "scripts/day15_materialize_flat_feature_store.py", "--input", str(training_rows), "--output-dir", str(flat_dir), "--train-ratio", "0.8"], cwd=repo)
    _run([sys.executable, "scripts/train_challenger_suite.py", "--input", str(flat_dir / "train.jsonl"), "--output-dir", str(suite_dir), "--min-rows", "20"], cwd=repo)
    _run(
        [
            sys.executable,
            "scripts/backtest_challenger_suite.py",
            "--suite-manifest",
            str(suite_dir / "challenger_suite_manifest.json"),
            "--feature-store",
            str(flat_dir / "test.jsonl"),
            "--output",
            str(backtest_report),
            "--min-rows",
            "10",
            "--transaction-cost-bps",
            "5",
            "--slippage-bps",
            "5",
        ],
        cwd=repo,
    )
    _run([sys.executable, "scripts/prepare_challenger_promotion.py", "--backtest-report", str(backtest_report), "--output-dir", str(promotion_dir)], cwd=repo)

    training_manifest = json.loads(training_rows.with_suffix(training_rows.suffix + ".manifest.json").read_text(encoding="utf-8"))
    feature_manifest = json.loads((flat_dir / "manifest.json").read_text(encoding="utf-8"))
    suite_manifest = json.loads((suite_dir / "challenger_suite_manifest.json").read_text(encoding="utf-8"))
    backtest = json.loads(backtest_report.read_text(encoding="utf-8"))
    promotion_report = json.loads((promotion_dir / "model_comparison_track_b.json").read_text(encoding="utf-8"))

    assert training_manifest["leakage_safe"] is True
    assert training_manifest["rows_joined"] >= 100
    assert feature_manifest["reproducibility"]["output_file_hashes"] is True
    assert suite_manifest["challenger_count"] >= 20
    assert len(backtest["challengers"]) == suite_manifest["challenger_count"]
    assert "buy_and_hold_return" in backtest["benchmark"]
    assert "candidate_win" in promotion_report
    assert (promotion_dir / "candidate_model_track_b.json").exists()
