"""Deterministic V4 purged temporal split planning for canonical observations."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from moneybot.services.market_data_providers import ExchangeCalendar

TEMPORAL_SPLIT_CONTRACT_VERSION = "alpha-atlas-v4-purged-temporal-split.v1"
TEMPORAL_SPLIT_DIAGNOSTIC_SCHEMA = "alpha-atlas-v4-split-diagnostics.v1"
TEMPORAL_SPLIT_PLAN_SCHEMA = "alpha-atlas-v4-split-plan.v1"
NO_CANDIDATE_STATUS = "NO_CANDIDATE_INSUFFICIENT_TEMPORAL_COVERAGE"
TIMING_CONTRACT = "alpha-atlas-v4-prediction-execution-contract.v1"
FEATURE_CONTRACT = "alpha-atlas-v4-features.v2"
CANONICAL_CONTRACT = "alpha-atlas-v4-canonical-observation.v2"
CANONICAL_SCHEMA = "alpha-atlas-v4-canonical-observations.v2"
REQUIRED_TIMING_FIELDS = (
    "decision_at",
    "feature_cutoff_at",
    "label_start_at",
    "entry_at",
    "exit_at",
)


class TemporalSplitDataError(ValueError):
    """Raised for malformed or incompatible canonical temporal data."""


@dataclass(frozen=True)
class TemporalSplitResult:
    plan: dict[str, Any]
    diagnostics: dict[str, Any]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise TemporalSplitDataError(f"missing_required_timing_field:{field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TemporalSplitDataError(f"invalid_timing_field:{field}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise TemporalSplitDataError(f"timing_field_not_utc:{field}")
    return parsed.astimezone(timezone.utc)


def _validate_row(row: Mapping[str, Any]) -> dict[str, Any]:
    identifier = row.get("canonical_observation_id")
    if not isinstance(identifier, str) or not identifier:
        raise TemporalSplitDataError("missing_canonical_observation_id")
    expected = {
        "canonical_observation_schema_version": CANONICAL_SCHEMA,
        "canonicalization_contract_version": CANONICAL_CONTRACT,
        "timing_contract_version": TIMING_CONTRACT,
        "model_feature_contract_version": FEATURE_CONTRACT,
    }
    for field, version in expected.items():
        if row.get(field) != version:
            raise TemporalSplitDataError(f"incompatible_contract:{field}")
    if row.get("model_sample_weight") != 1.0:
        raise TemporalSplitDataError("non_unit_model_sample_weight")
    times = {field: _utc(row.get(field), field) for field in REQUIRED_TIMING_FIELDS}
    if not (
        times["feature_cutoff_at"]
        <= times["decision_at"]
        < times["entry_at"]
        == times["label_start_at"]
        < times["exit_at"]
    ):
        raise TemporalSplitDataError("invalid_timing_order")
    horizon = row.get("label_horizon_sessions")
    if not isinstance(horizon, int) or horizon <= 0:
        raise TemporalSplitDataError("invalid_label_horizon_sessions")
    return {"id": identifier, "row": dict(row), "times": times, "horizon": horizon}


def _session_date(calendar: ExchangeCalendar, instant: datetime) -> str:
    return calendar.local_date(instant).isoformat()


def _next_sessions(
    calendar: ExchangeCalendar, session_date: str, count: int
) -> list[str]:
    current = datetime.fromisoformat(session_date).date()
    sessions = []
    for _ in range(max(0, count)):
        current = calendar.next_session(current)
        sessions.append(current.isoformat())
    return sessions


def plan_v4_temporal_split(
    rows: Iterable[Mapping[str, Any]],
    *,
    input_sha256: str,
    train_ratio: float = 0.8,
    embargo_sessions: int = 1,
    min_train_dates: int = 5,
    min_test_dates: int = 2,
    min_observations: int = 200,
) -> TemporalSplitResult:
    """Choose the feasible date boundary closest to the configured ratio."""
    if not 0 < train_ratio < 1:
        raise TemporalSplitDataError("invalid_train_ratio")
    calendar = ExchangeCalendar()
    validated = [_validate_row(row) for row in rows]
    identifiers = [item["id"] for item in validated]
    if not identifiers:
        raise TemporalSplitDataError("no_canonical_observations")
    if len(identifiers) != len(set(identifiers)):
        raise TemporalSplitDataError("duplicate_canonical_observation_id")
    for item in validated:
        item["split_date"] = _session_date(calendar, item["times"]["feature_cutoff_at"])
        item["entry_session"] = _session_date(calendar, item["times"]["entry_at"])
        item["exit_session"] = _session_date(calendar, item["times"]["exit_at"])
        item["decision_session"] = _session_date(calendar, item["times"]["decision_at"])
    dates = sorted({item["split_date"] for item in validated})
    min_train_rows = max(1, int(min_observations * train_ratio))
    min_test_rows = max(1, min_observations - min_train_rows)
    target_index = train_ratio * len(dates)
    candidates = []
    for boundary_index in range(1, len(dates)):
        boundary_date = dates[boundary_index]
        pre_train = [item for item in validated if item["split_date"] < boundary_date]
        pre_test = [item for item in validated if item["split_date"] >= boundary_date]
        embargo_dates = set(
            [boundary_date]
            + _next_sessions(calendar, boundary_date, max(0, embargo_sessions - 1))
        )
        test = [item for item in pre_test if item["split_date"] not in embargo_dates]
        earliest_test_entry = min(
            (item["times"]["entry_at"] for item in test), default=None
        )
        train = [
            item
            for item in pre_train
            if earliest_test_entry is not None
            and item["times"]["exit_at"] < earliest_test_entry
        ]
        train_dates = sorted({item["split_date"] for item in train})
        test_dates = sorted({item["split_date"] for item in test})
        reasons = []
        if len(train_dates) < min_train_dates:
            reasons.append("insufficient_train_dates")
        if len(test_dates) < min_test_dates:
            reasons.append("insufficient_test_dates")
        if len(train) < min_train_rows:
            reasons.append("insufficient_train_observations")
        if len(test) < min_test_rows:
            reasons.append("insufficient_test_observations")
        candidates.append(
            {
                "boundary_date": boundary_date,
                "boundary_index": boundary_index,
                "distance_from_ratio": abs(boundary_index - target_index),
                "pre_purge_train_rows": len(pre_train),
                "pre_purge_test_rows": len(pre_test),
                "purged_train_rows": len(pre_train) - len(train),
                "embargoed_test_rows": len(pre_test) - len(test),
                "final_train_rows": len(train),
                "final_test_rows": len(test),
                "final_train_dates": len(train_dates),
                "final_test_dates": len(test_dates),
                "rows_removed_by_minimum_history_rule": 0,
                "emptied_side": "train" if not train else "test" if not test else None,
                "final_eligible_row_rule": (
                    reasons[-1] if reasons else "boundary_feasible"
                ),
                "earliest_test_entry_at": (
                    earliest_test_entry.isoformat() if earliest_test_entry else None
                ),
                "latest_train_exit_at": (
                    max(item["times"]["exit_at"] for item in train).isoformat()
                    if train
                    else None
                ),
                "feasible": not reasons,
                "rejection_reasons": reasons,
                "train_ids": sorted(item["id"] for item in train),
                "test_ids": sorted(item["id"] for item in test),
            }
        )
    feasible = [candidate for candidate in candidates if candidate["feasible"]]
    selected = min(
        feasible,
        key=lambda candidate: (
            candidate["distance_from_ratio"],
            candidate["boundary_date"],
        ),
        default=None,
    )
    counts_by_date = Counter(item["split_date"] for item in validated)
    counts_by_entry = Counter(item["entry_session"] for item in validated)
    counts_by_exit = Counter(item["exit_session"] for item in validated)
    counts_by_horizon = Counter(str(item["horizon"]) for item in validated)
    missing = {
        field: sum(
            row.get(field) in {None, ""} for row in (item["row"] for item in validated)
        )
        for field in REQUIRED_TIMING_FIELDS
    }
    diagnostics = {
        "schema_version": TEMPORAL_SPLIT_DIAGNOSTIC_SCHEMA,
        "contract_version": TEMPORAL_SPLIT_CONTRACT_VERSION,
        "input_sha256": input_sha256,
        "total_input_rows": len(validated),
        "canonical_observations": len(validated),
        "raw_request_rows": sum(
            int(item["row"].get("raw_request_count", 1)) for item in validated
        ),
        "unique_temporal_groups": len(dates),
        "unique_symbols": len({str(item["row"].get("symbol")) for item in validated}),
        "unique_entry_sessions": len(set(counts_by_entry)),
        "unique_exit_sessions": len(set(counts_by_exit)),
        "unique_decision_dates": len({item["decision_session"] for item in validated}),
        "counts_by_cutoff_date": dict(sorted(counts_by_date.items())),
        "counts_by_entry_date": dict(sorted(counts_by_entry.items())),
        "counts_by_exit_date": dict(sorted(counts_by_exit.items())),
        "counts_by_horizon": dict(sorted(counts_by_horizon.items())),
        "label_distribution": dict(
            sorted(
                Counter(
                    str(item["row"].get("label_up_5d")) for item in validated
                ).items()
            )
        ),
        "missing_temporal_fields": missing,
        "rejected_row_counts": {},
        "canonical_multiplicity_distribution": dict(
            sorted(
                Counter(
                    str(item["row"].get("raw_request_count", 1)) for item in validated
                ).items()
            )
        ),
        "earliest_by_field": {
            field: min(item["times"][field] for item in validated).isoformat()
            for field in REQUIRED_TIMING_FIELDS
        },
        "latest_by_field": {
            field: max(item["times"][field] for item in validated).isoformat()
            for field in REQUIRED_TIMING_FIELDS
        },
        "configured_train_ratio": train_ratio,
        "embargo_sessions": embargo_sessions,
        "minimum_train_dates": min_train_dates,
        "minimum_test_dates": min_test_dates,
        "minimum_train_observations": min_train_rows,
        "minimum_test_observations": min_test_rows,
        "candidate_boundaries": [
            {
                key: value
                for key, value in candidate.items()
                if key not in {"train_ids", "test_ids"}
            }
            for candidate in candidates
        ],
        "selected_boundary_date": selected["boundary_date"] if selected else None,
        "status": "FEASIBLE" if selected else NO_CANDIDATE_STATUS,
        "failure_reason": (
            None if selected else "no_boundary_satisfies_frozen_temporal_minimums"
        ),
        "available_date_span": [dates[0], dates[-1]] if dates else None,
        "recommended_additional_data_requirement": (
            None
            if selected
            else "add independent eligible exchange sessions and canonical observations"
        ),
    }
    plan_core = {
        "schema_version": TEMPORAL_SPLIT_PLAN_SCHEMA,
        "contract_version": TEMPORAL_SPLIT_CONTRACT_VERSION,
        "input_sha256": input_sha256,
        "status": diagnostics["status"],
        "boundary_date": selected["boundary_date"] if selected else None,
        "train_canonical_observation_ids": selected["train_ids"] if selected else [],
        "test_canonical_observation_ids": selected["test_ids"] if selected else [],
        "embargo_sessions": embargo_sessions,
        "calendar_identifier": calendar.identifier,
        "candidate_win": False,
        "automatic_promotion": False,
        "ready_for_live_routing": False,
        "research_only": True,
    }
    plan = {**plan_core, "plan_sha256": canonical_json_hash(plan_core)}
    diagnostics["plan_sha256"] = plan["plan_sha256"]
    return TemporalSplitResult(plan=plan, diagnostics=diagnostics)


def validate_split_plan(
    plan: Mapping[str, Any], *, input_path: Path
) -> tuple[set[str], set[str]]:
    if plan.get("schema_version") != TEMPORAL_SPLIT_PLAN_SCHEMA:
        raise TemporalSplitDataError("incompatible_split_plan_schema")
    if plan.get("contract_version") != TEMPORAL_SPLIT_CONTRACT_VERSION:
        raise TemporalSplitDataError("incompatible_split_contract")
    if plan.get("status") != "FEASIBLE":
        raise TemporalSplitDataError("split_plan_is_not_feasible")
    if plan.get("input_sha256") != file_sha256(input_path):
        raise TemporalSplitDataError("split_plan_input_hash_mismatch")
    core = {key: value for key, value in plan.items() if key != "plan_sha256"}
    if plan.get("plan_sha256") != canonical_json_hash(core):
        raise TemporalSplitDataError("split_plan_hash_mismatch")
    train = set(plan.get("train_canonical_observation_ids") or [])
    test = set(plan.get("test_canonical_observation_ids") or [])
    if not train or not test or train & test:
        raise TemporalSplitDataError("invalid_split_plan_membership")
    return train, test
