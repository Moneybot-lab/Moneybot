from __future__ import annotations

import gzip
import hashlib
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from moneybot.services.alpha_atlas_v4_canonical_observations import canonicalize_v4_rows
from moneybot.services.alpha_atlas_v4_phase0 import (
    build_temporal_safety_certification,
    verify_observation,
)
from moneybot.services.corporate_actions import canonical_splits
from moneybot.services.market_data_providers import ExchangeCalendar
from moneybot.services.v4_portfolio_path import (
    V4ExecutionPolicy,
    reconstruct_v4_portfolio_path,
)
from scripts.build_massive_decision_training_rows import (
    build_training_rows_from_raw_market,
    emit_phase0_evidence_bundle,
    load_market_history,
)
from scripts.verify_alpha_atlas_v4_reconstructability import verify_artifact

CALENDAR = ExchangeCalendar()


def _fixture(tmp_path, split_days=()):
    raw = tmp_path / "raw"
    raw.mkdir(parents=True)
    sessions = []
    current = date(2026, 1, 2)
    while len(sessions) < 70:
        if CALENDAR.is_trading_day(current):
            sessions.append(current)
        current += timedelta(days=1)
    for symbol, base in (("AAPL", 100), ("SPY", 400), ("XLK", 200)):
        lines = ["ticker,date,open,high,low,close,volume"]
        for index, session in enumerate(sessions):
            close = base + index
            lines.append(
                f"{symbol},{session},{close},{close + 1},{close - 1},{close},1000"
            )
        (raw / f"{symbol}.csv").write_text("\n".join(lines) + "\n")
    splits = canonical_splits(
        {
            "ticker": "AAPL",
            "execution_date": sessions[index].isoformat(),
            "adjustment_type": "forward_split",
            "split_from": split_from,
            "split_to": split_to,
            "id": f"split-{index}",
        }
        for index, split_from, split_to in split_days
    )
    split_cache = tmp_path / "splits.jsonl"
    split_cache.write_text("".join(json.dumps(item) + "\n" for item in splits))
    market = load_market_history(raw)
    event = {
        "ts": int(
            datetime.combine(sessions[55], datetime.min.time(), tzinfo=timezone.utc)
            .replace(hour=17)
            .timestamp()
        ),
        "symbol": "AAPL",
        "endpoint": "quick_ask",
        "payload": {"recommendation": "BUY", "sector_etf": "XLK"},
    }
    rows, _ = build_training_rows_from_raw_market(
        [event], market, horizon_days=5, split_events=splits
    )
    evidence = tmp_path / "evidence"
    emit_phase0_evidence_bundle(
        rows=rows,
        market=market,
        split_events=splits,
        split_cache_path=split_cache,
        raw_root=raw,
        evidence_dir=evidence,
        max_selected_rows=10_000,
        max_bundle_bytes=10_000_000,
    )
    row = canonicalize_v4_rows(rows).observations[0]
    return row, evidence


def _rewrite_partition(row, evidence, section, mutate):
    manifest_path = evidence / "source_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    spec = manifest["sections"][section][0]
    path = evidence / spec["path"]
    records = [
        json.loads(line)
        for line in gzip.decompress(path.read_bytes()).decode().splitlines()
        if line
    ]
    mutate(records)
    raw = (
        "".join(
            json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n"
            for item in records
        )
    ).encode()
    compressed = gzip.compress(raw, mtime=0)
    path.write_bytes(compressed)
    spec["sha256"] = hashlib.sha256(compressed).hexdigest()
    spec["compressed_bytes"] = len(compressed)
    spec["uncompressed_bytes"] = len(raw)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    row["reconstruction_lineage"]["source_evidence_manifest_sha256"] = hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()


@pytest.mark.parametrize(
    "splits",
    [
        (),
        ((56, 1, 2),),  # Entry-date action is already on that session's basis.
        ((60, 1, 2),),  # Exit-date action adjusts every earlier close.
        ((57, 1, 2), (59, 2, 3)),
    ],
)
def test_real_phase0_certifies_independently_replayed_valuation_adjustments(
    tmp_path, splits
):
    row, evidence = _fixture(tmp_path, splits)
    result = verify_observation(row, root=evidence)
    assert result["status"] == "RECONSTRUCTABLE", result["failures"]
    assert result["valuation_certification"] == {
        "required": True,
        "policy_version": "alpha-atlas-v4-daily-close-valuation.v1",
        "path_sessions": 5,
        "resolved_source_sessions": 5,
        "independent_adjustments_verified": True,
        "failures": [],
    }


def test_missing_empty_and_partial_valuation_references_fail(tmp_path):
    row, evidence = _fixture(tmp_path)
    _rewrite_partition(
        row,
        evidence,
        "observation_lineage",
        lambda records: records[0].update(valuation_row_ids=[]),
    )
    failures = verify_observation(row, root=evidence)["failures"]
    assert "missing_valuation_source_references" in failures
    assert "missing_valuation_source_evidence" in failures

    row, evidence = _fixture(tmp_path / "partial")
    _rewrite_partition(
        row,
        evidence,
        "observation_lineage",
        lambda records: records[0]["valuation_row_ids"].pop(2),
    )
    assert (
        "valuation_path_source_session_mismatch"
        in verify_observation(row, root=evidence)["failures"]
    )

    row, evidence = _fixture(tmp_path / "duplicate")
    _rewrite_partition(
        row,
        evidence,
        "observation_lineage",
        lambda records: records[0]["valuation_row_ids"].append(
            records[0]["valuation_row_ids"][0]
        ),
    )
    assert (
        "duplicate_valuation_source_references"
        in verify_observation(row, root=evidence)["failures"]
    )


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            lambda item: item.update(raw_close=item["raw_close"] + 1),
            "valuation_path_raw_close_mismatch",
        ),
        (
            lambda item: item.update(session="2026-12-31"),
            "valuation_path_source_session_mismatch",
        ),
    ],
)
def test_wrong_embedded_source_values_fail_semantic_replay(tmp_path, mutation, reason):
    row, evidence = _fixture(tmp_path)
    mutation(row["valuation_path"][1])
    assert reason in verify_observation(row, root=evidence)["failures"]


def test_wrong_source_security_fails_with_valid_bundle_hashes(tmp_path):
    row, evidence = _fixture(tmp_path)
    lineage_id = row["reconstruction_lineage"]["lineage_id"]
    manifest = json.loads((evidence / "source_evidence_manifest.json").read_text())
    lineage_spec = manifest["sections"]["observation_lineage"][0]
    lineage_records = [
        json.loads(line)
        for line in gzip.decompress((evidence / lineage_spec["path"]).read_bytes())
        .decode()
        .splitlines()
        if line
    ]
    target_id = next(
        item for item in lineage_records if item["lineage_id"] == lineage_id
    )["valuation_row_ids"][1]

    def mutate(records):
        target = next(item for item in records if item["source_row_id"] == target_id)
        target["symbol"] = "WRONG"
        content = {
            key: target.get(key)
            for key in ("symbol", "date", "open", "high", "low", "close", "volume")
        }
        target["row_content_sha256"] = hashlib.sha256(
            json.dumps(
                {"values": content, "adjustment": target.get("adjustment")},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    _rewrite_partition(row, evidence, "selected_market_rows", mutate)
    assert (
        "valuation_source_security_mismatch"
        in verify_observation(row, root=evidence)["failures"]
    )


def test_joint_factor_and_adjusted_close_tamper_fails_independent_replay(tmp_path):
    row, evidence = _fixture(tmp_path, ((58, 1, 2),))
    item = row["valuation_path"][0]
    item["split_adjustment_factor"] *= 3
    item["adjusted_close"] = item["raw_close"] * item["split_adjustment_factor"]
    failures = verify_observation(row, root=evidence)["failures"]
    assert "valuation_path_independent_factor_mismatch" in failures
    assert "valuation_path_independent_adjusted_close_mismatch" in failures


def test_missing_policy_or_action_evidence_fails_and_blocks_certification(tmp_path):
    row, evidence = _fixture(tmp_path)
    row.pop("valuation_path_policy_version")
    observation = verify_observation(row, root=evidence)
    assert "missing_or_incompatible_valuation_path_policy" in observation["failures"]
    artifact = tmp_path / "mutated.jsonl"
    artifact.write_text(json.dumps(row, sort_keys=True, default=str) + "\n")
    report = verify_artifact(artifact, root=evidence)
    certification = build_temporal_safety_certification(
        artifact_path=artifact,
        verification_report=report,
        timing_contract_version="alpha-atlas-v4-prediction-execution-contract.v1",
    )
    assert certification["status"] != "VERIFIED_FOR_THIS_ARTIFACT"
    row.update(score=0.9, prediction=1)
    portfolio = reconstruct_v4_portfolio_path(
        [row],
        candidate_id="candidate",
        input_sha256="fixture",
        policy=V4ExecutionPolicy(),
    )
    assert portfolio["metrics"]["path_valid"] is False
    assert any(
        reason.startswith("uncertified_valuation_policy:")
        for reason in portfolio["metrics"]["invalid_reasons"]
    )

    missing_path, missing_path_evidence = _fixture(tmp_path / "path")
    missing_path.pop("valuation_path")
    assert (
        "missing_daily_valuation_path"
        in verify_observation(missing_path, root=missing_path_evidence)["failures"]
    )

    row, evidence = _fixture(tmp_path / "actions")
    _rewrite_partition(
        row,
        evidence,
        "corporate_action_evidence",
        lambda records: records[0].pop("actions"),
    )
    assert (
        "missing_or_malformed_valuation_action_evidence"
        in verify_observation(row, root=evidence)["failures"]
    )

    row, evidence = _fixture(tmp_path / "malformed", ((58, 1, 2),))
    _rewrite_partition(
        row,
        evidence,
        "corporate_action_evidence",
        lambda records: records[0]["actions"][0].update(split_to=0),
    )
    assert (
        "malformed_valuation_action_evidence"
        in verify_observation(row, root=evidence)["failures"]
    )
