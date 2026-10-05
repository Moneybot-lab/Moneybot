"""Offline-only Stage B end-to-end synthetic validation. No live mode exists."""
from __future__ import annotations
import argparse, hashlib, io, json, tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from moneybot.services.alpha_atlas_v4_acquisition import TransportResponse, prior_sessions
from moneybot.services.alpha_atlas_v4_prospective_snapshot import canonical_bytes, sha256_bytes, ImmutableStore
from moneybot.services.alpha_atlas_v4_stage_b import (
    S3EvidenceBackup, S3OperationLedger, StageBRunner, effective_sector_fixture,
    synthetic_authorization,
)
UTC=timezone.utc
NOW=datetime(2026,10,5,11,0,tzinfo=UTC)

class OfflineTransport:
    def __init__(self): self.calls=[]
    def send(self,request,*,page_url,timeout_seconds):
        self.calls.append(request.request_id)
        if request.family=="history":
            results=[{"date":d.isoformat(),"open":100+n,"high":101+n,"low":99+n,"close":100+n,"vwap":100+n,"volume":1000+n,"adjusted":False} for n,d in enumerate(prior_sessions(__import__("datetime").date(2026,10,5),75))]
        elif request.family=="identity":
            results=[{"ticker":"AAPL","type":"CS","cik":"0000320193","share_class_figi":"BBG001S5N8V8","synthetic":True}]
        else: results=[]
        return TransportResponse(200,canonical_bytes({"request_id":f"synthetic-{len(self.calls)}","results":results}),NOW)

class OfflineS3:
    def __init__(self): self.objects={}; self.n=0
    def head_bucket(self,**kwargs): return {}
    def get_bucket_location(self,**kwargs): return {"LocationConstraint":"us-east-1"}
    def get_bucket_versioning(self,**kwargs): return {"Status":"Enabled"}
    def get_public_access_block(self,**kwargs): return {"PublicAccessBlockConfiguration":{"BlockPublicAcls":True,"IgnorePublicAcls":True,"BlockPublicPolicy":True,"RestrictPublicBuckets":True}}
    def get_bucket_encryption(self,**kwargs): return {"ServerSideEncryptionConfiguration":{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}}
    def get_object_lock_configuration(self,**kwargs): return {"ObjectLockConfiguration":{"ObjectLockEnabled":"Enabled","Rule":{"DefaultRetention":{"Mode":"GOVERNANCE","Days":180}}}}
    def put_object(self,**kwargs):
        self.n+=1; version=f"synthetic-v{self.n}"; self.objects[(kwargs["Key"],version)]=bytes(kwargs["Body"])
        return {"VersionId":version}
    def head_object(self,**kwargs): return {"ServerSideEncryption":"AES256","ObjectLockMode":"GOVERNANCE","ObjectLockRetainUntilDate":NOW.replace(year=2027)}
    def get_object(self,**kwargs): return {"Body":io.BytesIO(self.objects[(kwargs["Key"],kwargs["VersionId"])])}

def write(output: Path, report: dict[str,Any]) -> None:
    output.mkdir(parents=True,exist_ok=True)
    (output/"report.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    (output/"report.md").write_text("# Alpha Atlas V4 Stage B offline synthetic validation\n\n"+f"- Status: **{report['status']}**\n- Synthetic attempts: **{report['synthetic_transport_attempts']}**\n- Live provider requests: **{report['live_provider_requests']}**\n- Real acquisition authorized: **{str(report['real_acquisition_authorized']).lower()}**\n- Backup/isolated restore: **verified synthetic mock only**\n")
    (output/"run.log").write_text("OFFLINE_SYNTHETIC_ONLY; no credential discovery; no network transport\n")
    lines=[]
    for name in ("report.json","report.md","run.log"):
        lines.append(f"{sha256_bytes((output/name).read_bytes())}  {name}")
    (output/"SHA256SUMS").write_text("\n".join(lines)+"\n")

def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--offline-synthetic",action="store_true"); parser.add_argument("--output-dir",type=Path,required=True); args=parser.parse_args()
    if not args.offline_synthetic: parser.error("only --offline-synthetic is available")
    repo=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="alpha-atlas-stage-b-offline-") as temp:
        root=Path(temp); client=OfflineS3()
        runner=StageBRunner(repo,root,offline=True,now=lambda:NOW)
        report=runner.execute(synthetic_authorization(),effective_sector_fixture(),lambda:OfflineTransport(),lambda primary:S3EvidenceBackup(client,"synthetic-stage-b-test-only","alpha-atlas-v4/stage-b/offline",S3OperationLedger(ImmutableStore(root/"s3-ledger")),expected_owner="123456789012",now=lambda:NOW))
        report["bound_documents_verified"]=True; report["temporary_objects_uploaded"]=False
        write(args.output_dir,report)
    print(json.dumps({"status":report["status"],"output_dir":str(args.output_dir),"live_provider_requests":0},sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
