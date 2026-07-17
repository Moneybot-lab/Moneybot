#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def build_track_b_commands(
    *,
    python_executable: str,
    project_root: Path,
    input_log: str,
    train_ratio: float,
    min_rows: int,
    output_dir: Path,
    production_model: str = "data/day1_baseline_model.json",
    dataset_limit: int | None = 50000,
) -> list[list[str]]:
    scripts_dir = project_root / "scripts"
    dataset_path = output_dir / "decision_training_snapshot_track_b.jsonl"
    candidate_model_path = output_dir / "candidate_model_track_b.json"
    comparison_report_path = output_dir / "model_comparison_track_b.json"
    build_dataset_command = [
        python_executable,
        str(scripts_dir / "day8_build_decision_training_dataset.py"),
        "--input",
        input_log,
        "--output",
        str(dataset_path),
    ]
    if dataset_limit is not None:
        build_dataset_command.extend(["--limit", str(max(1, int(dataset_limit)))])

    return [
        build_dataset_command,
        [python_executable, str(scripts_dir / "day10_train_candidate_model.py"), "--input", str(dataset_path), "--output-model", str(candidate_model_path), "--train-ratio", str(train_ratio), "--min-rows", str(min_rows)],
        [python_executable, str(scripts_dir / "day11_compare_candidate_vs_production.py"), "--input", str(dataset_path), "--candidate-model", str(candidate_model_path), "--production-model", str(production_model), "--output", str(comparison_report_path), "--train-ratio", str(train_ratio), "--min-rows", str(min_rows)],
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Track B offline challenger feature/model/backtest pipeline with zero live routing.")
    parser.add_argument("--input-log", default="data/decision_events.jsonl")
    parser.add_argument("--output-dir", default="data/track_b")
    parser.add_argument("--train-ratio", type=float, default=0.8)
    parser.add_argument("--min-rows", type=int, default=200)
    parser.add_argument("--production-model", default="data/day1_baseline_model.json")
    parser.add_argument(
        "--dataset-limit",
        type=int,
        default=50000,
        help="Decision-event rows to pass through to the dataset builder. Defaults to the workflow export size so mature rows are not truncated to day8's smaller standalone default.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    commands = build_track_b_commands(
        python_executable=sys.executable,
        project_root=PROJECT_ROOT,
        input_log=args.input_log,
        train_ratio=max(0.1, min(0.95, float(args.train_ratio))),
        min_rows=max(1, int(args.min_rows)),
        output_dir=output_dir,
        production_model=args.production_model,
        dataset_limit=max(1, int(args.dataset_limit)),
    )

    started_at = datetime.now(timezone.utc).isoformat()
    summary: dict[str, object] = {
        "track": "track_b_offline",
        "started_at_utc": started_at,
        "input_log": args.input_log,
        "output_dir": str(output_dir),
        "dry_run": bool(args.dry_run),
        "dataset_limit": max(1, int(args.dataset_limit)),
        "commands": commands,
        "steps": [],
        "success": False,
    }

    if args.dry_run:
        print(json.dumps(summary, indent=2))
        return

    for command in commands:
        completed = subprocess.run(command, cwd=str(PROJECT_ROOT), capture_output=True, text=True, check=False)
        step = {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        summary["steps"].append(step)
        if completed.returncode != 0:
            summary["success"] = False
            (output_dir / "track_b_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print(json.dumps(summary, indent=2))
            raise SystemExit(completed.returncode)

    summary["success"] = True
    summary["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    (output_dir / "track_b_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
