from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import moneybot.services.alpha_atlas_v4_acquisition as acq
from moneybot.services.alpha_atlas_v4_acquisition import *
from moneybot.services.alpha_atlas_v4_prospective_snapshot import canonical_bytes, sha256_bytes

UTC=timezone.utc


def manifest(value):
    value=dict(value); value["content_sha256"]=sha256_bytes(canonical_bytes(value)); return value


def manifests():
    return (manifest({"synthetic":True,"ordered_members":["AAA","BBB"],"typed_identifiers":{"AAA":"id:a","BBB":"id:b"}}),
            manifest({"synthetic":True,"stock_to_sector_etf":{"AAA":"XLK","BBB":"XLK"}}))


class Fake:
    def __init__(self,responses): self.responses=list(responses); self.calls=[]
    def send(self,request,*,page_url,timeout_seconds):
        self.calls.append((request.request_id,page_url,timeout_seconds))
        item=self.responses.pop(0)
        if isinstance(item,Exception): raise item
        return item


def response(payload, at=None, status=200):
    return TransportResponse(status,canonical_bytes(payload),at or datetime(2026,7,6,11,tzinfo=UTC))


def test_plan_deduplicates_shared_context_and_fixed_window():
    u,s=manifests(); plan=build_request_plan(u,s,date(2026,7,6))
    assert plan["context_symbols"]==["SPY","XLK"]
    assert [x.request_id for x in plan["requests"]].count("history:SPY")==1
    assert len(plan["requests"])==7 and len(plan["window_sessions"])==75
    assert all(x.params.get("adjusted")!="true" for x in plan["requests"])


def test_plan_rejects_missing_identity_sector_and_bad_hash():
    u,s=manifests(); u["typed_identifiers"]["AAA"]=None; u["content_sha256"]=sha256_bytes(canonical_bytes({k:v for k,v in u.items() if k!="content_sha256"}))
    with pytest.raises(CaptureError,match="TYPED_IDENTITY"): build_request_plan(u,s,date(2026,7,6))
    u,s=manifests(); del s["stock_to_sector_etf"]["AAA"]; s["content_sha256"]=sha256_bytes(canonical_bytes({k:v for k,v in s.items() if k!="content_sha256"}))
    with pytest.raises(CaptureError,match="SECTOR_MAPPING"): build_request_plan(u,s,date(2026,7,6))
    u,s=manifests(); u["content_sha256"]="0"*64
    with pytest.raises(CaptureError,match="MANIFEST_HASH"): build_request_plan(u,s,date(2026,7,6))


def test_pagination_completion_and_unsafe_url(tmp_path):
    spec=RequestSpec("splits:global","splits","/stocks/v1/splits",{"limit":"5000"},None,2,262144); timing=acquisition_clock(date(2026,7,6))
    fake=Fake([response({"results":[],"next_url":"https://api.massive.com/stocks/v1/splits?cursor=x&apiKey=SECRET"}),response({"results":[]})])
    out=AcquisitionRunner(ImmutableStore(tmp_path),fake,clock=lambda:datetime(2026,7,6,11,tzinfo=UTC)).execute(spec,"bootstrap",timing)
    assert out["complete"] and "SECRET" not in fake.calls[1][1] and "REDACTED" in fake.calls[1][1]
    fake=Fake([response({"results":[],"next_url":"https://evil.example/x"})])
    with pytest.raises(CaptureError,match="UNSAFE"): AcquisitionRunner(ImmutableStore(tmp_path/"x"),fake,clock=lambda:datetime(2026,7,6,11,tzinfo=UTC)).execute(spec,"bootstrap",timing)


def test_incomplete_page_cap_and_retries_count(tmp_path):
    spec=RequestSpec("splits:global","splits","/stocks/v1/splits",{"limit":"5000"},None,2,262144); now=datetime(2026,7,6,11,tzinfo=UTC)
    fake=Fake([response({"results":[],"next_url":"https://api.massive.com/x"},now),response({"results":[],"next_url":"https://api.massive.com/y"},now)])
    runner=AcquisitionRunner(ImmutableStore(tmp_path),fake,clock=lambda:now)
    with pytest.raises(CaptureError,match="INCOMPLETE_PAGINATION"): runner.execute(spec,"bootstrap",acquisition_clock(date(2026,7,6)))
    fake=Fake([response({"results":[]},now,status=503),response({"results":[]},now)])
    runner=AcquisitionRunner(ImmutableStore(tmp_path/"retry"),fake,clock=lambda:now); runner.execute(spec,"bootstrap",acquisition_clock(date(2026,7,6)))
    assert len([x for x in runner.ledger.records() if x["event"]=="RESERVED"])==2


def test_endpoint_specific_validation_rejects_adjusted_history(tmp_path):
    spec=RequestSpec("history:AAA","history","/v2/aggs/ticker/AAA/range/1/day/a/b",{"adjusted":"true"},"AAA",1,262144)
    with pytest.raises(CaptureError,match="INVALID_HISTORY_REQUEST"):
        AcquisitionRunner(ImmutableStore(tmp_path),Fake([])).execute(spec,"bootstrap",acquisition_clock(date(2026,7,6)))


def test_ledger_restart_uncertain_corruption_and_budget(tmp_path,monkeypatch):
    store=ImmutableStore(tmp_path); ledger=AttemptLedger(store,synthetic=True); now=datetime.now(UTC)
    attempt=ledger.reserve("bootstrap","x",now); ledger.event(attempt,"TRANSMITTING",now)
    assert AttemptLedger(store,synthetic=True).reconcile_uncertain(now)==1
    assert any(x["event"]=="UNCERTAIN" for x in ledger.records())
    monkeypatch.setitem(acq.STAGE_LIMITS,"bootstrap",1)
    with pytest.raises(CaptureError,match="BUDGET"): ledger.reserve("bootstrap","y",now)
    with (tmp_path/"acquisition_attempts.jsonl").open("a") as f: f.write("{}\n")
    with pytest.raises(CaptureError,match="LEDGER_CORRUPT"): ledger.records()


def test_recurring_budget_is_per_session_and_cumulative_persists(tmp_path,monkeypatch):
    monkeypatch.setitem(acq.STAGE_LIMITS,"recurring",1); ledger=AttemptLedger(ImmutableStore(tmp_path),synthetic=True); now=datetime.now(UTC)
    ledger.reserve("recurring:session-01","a",now)
    with pytest.raises(CaptureError,match="BUDGET"): ledger.reserve("recurring:session-01","b",now)
    ledger.reserve("recurring:session-02","c",now)
    assert len([x for x in AttemptLedger(ImmutableStore(tmp_path),synthetic=True).records() if x["event"]=="RESERVED"])==2


def test_timing_dst_holiday_deadline_and_crossing(tmp_path):
    assert acquisition_clock(date(2026,1,5)).cutoff.hour != acquisition_clock(date(2026,7,6)).cutoff.hour
    with pytest.raises(CaptureError,match="INELIGIBLE_SESSION"): acquisition_clock(date(2026,12,25))
    clock=acquisition_clock(date(2026,11,27)); assert clock.cutoff.astimezone(NY).time().isoformat()=="07:30:00"
    assert remaining_timeout(clock.cutoff-timedelta(seconds=1),clock,6)==1
    with pytest.raises(CaptureError,match="CLOSED"): remaining_timeout(clock.cutoff,clock,6)
    spec=RequestSpec("history:AAA","history","/v2/aggs/ticker/AAA/range/1/day/a/b",{"adjusted":"false"},"AAA",1,262144)
    fake=Fake([response({"results":[]},clock.cutoff+timedelta(microseconds=1))])
    with pytest.raises(CaptureError,match="LATE_RESPONSE"): AcquisitionRunner(ImmutableStore(tmp_path),fake,clock=lambda:clock.cutoff-timedelta(seconds=1)).execute(spec,"bootstrap",clock)


def test_split_arithmetic_forward_reverse_multiple_and_boundaries():
    bars=[{"date":"2026-01-01","open":100,"high":110,"low":90,"close":104,"vwap":102,"volume":1000,"adjusted":False},{"date":"2026-01-05","open":60,"high":60,"low":60,"close":60,"vwap":60,"volume":3000,"adjusted":False}]
    splits=[{"ticker":"AAA","execution_date":"2026-01-03","adjustment_type":"forward_split","split_from":1,"split_to":2,"id":"f"},{"ticker":"AAA","execution_date":"2026-01-05","adjustment_type":"reverse_split","split_from":2,"split_to":1,"id":"r"}]
    out=adjust_unadjusted_bars(bars,splits,date(2026,1,5),source_sha256="a"*64)
    assert out["bars"][0]["close"]==104 and out["bars"][0]["vwap"]==102 and out["bars"][0]["volume"]==1000
    assert out["bars"][1]["close"]==60 and out["bars"][1]["applied_split_ids"]==[]
    forward=adjust_unadjusted_bars(bars[:1],splits[:1],date(2026,1,3),source_sha256="a"*64)["bars"][0]
    assert forward["close"]==52 and forward["vwap"]==51 and forward["volume"]==2000


def test_adjustment_rejects_adjusted_and_preserves_revisions(tmp_path):
    with pytest.raises(CaptureError,match="INCOMPATIBLE"): adjust_unadjusted_bars([{"date":"2026-01-01","adjusted":True}],[],date(2026,1,2),source_sha256="a"*64)
    storage=EvidenceStorage(ImmutableStore(tmp_path/"p"),ImmutableStore(tmp_path/"b"))
    first=storage.publish_versioned("window.json",b"v1"); second=storage.publish_versioned("window.json",b"v2")
    assert first["path"]!=second["path"] and (tmp_path/"p"/first["path"]).read_bytes()==b"v1"


def test_provider_correction_appends_and_links_revision(tmp_path):
    now=datetime(2026,7,6,11,tzinfo=UTC); timing=acquisition_clock(date(2026,7,6))
    spec=RequestSpec("history:AAA","history","/v2/aggs/ticker/AAA/range/1/day/a/b",{"adjusted":"false"},"AAA",1,262144)
    store=ImmutableStore(tmp_path)
    first=AcquisitionRunner(store,Fake([response({"results":[{"date":"2026-01-01","close":1}]},now)]),clock=lambda:now).execute(spec,"bootstrap",timing)
    second=AcquisitionRunner(store,Fake([response({"results":[{"date":"2026-01-01","close":2}]},now+timedelta(seconds=1))]),clock=lambda:now).execute(spec,"bootstrap",timing)
    receipt=json.loads((tmp_path/second["objects"][0]["receipt_path"]).read_text())
    assert receipt["supersedes_source_sha256"]==first["objects"][0]["sha256"]
    assert (tmp_path/first["objects"][0]["path"]).is_file()


def test_feature_window_fail_closed():
    assert validate_feature_window([str(x) for x in range(49)],[str(x) for x in range(30)],[str(x) for x in range(10)])==["INSUFFICIENT_VALID_HISTORY"]
    reasons=validate_feature_window([str(x) for x in range(50)],[str(x) for x in range(20)],[str(x) for x in range(5)])
    assert "CONTEXT_ALIGNMENT_FAILED:SPY" in reasons and "CONTEXT_ALIGNMENT_FAILED:SECTOR" in reasons


def test_storage_backup_restore_corruption_and_limits(tmp_path,monkeypatch):
    storage=EvidenceStorage(ImmutableStore(tmp_path/"p"),ImmutableStore(tmp_path/"b")); item=storage.publish_versioned("x",b"payload")
    assert not storage.reconcile()["missing_backup"]
    assert item["completion_receipt"]["primary"]["path"].endswith(".completion.json")
    restored=storage.restore(item["path"],ImmutableStore(tmp_path/"r")); assert restored["sha256"]==item["sha256"]
    copies=storage.backup_primary_evidence(); assert copies and all((tmp_path/"b"/x["backup_path"]).is_file() for x in copies)
    (tmp_path/"b"/item["path"]).write_bytes(b"bad")
    with pytest.raises(CaptureError): storage.restore(item["path"],ImmutableStore(tmp_path/"r2"))
    monkeypatch.setattr(acq,"PRIMARY_LIMIT",1)
    with pytest.raises(CaptureError,match="PRIMARY_EVIDENCE_LIMIT"): EvidenceStorage(ImmutableStore(tmp_path/"p2"),ImmutableStore(tmp_path/"b2")).publish_versioned("x",b"12")


def test_handoff_missing_visible_and_complete_eligible():
    u,s=manifests(); plan=build_request_plan(u,s,date(2026,7,6)); timing=acquisition_clock(date(2026,7,6)); now=timing.cutoff
    missing=build_handoff(plan,{},generated_at=now,timing=timing); assert not missing["eligible"] and len(missing["missing_request_ids"])==7
    results={}
    for request in plan["requests"]:
        rows=([{"date":f"d{x}"} for x in range(50)] if request.family=="history" else
              [{"ticker":request.symbol}] if request.family=="identity" else [])
        results[request.request_id]={"complete":True,"objects":[{"sha256":"a"*64,"receipt_path":"r","late":False,"payload":{"results":rows}}]}
    bindings={stock:{"engine_version":ADJUSTMENT_ENGINE_VERSION,"window_sha256":"b"*64} for stock in plan["stocks"]}
    good=build_handoff(plan,results,generated_at=now,timing=timing,adjustment_bindings=bindings); assert good["eligible"] and not good["missing_request_ids"]
    assert cache_only_handoff(good) is good
    with pytest.raises(CaptureError,match="HANDOFF_INELIGIBLE"): cache_only_handoff(missing)


def test_real_mode_unavailable_and_cli_zero_network(tmp_path):
    with pytest.raises(CaptureError,match="REAL_ACQUISITION_DISABLED"): AcquisitionRunner(ImmutableStore(tmp_path/"x"),Fake([]),synthetic=False)
    env=dict(os.environ); env.pop("PYTHONPATH",None); env["MASSIVE_API_KEY"]="must-not-be-read"
    output=tmp_path/"evidence"
    proc=subprocess.run([sys.executable,"-m","scripts.run_alpha_atlas_v4_synthetic_acquisition","--synthetic","--output-dir",str(output)],cwd=Path(__file__).resolve().parents[1],env=env,text=True,capture_output=True)
    assert proc.returncode==0,proc.stderr
    report=json.loads((output/"report.json").read_text()); assert report["live_provider_requests"]==0 and report["real_acquisition_available"] is False
    subprocess.run(["sha256sum","-c","SHA256SUMS"],cwd=output,check=True,capture_output=True)
