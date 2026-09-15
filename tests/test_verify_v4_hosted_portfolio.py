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
    entry = {"event": "entry_fill", "session": "2026-01-02", "canonical_observation_id": "obs-1", "symbol": "ABC", "quantity": 10.0, "reference_price": 100.0, "fill_price": 100.05, "notional": 1000.5, "fee": 0.50025}
    exit_fill = {"event": "exit_fill", "session": "2026-01-02", "canonical_observation_id": "obs-1", "symbol": "ABC", "quantity": 10.0, "reference_price": 100.0, "fill_price": 99.95, "notional": 999.5, "fee": 0.49975}
    artifacts = {
        "execution_policy.json": policy,
        "execution_ledger.json": {"candidate_id": candidate, "events": [entry, exit_fill]},
        "daily_portfolio_equity.json": {"candidate_id": candidate, "rows": [{"session": "2026-01-01", "cash": 100000.0, "total_equity": 100000.0}, {"session": "2026-01-02", "cash": 99998.0, "total_equity": 99998.0}]},
        "valuation_evidence_manifest.json": {"evidence": [evidence]},
        "selected_portfolio_valuation_certification.json": {"candidate_id": candidate, "status": "VERIFIED", "scope": "SELECTED_PORTFOLIO_ACTUAL_HOLDINGS", "all_required_holding_marks_verified": True, "invalid_reasons": []},
        "portfolio_metrics.json": {"max_drawdown": -0.00002, "drawdown_peak_session": "2026-01-01", "drawdown_trough_session": "2026-01-02", "drawdown_recovery_session": None},
    }
    for name, value in artifacts.items():
        _write(portfolio / name, value)
    _write(root / "challenger_suite/backtest_report.json", {"primary_portfolio_candidate": candidate, "benchmark": {"benchmark_comparison_valid": True}, "challengers": [{"model_version": candidate, "promotion_gates": {"promotion_ready": False, "failed_gates": ["bootstrap_profit_confidence_failed"]}, "backtest_metrics": {"calibration": {}, "bootstrap_confidence": {}}}], "final_summary": {"ready_for_live_routing": False}})
    valuation = {"required": True, "status": "VERIFIED", "path_sessions": 1, "resolved_source_sessions": 1, "independent_adjustments_verified": True, "failures": []}
    _write(root / "phase0/reconstructability_report.json", {"status": "RECONSTRUCTABLE", "failure_count": 0, "rows_total": 1, "rows_checked": 1, "reconstructable_rows": 1, "valuation_diagnostic_failure_count": 0, "valuation_diagnostic_failure_reasons": {}, "results": [{"canonical_observation_id": "obs-1", "status": "RECONSTRUCTABLE", "valuation_certification": valuation}]})
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
    assert result["valuation_scope_verification"]["selected_portfolio_verified_observations"] == 1


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
        value = json.loads(paths["daily_portfolio_equity.json"].read_text()); value["rows"][-1]["total_equity"] += 1; _write(paths["daily_portfolio_equity.json"], value)
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


def _add_diagnostic(paths, *, observation_id="diagnostic-only", security_id="AUROW:2026-07-20"):
    report = json.loads(paths["reconstructability_report.json"].read_text())
    report["rows_total"] += 1
    report["rows_checked"] += 1
    report["reconstructable_rows"] += 1
    report["valuation_diagnostic_failure_count"] = report.get("valuation_diagnostic_failure_count", 0) + 1
    report["valuation_diagnostic_failure_reasons"] = {"valuation_path_missing_session": report["valuation_diagnostic_failure_count"]}
    report["results"].append({"canonical_observation_id": observation_id, "point_in_time_symbol_id": security_id, "status": "RECONSTRUCTABLE", "valuation_certification": {"required": True, "scope": "OBSERVATION_VALUATION_DIAGNOSTIC_ONLY", "status": "INCOMPLETE", "independent_adjustments_verified": False, "path_sessions": 5, "resolved_source_sessions": 4, "failures": ["valuation_path_missing_session"]}})
    _write(paths["reconstructability_report.json"], report)


def test_unrelated_diagnostic_failure_is_preserved_without_failing_portfolio(tmp_path):
    root, source, paths = _fixture(tmp_path)
    _add_diagnostic(paths)
    _refresh(source, paths)
    result = verify(root, source)
    assert result["status"] == "VERIFIED"
    assert result["phase0"]["valuation_diagnostic_failure_count"] == 1
    assert result["non_portfolio_valuation_diagnostics"] == {
        "count": 1,
        "portfolio_intersection_count": 0,
        "failures": [{
            "canonical_observation_id": "diagnostic-only",
            "point_in_time_security_id": "AUROW:2026-07-20",
            "scope": "OBSERVATION_VALUATION_DIAGNOSTIC_ONLY",
            "status": "INCOMPLETE",
            "failures": ["valuation_path_missing_session"],
            "path_sessions": 5,
            "resolved_source_sessions": 4,
        }],
    }


def test_diagnostic_failure_intersecting_selected_holding_fails(tmp_path):
    root, source, paths = _fixture(tmp_path)
    report = json.loads(paths["reconstructability_report.json"].read_text())
    report["valuation_diagnostic_failure_count"] = 1
    report["valuation_diagnostic_failure_reasons"] = {"valuation_path_missing_session": 1}
    certification = report["results"][0]["valuation_certification"]
    certification.update(status="INCOMPLETE", independent_adjustments_verified=False, resolved_source_sessions=0, failures=["valuation_path_missing_session"])
    _write(paths["reconstructability_report.json"], report); _refresh(source, paths)
    result = verify(root, source)
    assert "INDEPENDENT_ADJUSTMENT_NOT_VERIFIED" in result["failures"]
    assert result["non_portfolio_valuation_diagnostics"]["portfolio_intersection_count"] == 1


def test_phase0_core_failure_fails_independently(tmp_path):
    root, source, paths = _fixture(tmp_path)
    report = json.loads(paths["reconstructability_report.json"].read_text())
    report["failure_count"] = 1
    report["reconstructable_rows"] = 0
    _write(paths["reconstructability_report.json"], report); _refresh(source, paths)
    result = verify(root, source)
    assert "PHASE0_VALUATION_NOT_VERIFIED" in result["failures"]
    assert result["phase0"]["core_failure_count"] == 1


def test_selected_portfolio_certificate_must_be_verified(tmp_path):
    root, source, paths = _fixture(tmp_path)
    certificate = json.loads(paths["selected_portfolio_valuation_certification.json"].read_text())
    certificate["status"] = "INCOMPLETE"
    _write(paths["selected_portfolio_valuation_certification.json"], certificate); _refresh(source, paths)
    assert "SELECTED_PORTFOLIO_VALUATION_INCOMPLETE" in verify(root, source)["failures"]


def test_run_equivalent_four_diagnostics_are_outside_selected_scope(tmp_path):
    root, source, paths = _fixture(tmp_path)
    diagnostic_ids = (
        ("aav4obs_0f0c2297855060f8a457345b9fd3ad895453cfe3ef48c510eca4fc8f28ece598", "AUROW:2026-07-20"),
        ("aav4obs_61e2a89a39e6b18463a24a517b78c22572ccd230806612f8afab5872350b6d04", "RNWWW:2026-07-19"),
        ("aav4obs_8b995e4a446bb50a36c92aee7fd4e1d53549d4fb0dd530ebc763d4e72d29f4bb", "RNWWW:2026-07-20"),
        ("aav4obs_d3e033aa8ada1afc019b865fa0ce971a47763f9c5f855321d986642f3db0e92f", "RNWWW:2026-07-17"),
    )
    for observation_id, security_id in diagnostic_ids:
        _add_diagnostic(paths, observation_id=observation_id, security_id=security_id)
    _refresh(source, paths)
    result = verify(root, source)
    assert result["status"] == "VERIFIED"
    scope = result["valuation_scope_verification"]
    assert scope["full_universe_diagnostic_failures"] == 4
    assert scope["diagnostic_failure_selected_portfolio_intersection"] == 0
    assert scope["selected_portfolio_verified_observations"] == 1


def test_evidence_only_workflow_is_pinned_and_cannot_rerun_track_b():
    text = open(
        ".github/workflows/v4-verify-hosted-portfolio-evidence.yml",
        encoding="utf-8",
    ).read()
    assert text.startswith("name: V4 Verify Hosted Portfolio Evidence\n")
    assert 'SOURCE_RUN_ID: "35002276286"' in text
    assert 'SOURCE_RUN_ATTEMPT: "1"' in text
    assert "SOURCE_HEAD_SHA: 7da77897e10f228d2b60bedfbeb3c983836de5c6" in text
    assert "SOURCE_ARTIFACT: track-b-offline-output" in text
    assert "HOSTED_SOURCE_ARTIFACT_UNAVAILABLE" in text
    for forbidden in (
        "train_challenger_suite.py",
        "backtest_challenger_suite.py",
        "canonicalize_alpha_atlas_v4_rows.py",
        "build_massive_decision_training_rows.py",
    ):
        assert forbidden not in text
