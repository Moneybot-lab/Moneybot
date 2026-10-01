#!/usr/bin/env python3
"""Run synthetic-only V4 prospective capture validation (no providers/data)."""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from datetime import date
from pathlib import Path

from moneybot.services.alpha_atlas_v4_prospective_snapshot import (
    CONTRACT_SHA256, SCHEMA_SHA256, ImmutableStore, PilotBudget, canonical_bytes,
    session_times, sha256_bytes,
    verify_bound_documents,
)
from moneybot.services.runtime_paths import prospective_snapshot_root


def write_artifact_checksums(artifact_root: Path, members: tuple[str, ...] = ("report.json",)) -> Path:
    """Write portable checksums whose member paths are artifact-root relative."""
    root = artifact_root.resolve()
    lines = []
    for member in members:
        path = (root / member).resolve()
        if path.parent != root or not path.is_file():
            raise ValueError(f"invalid artifact member: {member}")
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {member}\n")
    output = root / "SHA256SUMS"
    output.write_text("".join(lines), encoding="utf-8")
    return output


def run_synthetic(root: Path) -> dict:
    store = ImmutableStore(prospective_snapshot_root(test_root=root))
    payload = canonical_bytes({"synthetic": True, "collection": False, "provider_requests": 0})
    with store.lock():
        written = store.publish("synthetic/evidence.json", payload)
        budget = PilotBudget(store).consume(sessions=1, assignments=1, storage_bytes=len(payload), provider_requests=0)
    timing = session_times(date(2026, 7, 6))
    return {
        "schema_version": "alpha-atlas-v4-prospective-synthetic-validation.v1",
        "status": "PASS",
        "mode": "SYNTHETIC_ONLY",
        "real_collection_available": False,
        "provider_requests": 0,
        "contract_sha256": CONTRACT_SHA256,
        "schema_sha256": SCHEMA_SHA256,
        "deadline_at": timing.deadline.isoformat(),
        "evidence": written,
        "budget": budget,
        "payload_sha256": sha256_bytes(payload),
        "bound_documents": verify_bound_documents(Path(__file__).resolve().parents[1]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--synthetic", action="store_true", required=True,
                        help="required; real collection has no CLI mode")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="aav4-synthetic-test-storage-") as tmp:
        report = run_synthetic(Path(tmp))
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output: args.output.write_text(rendered)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
