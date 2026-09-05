#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from moneybot.services.decision_log_export import audit_decision_log


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = audit_decision_log(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "status",
                    "total_physical_records",
                    "duplicate_physical_records",
                    "same_immutable_identity_with_different_payload",
                )
            },
            sort_keys=True,
        )
    )
    return 2 if report["integrity_failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
