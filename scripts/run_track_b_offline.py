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


NO_CANDIDATE_GENERATION_MARKERS = (
    "is a no-op clone; generation aborted before publishing reports",
)


def _is_expected_no_candidate_result(stderr: str) -> bool:
    """Return true when a generator safely declines to publish a clone.

    A next-generation search finding no model distinct from its parent is a
    valid no-promotion outcome, not an infrastructure failure. The generator
    deliberately exits non-zero before publishing, so the offline orchestrator
    must preserve routing safety while allowing diagnostics to be uploaded.
    """

    message = str(stderr or "").lower()
    return any(marker in message for marker in NO_CANDIDATE_GENERATION_MARKERS)


def build_track_b_commands(
    *,
    python_executable: str,
    project_root: Path,
    input_log: str,
    train_ratio: float,
    min_rows: int,
    output_dir: Path,
    dataset_limit: int | None = 50000,
) -> list[list[str]]:
    scripts_dir = project_root / "scripts"
    legacy_dataset_path = output_dir / "decision_training_snapshot_track_b.jsonl"
    massive_dataset_path = output_dir / "decision_training_snapshot_massive.jsonl"
    dataset_path = massive_dataset_path if training_source == "massive" else legacy_dataset_path
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
        [python_executable, str(scripts_dir / "day11_compare_candidate_vs_production.py"), "--input", str(dataset_path), "--candidate-model", str(candidate_model_path), "--production-model", "data/day1_baseline_model.json", "--output", str(comparison_report_path), "--train-ratio", str(train_ratio), "--min-rows", str(min_rows)],
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Track B offline challenger feature/model/backtest pipeline with zero live routing.")
    parser.add_argument("--input-log", default="data/decision_events.jsonl")
    parser.add_argument("--output-dir", default="data/track_b")
    parser.add_argument("--train-ratio", type=float, default=0.8)
    parser.add_argument("--min-rows", type=int, default=200)
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
    certification_path = servability_certification_path(output_dir)

    commands = build_track_b_commands(
        python_executable=sys.executable,
        project_root=PROJECT_ROOT,
        input_log=args.input_log,
        train_ratio=max(0.1, min(0.95, float(args.train_ratio))),
        min_rows=max(1, int(args.min_rows)),
        output_dir=output_dir,
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
            if _is_expected_no_candidate_result(completed.stderr):
                step["outcome"] = "no_candidate_generated"
                summary["success"] = True
                summary["outcome"] = "no_candidate_generated"
                summary["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
                (output_dir / "track_b_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
                print(json.dumps(summary, indent=2))
                return
            summary["success"] = False
            (output_dir / "track_b_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print(json.dumps(summary, indent=2))
            raise SystemExit(completed.returncode)

    summary["success"] = True
    corporate_audit = _load_json(output_dir / "training_quality" / "corporate_action_audit.json")
    split_manifest = _load_json(output_dir / "corporate_actions" / "split_adjustment_manifest.json")
    before_after = _load_json(output_dir / "training_quality" / "split_adjustment_before_after_report.json")
    quality_report = _load_json(output_dir / "training_quality" / "model_quality_report.json")
    summary["corporate_action_normalization"] = {
        "required": True,
        "passed": quality_report.get("corporate_action_normalization_passed") is True,
        "source": "massive",
        "policy": "event_time_split_adjusted",
        "split_event_count": corporate_audit.get("split_events_loaded"),
        "affected_symbols": corporate_audit.get("symbols_with_splits"),
        "affected_feature_rows": corporate_audit.get("affected_feature_rows"),
        "affected_label_rows": corporate_audit.get("affected_label_rows"),
        "split_metadata_hash": corporate_audit.get("split_metadata_hash"),
        "suspicious_rows_before": before_after.get("suspicious_rows_before"),
        "suspicious_rows_after": before_after.get("suspicious_rows_after"),
    }
    summary["alpha_atlas_v3"] = alpha_atlas_v3_summary(output_dir)
    summary["alpha_atlas_v31"] = alpha_atlas_v31_summary(output_dir)
    if certification_path.exists():
        certification = json.loads(certification_path.read_text(encoding="utf-8"))
        summary["servability_certification"] = {
            "passed": certification.get("passed") is True,
            "artifact_sha256": certification.get("candidate_artifact_sha256"),
            "feature_contract_version": certification.get("feature_contract_version"),
            "forecast_horizon": certification.get("forecast_horizon"),
            "blocking_issues": certification.get("blocking_reasons") or [],
            "certification_path": str(certification_path),
        }
    next_generation_manifest = output_dir / "next_generation" / "next_generation_challenger_manifest.json"
    if next_generation_manifest.exists():
        next_generation = json.loads(next_generation_manifest.read_text(encoding="utf-8"))
        scoreboard_path = output_dir / "challenger_vs_massive_baseline_report.json"
        scoreboard = json.loads(scoreboard_path.read_text(encoding="utf-8")) if scoreboard_path.exists() else {}
        summary["next_generation_challengers"] = {
            "generated": True,
            "count": len(next_generation.get("challengers") or []),
            "promotion_allowed": False,
            "routing_allowed": False,
            "manifest": str(next_generation_manifest),
            "leaderboard": scoreboard.get("leaderboard"),
        }
    summary["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    (output_dir / "track_b_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
