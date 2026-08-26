"""Research-only Alpha Atlas V4 canonical observations and evaluation utilities."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

CANONICAL_OBSERVATION_CONTRACT_VERSION = "alpha-atlas-v4-canonical-observation.v1"
CANONICAL_OBSERVATION_SCHEMA_VERSION = "alpha-atlas-v4-canonical-observations.v1"
CANONICAL_HASH_POLICY = "sha256-canonical-json-v1"
V4_RAW_ROW_SCHEMA = "massive-decision-training-rows.v4"

_KEY_FIELDS = (
    "point_in_time_symbol_id",
    "feature_cutoff_at",
    "entry_at",
    "exit_at",
    "label_horizon_sessions",
    "lane",
    "universe_policy_version",
    "timing_contract_version",
    "model_feature_contract_version",
    "execution_cost_policy_version",
)
_REQUEST_FIELDS = {
    "decision_id",
    "request_id",
    "endpoint",
    "decision_source",
    "decision_at",
    "ts",
    "user_id",
    "watchlist_id",
    "portfolio_id",
    "quick_ask_id",
    "alert_id",
    "ui_surface",
}
_MATERIAL_EXACT_FIELDS = {
    "feature_family_source_at",
    "feature_family_available_at",
    "entry_at",
    "label_start_at",
    "exit_at",
    "raw_entry_price",
    "adjusted_entry_price",
    "raw_exit_price",
    "adjusted_exit_price",
    "label_horizon_sessions",
    "label_split_ids",
    "label_split_adjustment_factor",
    "lane",
    "universe_policy_version",
    "execution_cost_policy_version",
    "timing_contract_version",
    "model_feature_contract_version",
    "canonical_dataset_schema_version",
}


class CanonicalizationError(ValueError):
    """Raised when raw V4 rows cannot be canonicalized without guessing."""

    def __init__(self, reason: str, *, canonical_observation_id: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.canonical_observation_id = canonical_observation_id
        self.diagnostics = {
            "conflict_count": int(reason.startswith("conflicting_material_fields:")),
            "conflicts_by_reason": (
                {reason: 1} if reason.startswith("conflicting_material_fields:") else {}
            ),
            "canonical_observation_id": canonical_observation_id,
        }


@dataclass(frozen=True)
class CanonicalizationResult:
    observations: tuple[dict[str, Any], ...]
    request_map: tuple[dict[str, Any], ...]
    diagnostics: dict[str, Any]


def _utc_iso(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CanonicalizationError(f"missing_required_field:{field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CanonicalizationError(f"invalid_utc_timestamp:{field}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CanonicalizationError(f"naive_timestamp:{field}")
    if parsed.utcoffset().total_seconds() != 0:
        raise CanonicalizationError(f"non_utc_timestamp:{field}")
    return parsed.astimezone(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_economic_key(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return the versioned economic identity, excluding request metadata."""
    if row.get("canonical_dataset_schema_version") != V4_RAW_ROW_SCHEMA:
        raise CanonicalizationError("incompatible_raw_dataset_schema")
    key = {field: row.get(field) for field in _KEY_FIELDS}
    for field in ("point_in_time_symbol_id", "lane", "universe_policy_version"):
        if not isinstance(key[field], str) or not key[field].strip():
            raise CanonicalizationError(f"missing_required_field:{field}")
    for field in ("feature_cutoff_at", "entry_at", "exit_at"):
        key[field] = _utc_iso(key[field], field)
    if (
        not isinstance(key["label_horizon_sessions"], int)
        or key["label_horizon_sessions"] <= 0
    ):
        raise CanonicalizationError("invalid_label_horizon_sessions")
    for field, expected in (
        ("timing_contract_version", "alpha-atlas-v4-prediction-execution-contract.v1"),
        ("model_feature_contract_version", "alpha-atlas-v4-features.v1"),
    ):
        if key[field] != expected:
            raise CanonicalizationError(f"incompatible_version:{field}")
    cost = key["execution_cost_policy_version"]
    if cost is not None and (not isinstance(cost, str) or not cost.strip()):
        raise CanonicalizationError("invalid_execution_cost_policy_version")
    return {
        "canonicalization_contract_version": CANONICAL_OBSERVATION_CONTRACT_VERSION,
        **key,
    }


def canonical_observation_id(row: Mapping[str, Any]) -> str:
    payload = _canonical_json(canonical_economic_key(row)).encode("utf-8")
    return f"aav4obs_{hashlib.sha256(payload).hexdigest()}"


def _material_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    names = sorted(
        name
        for name in row
        if name.startswith("feature_")
        or name.startswith("label_")
        or name.startswith("return_")
        or name in _MATERIAL_EXACT_FIELDS
    )
    return {name: row.get(name) for name in names}


def _request_identity(row: Mapping[str, Any], index: int) -> str:
    value = row.get("decision_id") or row.get("request_id")
    if not isinstance(value, str) or not value.strip():
        raise CanonicalizationError(f"missing_request_id:row_{index}")
    return value.strip()


def canonicalize_v4_rows(rows: Iterable[Mapping[str, Any]]) -> CanonicalizationResult:
    """Collapse compatible requests and fail closed on economic conflicts."""
    raw = [dict(row) for row in rows]
    groups: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    seen_requests: set[str] = set()
    endpoints: Counter[str] = Counter()
    for index, row in enumerate(raw):
        if row.get("canonicalization_contract_version") not in {
            None,
            CANONICAL_OBSERVATION_CONTRACT_VERSION,
        }:
            raise CanonicalizationError(
                "mixed_or_incompatible_canonicalization_version"
            )
        request_id = _request_identity(row, index)
        if request_id in seen_requests:
            raise CanonicalizationError("duplicate_request_id")
        seen_requests.add(request_id)
        observation_id = canonical_observation_id(row)
        groups.setdefault(observation_id, []).append((index, row))
        endpoints[str(row.get("endpoint") or "unknown")] += 1

    observations: list[dict[str, Any]] = []
    request_map: list[dict[str, Any]] = []
    multiplicities: Counter[int] = Counter()
    counts_by_symbol: Counter[str] = Counter()
    counts_by_cutoff_date: Counter[str] = Counter()
    counts_by_lane: Counter[str] = Counter()
    counts_by_horizon: Counter[str] = Counter()
    for observation_id in sorted(groups):
        members = groups[observation_id]
        reference = _material_payload(members[0][1])
        for _, candidate in members[1:]:
            other = _material_payload(candidate)
            if other != reference:
                differing = sorted(
                    name
                    for name in set(reference) | set(other)
                    if reference.get(name) != other.get(name)
                )
                reason = "conflicting_material_fields:" + ",".join(differing[:10])
                raise CanonicalizationError(
                    reason, canonical_observation_id=observation_id
                )
        ordered_members = sorted(
            members, key=lambda item: _request_identity(item[1], item[0])
        )
        representative = dict(ordered_members[0][1])
        decisions = sorted(
            _utc_iso(member.get("decision_at"), "decision_at")
            for _, member in ordered_members
        )
        for field in _REQUEST_FIELDS:
            representative.pop(field, None)
        representative.update(
            {
                "canonical_observation_id": observation_id,
                "canonicalization_contract_version": CANONICAL_OBSERVATION_CONTRACT_VERSION,
                "canonical_observation_schema_version": CANONICAL_OBSERVATION_SCHEMA_VERSION,
                "canonical_hash_policy": CANONICAL_HASH_POLICY,
                "raw_request_count": len(ordered_members),
                "model_sample_weight": 1.0,
                "originating_decision_at_min": decisions[0],
                "originating_decision_at_max": decisions[-1],
            }
        )
        observations.append(representative)
        multiplicities[len(ordered_members)] += 1
        counts_by_symbol[str(representative.get("symbol") or "unknown")] += 1
        counts_by_cutoff_date[str(representative["feature_cutoff_at"])[:10]] += 1
        counts_by_lane[str(representative["lane"])] += 1
        counts_by_horizon[str(representative["label_horizon_sessions"])] += 1
        for index, member in ordered_members:
            request_map.append(
                {
                    "request_id": _request_identity(member, index),
                    "endpoint": str(member.get("endpoint") or "unknown"),
                    "decision_at": _utc_iso(member.get("decision_at"), "decision_at"),
                    "canonical_observation_id": observation_id,
                    "canonicalization_contract_version": CANONICAL_OBSERVATION_CONTRACT_VERSION,
                    "decision_source": member.get("decision_source"),
                }
            )
    request_map.sort(key=lambda row: row["request_id"])
    duplicates = sum(count - 1 for count in (len(group) for group in groups.values()))
    largest = sorted(
        (
            {"canonical_observation_id": key, "raw_request_count": len(value)}
            for key, value in groups.items()
        ),
        key=lambda row: (-row["raw_request_count"], row["canonical_observation_id"]),
    )[:10]
    diagnostics = {
        "canonicalization_contract_version": CANONICAL_OBSERVATION_CONTRACT_VERSION,
        "raw_request_rows": len(raw),
        "canonical_observations": len(observations),
        "duplicate_rows_collapsed": duplicates,
        "duplicate_group_count": sum(len(group) > 1 for group in groups.values()),
        "multiplicity_distribution": {
            str(key): value for key, value in sorted(multiplicities.items())
        },
        "largest_duplicate_groups": largest,
        "conflict_count": 0,
        "conflicts_by_reason": {},
        "counts_by_symbol": dict(sorted(counts_by_symbol.items())),
        "counts_by_cutoff_date": dict(sorted(counts_by_cutoff_date.items())),
        "counts_by_lane": dict(sorted(counts_by_lane.items())),
        "counts_by_horizon": dict(sorted(counts_by_horizon.items())),
        "counts_by_endpoint": dict(sorted(endpoints.items())),
        "model_sample_weight_sum": float(len(observations)),
    }
    return CanonicalizationResult(tuple(observations), tuple(request_map), diagnostics)


def evaluate_canonical_observations(
    observations: Iterable[Mapping[str, Any]],
    scores: Mapping[str, float],
    *,
    threshold: float = 0.5,
) -> dict[str, Any]:
    """Compute research metrics exactly once per canonical observation."""
    rows = _validated_observations(observations)
    ids = [str(row.get("canonical_observation_id") or "") for row in rows]
    if len(ids) != len(set(ids)) or not all(ids):
        raise CanonicalizationError("evaluation_requires_unique_canonical_ids")
    if any(row.get("model_sample_weight") != 1.0 for row in rows):
        raise CanonicalizationError("invalid_model_sample_weight")
    probabilities = [float(scores[observation_id]) for observation_id in ids]
    labels = [float(row["label_up_5d"]) for row in rows]
    returns = [float(row["return_5d"]) for row in rows]
    predictions = [probability >= threshold for probability in probabilities]
    accuracy = (
        sum(
            prediction == (label >= 0.5)
            for prediction, label in zip(predictions, labels)
        )
        / len(rows)
        if rows
        else 0.0
    )
    brier = (
        sum(
            (probability - label) ** 2
            for probability, label in zip(probabilities, labels)
        )
        / len(rows)
        if rows
        else 0.0
    )
    selected = [value for value, prediction in zip(returns, predictions) if prediction]
    average_return = sum(selected) / len(selected) if selected else 0.0
    utility = (
        sum(
            value if prediction else 0.0
            for value, prediction in zip(returns, predictions)
        )
        / len(rows)
        if rows
        else 0.0
    )
    calibration_bins = []
    for lower in (0.0, 0.5):
        upper = lower + 0.5
        indices = [
            index
            for index, probability in enumerate(probabilities)
            if lower <= probability
            and (probability <= upper if upper == 1.0 else probability < upper)
        ]
        if indices:
            calibration_bins.append(
                {
                    "lower": lower,
                    "upper": upper,
                    "canonical_observations": len(indices),
                    "average_probability": round(
                        sum(probabilities[index] for index in indices) / len(indices),
                        12,
                    ),
                    "observed_rate": round(
                        sum(labels[index] for index in indices) / len(indices), 12
                    ),
                }
            )
    symbol_counts = Counter(str(row.get("symbol") or "unknown") for row in rows)
    cutoff_counts = Counter(str(row.get("feature_cutoff_at"))[:10] for row in rows)
    return {
        "raw_request_count": sum(
            int(row.get("raw_request_count") or 0) for row in rows
        ),
        "canonical_observation_count": len(rows),
        "accuracy": round(accuracy, 12),
        "brier_score": round(brier, 12),
        "average_selected_return": round(average_return, 12),
        "utility": round(utility, 12),
        "calibration_bins": calibration_bins,
        "max_symbol_concentration": round(
            max(symbol_counts.values(), default=0) / len(rows) if rows else 0.0, 12
        ),
        "max_cutoff_date_concentration": round(
            max(cutoff_counts.values(), default=0) / len(rows) if rows else 0.0, 12
        ),
    }


def canonical_date_block_bootstrap(
    observations: Iterable[Mapping[str, Any]],
    scores: Mapping[str, float],
    *,
    resamples: int = 500,
) -> dict[str, Any]:
    """Deterministically resample cutoff-date blocks of canonical observations."""
    rows = _validated_observations(observations)
    by_date: dict[str, list[float]] = {}
    for row in rows:
        observation_id = str(row["canonical_observation_id"])
        selected_return = (
            float(row["return_5d"]) if scores[observation_id] >= 0.5 else 0.0
        )
        by_date.setdefault(str(row["feature_cutoff_at"])[:10], []).append(
            selected_return
        )
    block_means = [sum(values) / len(values) for _, values in sorted(by_date.items())]
    if not block_means:
        return {"date_blocks": 0, "confidence_interval_95": None}
    state = 0xA17A5
    samples = []
    for _ in range(max(1, resamples)):
        chosen = []
        for _ in block_means:
            state = (1103515245 * state + 12345) % (2**31)
            chosen.append(block_means[state % len(block_means)])
        samples.append(sum(chosen) / len(chosen))
    ordered = sorted(samples)
    lower = ordered[int((len(ordered) - 1) * 0.025)]
    upper = ordered[int((len(ordered) - 1) * 0.975)]
    return {
        "method": "canonical_cutoff_exchange_date_block_bootstrap.v1",
        "date_blocks": len(block_means),
        "canonical_observations": len(rows),
        "resamples": max(1, resamples),
        "confidence_interval_95": [round(lower, 12), round(upper, 12)],
    }


def canonical_top_k(
    observations: Iterable[Mapping[str, Any]],
    scores: Mapping[str, float],
    *,
    k: int,
) -> tuple[str, ...]:
    rows = {
        str(row["canonical_observation_id"]): row
        for row in _validated_observations(observations)
    }
    return tuple(
        sorted(rows, key=lambda key: (-float(scores[key]), key))[: max(0, int(k))]
    )


def score_once_and_fan_out(
    observations: Iterable[Mapping[str, Any]],
    request_map: Iterable[Mapping[str, Any]],
    scorer: Callable[[Mapping[str, Any]], float],
) -> tuple[dict[str, float], tuple[dict[str, Any], ...]]:
    """Score canonical rows once, then perform immutable research-only fan-out."""
    validated = _validated_observations(observations)
    scores = {
        str(row["canonical_observation_id"]): float(scorer(row)) for row in validated
    }
    fan_out = tuple(
        {
            "request_id": str(mapping["request_id"]),
            "endpoint": mapping.get("endpoint"),
            "canonical_observation_id": str(mapping["canonical_observation_id"]),
            "score": scores[str(mapping["canonical_observation_id"])],
        }
        for mapping in request_map
    )
    return scores, fan_out


def _validated_observations(
    observations: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows = [dict(row) for row in observations]
    ids = [str(row.get("canonical_observation_id") or "") for row in rows]
    if not all(ids) or len(ids) != len(set(ids)):
        raise CanonicalizationError("canonical_utility_requires_unique_ids")
    if any(row.get("model_sample_weight") != 1.0 for row in rows):
        raise CanonicalizationError("canonical_utility_requires_unit_weights")
    return rows
