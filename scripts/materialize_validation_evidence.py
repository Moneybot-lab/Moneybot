#!/usr/bin/env python3
"""Build an independent historical validation dataset; never activate a model."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moneybot.services.historical_evidence import build_evidence
from moneybot.services.outcome_history_provider import MassivePreferredHistoryDownload
from moneybot.services.runtime_paths import decision_events_log_path, historical_validation_evidence_path
from moneybot.services.ops_runtime import peak_rss_mib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(decision_events_log_path()))
    parser.add_argument("--output", default=str(historical_validation_evidence_path()))
    args = parser.parse_args()
    manifest = build_evidence(args.input, args.output, download=MassivePreferredHistoryDownload())
    print(json.dumps({"complete": manifest["complete"], "counts": manifest["counts"],
                      "exclusions": manifest["exclusions"], "peak_rss_mib": peak_rss_mib()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
