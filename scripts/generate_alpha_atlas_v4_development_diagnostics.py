#!/usr/bin/env python3
"""Generate bounded diagnostics from already-captured development OOF evidence."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from moneybot.services.alpha_atlas_v4_development_diagnostics import (  # noqa: E402
    generate_development_diagnostics,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate V4 development-only diagnostics; performs no fitting or holdout backtest")
    parser.add_argument("--canonical-input", required=True, type=Path)
    parser.add_argument("--split-plan", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--oof-predictions", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--baseline-sha", required=True)
    args = parser.parse_args()
    outputs = generate_development_diagnostics(canonical_input=args.canonical_input, split_plan_path=args.split_plan,
        manifest_path=args.manifest, predictions_path=args.oof_predictions, output_dir=args.output_dir, baseline_sha=args.baseline_sha)
    print("\n".join(str(path) for path in outputs.values()))


if __name__ == "__main__":
    main()
