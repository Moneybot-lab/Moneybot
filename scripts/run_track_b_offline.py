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
    training_source: str = "massive",
) -> list[list[str]]:
    scripts_dir = project_root / "scripts"
    legacy_dataset_path = output_dir / "decision_training_snapshot_track_b.jsonl"
    massive_dataset_path = output_dir / "decision_training_snapshot_massive.jsonl"
    dataset_path = massive_dataset_path if training_source == "massive" else legacy_dataset_path
    candidate_model_path = output_dir / "candidate_model_track_b.json"
    comparison_report_path = output_dir / "model_comparison_track_b.json"
    massive_baseline_path = output_dir / "massive_baseline_model_v1.json"

    commands: list[list[str]] = []
    if training_source != "massive":
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
        commands.append(build_dataset_command)
    else:
        quality_dir = output_dir / "training_quality"
        commands.append([
            python_executable,
            str(scripts_dir / "train_massive_baseline_model.py"),
            "--train", str(quality_dir / "cleaned_train.jsonl"),
            "--test", str(quality_dir / "cleaned_test.jsonl"),
            "--all-cleaned", str(quality_dir / "cleaned_all.jsonl"),
            "--output", str(massive_baseline_path),
        ])

    if training_source == "massive":
        commands.extend([
            [python_executable, str(scripts_dir / "day10_train_candidate_model.py"), "--cleaned-train", str(output_dir / "training_quality" / "cleaned_train.jsonl"), "--cleaned-test", str(output_dir / "training_quality" / "cleaned_test.jsonl"), "--cleaned-all", str(output_dir / "training_quality" / "cleaned_all.jsonl"), "--model-version", "candidate_market_no_echo_v1", "--output-model", str(candidate_model_path), "--min-rows", str(min_rows)],
            [python_executable, str(scripts_dir / "day11_compare_candidate_vs_production.py"), "--input", str(output_dir / "training_quality" / "cleaned_test.jsonl"), "--input-is-holdout", "--candidate-model", str(candidate_model_path), "--production-model", str(production_model), "--massive-baseline-model", str(massive_baseline_path), "--output", str(comparison_report_path), "--min-rows", str(min_rows)],
            [python_executable, str(scripts_dir / "generate_next_generation_challengers.py"), "--train", str(output_dir / "training_quality" / "cleaned_train.jsonl"), "--test", str(output_dir / "training_quality" / "cleaned_test.jsonl"), "--all-cleaned", str(output_dir / "training_quality" / "cleaned_all.jsonl"), "--baseline-model", str(massive_baseline_path), "--comparison-report", str(comparison_report_path), "--output-dir", str(output_dir / "next_generation")],
        ])
    else:
        commands.extend([
            [python_executable, str(scripts_dir / "day10_train_candidate_model.py"), "--input", str(dataset_path), "--output-model", str(candidate_model_path), "--train-ratio", str(train_ratio), "--min-rows", str(min_rows)],
            [python_executable, str(scripts_dir / "day11_compare_candidate_vs_production.py"), "--input", str(dataset_path), "--candidate-model", str(candidate_model_path), "--production-model", str(production_model), "--output", str(comparison_report_path), "--train-ratio", str(train_ratio), "--min-rows", str(min_rows)],
        ])
    return commands


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
    parser.add_argument("--training-source", choices=("massive", "legacy"), default="massive", help="Use Massive-backed training rows by default; legacy rebuilds the older yfinance/day8 snapshot.")
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
        training_source=args.training_source,
    )

    started_at = datetime.now(timezone.utc).isoformat()
    summary: dict[str, object] = {
        "track": "track_b_offline",
        "started_at_utc": started_at,
        "input_log": args.input_log,
        "output_dir": str(output_dir),
        "dry_run": bool(args.dry_run),
        "dataset_limit": max(1, int(args.dataset_limit)),
        "training_source": args.training_source,
        "canonical_training_input": str(output_dir / ("training_quality/cleaned_all.jsonl" if args.training_source == "massive" else "decision_training_snapshot_track_b.jsonl")),
        "canonical_holdout_input": str(output_dir / "training_quality/cleaned_test.jsonl") if args.training_source == "massive" else None,
        "commands": commands,
        "steps": [],
        "success": False,
    }

    if args.dry_run:
        print(json.dumps(summary, indent=2))
        return

    if args.training_source == "massive":
        required_quality_inputs = [
            output_dir / "training_quality" / "cleaned_train.jsonl",
            output_dir / "training_quality" / "cleaned_test.jsonl",
            output_dir / "training_quality" / "cleaned_all.jsonl",
        ]
        missing_quality_inputs = [str(path) for path in required_quality_inputs if not path.exists()]
        if missing_quality_inputs:
            raise SystemExit(
                "Track B massive training requires cleaned training-quality inputs: "
                + ", ".join(missing_quality_inputs)
            )

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
    next_generation_manifest = output_dir / "next_generation" / "next_generation_challenger_manifest.json"
    if next_generation_manifest.exists():
        next_generation = json.loads(next_generation_manifest.read_text(encoding="utf-8"))
        summary["next_generation_challengers"] = {
            "generated": True,
            "count": len(next_generation.get("challengers") or []),
            "promotion_allowed": False,
            "routing_allowed": False,
            "manifest": str(next_generation_manifest),
        }
    summary["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    (output_dir / "track_b_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
