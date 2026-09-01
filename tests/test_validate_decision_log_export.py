import json

import pytest

from scripts.validate_decision_log_export import validate_export


def _fixture(tmp_path, *, final=200, body=None, declared_bytes=None, declared_lines=None):
    body = body if body is not None else b'{"id":1}\n{"id":2}\n'
    artifact = tmp_path / "decision_events.jsonl"
    artifact.write_bytes(body)
    size = len(body) if declared_bytes is None else declared_bytes
    lines = body.count(b"\n") if declared_lines is None else declared_lines
    headers = tmp_path / "_response_headers.txt"
    headers.write_text(
        "HTTP/2 502\ncontent-type: text/html\n\n"
        "HTTP/2 502\ncontent-type: text/html\n\n"
        f"HTTP/2 {final}\ncontent-type: application/x-ndjson; charset=utf-8\n"
        f"content-length: {size}\nx-decision-log-lines: {lines}\n\n"
    )
    return artifact, headers


def test_retry_header_history_accepts_valid_final_200_export(tmp_path):
    artifact, headers = _fixture(tmp_path)
    assert validate_export(artifact=artifact, headers=headers, final_status=200) == {
        "status": "VALID", "http_status": 200, "bytes": 18, "lines": 2
    }


def test_final_502_fails_closed(tmp_path):
    artifact, headers = _fixture(tmp_path, final=502)
    with pytest.raises(ValueError, match="final curl response was HTTP 502"):
        validate_export(artifact=artifact, headers=headers, final_status=502)


def test_malformed_jsonl_fails_closed(tmp_path):
    artifact, headers = _fixture(tmp_path, body=b'{"id":1}\nnot-json\n')
    with pytest.raises(ValueError, match="invalid JSON"):
        validate_export(artifact=artifact, headers=headers, final_status=200)


def test_truncated_file_fails_closed(tmp_path):
    artifact, headers = _fixture(tmp_path, body=b'{"id":1}')
    with pytest.raises(ValueError, match="newline-terminated"):
        validate_export(artifact=artifact, headers=headers, final_status=200)


@pytest.mark.parametrize("field", ["bytes", "lines"])
def test_declared_size_or_count_mismatch_fails_closed(tmp_path, field):
    options = {"declared_bytes": 999} if field == "bytes" else {"declared_lines": 999}
    artifact, headers = _fixture(tmp_path, **options)
    with pytest.raises(ValueError, match="mismatch"):
        validate_export(artifact=artifact, headers=headers, final_status=200)
