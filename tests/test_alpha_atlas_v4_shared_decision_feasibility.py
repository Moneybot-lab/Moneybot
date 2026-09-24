from datetime import datetime, timezone

import pytest

from moneybot.services.alpha_atlas_v4_shared_decision_feasibility import (
    assess_partition, cohort_distribution, previous_session_close_boundary,
    split_conflicts, utc,
)


BOUNDARY = "2026-01-02T21:00:00Z"


def row(identifier="a", ticker="A", cutoff=BOUNDARY, capture=BOUNDARY, freshness="fresh"):
    return {"canonical_observation_id": identifier, "ticker": ticker, "label_horizon_sessions": 5,
            "decision_at": "2026-01-03T02:00:00Z", "feature_cutoff_at": cutoff,
            "snapshot_available_at": capture, "feature_family_available_at": {"daily": BOUNDARY},
            "staleness_status": freshness, "entry_at": "2026-01-05T14:30:00Z", "exit_at": "2026-01-09T21:00:00Z"}


def boundaries():
    return {(5, "2026-01-05T14:30:00Z", "2026-01-09T21:00:00Z"): BOUNDARY}


def test_boundary_equality_qualifies_and_after_is_rejected():
    report = assess_partition([row(), row("b", "B", capture="2026-01-02T21:00:00.000001Z")], boundaries())
    assert report["dispositions"]["QUALIFYING"] == 1
    assert report["dispositions"]["ONLY_AFTER_BOUNDARY"] == 1


def test_utc_normalization_and_calendar_callback_boundary():
    assert utc("2026-01-02T16:00:00-05:00") == utc(BOUNDARY)
    result = previous_session_close_boundary("2026-01-05T09:30:00-05:00", lambda _: datetime(2026, 1, 2, 16, tzinfo=timezone.utc))
    assert result == datetime(2026, 1, 2, 16, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="TIMEZONE_REQUIRED"):
        utc("2026-01-02T16:00:00")


def test_unknown_availability_and_freshness_fail_closed():
    unknown_capture = row(); unknown_capture.pop("snapshot_available_at")
    unknown_freshness = row("b", "B", freshness=None)
    report = assess_partition([unknown_capture, unknown_freshness], boundaries())
    assert report["dispositions"]["UNKNOWN_AVAILABILITY_OR_FRESHNESS"] == 2


def test_stale_and_deterministic_snapshot_tie():
    stale = row("stale", "S", freshness="stale")
    a, b = row("a"), row("b")
    report = assess_partition([b, stale, a], boundaries())
    assert report["dispositions"]["STALE"] == 1
    assert next(x for x in report["opportunities"] if x["opportunity_key"][0] == "A")["selected_canonical_observation_id"] == "b"


def test_opportunity_and_cohort_reconciliation_includes_empty_cohort():
    missing = row("x", "X"); missing.pop("snapshot_available_at")
    report = assess_partition([row(), missing], boundaries())
    distribution = cohort_distribution(report["opportunities"])
    assert sum(report["dispositions"].values()) == report["opportunity_count"] == 2
    assert distribution["cohort_count"] == 1
    assert distribution["bins"]["one"]["count"] == 1


def test_expected_opportunity_without_snapshot_is_missing():
    expected = [("MISSING", 5, "2026-01-05T14:30:00Z", "2026-01-09T21:00:00Z")]
    report = assess_partition([], boundaries(), expected_opportunities=expected)
    assert report["opportunity_count"] == 1
    assert report["dispositions"]["MISSING"] == 1
    assert report["opportunities"][0]["diagnostic_flags"] == ["no_saved_snapshot"]


def test_split_conflicts_are_reported_not_repaired():
    shared = row("same")
    findings = split_conflicts([shared], [shared], "2026-01-04T00:00:00Z")
    assert findings["canonical_ids_reused_across_partitions"] == ["same"]
    assert len(findings["opportunities_spanning_partitions"]) == 1
    assert findings["training_outcomes_not_strictly_before_validation_boundary"] == ["same"]
