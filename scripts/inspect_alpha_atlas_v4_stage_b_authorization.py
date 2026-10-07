"""Network-free authorization hash/state inspection; never approves or executes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from moneybot.services.alpha_atlas_v4_prospective_snapshot import CaptureError
from moneybot.services.alpha_atlas_v4_stage_b import authorization_hashes


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--authorization",type=Path,required=True)
    args=parser.parse_args()
    raw=args.authorization.read_bytes()
    value=json.loads(raw)
    if not isinstance(value,dict): raise CaptureError("AUTHORIZATION_NOT_OBJECT")
    hashes=authorization_hashes(value,file_bytes=raw)
    result={"path":str(args.authorization),"status":value.get("status"),
            "execution_gate_usable":value.get("execution_gate_usable"),**hashes,
            "inspection_only":True,"approved_or_modified":False}
    print(json.dumps(result,sort_keys=True))
    return 0 if hashes["internal_content_valid"] else 2


if __name__=="__main__": raise SystemExit(main())
