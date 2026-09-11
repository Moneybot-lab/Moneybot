from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta, timezone

import pytest

from moneybot.services.alpha_atlas_v4_temporal_split import (
    NO_CANDIDATE_STATUS,
    TemporalSplitDataError,
    _embargo_session_dates,
    _walk_forward_feature_session,
    plan_v4_walk_forward_folds,
    plan_v4_temporal_split,
    validate_split_plan,
)
from moneybot.services.market_data_providers import ExchangeCalendar

CALENDAR = ExchangeCalendar()


def _advance(session: date, count: int) -> date:
    current = session
    for _ in range(count):
        current = CALENDAR.next_session(current)
    return current


def _sessions(count: int) -> list[date]:
    values = []
    current = date(2026, 1, 2)
    while len(values) < count:
        if CALENDAR.is_trading_day(current):
            values.append(current)
        current += timedelta(days=1)
    return values


def _row(session: date, index: int, *, horizon: int = 5, raw_count: int = 1):
    entry_session = CALENDAR.next_session(session)
    exit_session = _advance(entry_session, horizon - 1)
    cutoff = CALENDAR.session_close(session)
    decision = cutoff + timedelta(minutes=1)
    entry = CALENDAR.session_open(entry_session)
    exit_at = CALENDAR.session_close(exit_session)
    return {
        "canonical_observation_id": f"obs-{session.isoformat()}-{index}",
        "canonical_observation_schema_version": "alpha-atlas-v4-canonical-observations.v2",
        "canonicalization_contract_version": "alpha-atlas-v4-canonical-observation.v2",
        "timing_contract_version": "alpha-atlas-v4-prediction-execution-contract.v1",
        "model_feature_contract_version": "alpha-atlas-v4-features.v2",
        "model_sample_weight": 1.0,
        "raw_request_count": raw_count,
        "symbol": f"SYM{index % 4}",
        "decision_at": decision.isoformat(),
        "feature_cutoff_at": cutoff.isoformat(),
        "entry_at": entry.isoformat(),
        "label_start_at": entry.isoformat(),
        "exit_at": exit_at.isoformat(),
        "entry_session_date": entry_session.isoformat(),
        "exit_session_date": exit_session.isoformat(),
        "label_horizon_sessions": horizon,
        "exchange_calendar": CALENDAR.identifier,
        "label_up_5d": index % 2,
        "return_5d": 0.01 if index % 2 else -0.01,
    }


def _dataset(days=30, rows_per_day=10, *, mixed=False):
    return [
        _row(session, index, horizon=(10 if mixed and index % 3 == 0 else 5))
        for session in _sessions(days)
        for index in range(rows_per_day)
    ]


def _plan(rows, **kwargs):
    return plan_v4_temporal_split(
        rows,
        input_sha256="fixture-sha256",
        min_observations=kwargs.pop("min_observations", 40),
        **kwargs,
    )


def test_long_canonical_dataset_produces_nonempty_date_grouped_split():
    result = _plan(_dataset())
    assert result.plan["status"] == "FEASIBLE"
    assert result.plan["train_canonical_observation_ids"]
    assert result.plan["test_canonical_observation_ids"]
    membership = {
        identifier: side
        for side, identifiers in (
            ("train", result.plan["train_canonical_observation_ids"]),
            ("test", result.plan["test_canonical_observation_ids"]),
        )
        for identifier in identifiers
    }
    for session in _sessions(30):
        sides = {
            membership.get(f"obs-{session.isoformat()}-{index}") for index in range(10)
        } - {None}
        assert len(sides) <= 1


def test_input_order_request_multiplicity_and_outcomes_do_not_change_plan():
    rows = _dataset()
    baseline = _plan(rows).plan
    changed = [
        {
            **row,
            "raw_request_count": 99,
            "label_up_5d": 1 - row["label_up_5d"],
            "return_5d": -row["return_5d"],
        }
        for row in rows
    ]
    random.Random(7).shuffle(changed)
    assert _plan(changed).plan == baseline


def test_mixed_five_and_ten_session_intervals_use_actual_exits_without_overlap():
    rows = _dataset(days=35, mixed=True)
    result = _plan(rows)
    ids = {row["canonical_observation_id"]: row for row in rows}
    train = [ids[value] for value in result.plan["train_canonical_observation_ids"]]
    test = [ids[value] for value in result.plan["test_canonical_observation_ids"]]
    assert {row["label_horizon_sessions"] for row in rows} == {5, 10}
    assert max(row["exit_at"] for row in train) < min(row["entry_at"] for row in test)
    assert result.diagnostics["counts_by_horizon"] == {"10": 140, "5": 210}


def test_embargo_counts_exchange_sessions_not_weekends_or_holidays():
    result = _plan(_dataset(), embargo_sessions=2)
    boundary = date.fromisoformat(result.plan["boundary_date"])
    first_eligible = CALENDAR.next_session(CALENDAR.next_session(boundary))
    selected = set(result.plan["test_canonical_observation_ids"])
    assert not any(f"obs-{boundary.isoformat()}-" in value for value in selected)
    assert not any(
        f"obs-{CALENDAR.next_session(boundary).isoformat()}-" in value
        for value in selected
    )
    assert any(f"obs-{first_eligible.isoformat()}-" in value for value in selected)


def test_closest_feasible_boundary_is_deterministic_and_records_rejections():
    result = _plan(
        _dataset(days=18, rows_per_day=5),
        train_ratio=0.8,
        min_observations=20,
    )
    feasible = [
        item for item in result.diagnostics["candidate_boundaries"] if item["feasible"]
    ]
    expected = min(
        feasible, key=lambda item: (item["distance_from_ratio"], item["boundary_date"])
    )
    assert result.plan["boundary_date"] == expected["boundary_date"]
    assert any(
        not item["feasible"] for item in result.diagnostics["candidate_boundaries"]
    )


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("entry_at", None, "missing_required"),
        ("timing_contract_version", "wrong.v1", "incompatible_contract"),
        ("model_feature_contract_version", "wrong.v1", "incompatible_contract"),
        ("model_sample_weight", 2.0, "non_unit"),
    ],
)
def test_invalid_or_mixed_temporal_data_fails_closed(field, value, match):
    rows = _dataset(days=10, rows_per_day=2)
    rows[0][field] = value
    with pytest.raises(TemporalSplitDataError, match=match):
        _plan(rows, min_observations=5)


def test_short_valid_dataset_returns_controlled_no_candidate():
    result = _plan(_dataset(days=4, rows_per_day=2), min_observations=20)
    assert result.plan["status"] == NO_CANDIDATE_STATUS
    assert result.plan["candidate_win"] is False
    assert result.plan["automatic_promotion"] is False
    assert result.plan["ready_for_live_routing"] is False
    assert result.plan["train_canonical_observation_ids"] == []
    assert result.diagnostics["failure_reason"]


def test_excessive_request_collapse_is_visible_but_not_model_weight(tmp_path):
    rows = _dataset(days=12, rows_per_day=4)
    rows = [{**row, "raw_request_count": 1000} for row in rows]
    result = _plan(rows, min_observations=10)
    assert result.diagnostics["canonical_multiplicity_distribution"] == {
        "1000": len(rows)
    }
    assert result.diagnostics["canonical_observations"] == len(rows)


def test_plan_and_input_hashes_are_verified(tmp_path):
    rows = _dataset(days=15, rows_per_day=4)
    input_path = tmp_path / "all.jsonl"
    input_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    from moneybot.services.alpha_atlas_v4_temporal_split import file_sha256

    result = plan_v4_temporal_split(
        rows, input_sha256=file_sha256(input_path), min_observations=10
    )
    train, test = validate_split_plan(result.plan, input_path=input_path)
    assert train and test and not train & test
    tampered = {**result.plan, "boundary_date": "2099-01-01"}
    with pytest.raises(TemporalSplitDataError, match="plan_hash"):
        validate_split_plan(tampered, input_path=input_path)


def test_challenger_consumes_exact_frozen_v4_plan(tmp_path):
    from scripts.train_challenger_suite import train_challenger_suite

    rows = [
        {
            **row,
            "feature_close": 100.0 + index,
            "feature_return_1d_lagged": (index % 5) / 100,
            "feature_volume": 1000 + index,
        }
        for index, row in enumerate(_dataset(days=18, rows_per_day=4))
    ]
    input_path = tmp_path / "all.jsonl"
    input_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    from moneybot.services.alpha_atlas_v4_temporal_split import file_sha256

    result = plan_v4_temporal_split(
        rows, input_sha256=file_sha256(input_path), min_observations=10
    )
    plan_path = tmp_path / "challenger_split_plan.json"
    plan_path.write_text(json.dumps(result.plan))
    manifest = train_challenger_suite(
        input_path,
        tmp_path / "models",
        min_rows=10,
        split_plan_path=plan_path,
    )
    assert (
        manifest["temporal_validation_policy"]["split_plan_sha256"]
        == result.plan["plan_sha256"]
    )
    assert (
        manifest["temporal_validation_policy"]["split_input_sha256"]
        == result.plan["input_sha256"]
    )


def test_v4_walk_forward_is_grouped_deterministic_and_holdout_isolated():
    rows = _dataset(days=24, rows_per_day=3)
    development_ids = {row["canonical_observation_id"] for row in rows[: 18 * 3]}
    holdout_ids = {row["canonical_observation_id"] for row in rows} - development_ids

    expected = plan_v4_walk_forward_folds(
        rows,
        development_ids=development_ids,
        final_holdout_ids=holdout_ids,
    )
    shuffled = list(rows)
    random.Random(91).shuffle(shuffled)
    actual = plan_v4_walk_forward_folds(
        shuffled,
        development_ids=development_ids,
        final_holdout_ids=holdout_ids,
    )

    holdout_changed = [
        (
            {
                **row,
                "label_up_5d": 1 - row["label_up_5d"],
                "return_5d": -row["return_5d"],
                "feature_close": 1_000_000.0,
            }
            if row["canonical_observation_id"] in holdout_ids
            else row
        )
        for row in rows
    ]
    changed = plan_v4_walk_forward_folds(
        holdout_changed,
        development_ids=development_ids,
        final_holdout_ids=holdout_ids,
    )

    assert actual == expected
    assert changed == expected
    assert expected
    for fold in expected:
        members = set(fold["train_ids"]) | set(fold["validation_ids"])
        assert not members & holdout_ids
        assert fold["final_holdout_overlap_count"] == 0
        assert not fold["invalid_session_groups"]
        assert all(
            CALENDAR.is_trading_day(date.fromisoformat(key))
            for key in (
                fold["train_first_group"],
                fold["train_last_group"],
                fold["validation_first_group"],
                fold["validation_last_group"],
            )
        )
        for session in _sessions(18):
            group = {f"obs-{session.isoformat()}-{index}" for index in range(3)}
            assert not (
                group & set(fold["train_ids"]) and group - set(fold["train_ids"])
            )
            assert not (
                group & set(fold["validation_ids"])
                and group - set(fold["validation_ids"])
            )


def test_v4_walk_forward_purges_same_day_exit_and_decision_to_entry_overlap():
    rows = _dataset(days=18, rows_per_day=2)
    ids = {row["canonical_observation_id"] for row in rows}
    baseline = plan_v4_walk_forward_folds(rows, development_ids=ids, embargo_sessions=0)
    first = baseline[0]
    validation = next(
        row
        for row in rows
        if row["canonical_observation_id"] in first["validation_ids"]
    )
    decision = datetime.fromisoformat(validation["decision_at"])
    entry = datetime.fromisoformat(validation["entry_at"])
    candidates = [
        row for row in rows if row["canonical_observation_id"] in first["train_ids"]
    ]

    same_day = candidates[-1]
    same_day["exit_at"] = entry.replace(hour=20, minute=0).isoformat()
    between = candidates[-2]
    between["exit_at"] = (decision + (entry - decision) / 2).isoformat()
    repaired = plan_v4_walk_forward_folds(
        rows, development_ids=ids, embargo_sessions=0
    )[0]

    assert same_day["canonical_observation_id"] not in repaired["train_ids"]
    assert between["canonical_observation_id"] not in repaired["train_ids"]
    assert repaired["purged_train_rows"] >= 2
    assert (
        datetime.fromisoformat(repaired["latest_train_label_completion_at"])
        < decision
        < entry
    )


def test_five_session_horizon_crosses_weekend_and_market_holiday():
    row = _row(date(2026, 7, 1), 0, horizon=5)
    # Entry is July 2; July 3 is the observed Independence Day holiday.
    assert row["entry_session_date"] == "2026-07-02"
    assert row["exit_session_date"] == "2026-07-09"
    assert datetime.fromisoformat(row["exit_at"]).tzinfo == timezone.utc


def test_v4_walk_forward_zero_history_and_bad_timing_fail_closed():
    short = _dataset(days=5, rows_per_day=2)
    assert (
        plan_v4_walk_forward_folds(
            short, development_ids={row["canonical_observation_id"] for row in short}
        )
        == []
    )
    malformed = _dataset(days=8, rows_per_day=2)
    malformed[0]["decision_at"] = "not-a-timestamp"
    with pytest.raises(
        TemporalSplitDataError, match="invalid_timing_field:decision_at"
    ):
        plan_v4_walk_forward_folds(
            malformed,
            development_ids={row["canonical_observation_id"] for row in malformed},
        )


def test_walk_forward_session_mapping_handles_sunday_holiday_and_market_phases():
    def mapped(local_value: str) -> tuple[str, str]:
        instant = datetime.fromisoformat(local_value).replace(tzinfo=CALENDAR.timezone)
        return _walk_forward_feature_session(CALENDAR, instant.astimezone(timezone.utc))

    assert mapped("2026-05-31T12:00:00") == ("2026-05-29", "weekend_or_holiday")
    assert mapped("2026-06-01T08:00:00") == ("2026-05-29", "premarket")
    assert mapped("2026-06-01T10:00:00") == ("2026-06-01", "regular")
    assert mapped("2026-06-01T17:00:00") == ("2026-06-01", "after")
    assert mapped("2026-07-03T12:00:00") == ("2026-07-02", "weekend_or_holiday")
    assert mapped("2026-07-04T12:00:00") == ("2026-07-02", "weekend_or_holiday")
    assert mapped("2026-07-05T12:00:00") == ("2026-07-02", "weekend_or_holiday")


def test_walk_forward_session_mapping_honors_dst_and_early_close():
    winter = CALENDAR.session_open(date(2026, 1, 5))
    summer = CALENDAR.session_open(date(2026, 6, 1))
    assert (winter.hour, summer.hour) == (14, 13)
    assert _walk_forward_feature_session(CALENDAR, winter)[0] == "2026-01-05"
    assert _walk_forward_feature_session(CALENDAR, summer)[0] == "2026-06-01"

    early_close = date(2026, 11, 27)
    cutoff = CALENDAR.session_close(early_close) + timedelta(minutes=30)
    assert CALENDAR.is_early_close(early_close)
    assert _walk_forward_feature_session(CALENDAR, cutoff) == (
        "2026-11-27",
        "after",
    )


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (0, []),
        (1, ["2026-07-02"]),
        (2, ["2026-07-02", "2026-07-06"]),
        (3, ["2026-07-02", "2026-07-06", "2026-07-07"]),
    ],
)
def test_embargo_counts_exchange_sessions_not_observed_dates(count, expected):
    assert _embargo_session_dates(CALENDAR, "2026-07-02", count) == expected


def test_missing_observed_session_counts_without_removing_extra_group():
    sessions = _sessions(18)
    rows = _dataset(days=18, rows_per_day=2)
    # With one group absent the 17-group fixture's first boundary is Jan 20.
    # Remove every observation from the next valid session to reproduce a gap.
    missing_session = sessions[12]
    rows = [
        row
        for row in rows
        if not row["canonical_observation_id"].startswith(
            f"obs-{missing_session.isoformat()}-"
        )
    ]
    ids = {row["canonical_observation_id"] for row in rows}
    fold = plan_v4_walk_forward_folds(rows, development_ids=ids, embargo_sessions=2)[0]

    assert fold["embargo_session_dates"] == [
        "2026-01-20",
        "2026-01-21",
    ]
    assert fold["observed_embargo_groups"] == ["2026-01-20"]
    assert fold["validation_first_group"] == "2026-01-22"
    assert fold["embargoed_validation_rows"] == 2


def test_embargo_exhaustion_is_explicitly_unusable():
    rows = _dataset(days=6, rows_per_day=2)
    ids = {row["canonical_observation_id"] for row in rows}
    fold = plan_v4_walk_forward_folds(rows, development_ids=ids, embargo_sessions=1)[0]
    assert fold["usable"] is False
    assert fold["unusable_reason"] == (
        "empty_training_after_timing_purge;empty_validation_after_session_embargo"
    )
    assert fold["embargo_session_dates"] == [fold["planned_validation_first_session"]]


def test_walk_forward_rejects_missing_or_ambiguous_calendar_evidence():
    rows = _dataset(days=8, rows_per_day=2)
    ids = {row["canonical_observation_id"] for row in rows}
    rows[0]["exchange_calendar"] = "unknown-calendar"
    with pytest.raises(TemporalSplitDataError, match="incompatible_exchange_calendar"):
        plan_v4_walk_forward_folds(rows, development_ids=ids)
