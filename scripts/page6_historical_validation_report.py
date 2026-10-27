#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moneybot.services.ops_runtime import atomic_write_text
from moneybot.services.historical_validation import build_historical_validation_report
from moneybot.services.historical_evidence import EvidenceDataset
from moneybot.services.runtime_paths import historical_validation_evidence_path, historical_validation_report_path


def report_from_dataset(dataset, *, model_version=None, baseline=None, gate_options=None):
    model = model_version or dataset.single_model()
    if model and dataset.cohort_count(model) != 1:
        model = None
    cohort = dataset.cohort_id(model)
    manifest = dataset.manifest
    baseline = baseline or {}
    # Baselines must identify the identical evaluation contract and model cohort.
    baseline_compatible = not baseline or (
        baseline.get("model_version") == model and baseline.get("model_cohort") == cohort and
        baseline.get("dataset_manifest", {}).get("outcome_contract") == manifest["outcome_contract"]
    )
    report = build_historical_validation_report(
        rows=dataset.rows(model), dataset_manifest=manifest,
        baseline_metrics=baseline.get("metrics", {}) if baseline_compatible else {}, gate_options=gate_options,
    )
    metrics, gates = report["metrics"], report["promotion_gates"]
    outcome_errors = sum(manifest["exclusions"].get(key, 0) for key in (
        "unavailable_history_session", "invalid_realized_price", "duplicate_history_session", "unknown_history_provenance"
    ))
    extra = [
        ("outcome_data_complete", outcome_errors == 0, outcome_errors),
        ("evidence_complete", manifest.get("complete") is True, manifest.get("complete")),
        ("model_cohort_selected", model is not None, model),
        ("decision_freshness_known", metrics["unknown_freshness_rows"] == 0, metrics["unknown_freshness_rows"]),
        ("historical_provider_integrity", not manifest["history"].get("historical_provider_failures"), manifest["history"].get("historical_provider_failures", 0)),
        ("prediction_identity_integrity", not manifest["exclusions"].get("conflicting_prediction_identity"), manifest["exclusions"].get("conflicting_prediction_identity", 0)),
        ("baseline_contract_compatible", baseline_compatible, baseline_compatible),
    ]
    for name, passed, actual in extra:
        gates["gates"].append({"name": name, "passed": bool(passed), "actual": actual, "limit": True if isinstance(actual, bool) else 0, "severity": "blocker"})
    failed = [g["name"] for g in gates["gates"] if not g["passed"] and g.get("severity") == "blocker"]
    ready = not failed
    gates.update(promotion_ready=ready, status="pass" if ready else "blocked", failed_blockers=len(failed))
    insufficient = metrics["evaluated_rows"] < int((gate_options or {}).get("min_rows", 30)) or metrics["calibration_rows"] == 0
    report.update(model_version=model, model_cohort=cohort, execution_status="success",
                  validation_status="passed" if ready else ("insufficient_evidence" if insufficient else "blocked"),
                  promotion_ready=ready, rollout_recommendation="promote" if ready else "hold_shadow",
                  required_next_steps=failed, evidence_diagnostics=manifest["counts"],
                  calibration_status=metrics["calibration_status"])
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the dedicated prediction evidence dataset (never dashboard display rows).")
    parser.add_argument("--evidence", default=str(historical_validation_evidence_path()))
    parser.add_argument("--output", default=str(historical_validation_report_path()))
    parser.add_argument("--baseline")
    parser.add_argument("--model-version", help="Explicit cohort; automatic selection only when exactly one live model is present.")
    parser.add_argument("--licensing-review-complete", action="store_true", default=os.environ.get("HISTORICAL_VALIDATION_LICENSING_REVIEW_COMPLETE", "false").lower() == "true")
    parser.add_argument("--privacy-review-complete", action="store_true", default=os.environ.get("HISTORICAL_VALIDATION_PRIVACY_REVIEW_COMPLETE", "false").lower() == "true")
    parser.add_argument("--min-rows", type=int, default=int(os.environ.get("HISTORICAL_VALIDATION_MIN_ROWS", "30")))
    args = parser.parse_args()
    dataset = EvidenceDataset(args.evidence)
    try:
        baseline = json.loads(Path(args.baseline).read_text()) if args.baseline else None
        report = report_from_dataset(dataset, model_version=args.model_version, baseline=baseline, gate_options={
            "min_rows": max(30, args.min_rows), "licensing_review_complete": args.licensing_review_complete,
            "privacy_review_complete": args.privacy_review_complete,
        })
        atomic_write_text(Path(args.output), json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps({k: report[k] for k in ("execution_status", "validation_status", "promotion_ready")}))
        return 0
    finally:
        dataset.close()


if __name__ == "__main__":
    raise SystemExit(main())
