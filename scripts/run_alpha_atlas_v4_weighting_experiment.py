#!/usr/bin/env python3
"""Register or execute the bounded V4 development weight ablation."""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from moneybot.services.alpha_atlas_v4_weighting_experiment import build_registration, execute  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-register or run the two-arm development-only weighting experiment")
    parser.add_argument("mode", choices=("register", "execute"))
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--split-plan", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--capture", required=True, type=Path)
    parser.add_argument("--registration", required=True, type=Path)
    parser.add_argument("--expected-input-hash", required=True)
    parser.add_argument("--expected-plan-hash", required=True)
    parser.add_argument("--expected-manifest-hash", required=True)
    parser.add_argument("--expected-capture-hash", required=True)
    parser.add_argument("--original-capture-code-sha", required=True)
    parser.add_argument("--source-run", default="34689216730")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.mode == "register":
        registration = build_registration(args.input, args.split_plan, args.manifest, args.capture,
            source_run=args.source_run, expected_input_hash=args.expected_input_hash,
            expected_plan_hash=args.expected_plan_hash, expected_manifest_hash=args.expected_manifest_hash,
            expected_capture_hash=args.expected_capture_hash,
            original_capture_code_sha=args.original_capture_code_sha)
        args.registration.parent.mkdir(parents=True, exist_ok=True)
        args.registration.write_text(json.dumps(registration, indent=2, sort_keys=True) + "\n")
        companion = args.registration.with_suffix(".md")
        companion.write_text(f"# {registration['experiment_id']}\n\nRegistration hash: `{registration['registration_sha256']}`. Two arms only: frozen current big-loss weights versus total-weight-preserving uniform weights. Primary endpoint: equal-fold mean of symbol/date-balanced Brier differences (uniform minus current); favorable requires a negative mean and at least two negative fold differences. This is previously inspected development research and cannot promote or change a threshold.\n")
        print(registration["registration_sha256"])
        return
    if args.output_dir is None:
        parser.error("execute requires --output-dir")
    registration = json.loads(args.registration.read_text())
    expected = {
        "input_sha256": args.expected_input_hash,
        "split_plan_sha256": args.expected_plan_hash,
        "manifest_sha256": args.expected_manifest_hash,
        "capture_sha256": args.expected_capture_hash,
        "original_capture_code_sha": args.original_capture_code_sha,
    }
    if any(registration.get(key) != value for key, value in expected.items()):
        raise SystemExit("CLI expected hashes do not match immutable registration")
    paths = execute(registration, args.input, args.split_plan, args.manifest, args.capture,
        args.output_dir, code_sha=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip())
    print("\n".join(map(str, paths.values())))


if __name__ == "__main__":
    main()
