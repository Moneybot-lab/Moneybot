from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
import pandas as pd

from moneybot.services.alpha_atlas_v4_canonical_observations import (
    CANONICAL_OBSERVATION_CONTRACT_VERSION,
    CanonicalizationError,
    canonical_date_block_bootstrap,
    canonical_observation_id,
    canonical_top_k,
    canonicalize_v4_rows,
    evaluate_canonical_observations,
    score_once_and_fan_out,
)
from scripts import day11_compare_candidate_vs_production as production_compare
from scripts.canonicalize_alpha_atlas_v4_rows import materialize_canonical_observations
from scripts.train_massive_baseline_model import _duplicate_weights


def _row(request_id="request-1", endpoint="quick_ask", **overrides):
    row = {
        "decision_id": request_id,
        "endpoint": endpoint,
        "decision_source": "deterministic",
        "decision_at": "2026-03-23T12:00:00+00:00",
        "symbol": "AAPL",
        "point_in_time_symbol_id": "figi:BBG000B9XRY4",
        "feature_cutoff_at": "2026-03-23T11:59:00+00:00",
        "entry_at": "2026-03-23T13:30:00+00:00",
        "label_start_at": "2026-03-23T13:30:00+00:00",
        "exit_at": "2026-03-27T20:00:00+00:00",
        "label_horizon_sessions": 5,
        "lane": "track_b_research",
        "universe_policy_version": "track-b-us-equities.v1",
        "timing_contract_version": "alpha-atlas-v4-prediction-execution-contract.v1",
        "model_feature_contract_version": "alpha-atlas-v4-features.v2",
        "execution_cost_policy_version": None,
        "canonical_dataset_schema_version": "massive-decision-training-rows.v4",
        "exchange_calendar": "XNYS-rule-calendar.v1",
        "feature_close": 114.0,
        "feature_return_5d_lagged": 0.05,
        "feature_family_source_at": {
            "symbol_daily": "2026-03-20T20:00:00+00:00",
            "spy_daily": "2026-03-20T20:00:00+00:00",
        },
        "feature_family_available_at": {
            "symbol_daily": "2026-03-20T20:00:00+00:00",
            "spy_daily": "2026-03-20T20:00:00+00:00",
        },
        "raw_entry_price": 115.5,
        "adjusted_entry_price": 115.5,
        "raw_exit_price": 119.0,
        "adjusted_exit_price": 119.0,
        "label_split_ids": [],
        "label_split_adjustment_factor": 1.0,
        "label_up_5d": 1,
        "return_5d": round(119.0 / 115.5 - 1.0, 6),
        "split_metadata_hash": "split-hash",
        "price_adjustment_policy": "event_time_split_adjusted",
        "market_asof_date": "2026-03-20",
        "event_date": "2026-03-23",
    }
    row.update(overrides)
    return row


def _duplicate_rows():
    return [
        _row(
            "quick-1",
            "quick_ask",
            request_prior_state={
                "probability_up_delta_from_last_signal": 0.15,
                "prior_signal_source_identifier": "frozen-v3",
                "prior_signal_at": "2026-03-20T15:00:00+00:00",
            },
        ),
        _row(
            "watch-1",
            "user_watchlist",
            decision_at="2026-03-23T12:01:00+00:00",
            user_id=99,
            watchlist_id=123,
            request_prior_state={
                "probability_up_delta_from_last_signal": -0.2,
                "prior_signal_source_identifier": "legacy-rule",
                "prior_signal_at": "2026-03-21T15:00:00+00:00",
            },
        ),
    ]


def test_identical_economics_from_multiple_endpoints_collapse_once():
    result = canonicalize_v4_rows(_duplicate_rows())
    assert len(result.observations) == 1
    assert len(result.request_map) == 2
    assert result.observations[0]["raw_request_count"] == 2
    assert "request_prior_state" not in result.observations[0]
    assert "feature_probability_up_delta_from_last_signal" not in result.observations[0]
    assert [
        row["prior_signal_state"]["probability_up_delta_from_last_signal"]
        for row in result.request_map
    ] == [0.15, -0.2]


def test_request_metadata_does_not_change_canonical_id():
    first, second = _duplicate_rows()
    assert canonical_observation_id(first) == canonical_observation_id(second)


def test_deprecated_prior_state_feature_is_rejected_from_v2_model_contract():
    with pytest.raises(CanonicalizationError, match="deprecated_request_state"):
        canonicalize_v4_rows([_row(feature_probability_up_delta_from_last_signal=0.1)])


def test_prior_state_multiplicity_does_not_change_metrics_ranking_or_bootstrap():
    single = canonicalize_v4_rows([_duplicate_rows()[0]])
    repeated = canonicalize_v4_rows(_duplicate_rows())
    observation_id = single.observations[0]["canonical_observation_id"]
    scores = {observation_id: 0.8}
    assert evaluate_canonical_observations(single.observations, scores) | {
        "raw_request_count": 2
    } == evaluate_canonical_observations(repeated.observations, scores)
    assert canonical_top_k(single.observations, scores, k=1) == canonical_top_k(
        repeated.observations, scores, k=1
    )
    assert canonical_date_block_bootstrap(
        single.observations, scores, resamples=20
    ) == canonical_date_block_bootstrap(repeated.observations, scores, resamples=20)


def test_input_order_does_not_change_outputs_or_ids():
    forward = canonicalize_v4_rows(_duplicate_rows())
    reverse = canonicalize_v4_rows(reversed(_duplicate_rows()))
    assert forward.observations == reverse.observations
    assert forward.request_map == reverse.request_map


@pytest.mark.parametrize(
    "overrides",
    [
        {"point_in_time_symbol_id": "figi:MSFT"},
        {"feature_cutoff_at": "2026-03-23T12:00:00+00:00"},
        {"entry_at": "2026-03-24T13:30:00+00:00"},
        {"exit_at": "2026-03-30T20:00:00+00:00"},
        {"label_horizon_sessions": 10},
        {"lane": "ranking_research"},
        {"execution_cost_policy_version": "costs.v1"},
    ],
)
def test_economically_material_key_changes_remain_distinct(overrides):
    assert canonical_observation_id(_row()) != canonical_observation_id(
        _row("request-2", **overrides)
    )


def test_ticker_alias_collapses_through_point_in_time_identity():
    alias = _row("alias", symbol="OLD")
    current = _row("current", symbol="NEW")
    result = canonicalize_v4_rows([alias, current])
    assert len(result.observations) == 1


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"feature_close": 999.0}, "feature_close"),
        ({"label_up_5d": 0}, "label_up_5d"),
        ({"raw_entry_price": 999.0}, "raw_entry_price"),
    ],
)
def test_conflicting_material_duplicates_fail_closed(overrides, reason):
    with pytest.raises(CanonicalizationError, match=reason) as captured:
        canonicalize_v4_rows([_row(), _row("request-2", **overrides)])
    assert captured.value.diagnostics["conflict_count"] == 1
    assert captured.value.diagnostics["conflicts_by_reason"]


def test_conflict_diagnostic_reports_all_fields_and_sanitized_ranges():
    with pytest.raises(CanonicalizationError) as captured:
        canonicalize_v4_rows(
            [
                _row(),
                _row(
                    "request-2",
                    feature_close=999.0,
                    label_up_5d=0,
                    raw_entry_price=None,
                    label_split_adjustment_factor=2.0,
                ),
            ]
        )
    diagnostic = captured.value.diagnostics
    assert diagnostic["total_proposed_groups"] == 1
    assert diagnostic["conflicting_groups"] == 1
    assert diagnostic["rows_in_conflicting_groups"] == 2
    assert set(diagnostic["conflict_count_by_field"]) == {
        "feature_close",
        "label_split_adjustment_factor",
        "label_up_5d",
        "raw_entry_price",
    }
    assert (
        diagnostic["conflict_count_by_field"]["raw_entry_price"][
            "null_versus_value_conflicts"
        ]
        == 1
    )
    assert diagnostic["conflict_count_by_field"]["feature_close"]["numeric_range"] == {
        "minimum": 114.0,
        "maximum": 999.0,
    }
    assert diagnostic["examples_redacted"] is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("timing_contract_version", "timing.v999"),
        ("model_feature_contract_version", "features.v999"),
        ("canonicalization_contract_version", "canonical.v999"),
    ],
)
def test_mixed_or_incompatible_versions_fail_closed(field, value):
    with pytest.raises(CanonicalizationError, match="incompatible"):
        canonicalize_v4_rows([_row(), _row("request-2", **{field: value})])


def test_counts_and_unit_sample_weight_are_reported():
    result = canonicalize_v4_rows(
        [
            *_duplicate_rows(),
            _row("msft", point_in_time_symbol_id="figi:MSFT", symbol="MSFT"),
        ]
    )
    assert result.diagnostics["raw_request_rows"] == 3
    assert result.diagnostics["canonical_observations"] == 2
    assert result.diagnostics["duplicate_rows_collapsed"] == 1
    assert result.diagnostics["model_sample_weight_sum"] == 2.0
    assert {row["model_sample_weight"] for row in result.observations} == {1.0}


def test_v4_training_weight_ignores_endpoint_multiplicity_diagnostics():
    frame = pd.DataFrame(
        [
            {
                "canonicalization_contract_version": CANONICAL_OBSERVATION_CONTRACT_VERSION,
                "model_sample_weight": 1.0,
                "symbol": "AAPL",
                "event_date": "2026-03-23",
                "endpoint": "canonical",
                "decision_source": "canonical",
                "raw_request_count": 99,
            },
            {
                "canonicalization_contract_version": CANONICAL_OBSERVATION_CONTRACT_VERSION,
                "model_sample_weight": 1.0,
                "symbol": "AAPL",
                "event_date": "2026-03-23",
                "endpoint": "canonical",
                "decision_source": "canonical",
                "raw_request_count": 1,
            },
        ]
    )
    assert _duplicate_weights(frame).tolist() == [1.0, 1.0]


def test_endpoint_multiplicity_cannot_change_metrics():
    single = canonicalize_v4_rows([_row()])
    repeated = canonicalize_v4_rows(_duplicate_rows())
    observation_id = single.observations[0]["canonical_observation_id"]
    scores = {observation_id: 0.8}
    single_metrics = evaluate_canonical_observations(single.observations, scores)
    repeated_metrics = evaluate_canonical_observations(repeated.observations, scores)
    assert single_metrics["raw_request_count"] == 1
    assert repeated_metrics["raw_request_count"] == 2
    for metric in (
        "accuracy",
        "brier_score",
        "average_selected_return",
        "utility",
        "calibration_bins",
    ):
        assert single_metrics[metric] == repeated_metrics[metric]


def test_endpoint_multiplicity_cannot_narrow_bootstrap_interval():
    rows = []
    for index in range(4):
        cutoff = datetime(2026, 3, 20 + index, 20, tzinfo=timezone.utc).isoformat()
        rows.append(
            _row(
                f"base-{index}",
                point_in_time_symbol_id=f"figi:{index}",
                feature_cutoff_at=cutoff,
                return_5d=(-0.02 if index % 2 else 0.03),
                label_up_5d=(index % 2 == 0),
            )
        )
    repeated = [
        *rows,
        *[
            deepcopy({**row, "decision_id": f"duplicate-{index}", "endpoint": "alerts"})
            for index, row in enumerate(rows)
        ],
    ]
    first = canonicalize_v4_rows(rows)
    second = canonicalize_v4_rows(repeated)
    scores = {row["canonical_observation_id"]: 0.8 for row in first.observations}
    assert canonical_date_block_bootstrap(
        first.observations, scores, resamples=100
    ) == canonical_date_block_bootstrap(second.observations, scores, resamples=100)


def test_date_block_bootstrap_reports_canonical_blocks():
    result = canonicalize_v4_rows(
        [
            _row(),
            _row(
                "other",
                point_in_time_symbol_id="figi:MSFT",
                feature_cutoff_at="2026-03-24T20:00:00+00:00",
            ),
        ]
    )
    scores = {row["canonical_observation_id"]: 0.7 for row in result.observations}
    report = canonical_date_block_bootstrap(result.observations, scores, resamples=20)
    assert report["date_blocks"] == 2
    assert report["canonical_observations"] == 2


def test_top_k_contains_no_duplicate_canonical_observation():
    result = canonicalize_v4_rows(
        [
            *_duplicate_rows(),
            _row("other", point_in_time_symbol_id="figi:MSFT", symbol="MSFT"),
        ]
    )
    scores = {row["canonical_observation_id"]: 0.7 for row in result.observations}
    selected = canonical_top_k(result.observations, scores, k=10)
    assert len(selected) == len(set(selected)) == 2


def test_canonical_utilities_reject_duplicate_ids_instead_of_silent_deduplication():
    result = canonicalize_v4_rows([_row()])
    duplicated = [result.observations[0], result.observations[0]]
    observation_id = result.observations[0]["canonical_observation_id"]
    with pytest.raises(CanonicalizationError, match="unique_ids"):
        canonical_top_k(duplicated, {observation_id: 0.7}, k=1)


def test_score_fan_out_scores_once_and_preserves_score():
    result = canonicalize_v4_rows(_duplicate_rows())
    calls = []

    def scorer(observation):
        calls.append(observation["canonical_observation_id"])
        return 0.731

    scores, fan_out = score_once_and_fan_out(
        result.observations, result.request_map, scorer
    )
    assert len(calls) == 1
    assert len(scores) == 1
    assert {row["score"] for row in fan_out} == {0.731}
    assert {row["request_id"] for row in fan_out} == {"quick-1", "watch-1"}


def test_v2_production_comparison_lineage_remains_pinned():
    assert (
        production_compare.CURRENT_MASSIVE_CANONICAL_SCHEMA
        == "massive-decision-training-rows.v2"
    )
    assert (
        "massive-decision-training-rows.v4"
        not in production_compare.SUPPORTED_MASSIVE_CANONICAL_SCHEMAS
    )


def test_materialization_writes_observation_map_manifest_and_diagnostics(tmp_path):
    import json

    raw_path = tmp_path / "raw.jsonl"
    raw_path.write_text(
        "".join(json.dumps(row) + "\n" for row in _duplicate_rows()),
        encoding="utf-8",
    )
    raw_path.with_suffix(".jsonl.manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "massive-decision-training-rows.v4",
                "dataset_manifest_hash": "dataset-hash",
                "corporate_action_normalization_passed": True,
                "split_metadata_hash": "split-hash",
                "split_events_loaded": 0,
            }
        ),
        encoding="utf-8",
    )
    manifest = materialize_canonical_observations(raw_path, tmp_path / "canonical")
    assert manifest["raw_request_rows"] == 2
    assert manifest["canonical_observations"] == 1
    assert (tmp_path / "canonical" / "canonical_observations.jsonl").is_file()
    assert (tmp_path / "canonical" / "request_to_observation_map.jsonl").is_file()
    assert (tmp_path / "canonical" / "canonicalization_diagnostics.json").is_file()


def test_materialization_writes_bounded_diagnostic_before_conflict_exit(tmp_path):
    import json

    raw_path = tmp_path / "raw.jsonl"
    raw_path.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in [
                _row(),
                _row("request-2", feature_close=999.0, label_up_5d=0),
            ]
        ),
        encoding="utf-8",
    )
    raw_path.with_suffix(".jsonl.manifest.json").write_text(
        json.dumps({"schema_version": "massive-decision-training-rows.v4"}),
        encoding="utf-8",
    )
    output = tmp_path / "canonical"
    with pytest.raises(CanonicalizationError):
        materialize_canonical_observations(raw_path, output)
    diagnostic = json.loads(
        (output / "canonicalization_diagnostics.json").read_text(encoding="utf-8")
    )
    assert diagnostic["status"] == "failed_closed"
    assert set(diagnostic["conflict_count_by_field"]) == {
        "feature_close",
        "label_up_5d",
    }
