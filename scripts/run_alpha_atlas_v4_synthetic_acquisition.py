#!/usr/bin/env python3
"""Synthetic-only Stage A acquisition-to-handoff validation."""
from __future__ import annotations

import argparse
import json
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from moneybot.services.alpha_atlas_v4_acquisition import (
    AcquisitionRunner, EvidenceStorage, ImmutableStore, TransportResponse,
    acquisition_clock, adjust_unadjusted_bars, build_handoff,
    build_request_plan, verify_acquisition_documents,
)
from moneybot.services.alpha_atlas_v4_prospective_snapshot import canonical_bytes, sha256_bytes
from scripts.validate_alpha_atlas_v4_prospective_snapshot import write_artifact_checksums

UTC = timezone.utc


def _manifest(payload: dict) -> dict:
    payload=dict(payload); payload["content_sha256"]=sha256_bytes(canonical_bytes(payload)); return payload


class SyntheticTransport:
    def __init__(self, received_at: datetime): self.received_at=received_at; self.attempts=0
    def send(self, request, *, page_url, timeout_seconds):
        self.attempts+=1
        if request.family == "history":
            days=request.params["from"],request.params["to"]
            results=[{"date":day,"open":100,"high":102,"low":99,"close":101,"vwap":100.5,"volume":1000,"adjusted":False} for day in request_plan_window]
        elif request.family == "identity": results=[{"ticker":request.symbol,"typed_id":f"synthetic:{request.symbol}"}]
        else: results=[{"ticker":"AAA","execution_date":request_plan_window[-10],"adjustment_type":"forward_split","split_from":1,"split_to":2,"id":"synthetic-split"}]
        body=canonical_bytes({"status":"OK","request_id":f"synthetic-provider-{self.attempts}","results":results})
        return TransportResponse(200,body,self.received_at)


request_plan_window: list[str] = []


def run(root: Path, repository_root: Path) -> dict:
    day=date(2026,7,6); timing=acquisition_clock(day)
    universe=_manifest({"schema_version":"synthetic-universe.v1","synthetic":True,"ordered_members":["AAA","BBB"],"typed_identifiers":{"AAA":"synthetic:AAA","BBB":"synthetic:BBB"}})
    sectors=_manifest({"schema_version":"synthetic-sector-map.v1","synthetic":True,"stock_to_sector_etf":{"AAA":"XLK","BBB":"XLK"}})
    plan=build_request_plan(universe,sectors,day,stage="bootstrap")
    global request_plan_window; request_plan_window=plan["window_sessions"]
    now=datetime(2026,7,6,11,0,tzinfo=UTC); transport=SyntheticTransport(now)
    primary=ImmutableStore(root/"TEST_ONLY_PRIMARY"); backup=ImmutableStore(root/"TEST_ONLY_BACKUP"); restore=ImmutableStore(root/"TEST_ONLY_RESTORE")
    runner=AcquisitionRunner(primary,transport,synthetic=True,clock=lambda:now)
    results={}
    for spec in plan["requests"]: results[spec.request_id]=runner.execute(spec,"bootstrap",timing)
    split_results=results["splits:global"]["objects"][0]["payload"]["results"]
    evidence=EvidenceStorage(primary,backup)
    raw_backup=evidence.backup_primary_evidence()
    adjusted_results={}; bindings={}
    for stock in plan["stocks"]:
        raw=results[f"history:{stock}"]["objects"][0]
        adjusted=adjust_unadjusted_bars(raw["payload"]["results"],split_results,date.fromisoformat(request_plan_window[-1]),source_sha256=raw["sha256"])
        adjusted_results[stock]=evidence.publish_versioned(f"derived/{stock}-window.json",canonical_bytes(adjusted)); bindings[stock]={**adjusted["binding"],"window_sha256":adjusted["window_sha256"]}
    handoff=build_handoff(plan,results,generated_at=now,timing=timing,adjustment_bindings=bindings)
    handoff_result=evidence.publish_versioned("handoff/session.json",canonical_bytes(handoff))
    restored=evidence.restore(handoff_result["path"],restore)
    return {"schema_version":"alpha-atlas-v4-stage-a-synthetic-result.v1","status":"PASS","mode":"SYNTHETIC_ONLY","real_acquisition_available":False,"live_provider_requests":0,"synthetic_transport_attempts":transport.attempts,"handoff_eligible":handoff["eligible"],"bound_documents":verify_acquisition_documents(repository_root),"plan":{"stocks":plan["stocks"],"context_symbols":plan["context_symbols"],"request_count":len(plan["requests"]),"window_sessions":len(plan["window_sessions"])},"raw_primary_backup":{"object_count":len(raw_backup),"bytes":sum(x["bytes"] for x in raw_backup)},"adjusted_windows":adjusted_results,"handoff":handoff_result,"restore":{"sha256":restored["sha256"],"bytes":restored["bytes"],"synthetic_only":True},"limitations":["fake transport","temporary test storage","not retention or runtime evidence"]}


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--synthetic",action="store_true",required=True); parser.add_argument("--output-dir",type=Path,required=True); args=parser.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="aav4-stage-a-test-storage-") as tmp: report=run(Path(tmp),Path(__file__).resolve().parents[1])
        code=0
    except Exception as exc:
        report={"schema_version":"alpha-atlas-v4-stage-a-synthetic-result.v1","status":"FAIL","mode":"SYNTHETIC_ONLY","real_acquisition_available":False,"live_provider_requests":0,"error":{"type":type(exc).__name__,"message":str(exc)}}; code=1
    (args.output_dir/"report.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    (args.output_dir/"report.md").write_text(f"# Stage A synthetic acquisition\n\n- Status: `{report['status']}`\n- Live provider requests: `0`\n- Real acquisition available: `false`\n")
    (args.output_dir/"run.log").write_text(f"mode=SYNTHETIC_ONLY status={report['status']} live_provider_requests=0\n")
    write_artifact_checksums(args.output_dir,("report.json","report.md","run.log")); print(json.dumps(report,sort_keys=True)); return code


if __name__ == "__main__": raise SystemExit(main())
