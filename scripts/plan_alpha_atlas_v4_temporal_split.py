#!/usr/bin/env python3
"""Write a frozen V4 temporal split plan and sanitized diagnostics."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moneybot.services.alpha_atlas_v4_temporal_split import (
    TEMPORAL_SPLIT_DIAGNOSTIC_SCHEMA,
    TEMPORAL_SPLIT_CONTRACT_VERSION,
    TemporalSplitDataError,
    file_sha256,
    plan_v4_temporal_split,
)


def _rows(path: Path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--train-ratio", type=float, default=0.8)
    parser.add_argument("--embargo-sessions", type=int, default=1)
    parser.add_argument("--min-train-dates", type=int, default=5)
    parser.add_argument("--min-test-dates", type=int, default=2)
    parser.add_argument("--min-observations", type=int, default=200)
    args = parser.parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    rows = _rows(input_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = plan_v4_temporal_split(
            rows,
            input_sha256=file_sha256(input_path),
            train_ratio=args.train_ratio,
            embargo_sessions=max(1, args.embargo_sessions),
            min_train_dates=max(1, args.min_train_dates),
            min_test_dates=max(1, args.min_test_dates),
            min_observations=max(1, args.min_observations),
        )
    except TemporalSplitDataError as exc:
        (output_dir / "challenger_split_diagnostics.json").write_text(
            json.dumps(
                {
                    "schema_version": TEMPORAL_SPLIT_DIAGNOSTIC_SCHEMA,
                    "contract_version": TEMPORAL_SPLIT_CONTRACT_VERSION,
                    "status": "INVALID_DATA",
                    "failure_reason": str(exc),
                    "research_only": True,
                    "automatic_promotion": False,
                    "ready_for_live_routing": False,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        raise
    diagnostics = dict(result.diagnostics)
    if result.plan["status"] == "FEASIBLE":
        by_id = {row["canonical_observation_id"]: row for row in rows}
        for name, key in (
            ("challenger_train.jsonl", "train_canonical_observation_ids"),
            ("challenger_test.jsonl", "test_canonical_observation_ids"),
        ):
            content = "".join(
                json.dumps(by_id[identifier], sort_keys=True) + "\n"
                for identifier in result.plan[key]
            )
            (output_dir / name).write_text(content, encoding="utf-8")
            diagnostics[f"{name}_sha256"] = hashlib.sha256(
                content.encode("utf-8")
            ).hexdigest()
    (output_dir / "challenger_split_diagnostics.json").write_text(
        json.dumps(diagnostics, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "challenger_split_plan.json").write_text(
        json.dumps(result.plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result.plan, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
