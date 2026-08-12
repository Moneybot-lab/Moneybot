#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moneybot.services.historical_validation import (
    build_dataset_manifest,
    build_historical_validation_report,
)
from moneybot.services.runtime_paths import (
    decision_outcomes_snapshot_path,
    historical_validation_report_path,
)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    candidates = data.get("rows_5d") or data.get("rows") or []
    return [dict(row) for row in candidates if isinstance(row, dict)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Page 6 historical validation and rollout report.")
    parser.add_argument("--outcomes", default=str(decision_outcomes_snapshot_path()))
    parser.add_argument("--output", default=str(historical_validation_report_path()))
    parser.add_argument("--baseline")
    parser.add_argument("--dataset-id", default="decision-outcomes-current")
    parser.add_argument("--dataset-source", default="decision_outcomes_snapshot")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--includes-delisted", action="store_true")
    parser.add_argument("--licensing-review-complete", action="store_true", default=os.environ.get("HISTORICAL_VALIDATION_LICENSING_REVIEW_COMPLETE", "false").lower() == "true")
    parser.add_argument("--privacy-review-complete", action="store_true", default=os.environ.get("HISTORICAL_VALIDATION_PRIVACY_REVIEW_COMPLETE", "false").lower() == "true")
    parser.add_argument("--min-rows", type=int, default=int(os.environ.get("HISTORICAL_VALIDATION_MIN_ROWS", "30")))
    args = parser.parse_args()

    outcomes = _read_json(Path(args.outcomes))
    rows = _rows(outcomes)
    baseline_payload = _read_json(Path(args.baseline)) if args.baseline else {}
    baseline_metrics = baseline_payload.get("metrics") if isinstance(baseline_payload.get("metrics"), dict) else baseline_payload
    manifest = build_dataset_manifest(
        dataset_id=args.dataset_id,
        source=args.dataset_source,
        rows=rows,
        start_date=args.start_date,
        end_date=args.end_date,
        includes_delisted=args.includes_delisted,
        notes=("Generated from materialized MoneyBot decision outcomes.",),
    )
    report = build_historical_validation_report(
        rows=rows,
        dataset_manifest=manifest,
        baseline_metrics=baseline_metrics,
        gate_options={
            "min_rows": max(1, args.min_rows),
            "licensing_review_complete": args.licensing_review_complete,
            "privacy_review_complete": args.privacy_review_complete,
        },
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(output),
        "evaluated_rows": report["metrics"]["evaluated_rows"],
        "promotion_ready": report["promotion_gates"]["promotion_ready"],
        "rollout_recommendation": report["rollout_recommendation"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
