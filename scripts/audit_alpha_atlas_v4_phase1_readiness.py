#!/usr/bin/env python3
"""Generate the deterministic, read-only V4 Phase 1 technical readiness audit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from moneybot.services.alpha_atlas_v4_phase1 import (  # noqa: E402
    PHASE1_REPORT_VERSION,
    authoritative_feature_mapping,
    controlled_backfill_plan,
    run_preflight,
    source_inventory,
)


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="docs/reports")
    parser.add_argument("--execute-probes", action="store_true")
    args = parser.parse_args()
    output = Path(args.output_dir)
    inventory = source_inventory()
    preflight = run_preflight(execute=args.execute_probes)
    mapping = authoritative_feature_mapping()
    plan = controlled_backfill_plan()
    overall = {
        "schema_version": PHASE1_REPORT_VERSION,
        "scope": "private_personal_use_research_only",
        "scope_boundaries": {
            "owner_investment_research_and_personal_account_management": True,
            "future_commercial_project_in_scope": False,
            "vendor_licensing_confirmation_required": False,
            "subscription_change_or_purchase_requested": False,
        },
        "phase0_baseline": {
            "track_b_run": "33960970412-1",
            "decision_log_records": 50219,
            "export_records": 50219,
            "continuity": "PREFIX_VERIFIED",
            "export_sha256": "7472b9da172130cd7f1e847c9c3ea131662dc995ec7aefe667c445bde1e58d22",
            "reconstructable_rows": 32619,
            "rows_total": 32619,
            "artifact_sha256": "2877edece1ad0a8fca48ffff8a313359cb37da37785a87311cb47750ff3196b5",
            "results_sha256": "a58d709d65a169df4afee8ac443db6a6579c6847e9355b76dd4afa0a2224d6dc",
        },
        "verdict": "BLOCKED_FULL_UNIVERSE_BACKFILL",
        "inventory_sha256": inventory["inventory_sha256"],
        "feature_mapping_sha256": mapping["mapping_sha256"],
        "backfill_plan_sha256": plan["plan_sha256"],
        "technical_access_status": preflight["overall_status"],
        "blockers": plan["blocking_conditions"],
        "production_behavior_changed": False,
        "full_backfill_started": False,
    }
    _write(output / "alpha_atlas_v4_phase1_source_inventory.json", inventory)
    _write(output / "alpha_atlas_v4_phase1_preflight.json", preflight)
    _write(output / "alpha_atlas_v4_phase1_feature_mapping.json", mapping)
    _write(output / "alpha_atlas_v4_phase1_backfill_plan.json", plan)
    _write(output / "alpha_atlas_v4_phase1_readiness.json", overall)
    print(json.dumps(overall, sort_keys=True))
    return (
        0 if not args.execute_probes or preflight["overall_status"] == "COMPLETE" else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
