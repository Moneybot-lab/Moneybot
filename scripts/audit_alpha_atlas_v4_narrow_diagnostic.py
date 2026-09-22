#!/usr/bin/env python3
"""Audit frozen grouping/membership for the narrow diagnostic; never score."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from moneybot.services.alpha_atlas_v4_baseline_registration import _load_development_rows
from moneybot.services.alpha_atlas_v4_narrow_diagnostic import grouping_audit, validate_spec


def main() -> int:
    p=argparse.ArgumentParser()
    for name in ("specification","canonical","plan","manifest","capture","output-dir"): p.add_argument("--"+name,type=Path,required=True)
    p.add_argument("--reviewed-specification-hash",required=True)
    a=p.parse_args(); a.output_dir.mkdir(parents=True,exist_ok=True)
    inputs={"canonical":a.canonical,"plan":a.plan,"manifest":a.manifest,"capture":a.capture}
    spec=validate_spec(a.specification,a.reviewed_specification_hash,inputs)
    plan=json.loads(a.plan.read_text()); development=set(map(str,plan["train_canonical_observation_ids"]))
    rows,_=_load_development_rows(a.canonical,development)
    capture=json.loads(a.capture.read_text()); assignments=[]
    for item in capture:
        for record in item.get("records") or []:
            assignments.append({"candidate":item["model_version"],"fold":item["fold_index"],"canonical_observation_id":record["id"]})
    assigned_ids={x["canonical_observation_id"] for x in assignments}
    audit=grouping_audit([rows[x] for x in sorted(assigned_ids)],assignments)
    audit.update({"schema_version":"alpha-atlas-v4-narrow-diagnostic-grouping-audit.v1","status":"AUDIT_COMPLETE_NO_SCORING",
      "specification_sha256":a.reviewed_specification_hash,"source_registration":spec["source_registration"],
      "performance_scoring_executed":False,"holdout_content_loaded":False})
    (a.output_dir/'narrow_diagnostic_grouping_audit.json').write_text(json.dumps(audit,indent=2,sort_keys=True)+'\n')
    (a.output_dir/'narrow_diagnostic_grouping_audit.md').write_text(
      f"# Narrow diagnostic grouping audit\n\n- Status: `{audit['status']}`.\n- Rows / assignments: `{audit['rows']}` / `{audit['assignments']}`.\n"
      f"- Ticker/date timing groups / cohorts: `{audit['ticker_date_timing_groups']}` / `{audit['cohorts']}`.\n"
      f"- Multiplicity: `{audit['groups_by_row_multiplicity']}`.\n- Weights reconcile: `{audit['weights_reconcile_per_cohort']}`.\n"
      "- Performance scoring executed: `false`.\n- Full row mapping is retained in the JSON report.\n")
    return 0


if __name__=='__main__': raise SystemExit(main())
