"""Manual-only observer CLI. No default execution and no Stage B/provider entry point."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from moneybot.services.alpha_atlas_v4_prospective_snapshot import CaptureError
from moneybot.services.alpha_atlas_v4_stage_b_monitor import observe_passive, run_synthetic


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--offline-synthetic', action='store_true')
    modes.add_argument('--passive-observation', action='store_true')
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--authorization', type=Path)
    parser.add_argument('--authorization-sha256')
    args = parser.parse_args()
    try:
        repo = Path(__file__).resolve().parents[1]
        if args.offline_synthetic:
            if args.authorization or args.authorization_sha256:
                raise CaptureError('SYNTHETIC_AUTHORIZATION_FORBIDDEN')
            result = run_synthetic(repo, args.output_dir)
        else:
            if not args.authorization or not args.authorization_sha256:
                raise CaptureError('MONITOR_AUTHORIZATION_REQUIRED')
            result = observe_passive(repo, args.authorization, args.authorization_sha256, args.output_dir)
        print(json.dumps({'status': result['status'], 'mode': result['mode'],
                          'observation_count': result['observation_count'],
                          'overall_stage_b': 'OPEN'}, sort_keys=True))
        return 0 if result['status'] == 'COMPLETE_WITH_UNKNOWNS' else 2
    except (CaptureError, KeyboardInterrupt):
        # Codes are fixed/sanitized; do not echo args, paths, connection URLs or exceptions.
        print(json.dumps({'status': 'BLOCKED_OR_INCOMPLETE', 'overall_stage_b': 'OPEN'}))
        return 2
    except Exception:
        print(json.dumps({'status': 'BLOCKED_OR_INCOMPLETE', 'overall_stage_b': 'OPEN'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
