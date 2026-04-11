#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def build_weekly_refresh_commands(
    *,
    python_executable: str,
    project_root: Path,
    input_log: str,
) -> list[list[str]]:
    scripts_dir = project_root / "scripts"
    return [
        [python_executable, str(scripts_dir / "day1_refresh_artifact.py")],
        [python_executable, str(scripts_dir / "run_daily_ops.py"), "--input-log", input_log, "--skip-day1-refresh"],
    ]


def main() -> None:
    base_dir = os.getenv("MONEYBOT_PERSISTENT_DATA_DIR", "data")
    os.makedirs(base_dir, exist_ok=True)
    parser = argparse.ArgumentParser(description="Run weekly model refresh + daily ops bundle.")
    parser.add_argument("--input-log", default=os.path.join(base_dir, "decision_events.jsonl"))
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    commands = build_weekly_refresh_commands(
        python_executable=sys.executable,
        project_root=project_root,
        input_log=args.input_log,
    )
    for command in commands:
        print("Running:", " ".join(command))
        subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
