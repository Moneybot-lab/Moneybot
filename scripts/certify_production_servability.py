#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moneybot.services.production_servability import certify_candidate


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Certify that a Track B artifact can be reproduced safely in production."
    )
    parser.add_argument(
        "--candidate-model", default="data/track_b/candidate_model_track_b.json"
    )
    parser.add_argument(
        "--output", default="data/track_b/production_servability_certification.json"
    )
    parser.add_argument("--comparison-report")
    args = parser.parse_args()

    candidate_path = Path(args.candidate_model)
    certification = certify_candidate(candidate_path)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(certification, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    if args.comparison_report:
        comparison_path = Path(args.comparison_report)
        try:
            comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            comparison = {}
        comparison["servability_certification"] = {
            "passed": certification["passed"],
            "artifact_sha256": certification["candidate_artifact_sha256"],
            "feature_contract_version": certification["feature_contract_version"],
            "forecast_horizon": certification["forecast_horizon"],
            "required_features": len(certification["required_features"]),
            "servable_features": sum(
                item["servable"] for item in certification["feature_audit"]
            ),
            "blocking_issues": certification["blocking_reasons"],
            "warnings": certification["warnings"],
            "certification_path": str(output_path),
        }
        promotion_gates = comparison.setdefault("production_promotion_gates", {})
        promotion_gates["servability_certification_passed"] = certification["passed"]
        promotion_gates["servability_blocking_reasons"] = certification[
            "blocking_reasons"
        ]
        comparison_path.write_text(
            json.dumps(comparison, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    print(json.dumps(certification, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
