#!/usr/bin/env python3
"""Safely inspect and render the two reports in one pinned Actions artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile

REPOSITORY = "Moneybot-lab/Moneybot"
WORKFLOW = "Alpha Atlas V4 Historical Coverage Diagnostics"
RUN_ID = 35178703375
RUN_ATTEMPT = 1
HEAD_SHA = "0dc495973f7b2bb2892c6e78ae0d933d475fe80d"
CONCLUSION = "success"
ARTIFACT_ID = 10479149714
ARTIFACT_NAME = "alpha-atlas-v4-historical-coverage-diagnostics-35178703375-1"
ARTIFACT_DIGEST = "sha256:791ae2f975b2d91c69a3e6ba05572ea2ab2e5d61db80765526c1a7753d09c8f5"
REPORT_NAMES = (
    "alpha_atlas_v4_historical_coverage_diagnostics.json",
    "alpha_atlas_v4_historical_coverage_diagnostics.md",
)
MAX_MEMBERS = 256
MAX_MEMBER_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
# GitHub rejects an individual step summary over 1 MiB. Leave ample overhead.
MAX_SUMMARY_BYTES = 900 * 1024


def _fail(code: str) -> None:
    raise ValueError(code)


def validate_source(run: dict, artifact_listing: dict) -> dict:
    expected_run = {
        "name": WORKFLOW, "run_attempt": RUN_ATTEMPT, "head_sha": HEAD_SHA,
        "conclusion": CONCLUSION,
    }
    if run.get("id") != RUN_ID:
        _fail("SOURCE_RUN_ID_MISMATCH")
    repository = (run.get("repository") or {}).get("full_name")
    if repository != REPOSITORY:
        _fail("SOURCE_REPOSITORY_MISMATCH")
    for key, expected in expected_run.items():
        if run.get(key) != expected:
            _fail(f"SOURCE_RUN_{key.upper()}_MISMATCH")

    artifacts = artifact_listing.get("artifacts")
    if not isinstance(artifacts, list):
        _fail("ARTIFACT_METADATA_MALFORMED")
    matches = [item for item in artifacts if item.get("id") == ARTIFACT_ID]
    if len(matches) != 1:
        _fail("ARTIFACT_ID_MISSING_OR_AMBIGUOUS")
    artifact = matches[0]
    if artifact.get("name") != ARTIFACT_NAME:
        _fail("ARTIFACT_NAME_MISMATCH")
    if artifact.get("expired") is not False:
        _fail("ARTIFACT_EXPIRED")
    if artifact.get("digest") != ARTIFACT_DIGEST:
        _fail("ARTIFACT_METADATA_DIGEST_MISMATCH")
    return artifact


def verify_archive_digest(path: Path) -> str:
    """Compare the downloaded archive bytes with GitHub's upload-artifact digest."""
    actual = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != ARTIFACT_DIGEST:
        _fail("DOWNLOADED_ARCHIVE_DIGEST_MISMATCH")
    return actual


def _safe_member(info: zipfile.ZipInfo) -> None:
    name = info.filename
    if not name or "\\" in name or any(ord(char) < 32 or ord(char) == 127 for char in name):
        _fail("UNSAFE_ARCHIVE_MEMBER_PATH")
    path = PurePosixPath(name)
    if (path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts)
            or (path.parts and path.parts[0].endswith(":"))):
        _fail("UNSAFE_ARCHIVE_MEMBER_PATH")
    if info.flag_bits & 0x1:
        _fail("ENCRYPTED_ARCHIVE_MEMBER")
    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
        _fail("UNSUPPORTED_ARCHIVE_COMPRESSION")
    mode = info.external_attr >> 16
    kind = stat.S_IFMT(mode)
    if kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
        _fail("UNSUPPORTED_ARCHIVE_MEMBER_TYPE")
    if info.file_size > MAX_MEMBER_BYTES:
        _fail("ARCHIVE_MEMBER_SIZE_LIMIT_EXCEEDED")


def inspect_archive(path: Path) -> tuple[list[dict], dict[str, bytes]]:
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("INVALID_ZIP_ARCHIVE") from exc
    with archive:
        members = archive.infolist()
        if len(members) > MAX_MEMBERS:
            _fail("ARCHIVE_MEMBER_COUNT_LIMIT_EXCEEDED")
        if sum(info.file_size for info in members) > MAX_TOTAL_BYTES:
            _fail("ARCHIVE_TOTAL_SIZE_LIMIT_EXCEEDED")
        seen: set[str] = set()
        inventory = []
        for info in members:
            _safe_member(info)
            normalized = str(PurePosixPath(info.filename)).casefold()
            if normalized in seen:
                _fail("DUPLICATE_OR_AMBIGUOUS_ARCHIVE_MEMBER")
            seen.add(normalized)
            inventory.append({"name": info.filename, "uncompressed_bytes": info.file_size,
                              "compressed_bytes": info.compress_size,
                              "kind": "directory" if info.is_dir() else "file"})
        reports = {}
        for name in REPORT_NAMES:
            exact = [info for info in members if info.filename == name and not info.is_dir()]
            if len(exact) != 1:
                _fail(f"REPORT_MISSING_OR_AMBIGUOUS:{name}")
            reports[name] = archive.read(exact[0])
    return inventory, reports


def _literal_fence(data: bytes, language: str, title: str) -> bytes:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("REPORT_NOT_UTF8") from exc
    run = maximum = 0
    for char in text:
        run = run + 1 if char == "`" else 0
        maximum = max(maximum, run)
    fence = "`" * max(3, maximum + 1)
    rendered = f"## {title}\n\n{fence}{language}\n{text}"
    if not text.endswith("\n"):
        rendered += "\n"
    rendered += f"{fence}\n"
    result = rendered.encode("utf-8")
    if len(result) > MAX_SUMMARY_BYTES:
        _fail(f"STEP_SUMMARY_SIZE_LIMIT_EXCEEDED:{title}")
    return result


def build_outputs(run: dict, artifact: dict, archive_path: Path, output_dir: Path) -> dict:
    digest = verify_archive_digest(archive_path)
    inventory, reports = inspect_archive(archive_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name, data in reports.items():
        (output_dir / name).write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()

    inventory_lines = [
        "# Historical coverage evidence extraction", "",
        "Evidence extraction succeeded. This does **not** certify complete historical coverage.", "",
        f"- Repository: `{REPOSITORY}`", f"- Source workflow: `{WORKFLOW}`",
        f"- Run / attempt: `{RUN_ID}` / `{RUN_ATTEMPT}`", f"- Head SHA: `{HEAD_SHA}`",
        f"- Artifact ID / name: `{ARTIFACT_ID}` / `{ARTIFACT_NAME}`",
        f"- GitHub/downloaded archive digest: `{digest}`", "- Metadata and archive validation: `PASSED`", "",
        "## Archive inventory", "", "| Member | Kind | Uncompressed bytes | Compressed bytes |", "|---|---:|---:|---:|",
    ]
    for item in inventory:
        safe_name = item["name"].replace("|", "\\|")
        inventory_lines.append(f"| `{safe_name}` | {item['kind']} | {item['uncompressed_bytes']} | {item['compressed_bytes']} |")
    inventory_lines += ["", "## Exact report hashes", ""]
    inventory_lines += [f"- `{name}`: `sha256:{hashes[name]}`" for name in REPORT_NAMES]
    inventory_lines += ["", "Nested archives, if listed above, were not opened. No archive content was executed. This inspection does not prove the archive malware-free.", ""]
    inventory_summary = "\n".join(inventory_lines).encode()
    if len(inventory_summary) > MAX_SUMMARY_BYTES:
        _fail("STEP_SUMMARY_SIZE_LIMIT_EXCEEDED:inventory")
    (output_dir / "summary_inventory.md").write_bytes(inventory_summary)
    (output_dir / "summary_report_json.md").write_bytes(
        _literal_fence(reports[REPORT_NAMES[0]], "json", "Report 1 of 2 — diagnostic JSON (literal text)"))
    (output_dir / "summary_report_markdown.md").write_bytes(
        _literal_fence(reports[REPORT_NAMES[1]], "text", "Report 2 of 2 — diagnostic Markdown (literal text)"))
    manifest = {"source": {"repository": REPOSITORY, "workflow": WORKFLOW, "run_id": RUN_ID,
                            "run_attempt": RUN_ATTEMPT, "head_sha": HEAD_SHA, "conclusion": CONCLUSION,
                            "artifact_id": ARTIFACT_ID, "artifact_name": ARTIFACT_NAME,
                            "artifact_digest": digest}, "validation": "PASSED",
                "inventory": inventory, "report_sha256": hashes}
    (output_dir / "inspection_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main() -> int:
    global RUN_ID, RUN_ATTEMPT, HEAD_SHA, CONCLUSION, ARTIFACT_ID, ARTIFACT_NAME, ARTIFACT_DIGEST
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-metadata", type=Path, required=True)
    parser.add_argument("--artifact-metadata", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-run-id", type=int)
    parser.add_argument("--expected-run-attempt", type=int)
    parser.add_argument("--expected-head-sha")
    parser.add_argument("--expected-conclusion")
    parser.add_argument("--expected-artifact-id", type=int)
    parser.add_argument("--expected-artifact-name")
    parser.add_argument("--expected-artifact-digest")
    args = parser.parse_args()
    RUN_ID = args.expected_run_id or RUN_ID
    RUN_ATTEMPT = args.expected_run_attempt or RUN_ATTEMPT
    HEAD_SHA = args.expected_head_sha or HEAD_SHA
    CONCLUSION = args.expected_conclusion or CONCLUSION
    ARTIFACT_ID = args.expected_artifact_id or ARTIFACT_ID
    ARTIFACT_NAME = args.expected_artifact_name or ARTIFACT_NAME
    ARTIFACT_DIGEST = args.expected_artifact_digest or ARTIFACT_DIGEST
    run = json.loads(args.run_metadata.read_text())
    listing = json.loads(args.artifact_metadata.read_text())
    artifact = validate_source(run, listing)
    build_outputs(run, artifact, args.archive, args.output_dir)
    print(json.dumps({"status": "EVIDENCE_EXTRACTION_VERIFIED", "certification": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
