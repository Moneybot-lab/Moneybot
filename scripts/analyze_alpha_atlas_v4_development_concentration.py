#!/usr/bin/env python3
"""Analyze an existing diagnostic artifact; never fits or downloads anything."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from moneybot.services.alpha_atlas_v4_concentration_diagnostics import analyze_artifact  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Artifact-only V4 development concentration analysis")
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--expected-capture-sha256", required=True)
    parser.add_argument("--expected-input-sha256", required=True)
    parser.add_argument("--expected-split-plan-sha256", required=True)
    parser.add_argument("--expected-diagnostic-code-sha", required=True)
    parser.add_argument("--expected-workflow-run", required=True)
    parser.add_argument("--expected-source-run", required=True)
    parser.add_argument("--selected-candidate", default="challenger-big-loss-avoider-v1")
    args = parser.parse_args()
    outputs = analyze_artifact(args.artifact, args.output_dir,
        expected_capture_sha256=args.expected_capture_sha256,
        expected_input_sha256=args.expected_input_sha256,
        expected_split_plan_sha256=args.expected_split_plan_sha256,
        expected_diagnostic_code_sha=args.expected_diagnostic_code_sha,
        expected_workflow_run=args.expected_workflow_run,
        expected_source_run=args.expected_source_run,
        selected_candidate=args.selected_candidate)
    print("\n".join(str(path) for path in outputs.values()))


if __name__ == "__main__":
    main()
