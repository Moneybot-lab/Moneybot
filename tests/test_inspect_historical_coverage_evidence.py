from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
import zipfile

import pytest

import scripts.inspect_historical_coverage_evidence as inspector


def _run() -> dict:
    return {"id": inspector.RUN_ID, "name": inspector.WORKFLOW,
            "run_attempt": inspector.RUN_ATTEMPT, "head_sha": inspector.HEAD_SHA,
            "conclusion": inspector.CONCLUSION,
            "repository": {"full_name": inspector.REPOSITORY}}


def _listing(**changes) -> dict:
    artifact = {"id": inspector.ARTIFACT_ID, "name": inspector.ARTIFACT_NAME,
                "expired": False, "digest": inspector.ARTIFACT_DIGEST}
    artifact.update(changes)
    return {"artifacts": [artifact]}


def _archive(path: Path, members: dict[str, bytes] | None = None) -> Path:
    members = members or {
        inspector.REPORT_NAMES[0]: b'{"status":"VERIFIED_DIAGNOSTIC_EXECUTION"}\n',
        inspector.REPORT_NAMES[1]: b"# Diagnostic\n\nNot a coverage certification.\n",
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


@pytest.mark.parametrize("field,value,code", [
    ("name", "Wrong workflow", "SOURCE_RUN_NAME_MISMATCH"),
    ("run_attempt", 2, "SOURCE_RUN_RUN_ATTEMPT_MISMATCH"),
    ("head_sha", "0" * 40, "SOURCE_RUN_HEAD_SHA_MISMATCH"),
    ("conclusion", "failure", "SOURCE_RUN_CONCLUSION_MISMATCH"),
])
def test_source_run_mismatches_fail(field, value, code):
    run = _run(); run[field] = value
    with pytest.raises(ValueError, match=code):
        inspector.validate_source(run, _listing())


@pytest.mark.parametrize("changes,code", [
    ({"id": 1}, "ARTIFACT_ID_MISSING_OR_AMBIGUOUS"),
    ({"name": "wrong"}, "ARTIFACT_NAME_MISMATCH"),
    ({"expired": True}, "ARTIFACT_EXPIRED"),
    ({"digest": "sha256:" + "0" * 64}, "ARTIFACT_METADATA_DIGEST_MISMATCH"),
])
def test_artifact_metadata_mismatches_fail(changes, code):
    with pytest.raises(ValueError, match=code):
        inspector.validate_source(_run(), _listing(**changes))


def test_download_digest_is_mandatory(tmp_path, monkeypatch):
    path = tmp_path / "artifact.zip"; path.write_bytes(b"downloaded bytes")
    monkeypatch.setattr(inspector, "ARTIFACT_DIGEST", "sha256:" + "0" * 64)
    with pytest.raises(ValueError, match="DOWNLOADED_ARCHIVE_DIGEST_MISMATCH"):
        inspector.verify_archive_digest(path)
    expected = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(inspector, "ARTIFACT_DIGEST", expected)
    assert inspector.verify_archive_digest(path) == expected


@pytest.mark.parametrize("unsafe", ["../report.json", "/absolute/report.json", "dir\\report.json", "C:/report.json", "bad\nname"])
def test_unsafe_member_paths_are_rejected(tmp_path, unsafe):
    path = _archive(tmp_path / "unsafe.zip", {unsafe: b"x", **{
        inspector.REPORT_NAMES[0]: b"{}", inspector.REPORT_NAMES[1]: b"ok"}})
    with pytest.raises(ValueError, match="UNSAFE_ARCHIVE_MEMBER_PATH"):
        inspector.inspect_archive(path)


def test_links_and_case_ambiguous_names_are_rejected(tmp_path):
    link_path = tmp_path / "link.zip"
    with zipfile.ZipFile(link_path, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "target")
    with pytest.raises(ValueError, match="UNSUPPORTED_ARCHIVE_MEMBER_TYPE"):
        inspector.inspect_archive(link_path)

    duplicate = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr("Report.JSON", "one")
        archive.writestr("report.json", "two")
    with pytest.raises(ValueError, match="DUPLICATE_OR_AMBIGUOUS_ARCHIVE_MEMBER"):
        inspector.inspect_archive(duplicate)


def test_encrypted_and_unsupported_compression_members_are_rejected():
    encrypted = zipfile.ZipInfo("encrypted.json")
    encrypted.flag_bits |= 0x1
    with pytest.raises(ValueError, match="ENCRYPTED_ARCHIVE_MEMBER"):
        inspector._safe_member(encrypted)
    unsupported = zipfile.ZipInfo("compressed.json")
    unsupported.compress_type = zipfile.ZIP_BZIP2
    with pytest.raises(ValueError, match="UNSUPPORTED_ARCHIVE_COMPRESSION"):
        inspector._safe_member(unsupported)


def test_missing_exact_top_level_report_fails(tmp_path):
    path = _archive(tmp_path / "missing.zip", {"nested/" + inspector.REPORT_NAMES[0]: b"{}",
                                                inspector.REPORT_NAMES[1]: b"ok"})
    with pytest.raises(ValueError, match="REPORT_MISSING_OR_AMBIGUOUS"):
        inspector.inspect_archive(path)


def test_reports_are_preserved_and_rendered_as_literal_fenced_text(tmp_path, monkeypatch):
    injected = b"# source heading\n```\n</details>\n| injected | table |\n```\n"
    original_json = b'{"text":"literal"}\n'
    archive_path = _archive(tmp_path / "source.zip", {
        inspector.REPORT_NAMES[0]: original_json,
        inspector.REPORT_NAMES[1]: injected,
        "nested-evidence.zip": b"listed but never opened",
    })
    digest = "sha256:" + hashlib.sha256(archive_path.read_bytes()).hexdigest()
    monkeypatch.setattr(inspector, "ARTIFACT_DIGEST", digest)
    listing = _listing(digest=digest)
    artifact = inspector.validate_source(_run(), listing)
    output = tmp_path / "out"
    manifest = inspector.build_outputs(_run(), artifact, archive_path, output)

    assert (output / inspector.REPORT_NAMES[0]).read_bytes() == original_json
    assert (output / inspector.REPORT_NAMES[1]).read_bytes() == injected
    rendered = (output / "summary_report_markdown.md").read_text()
    assert "````text\n" in rendered and "\n````\n" in rendered
    assert injected.decode() in rendered
    assert manifest["report_sha256"][inspector.REPORT_NAMES[1]] == hashlib.sha256(injected).hexdigest()
    inventory = (output / "summary_inventory.md").read_text()
    assert "does **not** certify complete historical coverage" in inventory
    assert "nested-evidence.zip" in inventory


def test_manual_workflow_uses_builtin_token_and_never_runs_diagnostics():
    text = Path(".github/workflows/inspect-historical-coverage-evidence.yml").read_text()
    assert text.startswith("name: Inspect Historical Coverage Evidence\n")
    assert "workflow_dispatch:" in text and "schedule:" not in text and "push:" not in text
    assert "actions: read" in text and "contents: read" in text
    assert "GH_TOKEN: ${{ github.token }}" in text
    assert "MASSIVE_API_KEY" not in text
    assert "diagnose_alpha_atlas" not in text
    assert text.count("GITHUB_STEP_SUMMARY") == 3
    for value in (str(inspector.RUN_ID), str(inspector.ARTIFACT_ID), inspector.HEAD_SHA):
        assert value in (text + Path("scripts/inspect_historical_coverage_evidence.py").read_text())
