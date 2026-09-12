"""End-to-end training-layer regression for frozen V4 holdout isolation.

Synthetic feature mutations intentionally do not claim Phase 0 reconstruction:
this exercises the challenger training contract after canonical certification.
"""

from __future__ import annotations

import copy
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from moneybot.services.alpha_atlas_v4_temporal_split import (
    canonical_json_hash,
    file_sha256,
    plan_v4_temporal_split,
)
from moneybot.services.alpha_atlas_v4_phase0 import fit_feature_fill_policy
from moneybot.services.market_data_providers import ExchangeCalendar
from scripts.train_challenger_suite import train_challenger_suite

CALENDAR = ExchangeCalendar()


def _sessions(count: int) -> list[date]:
    sessions: list[date] = []
    current = date(2026, 1, 2)
    while len(sessions) < count:
        if CALENDAR.is_trading_day(current):
            sessions.append(current)
        current += timedelta(days=1)
    return sessions


def _fixture_rows() -> list[dict[str, Any]]:
    sessions = _sessions(30)
    rows: list[dict[str, Any]] = []
    for session_index, session in enumerate(sessions):
        entry_session = CALENDAR.next_session(session)
        exit_session = entry_session
        for _ in range(4):
            exit_session = CALENDAR.next_session(exit_session)
        for symbol_index, symbol in enumerate(("AAPL", "MSFT", "NVDA", "JPM")):
            index = session_index * 4 + symbol_index
            cutoff = CALENDAR.session_close(session)
            entry = CALENDAR.session_open(entry_session)
            outcome = 1 if (session_index + symbol_index) % 3 else 0
            value = ((session_index % 9) - 4) * (symbol_index + 1)
            rows.append(
                {
                    "canonical_observation_id": f"iso-{session}-{symbol}",
                    "canonical_observation_schema_version": "alpha-atlas-v4-canonical-observations.v2",
                    "canonicalization_contract_version": "alpha-atlas-v4-canonical-observation.v2",
                    "timing_contract_version": "alpha-atlas-v4-prediction-execution-contract.v1",
                    "model_feature_contract_version": "alpha-atlas-v4-features.v2",
                    "model_sample_weight": 1.0,
                    "raw_request_count": 1,
                    "symbol": symbol,
                    "event_date": session.isoformat(),
                    "decision_at": (cutoff + timedelta(minutes=1)).isoformat(),
                    "feature_cutoff_at": cutoff.isoformat(),
                    "entry_at": entry.isoformat(),
                    "label_start_at": entry.isoformat(),
                    "exit_at": CALENDAR.session_close(exit_session).isoformat(),
                    "entry_session_date": entry_session.isoformat(),
                    "exit_session_date": exit_session.isoformat(),
                    "label_horizon_sessions": 5,
                    "exchange_calendar": CALENDAR.identifier,
                    "feature_close": None if index % 17 == 0 else 100.0 + value,
                    "feature_return_1d_lagged": None
                    if index % 19 == 0
                    else value / 100.0,
                    "feature_volume": None if index % 23 == 0 else 1_000.0 + index * 13,
                    "feature_momentum": ((index % 11) - 5) / 7.0,
                    "label_up_5d": outcome,
                    "return_5d": (0.01 + (index % 5) / 100.0) * (1 if outcome else -1),
                    "return_bin_5d": "gain" if outcome else "loss",
                }
            )
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def _plan_for_input(base_plan: dict[str, Any], input_path: Path) -> dict[str, Any]:
    core = {
        key: copy.deepcopy(value)
        for key, value in base_plan.items()
        if key != "plan_sha256"
    }
    core["input_sha256"] = file_sha256(input_path)
    return {**core, "plan_sha256": canonical_json_hash(core)}


def _artifact_states(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        item["model_version"]: json.loads(Path(item["model_path"]).read_text())
        for item in manifest["challengers"]
    }


def _development_state(
    manifest: dict[str, Any], observer: list[dict[str, Any]]
) -> dict[str, Any]:
    # Excluded deliberately: paths/timestamps/input hashes and all top-level
    # holdout metrics. Included: every learned artifact, recipe, fold fit,
    # development prediction/metric, and every development ranking.
    challengers = [
        {
            "model_version": item["model_version"],
            "model_type": item["model_type"],
            "spec": item["spec"],
            "lineage": item["lineage"],
            "walk_forward": item["metrics"]["walk_forward"],
            "walk_forward_passed": item["metrics"]["walk_forward_passed"],
            "walk_forward_ranking_objective": item["metrics"][
                "walk_forward_ranking_objective"
            ],
        }
        for item in manifest["challengers"]
    ]
    return {
        "feature_columns": manifest["feature_columns"],
        "feature_fill_values": manifest["feature_fill_values"],
        "feature_fill_policy": manifest["feature_fill_policy"],
        "walk_forward_windows": manifest["walk_forward_windows"],
        "challengers": challengers,
        "candidate_inventory": manifest["model_type_counts"],
        "candidate_families": (
            manifest["phase_2_candidate_families"],
            manifest["phase_3_candidate_families"],
            manifest["specialized_challenger_families"],
        ),
        "ranked_model_versions": manifest["ranked_model_versions"],
        "candidate_lanes": manifest["candidate_lanes"],
        "selected_scoring_model": manifest["mistake_mining"]["model_version"],
        "artifacts": _artifact_states(manifest),
        "fold_fits": observer,
    }


def _holdout_state(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        item["model_version"]: {
            key: value
            for key, value in item["metrics"].items()
            if not key.startswith("walk_forward")
        }
        for item in manifest["challengers"]
    }


def test_v4_holdout_mutations_do_not_change_fitting_or_selection(tmp_path):
    baseline_rows = _fixture_rows()
    initial_path = tmp_path / "initial.jsonl"
    _write_jsonl(initial_path, baseline_rows)
    split = plan_v4_temporal_split(
        baseline_rows,
        input_sha256=file_sha256(initial_path),
        min_observations=40,
    ).plan
    development_ids = set(split["train_canonical_observation_ids"])
    holdout_ids = set(split["test_canonical_observation_ids"])
    assert development_ids and holdout_ids and not development_ids & holdout_ids

    variants: dict[str, list[dict[str, Any]]] = {}
    for name in ("baseline", "features", "outcomes", "combined"):
        rows = copy.deepcopy(baseline_rows)
        for index, row in enumerate(rows):
            if row["canonical_observation_id"] not in holdout_ids:
                continue
            if name in {"features", "combined"}:
                row["feature_close"] = None if index % 2 else -50_000.0 - index
                row["feature_return_1d_lagged"] = 100.0 - index * 3.0
                row["feature_volume"] = None if index % 3 else 9_000_000.0 + index
                row["feature_momentum"] = -1_000.0 * (index + 1)
            if name in {"outcomes", "combined"}:
                row["label_up_5d"] = 1 - int(row["label_up_5d"])
                row["return_5d"] = 0.25 if row["label_up_5d"] else -0.25
                row["return_bin_5d"] = "big_gain" if row["label_up_5d"] else "big_loss"
        variants[name] = rows

    baseline_by_id = {row["canonical_observation_id"]: row for row in baseline_rows}
    # Negative control: fitting the real policy on all rows (the historical
    # contamination shape) is observably sensitive to holdout-only mutations.
    feature_names = [
        "feature_close",
        "feature_return_1d_lagged",
        "feature_volume",
        "feature_momentum",
    ]
    assert fit_feature_fill_policy(
        pd.DataFrame(variants["baseline"]), feature_names
    ) != fit_feature_fill_policy(pd.DataFrame(variants["features"]), feature_names)
    results: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    for name, rows in variants.items():
        by_id = {row["canonical_observation_id"]: row for row in rows}
        assert all(
            by_id[identifier] == baseline_by_id[identifier]
            for identifier in development_ids
        )
        if name != "baseline":
            assert any(
                by_id[identifier] != baseline_by_id[identifier]
                for identifier in holdout_ids
            )
        input_path = tmp_path / f"{name}.jsonl"
        plan_path = tmp_path / f"{name}-plan.json"
        _write_jsonl(input_path, rows)
        variant_plan = _plan_for_input(split, input_path)
        plan_path.write_text(json.dumps(variant_plan, sort_keys=True))
        assert (
            variant_plan["train_canonical_observation_ids"]
            == split["train_canonical_observation_ids"]
        )
        assert (
            variant_plan["test_canonical_observation_ids"]
            == split["test_canonical_observation_ids"]
        )
        assert variant_plan["boundary_date"] == split["boundary_date"]
        observer: list[dict[str, Any]] = []
        manifest = train_challenger_suite(
            input_path,
            tmp_path / f"models-{name}",
            min_rows=40,
            split_plan_path=plan_path,
            walk_forward_observer=observer.append,
        )
        results[name] = manifest, observer

    expected = _development_state(*results["baseline"])
    for name in ("features", "outcomes", "combined"):
        assert _development_state(*results[name]) == expected

    baseline_holdout = _holdout_state(results["baseline"][0])
    for name in ("features", "outcomes", "combined"):
        assert _holdout_state(results[name][0]) != baseline_holdout

    exercised = {item["model_type"] for item in results["baseline"][0]["challengers"]}
    assert exercised == {
        "abstention_linear",
        "baseline_classifier",
        "calibrated_linear",
        "decision_stump",
        "hard_example_linear",
        "logistic_regression",
        "ranking_lane_linear",
        "shallow_decision_tree",
        "two_stage_risk_filter",
    }
