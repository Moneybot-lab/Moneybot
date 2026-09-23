#!/usr/bin/env python3
"""Validate the cohort-ranking pins; real-evidence scoring is intentionally disabled."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from moneybot.services.alpha_atlas_v4_cohort_ranking import (
    REAL_SCORING_ENABLED, RankingContractError, validate_frozen_contract,
)


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--proposal",type=Path,required=True)
    parser.add_argument("--evidence",type=Path,required=True)
    parser.add_argument("--lineage",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--request-real-scoring",action="store_true")
    args=parser.parse_args(); args.output.parent.mkdir(parents=True,exist_ok=True)
    try:
        report=validate_frozen_contract(args.proposal,args.evidence,args.lineage)
        report.update({"schema_version":"alpha-atlas-v4-cohort-ranking-validation.v1",
            "validation":"PASSED","scoring":"NOT_RUN","selected_count":None,"abstained_count":None,
            "metric_denominators":None,"weighting_reconciliation":None,
            "inputs":{"proposal":str(args.proposal),"evidence":str(args.evidence),"lineage":str(args.lineage)}})
        if args.request_real_scoring:
            if not REAL_SCORING_ENABLED:
                raise RankingContractError("REAL_SCORING_HARD_DISABLED_REQUIRES_CODE_CHANGE")
            raise RankingContractError("REAL_SCORING_ENTRYPOINT_NOT_IMPLEMENTED")
        code=0
    except Exception as exc:
        report={"schema_version":"alpha-atlas-v4-cohort-ranking-validation.v1","validation":"FAILED",
            "scoring":"NOT_RUN","reason_code":getattr(exc,"code",type(exc).__name__),
            "failure_details":getattr(exc,"details",{}),"real_scoring_enabled":REAL_SCORING_ENABLED,
            "selected_count":None,"abstained_count":None,"metric_denominators":None,"weighting_reconciliation":None,
            "inputs":{"proposal":str(args.proposal),"evidence":str(args.evidence),"lineage":str(args.lineage)}}
        code=2
    args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
