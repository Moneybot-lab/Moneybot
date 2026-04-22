#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moneybot.services.runtime_paths import day1_baseline_model_path, day1_training_snapshot_path


def build_day1_commands(
    *,
    python_executable: str,
    project_root: Path,
    output_snapshot: str,
    output_model: str,
    period: str,
    interval: str,
    horizon_days: int,
    target_return: float,
    train_ratio: float,
    symbols: list[str],
) -> list[list[str]]:
    generate_script = project_root / "scripts" / "day1_generate_training_data.py"
    train_script = project_root / "scripts" / "day1_train_baseline_model.py"

    return [
        [
            python_executable,
            str(generate_script),
            "--output",
            output_snapshot,
            "--period",
            period,
            "--interval",
            interval,
            "--horizon-days",
            str(horizon_days),
            "--target-return",
            str(target_return),
            "--symbols",
            *symbols,
        ],
        [
            python_executable,
            str(train_script),
            "--input",
            output_snapshot,
            "--output-model",
            output_model,
            "--horizon-days",
            str(horizon_days),
            "--target-return",
            str(target_return),
            "--train-ratio",
            str(train_ratio),
        ],
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the Day-1 training snapshot and retrain the baseline artifact in one command."
    )
    parser.add_argument("--output-snapshot", default=str(day1_training_snapshot_path()))
    parser.add_argument("--output-model", default=str(day1_baseline_model_path()))
    parser.add_argument("--period", default="2y")
    parser.add_argument("--interval", default="1d")
    parser.add_argument("--horizon-days", type=int, default=5)
    parser.add_argument("--target-return", type=float, default=0.0)
    parser.add_argument("--train-ratio", type=float, default=0.8)
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=["AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "GOOGL", "NFLX", "AMD", "JPM"],
    )
    args = parser.parse_args()

    commands = build_day1_commands(
        python_executable=sys.executable,
        project_root=PROJECT_ROOT,
        output_snapshot=args.output_snapshot,
        output_model=args.output_model,
        period=args.period,
        interval=args.interval,
        horizon_days=args.horizon_days,
        target_return=args.target_return,
        train_ratio=args.train_ratio,
        symbols=[symbol.upper() for symbol in args.symbols],
    )

    print(f"Project root: {PROJECT_ROOT}")
    print(f"Current working directory: {Path.cwd()}")
    if Path.cwd().resolve() != PROJECT_ROOT.resolve():
        print("Note: You are not in the repo root, but this wrapper still works because it uses absolute script paths.")

    for command in commands:
        print("\n>>>", " ".join(command))
        subprocess.run(command, check=True, cwd=PROJECT_ROOT)

    print("\nDone. Refreshed:")
    print(f"- Snapshot: {args.output_snapshot}")
    print(f"- Model: {args.output_model}")


if __name__ == "__main__":
    main()
