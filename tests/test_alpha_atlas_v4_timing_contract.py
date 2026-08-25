from dataclasses import fields
from datetime import datetime, timezone

import pytest

from moneybot.services.alpha_atlas_v4_timing_contract import (
    ALPHA_ATLAS_V4_TIMING_CONTRACT_VERSION,
    AlphaAtlasV4TimingRecord,
)


def _record(**overrides):
    values = {
        "decision_id": "decision-123",
        "symbol": "AAPL",
        "point_in_time_symbol_id": "massive:2026-08-25:AAPL",
        "exchange": "XNAS",
        "trading_calendar": "XNYS",
        "model_feature_contract_version": "alpha-atlas-v4-features.v1",
        "decision_at": datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
        "feature_cutoff_at": datetime(2026, 8, 25, 11, 59, tzinfo=timezone.utc),
        "latest_source_bar_at": {
            "symbol_daily": datetime(2026, 8, 24, 20, 0, tzinfo=timezone.utc),
            "spy_daily": datetime(2026, 8, 24, 20, 0, tzinfo=timezone.utc),
        },
        "entry_at": datetime(2026, 8, 25, 13, 30, tzinfo=timezone.utc),
        "label_start_at": datetime(2026, 8, 25, 13, 30, tzinfo=timezone.utc),
        "exit_at": datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc),
        "entry_price_source": "official_regular_session_open",
        "exit_price_source": "official_regular_session_close",
        "data_provider_id": "massive",
        "corporate_action_adjustment_ids": (),
        "staleness_status": "fresh",
        "rejection_reason": None,
        "code_commit": "abc123",
        "dataset_manifest_hash": "sha256:123",
        "transaction_cost_bps": None,
        "entry_slippage_bps": None,
        "exit_slippage_bps": None,
    }
    values.update(overrides)
    return AlphaAtlasV4TimingRecord(**values)


def test_valid_premarket_timing_record():
    assert _record().entry_at.hour == 13


def test_valid_regular_session_timing_record():
    record = _record(
        decision_at=datetime(2026, 8, 25, 15, 0, tzinfo=timezone.utc),
        feature_cutoff_at=datetime(2026, 8, 25, 14, 59, tzinfo=timezone.utc),
        entry_at=datetime(2026, 8, 26, 13, 30, tzinfo=timezone.utc),
        label_start_at=datetime(2026, 8, 26, 13, 30, tzinfo=timezone.utc),
        exit_at=datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc),
    )
    assert record.feature_cutoff_at < record.decision_at


def test_valid_after_hours_timing_record():
    record = _record(
        decision_at=datetime(2026, 8, 25, 21, 0, tzinfo=timezone.utc),
        feature_cutoff_at=datetime(2026, 8, 25, 20, 59, tzinfo=timezone.utc),
        latest_source_bar_at={
            "symbol_daily": datetime(2026, 8, 25, 20, 0, tzinfo=timezone.utc)
        },
        entry_at=datetime(2026, 8, 26, 13, 30, tzinfo=timezone.utc),
        label_start_at=datetime(2026, 8, 26, 13, 30, tzinfo=timezone.utc),
        exit_at=datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc),
    )
    assert record.latest_source_bar_at["symbol_daily"].day == 25


@pytest.mark.parametrize(
    "field",
    ["decision_at", "feature_cutoff_at", "entry_at", "label_start_at", "exit_at"],
)
def test_rejects_naive_datetimes(field):
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        _record(**{field: datetime(2026, 8, 25, 12, 0)})


def test_rejects_feature_cutoff_after_decision():
    with pytest.raises(ValueError, match="feature_cutoff_at must be <="):
        _record(feature_cutoff_at=datetime(2026, 8, 25, 12, 1, tzinfo=timezone.utc))


def test_rejects_decision_not_before_label_start():
    stamp = datetime(2026, 8, 25, 13, 30, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="decision_at must be <"):
        _record(decision_at=stamp)


@pytest.mark.parametrize(
    "overrides",
    [
        {"entry_at": datetime(2026, 8, 25, 13, 29, tzinfo=timezone.utc)},
        {"exit_at": datetime(2026, 8, 25, 13, 30, tzinfo=timezone.utc)},
    ],
)
def test_rejects_invalid_entry_label_exit_ordering(overrides):
    with pytest.raises(ValueError, match="daily-swing ordering"):
        _record(**overrides)


def test_schema_version_serialization():
    payload = _record().to_dict()
    assert payload["contract_version"] == ALPHA_ATLAS_V4_TIMING_CONTRACT_VERSION
    assert payload["decision_at"].endswith("+00:00")
    assert payload["corporate_action_adjustment_ids"] == []


@pytest.mark.parametrize(
    "missing", ["decision_id", "data_provider_id", "dataset_manifest_hash"]
)
def test_missing_required_provenance_fails_closed(missing):
    kwargs = {missing: ""}
    with pytest.raises(ValueError, match=f"{missing} is required"):
        _record(**kwargs)


def test_all_provenance_fields_are_constructor_fields():
    names = {field.name for field in fields(AlphaAtlasV4TimingRecord)}
    assert {"rejection_reason", "code_commit", "latest_source_bar_at"} <= names
