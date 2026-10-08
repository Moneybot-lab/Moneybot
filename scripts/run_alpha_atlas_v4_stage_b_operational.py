"""Non-scheduled Stage B runner; an expired or unapproved fixture fails before clients."""
from __future__ import annotations
import argparse, json, os
from datetime import date
from pathlib import Path
from typing import Any

from moneybot.services.alpha_atlas_v4_prospective_snapshot import CaptureError, ImmutableStore
from moneybot.services.alpha_atlas_v4_stage_b import (
    MassiveStageBTransport, REQUIRED_ROOT, RuntimeResourceGuard, S3EvidenceBackup,
    AWS_ACCESS_KEY_ENV, AWS_SECRET_KEY_ENV, MASSIVE_KEY_ENV, S3OperationLedger,
    StageBPhaseFailure, StageBRunner, create_boto3_s3_client, load_fixture,
    load_runtime_config, validate_explicit_credentials,
)

def _load(path: Path) -> dict[str,Any]:
    value=json.loads(path.read_text())
    if not isinstance(value,dict): raise CaptureError("CONFIG_NOT_OBJECT",str(path))
    return value

def _aws_credentials(environ: Any) -> tuple[str,str]:
    """Compatibility helper restricted to the two dedicated Stage B names."""
    return str(environ.get(AWS_ACCESS_KEY_ENV,"")),str(environ.get(AWS_SECRET_KEY_ENV,""))

def main() -> int:
    parser=argparse.ArgumentParser(description="Stage B fixed-session operational runner (no schedule)")
    parser.add_argument("--authorization",type=Path,required=True); parser.add_argument("--approved-authorization-sha256",required=True)
    sector=parser.add_mutually_exclusive_group(required=True)
    sector.add_argument("--sector-context",type=Path,dest="sector_context")
    sector.add_argument("--sector-evidence",type=Path,dest="sector_context",help="legacy dated-mapping input")
    parser.add_argument("--config",type=Path,required=True)
    parser.add_argument("--fixture",type=Path,required=True)
    parser.add_argument("--fixture-file-sha256",required=True); parser.add_argument("--fixture-content-sha256",required=True)
    parser.add_argument("--recovery-authorization",type=Path)
    parser.add_argument("--recovery-authorization-sha256")
    parser.add_argument("--session",type=date.fromisoformat,required=True)
    parser.add_argument("--output",type=Path,required=True); args=parser.parse_args()
    if args.output.exists(): raise CaptureError("RESULT_OUTPUT_EXISTS",str(args.output))
    try:
        authorization=_load(args.authorization); sector=_load(args.sector_context); config=load_runtime_config(args.config,require_owner=True)
        root=Path(config["persistent_root"]); guard=RuntimeResourceGuard(root,max_rss_kib=int(config["max_rss_kib"]))
        repo=Path(__file__).resolve().parents[1]
        try: fixture_name=args.fixture.resolve().relative_to((repo/"docs/reports").resolve()).as_posix()
        except ValueError as exc: raise CaptureError("FIXTURE_PATH_OUTSIDE_REPORTS") from exc
        runner=StageBRunner(repo,root,offline=False,approved_authorization_sha256=args.approved_authorization_sha256,operational_config_sha256=str(config["content_sha256"]),fixture_name=fixture_name,fixture_file_sha256=args.fixture_file_sha256,fixture_content_sha256=args.fixture_content_sha256,session=args.session,runtime_guard=guard)
        # These closures discover credentials/create clients only after runner.preflight succeeds.
        def credential_loader() -> dict[str,str]:
            return validate_explicit_credentials(os.environ)
        def backup_factory(primary: ImmutableStore,credentials: dict[str,str]) -> S3EvidenceBackup:
            access_key=credentials[AWS_ACCESS_KEY_ENV]; secret_key=credentials[AWS_SECRET_KEY_ENV]
            client=create_boto3_s3_client(access_key_id=access_key,secret_access_key=secret_key)
            ledger=S3OperationLedger(ImmutableStore(root/"s3-operation-ledger"))
            if ledger.reconcile_uncertain(): raise CaptureError("S3_UNCERTAIN_OPERATIONS_REQUIRE_REVIEW")
            return S3EvidenceBackup(client,str(config["s3_bucket"]),str(config["s3_prefix"]),ledger,expected_owner=str(config["s3_expected_owner"]))
        def transport_factory(credentials: dict[str,str]) -> MassiveStageBTransport:
            return MassiveStageBTransport(load_fixture(repo,fixture_name=fixture_name,fixture_content_sha256=args.fixture_content_sha256,session=args.session),credentials[MASSIVE_KEY_ENV])
        if bool(args.recovery_authorization) != bool(args.recovery_authorization_sha256):
            raise CaptureError("RECOVERY_ARGUMENTS_INCOMPLETE")
        if args.recovery_authorization:
            recovery=_load(args.recovery_authorization)
            result=runner.execute_continuation(authorization,recovery,args.recovery_authorization_sha256,
                                               sector,transport_factory,backup_factory,credential_loader)
        else:
            result=runner.execute(authorization,sector,transport_factory,backup_factory,credential_loader)
        exit_code=0
    except StageBPhaseFailure as exc:
        result=exc.report; exit_code=2
    except Exception as exc:
        error_code=exc.code if isinstance(exc,CaptureError) else type(exc).__name__
        result={"status":"FAIL","error_code":error_code,"detail":error_code,
                "last_completed_phase":None,"failing_phase":"LOCAL_PREFLIGHT_OR_CREDENTIAL_VALIDATION",
                "execution_claim_exists":False,"acquisition_started":False,"stage_b_completed":False,"stage_b_executed":False}
        exit_code=2
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open("x") as handle: handle.write(json.dumps(result,indent=2,sort_keys=True)+"\n")
    return exit_code
if __name__=="__main__": raise SystemExit(main())
