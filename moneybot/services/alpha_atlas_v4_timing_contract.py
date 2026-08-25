"""Alpha Atlas V4 prediction/execution timing record.

This module is deliberately not imported by any V3/V3.1 or production path.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping

ALPHA_ATLAS_V4_TIMING_CONTRACT_VERSION = (
    "alpha-atlas-v4-prediction-execution-contract.v1"
)


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")


def _require_utc(name: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware UTC datetime")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be UTC")


@dataclass(frozen=True)
class AlphaAtlasV4TimingRecord:
    """Validated provenance envelope for one future V4 research/shadow row."""

    decision_id: str
    symbol: str
    point_in_time_symbol_id: str
    exchange: str
    trading_calendar: str
    model_feature_contract_version: str
    decision_at: datetime
    feature_cutoff_at: datetime
    latest_source_bar_at: Mapping[str, datetime]
    entry_at: datetime
    label_start_at: datetime
    exit_at: datetime
    entry_price_source: str
    exit_price_source: str
    data_provider_id: str
    corporate_action_adjustment_ids: tuple[str, ...]
    staleness_status: str
    rejection_reason: str | None
    code_commit: str
    dataset_manifest_hash: str
    transaction_cost_bps: float | None
    entry_slippage_bps: float | None
    exit_slippage_bps: float | None
    contract_version: str = ALPHA_ATLAS_V4_TIMING_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.contract_version != ALPHA_ATLAS_V4_TIMING_CONTRACT_VERSION:
            raise ValueError("unsupported Alpha Atlas V4 timing contract version")
        for name in (
            "decision_id",
            "symbol",
            "point_in_time_symbol_id",
            "exchange",
            "trading_calendar",
            "model_feature_contract_version",
            "entry_price_source",
            "exit_price_source",
            "data_provider_id",
            "staleness_status",
            "code_commit",
            "dataset_manifest_hash",
        ):
            _require_text(name, getattr(self, name))
        for name in (
            "decision_at",
            "feature_cutoff_at",
            "entry_at",
            "label_start_at",
            "exit_at",
        ):
            _require_utc(name, getattr(self, name))
        if (
            not isinstance(self.latest_source_bar_at, Mapping)
            or not self.latest_source_bar_at
        ):
            raise ValueError(
                "latest_source_bar_at is required for every feature family"
            )
        for family, timestamp in self.latest_source_bar_at.items():
            _require_text("feature family", family)
            _require_utc(f"latest_source_bar_at[{family}]", timestamp)
            if timestamp > self.feature_cutoff_at:
                raise ValueError(
                    "source-bar timestamps must not exceed feature_cutoff_at"
                )
        if self.feature_cutoff_at > self.decision_at:
            raise ValueError("feature_cutoff_at must be <= decision_at")
        if self.decision_at >= self.label_start_at:
            raise ValueError("decision_at must be < label_start_at")
        if not (
            self.decision_at < self.entry_at
            and self.entry_at == self.label_start_at
            and self.label_start_at < self.exit_at
        ):
            raise ValueError(
                "daily-swing ordering requires decision_at < entry_at == "
                "label_start_at < exit_at"
            )
        if not isinstance(self.corporate_action_adjustment_ids, tuple):
            raise ValueError("corporate_action_adjustment_ids must be a tuple")
        for name in (
            "transaction_cost_bps",
            "entry_slippage_bps",
            "exit_slippage_bps",
        ):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, (int, float)) or value < 0):
                raise ValueError(f"{name} must be a non-negative number or None")

    def to_dict(self) -> dict[str, Any]:
        """Return a stable JSON-compatible representation."""
        payload = asdict(self)
        for name in (
            "decision_at",
            "feature_cutoff_at",
            "entry_at",
            "label_start_at",
            "exit_at",
        ):
            payload[name] = getattr(self, name).isoformat()
        payload["latest_source_bar_at"] = {
            family: timestamp.isoformat()
            for family, timestamp in self.latest_source_bar_at.items()
        }
        payload["corporate_action_adjustment_ids"] = list(
            self.corporate_action_adjustment_ids
        )
        return payload
