"""Non-scheduled Stage B runner; an expired or unapproved fixture fails before clients."""
from __future__ import annotations
import argparse, json, os
from pathlib import Path
from typing import Any

from moneybot.services.alpha_atlas_v4_prospective_snapshot import CaptureError, ImmutableStore, canonical_bytes, sha256_bytes
from moneybot.services.alpha_atlas_v4_stage_b import (
    MassiveStageBTransport, REQUIRED_ROOT, RuntimeResourceGuard, S3EvidenceBackup,
    S3OperationLedger, StageBRunner, create_boto3_s3_client, load_fixture,
)

def _load(path: Path) -> dict[str,Any]:
    value=json.loads(path.read_text())
    if not isinstance(value,dict): raise CaptureError("CONFIG_NOT_OBJECT",str(path))
    return value

def _validate_config(config: dict[str,Any]) -> None:
    required={"persistent_root","s3_bucket","s3_prefix","s3_expected_owner","s3_region","max_rss_kib"}
    if set(config)<required or config["persistent_root"]!=REQUIRED_ROOT or config["s3_region"]!="us-east-1": raise CaptureError("OPERATIONAL_CONFIG_INVALID")
    if not str(config["s3_bucket"]).strip() or not str(config["s3_prefix"]).strip(): raise CaptureError("OPERATIONAL_CONFIG_INVALID")
    owner=str(config["s3_expected_owner"])
    if len(owner)!=12 or not owner.isdigit(): raise CaptureError("OPERATIONAL_CONFIG_INVALID")

def main() -> int:
    parser=argparse.ArgumentParser(description="Stage B fixed-session operational runner (no schedule)")
    parser.add_argument("--authorization",type=Path,required=True); parser.add_argument("--approved-authorization-sha256",required=True)
    parser.add_argument("--sector-evidence",type=Path,required=True); parser.add_argument("--config",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True); args=parser.parse_args()
    try:
        authorization=_load(args.authorization); sector=_load(args.sector_evidence); config=_load(args.config); _validate_config(config)
        root=Path(config["persistent_root"]); guard=RuntimeResourceGuard(root,max_rss_kib=int(config["max_rss_kib"]))
        repo=Path(__file__).resolve().parents[1]
        runner=StageBRunner(repo,root,offline=False,approved_authorization_sha256=args.approved_authorization_sha256,operational_config_sha256=sha256_bytes(canonical_bytes(config)),runtime_guard=guard)
        # These closures discover credentials/create clients only after runner.preflight succeeds.
        def backup_factory(primary: ImmutableStore) -> S3EvidenceBackup:
            client=create_boto3_s3_client(access_key_id=os.environ.get("ALPHA_ATLAS_V4_AWS_ACCESS_KEY_ID",""),secret_access_key=os.environ.get("ALPHA_ATLAS_V4_AWS_SECRET_ACCESS_KEY",""),session_token=os.environ.get("ALPHA_ATLAS_V4_AWS_SESSION_TOKEN"))
            ledger=S3OperationLedger(ImmutableStore(root/"s3-operation-ledger"))
            if ledger.reconcile_uncertain(): raise CaptureError("S3_UNCERTAIN_OPERATIONS_REQUIRE_REVIEW")
            return S3EvidenceBackup(client,str(config["s3_bucket"]),str(config["s3_prefix"]),ledger,expected_owner=str(config["s3_expected_owner"]))
        def transport_factory() -> MassiveStageBTransport:
            return MassiveStageBTransport(load_fixture(repo),os.environ.get("ALPHA_ATLAS_V4_MASSIVE_API_KEY",""))
        result=runner.execute(authorization,sector,transport_factory,backup_factory)
        exit_code=0
    except Exception as exc:
        result={"status":"FAIL","error_code":exc.code if isinstance(exc,CaptureError) else type(exc).__name__,"detail":str(exc),"stage_b_executed":False}
        exit_code=2
    args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    return exit_code
if __name__=="__main__": raise SystemExit(main())
