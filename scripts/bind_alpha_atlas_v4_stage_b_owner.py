"""Offline-only owner-ID binder for the Stage B runtime configuration."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
from moneybot.services.alpha_atlas_v4_prospective_snapshot import canonical_bytes, sha256_bytes
from moneybot.services.alpha_atlas_v4_stage_b import load_runtime_config

def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument('--config',type=Path,required=True); parser.add_argument('--owner-id',required=True); parser.add_argument('--output',type=Path,required=True); args=parser.parse_args()
    if len(args.owner_id)!=12 or not args.owner_id.isdigit(): parser.error('--owner-id must be the verified 12-digit AWS account owner ID')
    value=load_runtime_config(args.config,require_owner=False); value.pop('content_sha256',None)
    value['s3_expected_owner']=args.owner_id; value['status']='OWNER_BOUND_PENDING_AUTHORIZATION_AND_RUNTIME_VERIFICATION'
    value['content_sha256']=sha256_bytes(canonical_bytes(value)); args.output.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
    load_runtime_config(args.output,require_owner=True)
    print(json.dumps({'path':str(args.output),'content_sha256':value['content_sha256'],'file_sha256':hashlib.sha256(args.output.read_bytes()).hexdigest()},sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
