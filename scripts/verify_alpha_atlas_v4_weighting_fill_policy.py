#!/usr/bin/env python3
from __future__ import annotations
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from moneybot.services.alpha_atlas_v4_weighting_fill_evidence import verify_fill_policy_evidence  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Persist evidence for the already-used V4 fold fill policies")
    for flag in ("diagnostic-capture", "registration", "feature-store", "reload-report", "corrected-states", "output"):
        parser.add_argument(f"--{flag}", required=True, type=Path)
    args = parser.parse_args()
    report = verify_fill_policy_evidence(diagnostic_capture=args.diagnostic_capture,
        registration_path=args.registration, feature_store=args.feature_store,
        reload_report_path=args.reload_report, corrected_states_path=args.corrected_states,
        output_path=args.output)
    if report["status"] != "VERIFIED":
        raise SystemExit("FILL_POLICY_CERTIFICATION: NOT_VERIFIED")
    print("FILL_POLICY_CERTIFICATION: VERIFIED")


if __name__ == "__main__":
    main()
