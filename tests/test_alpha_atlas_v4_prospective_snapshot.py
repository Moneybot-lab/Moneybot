from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from moneybot.services.alpha_atlas_v4_prospective_snapshot import *
from moneybot.services.runtime_paths import prospective_snapshot_root
from scripts.validate_alpha_atlas_v4_prospective_snapshot import write_artifact_checksums


def snap(ready, times, *, sid="a", status="COMPLETE", family=None):
    return {"snapshot_id": sid, "status": status, "ready_for_use_at": ready.isoformat(),
            "assembly_completed_at": ready.isoformat(), "families": {"daily": family or {
                "source_received_at": (ready-timedelta(hours=1)).isoformat(),
                "receipt_evidence_kind": "COLLECTOR_RECEIPT_CLOCK", "freshness_status": "FRESH"}},
            "eligibility_reason_codes": []}


def test_deadline_inclusive_and_everything_after_late():
    t = session_times(date(2026, 7, 6))
    assert disposition(validate_snapshot(snap(t.deadline, t), t), has_attempt=True) == "ELIGIBLE"
    for delta in (timedelta(microseconds=1), timedelta(minutes=3), timedelta(minutes=5)):
        assert disposition(validate_snapshot(snap(t.deadline + delta, t), t), has_attempt=True) == "LATE"


def test_late_correction_cannot_replace_eligible_and_lexical_tie_break():
    t = session_times(date(2026, 7, 6)); early = t.deadline - timedelta(minutes=1)
    assert select_eligible([snap(early, t, sid="a"), snap(early, t, sid="z"), snap(t.decision, t, sid="correction")], t)["snapshot_id"] == "z"


def test_receipt_is_not_inferred_and_stale_precedence():
    t = session_times(date(2026, 7, 6))
    family = {"source_event_at": (t.deadline-timedelta(days=1)).isoformat(), "source_received_at": None,
              "receipt_evidence_kind": "UNKNOWN", "freshness_status": "STALE"}
    reasons = validate_snapshot(snap(t.deadline, t, family=family), t)
    assert "UNKNOWN_PROVENANCE:daily" in reasons and disposition(reasons, has_attempt=True) == "UNKNOWN"


def test_cache_only_never_invokes_lazy_fetch():
    class Lazy(dict):
        def __missing__(self, key): raise AssertionError("network/lazy fetch")
        def get(self, key, default=None): raise AssertionError("get may fetch")
    assert cache_only_adapt("MISS", Lazy(), {"daily"})["status"] == "MISSING"
    result = cache_only_adapt("A", {"A": {"features": {}, "families": {}}}, {"daily"})
    assert "MISSING_FAMILY:daily" in result["reasons"]


def test_universe_and_full_reconciliation():
    manifest = {"ordered_members": ["A", "B"]}
    assert validate_universe(manifest)
    with pytest.raises(CaptureError): validate_universe({"ordered_members": ["B", "A"]})
    assert [disposition([], has_attempt=True), disposition([], has_attempt=False)] == ["ELIGIBLE", "MISSING"]
    assert [cohort_size_label(x) for x in (0, 1, 4, 5, 6)] == ["zero", "one", "two_through_four", "exactly_five", "more_than_five"]


def test_atomic_immutable_corruption_restart_and_budget(tmp_path):
    store = ImmutableStore(tmp_path); payload = b"one"
    result = store.publish("x", payload)
    assert not result["duplicate"]
    assert store.publish("x", payload)["duplicate"]
    with pytest.raises(CaptureError): store.publish("x", b"two")
    (tmp_path / "x").write_bytes(b"corrupt")
    with pytest.raises(CaptureError, match="READBACK_CHECKSUM_FAILURE"):
        store.verify("x", result["sha256"], result["bytes"])
    PilotBudget(store).consume(sessions=1, assignments=2, storage_bytes=3, provider_requests=0)
    assert PilotBudget(ImmutableStore(tmp_path)).totals()["assignments"] == 2
    with pytest.raises(CaptureError): PilotBudget(store).consume(provider_requests=1)


def test_concurrent_lock_fails(tmp_path):
    a, b = ImmutableStore(tmp_path), ImmutableStore(tmp_path)
    with a.lock():
        with pytest.raises(CaptureError, match="CONCURRENT_RUN"):
            with b.lock(): pass


def test_forbidden_fields_and_calendar_boundaries():
    t = session_times(date(2026, 11, 27))  # known early-close session
    bad = snap(t.deadline, t); bad["features"] = {"prediction": 1}
    assert disposition(validate_snapshot(bad, t), has_attempt=True) == "INVALID"
    with pytest.raises(CaptureError): session_times(date(2026, 12, 25))
    winter, summer = session_times(date(2026, 1, 5)), session_times(date(2026, 7, 6))
    assert winter.freeze.hour != summer.freeze.hour
    assert t.official_open.hour == 14


def test_persistent_root_required(monkeypatch):
    monkeypatch.delenv("MONEYBOT_PERSISTENT_DATA_DIR", raising=False)
    with pytest.raises(RuntimeError, match="PERSISTENT_RUNTIME_ROOT_REQUIRED"): prospective_snapshot_root()


def test_exact_cli_without_pythonpath(tmp_path):
    env = dict(os.environ); env.pop("PYTHONPATH", None)
    proc = subprocess.run([sys.executable, "-m", "scripts.validate_alpha_atlas_v4_prospective_snapshot", "--synthetic"],
                          cwd=os.path.dirname(os.path.dirname(__file__)), env=env, text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["provider_requests"] == 0 and report["real_collection_available"] is False


def test_no_real_collection_mode():
    proc = subprocess.run([sys.executable, "-m", "scripts.validate_alpha_atlas_v4_prospective_snapshot"], text=True, capture_output=True)
    assert proc.returncode != 0


def test_artifact_checksums_are_relative_and_verify_after_packaging(tmp_path):
    staging = tmp_path / "evidence"; staging.mkdir()
    (staging / "report.json").write_bytes(b'{"status":"PASS"}\n')
    manifest = write_artifact_checksums(staging)
    assert manifest.read_text().endswith("  report.json\n")
    assert "evidence/report.json" not in manifest.read_text()
    packaged = tmp_path / "uploaded-member"; packaged.mkdir()
    for name in ("report.json", "SHA256SUMS"):
        (packaged / name).write_bytes((staging / name).read_bytes())
    subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=packaged, check=True,
                   text=True, capture_output=True)


def test_proposed_universe_is_source_bound_and_not_approved():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "docs/reports/alpha_atlas_v4_proposed_pilot_universe.v1.json").read_text())
    claimed = manifest.pop("content_sha256")
    assert sha256_bytes(canonical_bytes(manifest)) == claimed
    assert manifest["ordered_members"] == ["QQQ", "SPY"]
    assert manifest["approval_status"] == "PENDING_USER_REVIEW"
    assert manifest["typed_identifiers"] == {"QQQ": None, "SPY": None}
    assert sha256_bytes((root / manifest["source"]["path"]).read_bytes()) == manifest["source"]["source_sha256"]
    assert sha256_bytes((root / manifest["source"]["deployment_path"]).read_bytes()) == manifest["source"]["deployment_sha256"]


def test_readiness_report_is_fail_closed_and_records_hosted_smoke():
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "docs/reports/alpha_atlas_v4_prospective_pilot_readiness.v1.json").read_text())
    assert report["decision"] == "BLOCKED"
    assert report["hosted_synthetic_check"]["run"] == "36457965993-1"
    assert report["hosted_synthetic_check"]["uploaded_report_json_sha256"] == "7cb11d7d811bf1b3248dae0919a18620d45fc61f0059a7637c52a1957616c9b4"
    assert report["runtime"]["runtime_probe_performed"] is False
    assert {item["status"] for item in report["cache_inspection"]["families"]} == {"UNAVAILABLE"}
    assert report["prohibitions_observed"]["provider_requests"] == 0
