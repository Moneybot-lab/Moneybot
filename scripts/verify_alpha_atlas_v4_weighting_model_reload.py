#!/usr/bin/env python3
"""Verify repaired weighting model metadata and replay without fitting."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from moneybot.services.alpha_atlas_v4_weighting_model_reload import repair_and_verify  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--paired-comparison", required=True, type=Path)
    parser.add_argument("--registration", required=True, type=Path)
    parser.add_argument("--feature-store", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--execution-code-sha", required=True)
    args = parser.parse_args()
    report = repair_and_verify(execution_predictions=args.predictions,
        paired_comparison=args.paired_comparison, registration_path=args.registration,
        feature_store=args.feature_store, output_dir=args.output_dir,
        execution_code_sha=args.execution_code_sha)
    if not report["all_replays_verified"]:
        raise SystemExit("model metadata repaired, but replay is blocked or unresolved; inspect report")


if __name__ == "__main__":
    main()
