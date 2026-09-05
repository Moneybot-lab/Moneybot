import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from moneybot.services import decision_log_export as export


def _write(path, count, *, start=0):
    with path.open("a" if start else "w") as handle:
        for index in range(start, start + count):
            handle.write(
                json.dumps(
                    {"ts": index // 3, "event_id": f"event-{index}", "symbol": "AAPL"}
                )
                + "\n"
            )


def _checkpoint(source, state):
    scan = export._scan(source)
    state.write_text(
        json.dumps(
            {
                "schema_version": export.CHECKPOINT_VERSION,
                "source_identity": export._source_identity(source),
                "previous_complete_bytes": scan["bytes"],
                "previous_complete_records": scan["records"],
                "previous_sha256": scan["sha256"],
                "earliest_timestamp": scan["earliest"],
                "latest_timestamp": scan["latest"],
                "complete": True,
                "truncated": False,
                "integrity_clean": True,
                "manifest_created_at": "test",
                "checkpoint_updated_at": "test",
                "ordering_version": export.ORDERING_VERSION,
            }
        )
    )
    return scan


@pytest.mark.parametrize("count", [49_999, 50_000, 50_001, 75_123])
def test_unbounded_export_crosses_old_boundary(tmp_path, count):
    source, state = tmp_path / "events.jsonl", tmp_path / "state.json"
    _write(source, count)
    _checkpoint(source, state)
    manifest = export.analyze_decision_log(source, state_path=state)
    assert manifest["source_total_records"] == manifest["exported_records"] == count
    assert manifest["continuity"]["status"] == "PREFIX_VERIFIED"
    assert (
        b"".join(export.stream_exact_prefix(source, manifest["content_bytes"]))
        == source.read_bytes()
    )


def test_verified_seed_bootstrap_then_unchanged_and_growth(tmp_path, monkeypatch):
    source, state = tmp_path / "events.jsonl", tmp_path / "state.json"
    _write(source, 3)
    scan = export._scan(source)
    seed = {
        "content_bytes": scan["bytes"],
        "record_count": 3,
        "sha256": scan["sha256"],
        "earliest_timestamp": 0,
        "latest_timestamp": 0,
        "manifest_created_at": "seed",
    }
    monkeypatch.setattr(export, "VERIFIED_SEED", seed)
    baseline = export.analyze_decision_log(source, state_path=state, bootstrap=True)
    assert baseline["continuity"]["status"] == "BASELINE_CREATED"
    assert not state.exists()
    committed = export.advance_checkpoint(
        source, baseline, state_path=state, bootstrap=True
    )
    assert committed["checkpoint_advanced"] is True
    assert export._bootstrap_marker_path(state).exists()
    unchanged = export.analyze_decision_log(source, state_path=state)
    assert unchanged["continuity"]["status"] == "PREFIX_VERIFIED"
    _write(source, 2, start=3)
    grown = export.analyze_decision_log(source, state_path=state)
    assert grown["exported_records"] == 5
    assert grown["continuity"]["current_prefix_sha256"] == scan["sha256"]
    state.unlink()
    with pytest.raises(export.ContinuityError) as error:
        export.analyze_decision_log(source, state_path=state, bootstrap=True)
    assert error.value.status == "CHECKPOINT_MISSING"


@pytest.mark.parametrize("growth", [0, 2])
def test_changed_historical_byte_fails_for_same_or_longer_source(tmp_path, growth):
    source, state = tmp_path / "events.jsonl", tmp_path / "state.json"
    _write(source, 4)
    _checkpoint(source, state)
    data = source.read_bytes().replace(b"event-1", b"event-X", 1)
    source.write_bytes(data)
    if growth:
        _write(source, growth, start=4)
    with pytest.raises(export.ContinuityError, match="checkpoint prefix") as error:
        export.analyze_decision_log(source, state_path=state)
    assert error.value.status == "PREFIX_MISMATCH"


def test_truncated_source_and_decreased_record_count_fail(tmp_path):
    source, state = tmp_path / "events.jsonl", tmp_path / "state.json"
    _write(source, 4)
    _checkpoint(source, state)
    source.write_bytes(source.read_bytes().splitlines(keepends=True)[0])
    with pytest.raises(export.ContinuityError) as error:
        export.analyze_decision_log(source, state_path=state)
    assert error.value.status == "SOURCE_REGRESSION"


@pytest.mark.parametrize(
    "contents,status", [(None, "CHECKPOINT_MISSING"), ("bad", "CHECKPOINT_CORRUPT")]
)
def test_missing_or_corrupt_checkpoint_fails(tmp_path, contents, status):
    source, state = tmp_path / "events.jsonl", tmp_path / "state.json"
    _write(source, 2)
    if contents is not None:
        state.write_text(contents)
    with pytest.raises(export.ContinuityError) as error:
        export.analyze_decision_log(source, state_path=state)
    assert error.value.status == status


def test_interrupted_stream_does_not_advance_and_atomic_commit_does(tmp_path):
    source, state = tmp_path / "events.jsonl", tmp_path / "state.json"
    _write(source, 3)
    _checkpoint(source, state)
    before = state.read_bytes()
    _write(source, 1, start=3)
    manifest = export.analyze_decision_log(source, state_path=state)
    generator = export.stream_exact_prefix(source, manifest["content_bytes"])
    next(generator)
    generator.close()
    assert state.read_bytes() == before
    export.advance_checkpoint(source, manifest, state_path=state)
    assert (
        json.loads(state.read_text())["previous_sha256"] == manifest["content_sha256"]
    )
    assert not list(tmp_path.glob("*.tmp"))


def test_generator_fails_if_snapshot_is_truncated_after_analysis(tmp_path):
    source, state = tmp_path / "events.jsonl", tmp_path / "state.json"
    _write(source, 3)
    _checkpoint(source, state)
    manifest = export.analyze_decision_log(source, state_path=state)
    source.write_bytes(source.read_bytes()[:-5])
    with pytest.raises(RuntimeError, match="changed during export"):
        b"".join(export.stream_exact_prefix(source, manifest["content_bytes"]))


def test_checkpoint_write_failure_preserves_previous(tmp_path, monkeypatch):
    source, state = tmp_path / "events.jsonl", tmp_path / "state.json"
    _write(source, 3)
    _checkpoint(source, state)
    before = state.read_bytes()
    _write(source, 1, start=3)
    manifest = export.analyze_decision_log(source, state_path=state)
    monkeypatch.setattr(
        export.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("no"))
    )
    with pytest.raises(export.ContinuityError) as error:
        export.advance_checkpoint(source, manifest, state_path=state)
    assert error.value.status == "CHECKPOINT_WRITE_FAILED"
    assert state.read_bytes() == before


def test_concurrent_commits_are_monotonic(tmp_path):
    source, state = tmp_path / "events.jsonl", tmp_path / "state.json"
    _write(source, 20)
    _checkpoint(source, state)
    _write(source, 1, start=20)
    manifest = export.analyze_decision_log(source, state_path=state)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: export.advance_checkpoint(source, manifest, state_path=state),
                range(2),
            )
        )
    assert sum(item["checkpoint_advanced"] for item in results) == 1
    assert json.loads(state.read_text())["previous_complete_records"] == 21


def test_duplicate_audit_distinguishes_exact_repeats_and_conflicts(tmp_path):
    source = tmp_path / "events.jsonl"
    one = {"ts": 1, "decision_id": "one", "symbol": "AAPL", "endpoint": "quick"}
    repeated = {"ts": 1, "decision_id": "two", "symbol": "AAPL", "endpoint": "quick"}
    conflict = {**one, "symbol": "MSFT"}
    source.write_text(
        "".join(json.dumps(row) + "\n" for row in (one, repeated, one, conflict))
    )
    first = export.audit_decision_log(source)
    second = export.audit_decision_log(source)
    assert first == second
    assert first["duplicate_physical_records"] == 1
    assert first["same_immutable_identity_with_different_payload"] == 1
    assert first["status"] == "FAILED_CONFLICTING_IDENTITIES"
    assert "AAPL" not in json.dumps(first) and "MSFT" not in json.dumps(first)


def test_duplicate_audit_normalizes_json_but_not_repeated_observations(tmp_path):
    source = tmp_path / "events.jsonl"
    source.write_text(
        '{"ts":1,"event_id":"one","symbol":"AAPL"}\n'
        '{ "symbol":"AAPL", "event_id":"one", "ts":1 }\n'
        '{"ts":1,"event_id":"two","symbol":"AAPL"}\n'
    )
    audit = export.audit_decision_log(source)
    assert audit["total_physical_records"] == 3
    assert audit["unique_records"] == 2
    assert audit["duplicate_physical_records"] == 1
    assert audit["byte_identical_duplicate_groups"] == 0
    assert audit["same_immutable_identity_with_different_payload"] == 0
