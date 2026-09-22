"""Resolve pinned supplemental registration evidence without recursive guessing."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


SOURCES = {
    "prior_registration": {
        "root": "prior_root",
        "relative_path": "reports/registration.json",
        "basename": "registration.json",
        "copy_name": "prior-registration.json",
    },
    "identity_evidence": {
        "root": "identity_root",
        "relative_path": "alpha_atlas_v4_historical_coverage_diagnostics.json",
        "basename": "alpha_atlas_v4_historical_coverage_diagnostics.json",
        "copy_name": "identity-evidence.json",
    },
    "cost_policy_evidence": {
        "root": "track_root",
        "relative_path": "track_b/runs/34689216730-1/challenger_suite/portfolio_path/execution_policy.json",
        "basename": "execution_policy.json",
        "copy_name": "other-scope-execution-policy.json",
    },
}


class ResolutionError(RuntimeError):
    pass


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inventory(root: Path, basename: str) -> list[dict[str, Any]]:
    if not root.is_dir():
        return []
    return [
        {"artifact_relative_path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "sha256_computed": _sha(path)}
        for path in sorted(root.rglob(basename)) if path.is_file()
    ]


def _validate_json(role: str, path: Path) -> None:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ResolutionError(f"{role.upper()}_INVALID_JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ResolutionError(f"{role.upper()}_INVALID_SCHEMA: top level must be an object")
    if role == "identity_evidence":
        schema = value.get("schema_version")
        if schema not in {"alpha-atlas-v4-historical-coverage-diagnostics.v1", "alpha-atlas-v4-historical-coverage-diagnostics.v2"}:
            raise ResolutionError(f"IDENTITY_EVIDENCE_SCHEMA_MISMATCH: {schema!r}")
        if value.get("research_only") is not True or value.get("ready_for_live_routing") is not False:
            raise ResolutionError("IDENTITY_EVIDENCE_SAFETY_SCHEMA_MISMATCH")
    elif role == "prior_registration" and "registration_sha256" not in value:
        raise ResolutionError("PRIOR_REGISTRATION_SCHEMA_MISMATCH")
    elif role == "cost_policy_evidence" and not any(key in value for key in ("policy_version", "transaction_cost_bps", "slippage_bps")):
        raise ResolutionError("COST_POLICY_EVIDENCE_SCHEMA_MISMATCH")


def resolve(*, prior_root: Path, identity_root: Path, track_root: Path, output_dir: Path,
            validated_metadata: Path, expected_identity_hash: str | None = None,
            expected_cost_hash: str | None = None,
            expected_cost_hash_provenance: str | None = None,
            expected_cost_bytes: int | None = None) -> dict[str, Any]:
    metadata = json.loads(validated_metadata.read_text())
    output_dir.mkdir(parents=True, exist_ok=True)
    roots = {"prior_root": prior_root, "identity_root": identity_root, "track_root": track_root}
    report: dict[str, Any] = {"status": "FAILED", "reason_codes": [], "sources": {}}
    try:
        for role, spec in SOURCES.items():
            root = roots[spec["root"]]
            candidates = _inventory(root, spec["basename"])
            intended = root / spec["relative_path"]
            entry = {"selection_basis": "PINNED_ARTIFACT_RELATIVE_PATH", "artifact_relative_path": spec["relative_path"],
                     "basename_candidate_inventory": candidates}
            report["sources"][role] = entry
            if not intended.is_file():
                raise ResolutionError(f"{role.upper()}_INTENDED_PATH_MISSING: {spec['relative_path']}; candidates={candidates}")
            _validate_json(role, intended)
            computed = _sha(intended)
            if role == "identity_evidence" and expected_identity_hash and computed != expected_identity_hash:
                raise ResolutionError(f"IDENTITY_EVIDENCE_EXPECTED_HASH_MISMATCH: expected={expected_identity_hash} computed={computed}")
            if role == "cost_policy_evidence" and expected_cost_hash and computed != expected_cost_hash:
                raise ResolutionError(f"COST_POLICY_EVIDENCE_EXPECTED_HASH_MISMATCH: expected={expected_cost_hash} computed={computed}")
            if role == "cost_policy_evidence" and expected_cost_bytes is not None and intended.stat().st_size != expected_cost_bytes:
                raise ResolutionError(f"COST_POLICY_EVIDENCE_EXPECTED_SIZE_MISMATCH: expected={expected_cost_bytes} computed={intended.stat().st_size}")
            origin = "identity" if role == "identity_evidence" else ("prior" if role == "prior_registration" else "track")
            if origin not in metadata:
                raise ResolutionError(f"{role.upper()}_PINNED_METADATA_MISSING")
            destination = output_dir / spec["copy_name"]
            shutil.copyfile(intended, destination)
            if destination.read_bytes() != intended.read_bytes():
                raise ResolutionError(f"{role.upper()}_BYTE_PRESERVATION_FAILED")
            entry.update(metadata[origin])
            expected_hash = expected_identity_hash if role == "identity_evidence" else (expected_cost_hash if role == "cost_policy_evidence" else None)
            entry.update({"consumed_copy_path": str(destination), "bytes": intended.stat().st_size,
                          "sha256_computed": computed, "expected_report_sha256": expected_hash,
                          "expected_hash_status": ("VALIDATED_AGAINST_RECORDED_HOSTED_HASH" if role == "cost_policy_evidence" and expected_hash
                                                   else "VALIDATED" if expected_hash else "NOT_INDEPENDENTLY_AVAILABLE"),
                          "expected_hash_provenance": expected_cost_hash_provenance if role == "cost_policy_evidence" else None,
                          "expected_bytes": expected_cost_bytes if role == "cost_policy_evidence" else None})
        report["status"] = "RESOLVED"
        environment_names = {"prior_registration": "PRIOR_REGISTRATION", "identity_evidence": "IDENTITY_EVIDENCE",
                             "cost_policy_evidence": "COST_POLICY_EVIDENCE"}
        (output_dir / "supplemental-paths.env").write_text("\n".join(
            f"{environment_names[role]}={entry['consumed_copy_path']}" for role, entry in report["sources"].items()
        ) + "\n")
    except (ResolutionError, KeyError, json.JSONDecodeError) as exc:
        report["reason_codes"].append(str(exc).split(":", 1)[0])
        report["error"] = str(exc)
        raise
    finally:
        (output_dir / "supplemental-resolution.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        lines = ["# Supplemental evidence resolution", "", f"- Status: `{report['status']}`"]
        if report.get("error"): lines += [f"- Error: `{report['error']}`"]
        for role, entry in report["sources"].items():
            lines += ["", f"## {role}", f"- Intended path: `{entry['artifact_relative_path']}`", f"- Selection: `{entry['selection_basis']}`", "- Basename inventory:"]
            lines += [f"  - `{x['artifact_relative_path']}` — {x['bytes']} bytes — `{x['sha256_computed']}`" for x in entry["basename_candidate_inventory"]] or ["  - none"]
        (output_dir / "supplemental-resolution.md").write_text("\n".join(lines) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prior-root", type=Path, required=True); parser.add_argument("--identity-root", type=Path, required=True)
    parser.add_argument("--track-root", type=Path, required=True); parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--validated-metadata", type=Path, required=True); parser.add_argument("--expected-identity-hash")
    parser.add_argument("--expected-cost-hash"); parser.add_argument("--expected-cost-hash-provenance")
    parser.add_argument("--expected-cost-bytes", type=int)
    args = parser.parse_args()
    try:
        resolve(**vars(args))
    except Exception as exc:
        print(f"supplemental evidence resolution failed: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
