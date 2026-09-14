#!/usr/bin/env python3
"""Persist frozen-fold V4 development OOF predictions without holdout scoring."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.train_challenger_suite import (  # noqa: E402
    capture_v4_development_walk_forward_predictions,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture development-only OOF predictions from frozen V4 folds and recipes")
    parser.add_argument("--canonical-input", required=True, type=Path)
    parser.add_argument("--split-plan", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--provenance-output", required=True, type=Path)
    args = parser.parse_args()
    capture, provenance = capture_v4_development_walk_forward_predictions(
        args.canonical_input, args.split_plan, args.manifest
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.provenance_output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(capture, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.provenance_output.write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(provenance, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
