#!/usr/bin/env python3
"""Verify persisted V4 source lineage and issue artifact-bound Phase 0 evidence."""

from __future__ import annotations
import argparse, json, sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from moneybot.services.alpha_atlas_v4_phase0 import (
    RECONSTRUCTION_VERSION,
    build_temporal_safety_certification,
    sha256_file,
    sha256_value,
    verify_observation,
)


def verify_artifact(
    input_path: Path,
    *,
    root: Path,
    max_observations: int = 0,
    observation_id: str | None = None,
) -> dict:
    rows = []
    with input_path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    if observation_id:
        selected = [
            row
            for row in rows
            if str(row.get("canonical_observation_id") or "") == observation_id
        ]
        if not selected:
            raise ValueError("requested canonical observation ID was not found")
    else:
        selected = rows if max_observations <= 0 else rows[:max_observations]
    cache = {}
    results = [verify_observation(row, root=root, cache=cache) for row in selected]
    reasons = Counter(reason for result in results for reason in result["failures"])
    failures = sum(result["status"] != "RECONSTRUCTABLE" for result in results)
    full_scope = len(selected) == len(rows) and not observation_id
    report = {
        "schema_version": RECONSTRUCTION_VERSION,
        "status": (
            "RECONSTRUCTABLE"
            if selected and not failures and len(selected) == len(rows)
            else ("NOT_RECONSTRUCTABLE" if failures else "PARTIAL")
        ),
        "artifact_path_label": input_path.name,
        "artifact_sha256": sha256_file(input_path),
        "verification_scope": "FULL_ARTIFACT" if full_scope else "BOUNDED_SAMPLE",
        "requested_observation_id": observation_id,
        "rows_total": len(rows),
        "rows_checked": len(selected),
        "reconstructable_rows": len(selected) - failures,
        "failure_count": failures,
        "failure_reasons": dict(sorted(reasons.items())),
        "results": results,
    }
    report["results_sha256"] = sha256_value(results)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--certification-output", required=True)
    parser.add_argument("--max-observations", type=int, default=0)
    parser.add_argument("--observation-id")
    args = parser.parse_args()
    input_path = Path(args.input)
    report = verify_artifact(
        input_path,
        root=Path(args.root),
        max_observations=max(0, args.max_observations),
        observation_id=args.observation_id,
    )
    certification = build_temporal_safety_certification(
        artifact_path=input_path,
        verification_report=report,
        timing_contract_version="alpha-atlas-v4-prediction-execution-contract.v1",
    )
    for path, payload in (
        (Path(args.output), report),
        (Path(args.certification_output), certification),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    # Keep the hosted log useful even when the certification intentionally exits 2.
    # The full reports remain artifacts; this bounded summary identifies the violated
    # invariant without relying on GitHub's rendering of a nested report.
    print(
        json.dumps(
            {
                "status": report["status"],
                "certification_status": certification["status"],
                "rows_total": report["rows_total"],
                "rows_checked": report["rows_checked"],
                "failure_count": report["failure_count"],
                "failure_reasons": report["failure_reasons"],
                "artifact_sha256": certification["artifact_sha256"],
                "verification_report_sha256": certification[
                    "verification_report_sha256"
                ],
                "timing_contract_version": certification[
                    "timing_contract_version"
                ],
                "validator_version": certification["validator_version"],
                "report_integrity_failures": certification[
                    "report_integrity_failures"
                ],
            },
            sort_keys=True,
        )
    )
    return 0 if certification["status"] == "VERIFIED_FOR_THIS_ARTIFACT" else 2


if __name__ == "__main__":
    raise SystemExit(main())
