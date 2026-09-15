from __future__ import annotations

import hashlib
import json

import pytest

from scripts.verify_v4_hosted_portfolio import PORTFOLIO_FILES, verify


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n")


def _fixture(tmp_path):
    root = tmp_path / "123-1"
    portfolio = root / "challenger_suite/portfolio_path"
    candidate = "frozen-development-candidate"
    policy = {"starting_capital": 100000.0, "transaction_cost_bps": 5.0, "slippage_bps": 5.0}
    evidence = {"canonical_observation_id": "obs-1", "security_id": "sec-1", "marks": {"2026-01-02": 100.0}}
    artifacts = {
        "execution_policy.json": policy,
        "execution_ledger.json": {"candidate_id": candidate, "events": []},
        "daily_portfolio_equity.json": {"candidate_id": candidate, "rows": [{"session": "2026-01-02", "cash": 100000.0, "total_equity": 100000.0}]},
        "valuation_evidence_manifest.json": {"evidence": [evidence]},
        "selected_portfolio_valuation_certification.json": {"candidate_id": candidate, "status": "VERIFIED"},
        "portfolio_metrics.json": {"max_drawdown": 0.0, "drawdown_peak_session": "2026-01-02", "drawdown_trough_session": "2026-01-02", "drawdown_recovery_session": None},
    }
    for name, value in artifacts.items():
        _write(portfolio / name, value)
    _write(root / "challenger_suite/backtest_report.json", {"primary_portfolio_candidate": candidate, "benchmark": {"benchmark_comparison_valid": True}, "challengers": [{"model_version": candidate, "promotion_gates": {"promotion_ready": False, "failed_gates": ["bootstrap_profit_confidence_failed"]}, "backtest_metrics": {"calibration": {}, "bootstrap_confidence": {}}}], "final_summary": {"ready_for_live_routing": False}})
    valuation = {"required": True, "status": "VERIFIED", "path_sessions": 1, "resolved_source_sessions": 1, "independent_adjustments_verified": True, "failures": []}
    _write(root / "phase0/reconstructability_report.json", {"status": "RECONSTRUCTABLE", "failure_count": 0, "rows_checked": 1, "results": [{"status": "RECONSTRUCTABLE", "valuation_certification": valuation}]})
    _write(root / "phase0/temporal_safety_certification.json", {"status": "VERIFIED_FOR_THIS_ARTIFACT"})
    _write(root / "track_b_v4_research_summary.json", {"primary_portfolio_candidate": candidate, "automatic_promotion": False, "ready_for_live_routing": False})
    paths = {name: portfolio / name for name in PORTFOLIO_FILES}
    paths.update({"backtest_report.json": root / "challenger_suite/backtest_report.json", "reconstructability_report.json": root / "phase0/reconstructability_report.json", "temporal_safety_certification.json": root / "phase0/temporal_safety_certification.json", "track_b_v4_research_summary.json": root / "track_b_v4_research_summary.json"})
    source = {"hosted_source": {"environment": "github_actions", "workflow": "Track B Offline Challenger", "run_id": 123, "run_attempt": 1, "head_sha": "a" * 40}, "artifact_sha256": {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}}
    return root, source, paths


def _refresh(source, paths):
    source["artifact_sha256"] = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items() if path.exists()}


def test_valid_hosted_fixture_passes(tmp_path):
    root, source, _ = _fixture(tmp_path)
    result = verify(root, source)
    assert result["status"] == "VERIFIED"
    assert result["model_fitting_performed"] is False


def test_local_or_synthetic_source_cannot_masquerade_as_hosted(tmp_path):
    root, source, _ = _fixture(tmp_path)
    source["hosted_source"]["environment"] = "local"
    assert "HOSTED_SOURCE_IDENTITY_MISMATCH" in verify(root, source)["failures"]


def test_exact_source_hash_binding_is_required(tmp_path):
    root, source, _ = _fixture(tmp_path)
    source["artifact_sha256"]["execution_policy.json"] = "0" * 64
    assert "HOSTED_SOURCE_IDENTITY_MISMATCH" in verify(root, source)["failures"]


def test_missing_portfolio_artifact_fails(tmp_path):
    root, source, paths = _fixture(tmp_path)
    paths["execution_ledger.json"].unlink(); _refresh(source, paths)
    assert "MISSING_PORTFOLIO_ARTIFACT" in verify(root, source)["failures"]


@pytest.mark.parametrize("field,status", [("candidate", "CANDIDATE_IDENTITY_MISMATCH"), ("equity", "EQUITY_RECONCILIATION_FAILED"), ("drawdown", "DRAWDOWN_RECONSTRUCTION_FAILED"), ("valuation_count", "VALUATION_SOURCE_COUNT_MISMATCH"), ("adjustment", "INDEPENDENT_ADJUSTMENT_NOT_VERIFIED"), ("promotion", "AUTOMATIC_PROMOTION_ENABLED"), ("routing", "LIVE_ROUTING_ENABLED")])
def test_fail_closed_mutations(tmp_path, field, status):
    root, source, paths = _fixture(tmp_path)
    if field == "candidate":
        value = json.loads(paths["execution_ledger.json"].read_text()); value["candidate_id"] = "other"; _write(paths["execution_ledger.json"], value)
    elif field == "equity":
        value = json.loads(paths["daily_portfolio_equity.json"].read_text()); value["rows"][0]["total_equity"] += 1; _write(paths["daily_portfolio_equity.json"], value)
    elif field == "drawdown":
        value = json.loads(paths["portfolio_metrics.json"].read_text()); value["max_drawdown"] = -0.5; _write(paths["portfolio_metrics.json"], value)
    elif field in ("valuation_count", "adjustment"):
        value = json.loads(paths["reconstructability_report.json"].read_text()); cert = value["results"][0]["valuation_certification"]
        cert["resolved_source_sessions"] = 0 if field == "valuation_count" else 1
        if field == "adjustment": cert["independent_adjustments_verified"] = False
        _write(paths["reconstructability_report.json"], value)
    else:
        value = json.loads(paths["track_b_v4_research_summary.json"].read_text()); value["automatic_promotion" if field == "promotion" else "ready_for_live_routing"] = True; _write(paths["track_b_v4_research_summary.json"], value)
    _refresh(source, paths)
    assert status in verify(root, source)["failures"]


@pytest.mark.parametrize("mutation,status", [("duplicate", "LEDGER_RECONCILIATION_FAILED"), ("fee", "FEE_SLIPPAGE_RECONCILIATION_FAILED")])
def test_ledger_and_cost_mismatches_fail(tmp_path, mutation, status):
    root, source, paths = _fixture(tmp_path)
    ledger = json.loads(paths["execution_ledger.json"].read_text())
    event = {"event": "entry_fill", "session": "2026-01-02", "canonical_observation_id": "obs-1", "symbol": "ABC", "quantity": 10.0, "reference_price": 100.0, "fill_price": 100.05, "notional": 1000.5, "fee": 0.50025}
    ledger["events"] = [event, dict(event)] if mutation == "duplicate" else [{**event, "fee": 9.0}]
    _write(paths["execution_ledger.json"], ledger); _refresh(source, paths)
    assert status in verify(root, source)["failures"]
