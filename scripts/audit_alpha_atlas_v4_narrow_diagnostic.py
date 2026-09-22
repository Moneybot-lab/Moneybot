#!/usr/bin/env python3
"""Audit frozen grouping/membership for the narrow diagnostic; never score."""
from __future__ import annotations
import argparse, json, shutil
from pathlib import Path
from moneybot.services.alpha_atlas_v4_baseline_registration import _load_development_rows
from moneybot.services.alpha_atlas_v4_narrow_diagnostic import DiagnosticSpecError, grouping_audit, validate_spec


def main() -> int:
    p=argparse.ArgumentParser()
    for name in ("specification","canonical","plan","manifest","capture","output-dir"): p.add_argument("--"+name,type=Path,required=True)
    p.add_argument("--reviewed-specification-hash",required=True)
    a=p.parse_args(); a.output_dir.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(a.specification,a.output_dir/'reviewed_narrow_diagnostic_specification.json')
    inputs={"canonical":a.canonical,"plan":a.plan,"manifest":a.manifest,"capture":a.capture}
    try:
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
          "input_validation_completed":True,"grouping_executed":True,"performance_scoring_executed":False,"holdout_content_loaded":False})
    except Exception as exc:
        audit={"schema_version":"alpha-atlas-v4-narrow-diagnostic-grouping-audit.v1","status":"AUDIT_INPUT_VALIDATION_FAILED",
          "reason_code":exc.code if isinstance(exc,DiagnosticSpecError) else type(exc).__name__,
          "input_role":getattr(exc,"role",None),"hash_type":getattr(exc,"hash_type",None),
          "expected_hash":getattr(exc,"expected",None),"actual_hash":getattr(exc,"actual",None),
          "specification_sha256_expected":a.reviewed_specification_hash,"input_validation_completed":False,
          "grouping_executed":False,"performance_scoring_executed":False,"holdout_content_loaded":False}
    (a.output_dir/'narrow_diagnostic_grouping_audit.json').write_text(json.dumps(audit,indent=2,sort_keys=True)+'\n')
    (a.output_dir/'narrow_diagnostic_grouping_audit.md').write_text(
      f"# Narrow diagnostic grouping audit\n\n- Status: `{audit['status']}`.\n- Rows / assignments: `{audit.get('rows')}` / `{audit.get('assignments')}`.\n"
      f"- Input role / hash type: `{audit.get('input_role')}` / `{audit.get('hash_type')}`.\n"
      f"- Expected / actual: `{audit.get('expected_hash')}` / `{audit.get('actual_hash')}`.\n"
      f"- Ticker/date timing groups / cohorts: `{audit.get('ticker_date_timing_groups')}` / `{audit.get('cohorts')}`.\n"
      f"- Multiplicity: `{audit.get('groups_by_row_multiplicity')}`.\n- Weights reconcile: `{audit.get('weights_reconcile_per_cohort')}`.\n"
      f"- Grouping / performance scoring executed: `{audit.get('grouping_executed')}` / `false`.\n- Full row mapping is retained in the JSON report after successful validation.\n")
    return 0 if audit["status"]=="AUDIT_COMPLETE_NO_SCORING" else 2


if __name__=='__main__': raise SystemExit(main())
