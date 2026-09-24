"""Metadata-only shared-decision snapshot feasibility checks.

This module deliberately does not read outcomes or feature values.  A source-bar
timestamp is not treated as proof that an assembled snapshot was available.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Iterable


FINAL_DISPOSITIONS = (
    "QUALIFYING", "MISSING", "ONLY_AFTER_BOUNDARY", "STALE",
    "UNKNOWN_AVAILABILITY_OR_FRESHNESS", "INVALID_OR_CONFLICTING_METADATA",
)


def utc(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception as exc:
        raise ValueError("INVALID_TIMESTAMP") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("TIMEZONE_REQUIRED")
    return parsed.astimezone(timezone.utc)


def window_key(row: dict[str, Any]) -> tuple[Any, str, str]:
    return (row.get("label_horizon_sessions"), str(row.get("entry_at")), str(row.get("exit_at")))


def opportunity_key(row: dict[str, Any]) -> tuple[str, Any, str, str]:
    return (str(row.get("ticker") or row.get("symbol") or "").upper(), *window_key(row))


def previous_session_close_boundary(
    entry_at: Any, previous_session_close: Callable[[datetime], datetime]
) -> datetime:
    """Proposed convention: preceding XNYS session's official close.

    The calendar callback is mandatory so weekends, holidays, and early closes
    cannot be inferred from wall-clock arithmetic.
    """
    entry = utc(entry_at)
    boundary = utc(previous_session_close(entry))
    if boundary >= entry:
        raise ValueError("BOUNDARY_NOT_STRICTLY_BEFORE_ENTRY")
    return boundary


def _candidate_state(row: dict[str, Any], boundary: datetime) -> tuple[str, set[str]]:
    flags: set[str] = set()
    try:
        cutoff = utc(row.get("feature_cutoff_at"))
    except ValueError:
        return "INVALID_OR_CONFLICTING_METADATA", {"invalid_feature_cutoff"}
    capture_raw = row.get("snapshot_available_at") or row.get("snapshot_constructed_at")
    if capture_raw in (None, ""):
        return "UNKNOWN_AVAILABILITY_OR_FRESHNESS", {"snapshot_availability_unproven"}
    try:
        capture = utc(capture_raw)
    except ValueError:
        return "INVALID_OR_CONFLICTING_METADATA", {"invalid_snapshot_availability"}
    if cutoff > boundary or capture > boundary:
        return "ONLY_AFTER_BOUNDARY", {"after_boundary"}
    availability = row.get("feature_family_available_at")
    if not isinstance(availability, dict) or not availability or any(v in (None, "") for v in availability.values()):
        return "UNKNOWN_AVAILABILITY_OR_FRESHNESS", {"source_availability_unproven"}
    try:
        if any(utc(value) > boundary for value in availability.values()):
            return "ONLY_AFTER_BOUNDARY", {"source_available_after_boundary"}
    except ValueError:
        return "INVALID_OR_CONFLICTING_METADATA", {"invalid_source_availability"}
    freshness = row.get("staleness_status")
    if freshness in (None, "", "unknown"):
        return "UNKNOWN_AVAILABILITY_OR_FRESHNESS", {"freshness_unproven"}
    if freshness != "fresh":
        return "STALE", {"stale"}
    return "QUALIFYING", flags


def assess_partition(
    rows: Iterable[dict[str, Any]], boundaries: dict[tuple[Any, str, str], Any],
    *, expected_opportunities: Iterable[tuple[str, Any, str, str]] = (),
) -> dict[str, Any]:
    """Reconcile one final disposition per ticker/execution-window opportunity."""
    grouped: dict[tuple[str, Any, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[opportunity_key(row)].append(row)
    for key in expected_opportunities:
        grouped.setdefault((str(key[0]).upper(), *key[1:]), [])
    opportunities = []
    for key, candidates in sorted(grouped.items()):
        boundary_raw = boundaries.get(key[1:])
        flags: set[str] = set()
        if not candidates:
            disposition, chosen = "MISSING", None
            flags.add("no_saved_snapshot")
        elif boundary_raw is None:
            disposition = "INVALID_OR_CONFLICTING_METADATA"
            chosen = None
            flags.add("missing_common_boundary")
        else:
            try:
                boundary = utc(boundary_raw)
                states = [(row, *_candidate_state(row, boundary)) for row in candidates]
                flags.update(flag for _, _, item_flags in states for flag in item_flags)
                eligible = [row for row, state, _ in states if state == "QUALIFYING"]
                if eligible:
                    # Latest cutoff, then lexical canonical ID is the fixed tie rule.
                    chosen = sorted(eligible, key=lambda row: (utc(row["feature_cutoff_at"]), str(row.get("canonical_observation_id"))))[-1]
                    disposition = "QUALIFYING"
                else:
                    chosen = None
                    priority = ("INVALID_OR_CONFLICTING_METADATA", "ONLY_AFTER_BOUNDARY", "STALE", "UNKNOWN_AVAILABILITY_OR_FRESHNESS")
                    present = {state for _, state, _ in states}
                    disposition = next(state for state in priority if state in present)
            except ValueError:
                disposition, chosen = "INVALID_OR_CONFLICTING_METADATA", None
                flags.add("invalid_common_boundary")
        opportunities.append({
            "opportunity_key": list(key), "candidate_count": len(candidates),
            "disposition": disposition, "diagnostic_flags": sorted(flags),
            "selected_canonical_observation_id": None if chosen is None else chosen.get("canonical_observation_id"),
            "original_timestamps": [{k: row.get(k) for k in ("canonical_observation_id", "decision_at", "feature_cutoff_at", "snapshot_constructed_at", "snapshot_available_at")} for row in candidates],
            "common_boundary": boundary_raw,
        })
    counts = Counter(item["disposition"] for item in opportunities)
    for disposition in FINAL_DISPOSITIONS:
        counts.setdefault(disposition, 0)
    return {"opportunity_count": len(opportunities), "dispositions": dict(counts), "opportunities": opportunities}


def cohort_distribution(opportunities: Iterable[dict[str, Any]]) -> dict[str, Any]:
    cohorts: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for item in opportunities:
        cohorts[tuple(item["opportunity_key"][1:])].append(item)
    bins = Counter()
    for items in cohorts.values():
        eligible = sum(item["disposition"] == "QUALIFYING" for item in items)
        label = "zero" if eligible == 0 else "one" if eligible == 1 else "two_through_four" if eligible < 5 else "exactly_five" if eligible == 5 else "more_than_five"
        bins[label] += 1
    denominator = len(cohorts)
    labels = ("zero", "one", "two_through_four", "exactly_five", "more_than_five")
    return {"cohort_count": denominator, "bins": {label: {"count": bins[label], "denominator": denominator, "proportion": bins[label] / denominator if denominator else None} for label in labels},
            "selection_limitation": "For cohorts with <=5 eligible tickers, min(5, eligible) selects the full eligible baseline."}


def split_conflicts(train: Iterable[dict[str, Any]], validation: Iterable[dict[str, Any]], validation_boundary: Any) -> dict[str, Any]:
    train, validation = list(train), list(validation)
    train_ids = {str(row.get("canonical_observation_id")) for row in train}
    valid_ids = {str(row.get("canonical_observation_id")) for row in validation}
    train_opportunities = {opportunity_key(row) for row in train}
    valid_opportunities = {opportunity_key(row) for row in validation}
    try:
        boundary = utc(validation_boundary)
        overlap = [row.get("canonical_observation_id") for row in train if utc(row.get("exit_at")) >= boundary]
    except ValueError:
        overlap = ["UNKNOWN_INVALID_VALIDATION_BOUNDARY"]
    return {"canonical_ids_reused_across_partitions": sorted(train_ids & valid_ids),
            "opportunities_spanning_partitions": [list(x) for x in sorted(train_opportunities & valid_opportunities)],
            "training_outcomes_not_strictly_before_validation_boundary": sorted(str(x) for x in overlap),
            "walk_forward_reuse_across_different_folds": "PERMITTED_BUT_NOT_EVALUATED_BY_THIS_WITHIN_FOLD_CHECK"}
