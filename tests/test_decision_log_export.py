import hashlib
import json

import pytest

from moneybot.services.decision_log_export import (
    analyze_decision_log,
    stream_exact_prefix,
)


def _write(path, count):
    with path.open("w") as handle:
        for index in range(count):
            handle.write(
                json.dumps(
                    {"ts": index // 3, "event_id": f"event-{index}", "symbol": "AAPL"}
                )
                + "\n"
            )


@pytest.mark.parametrize("count", [49_999, 50_000, 50_001, 75_123])
def test_unbounded_manifest_accounts_for_every_record_across_old_boundary(
    tmp_path, count
):
    path = tmp_path / "events.jsonl"
    _write(path, count)
    first = analyze_decision_log(path)
    second = analyze_decision_log(path)
    assert first["source_total_records"] == first["exported_records"] == count
    assert first["pagination_complete"] is True
    assert first["truncated"] is False
    assert first["earliest_timestamp"] == 0
    assert first["latest_timestamp"] == (count - 1) // 3
    assert first["content_sha256"] == second["content_sha256"]
    assert (
        b"".join(stream_exact_prefix(path, first["content_bytes"])) == path.read_bytes()
    )


def test_exact_duplicates_reported_but_legitimate_repeated_symbol_requests_remain(
    tmp_path,
):
    path = tmp_path / "events.jsonl"
    one = {"ts": 1, "event_id": "one", "symbol": "AAPL"}
    two = {"ts": 1, "event_id": "two", "symbol": "AAPL"}
    path.write_text("".join(json.dumps(row) + "\n" for row in (one, two, one)))
    result = analyze_decision_log(path)
    assert result["exported_records"] == 3
    assert result["duplicate_records_detected"] == 1


@pytest.mark.parametrize("payload", [b"", b'{"ts":1}', b"not-json\n", b"[]\n"])
def test_empty_or_malformed_source_is_detected(tmp_path, payload):
    path = tmp_path / "events.jsonl"
    path.write_bytes(payload)
    if not payload:
        assert analyze_decision_log(path)["source_total_records"] == 0
    else:
        with pytest.raises(ValueError):
            analyze_decision_log(path)
