#!/usr/bin/env python3
"""Independently verify completed, run-bound V4 Track B portfolio evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

PORTFOLIO_FILES = (
    "execution_policy.json",
    "execution_ledger.json",
    "daily_portfolio_equity.json",
    "valuation_evidence_manifest.json",
    "selected_portfolio_valuation_certification.json",
    "portfolio_metrics.json",
)
TOLERANCE = 1e-6


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"object required: {path}")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(root: Path, source: dict[str, Any]) -> dict[str, Any]:
    """Check existing evidence only; never generate or repair economic artifacts."""
    failures: list[str] = []
    identity = source.get("hosted_source") or {}
    hashes = source.get("artifact_sha256") or {}
    if not (
        identity.get("environment") == "github_actions"
        and identity.get("workflow") == "Track B Offline Challenger"
        and str(identity.get("run_id", "")).isdigit()
        and int(identity.get("run_attempt", 0)) > 0
        and len(str(identity.get("head_sha", ""))) == 40
    ):
        failures.append("HOSTED_SOURCE_IDENTITY_MISMATCH")

    paths = {name: root / "challenger_suite" / "portfolio_path" / name for name in PORTFOLIO_FILES}
    paths.update({
        "backtest_report.json": root / "challenger_suite" / "backtest_report.json",
        "reconstructability_report.json": root / "phase0" / "reconstructability_report.json",
        "temporal_safety_certification.json": root / "phase0" / "temporal_safety_certification.json",
        "track_b_v4_research_summary.json": root / "track_b_v4_research_summary.json",
    })
    missing = [name for name, path in paths.items() if not path.is_file()]
    if any(name in missing for name in PORTFOLIO_FILES):
        failures.append("MISSING_PORTFOLIO_ARTIFACT")
    if missing:
        failures.append("MISSING_REQUIRED_HOSTED_EVIDENCE")
    actual_hashes = {name: _sha(path) for name, path in paths.items() if path.is_file()}
    if set(hashes) != set(paths) or any(hashes.get(k) != v for k, v in actual_hashes.items()):
        failures.append("HOSTED_SOURCE_IDENTITY_MISMATCH")
    if missing:
        return _result(identity, actual_hashes, failures)

    policy, ledger, equity, valuation, certificate, metrics = (
        _load(paths[name]) for name in PORTFOLIO_FILES
    )
    backtest = _load(paths["backtest_report.json"])
    phase0 = _load(paths["reconstructability_report.json"])
    temporal = _load(paths["temporal_safety_certification.json"])
    summary = _load(paths["track_b_v4_research_summary.json"])

    valuation_rows = [r.get("valuation_certification") or {} for r in phase0.get("results", []) if (r.get("valuation_certification") or {}).get("required")]
    if not (phase0.get("status") == "RECONSTRUCTABLE" and phase0.get("failure_count") == 0 and temporal.get("status") == "VERIFIED_FOR_THIS_ARTIFACT"):
        failures.append("PHASE0_VALUATION_NOT_VERIFIED")
    if any(v.get("path_sessions") != v.get("resolved_source_sessions") for v in valuation_rows):
        failures.append("VALUATION_SOURCE_COUNT_MISMATCH")
    if any(v.get("independent_adjustments_verified") is not True for v in valuation_rows):
        failures.append("INDEPENDENT_ADJUSTMENT_NOT_VERIFIED")
    candidate = backtest.get("primary_portfolio_candidate")
    candidate_values = {ledger.get("candidate_id"), equity.get("candidate_id"), certificate.get("candidate_id"), summary.get("primary_portfolio_candidate")}
    if not candidate or candidate_values != {candidate}:
        failures.append("CANDIDATE_IDENTITY_MISMATCH")

    events = ledger.get("events") or []
    evidence = {x.get("canonical_observation_id"): x for x in valuation.get("evidence") or []}
    seen: set[tuple[str, str]] = set()
    positions: dict[str, dict[str, Any]] = {}
    duplicate = 0
    fee_slippage_mismatch = False
    rejection_reasons: dict[str, int] = {}
    for event in events:
        kind, oid = event.get("event"), str(event.get("canonical_observation_id") or "")
        key = (kind, oid)
        if not oid or key in seen:
            duplicate += 1
        seen.add(key)
        if kind == "order_rejected":
            reason = str(event.get("reason") or "")
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
            if not reason:
                failures.append("LEDGER_RECONCILIATION_FAILED")
        elif kind in ("entry_fill", "exit_fill"):
            ref, fill, qty = (float(event[x]) for x in ("reference_price", "fill_price", "quantity"))
            rate = float(policy["slippage_bps"]) / 10_000
            expected_fill = ref * (1 + rate if kind == "entry_fill" else 1 - rate)
            expected_fee = float(event["notional"]) * float(policy["transaction_cost_bps"]) / 10_000
            if abs(fill - expected_fill) > TOLERANCE or abs(float(event["fee"]) - expected_fee) > TOLERANCE:
                fee_slippage_mismatch = True
            if kind == "entry_fill":
                if oid not in evidence or oid in positions:
                    failures.append("LEDGER_RECONCILIATION_FAILED")
                positions[oid] = event
            elif oid not in positions:
                failures.append("LEDGER_RECONCILIATION_FAILED")
            else:
                positions.pop(oid)
        else:
            failures.append("LEDGER_RECONCILIATION_FAILED")
    if duplicate:
        failures.append("LEDGER_RECONCILIATION_FAILED")
    if fee_slippage_mismatch:
        failures.append("FEE_SLIPPAGE_RECONCILIATION_FAILED")

    cash = float(policy["starting_capital"])
    positions = {}
    by_session: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_session.setdefault(str(event.get("session")), []).append(event)
    mismatches, maximum, first_mismatch = 0, 0.0, None
    for row in equity.get("rows") or []:
        session = str(row.get("session"))
        for event in by_session.get(session, []):
            oid = str(event.get("canonical_observation_id"))
            if event.get("event") == "entry_fill":
                cash -= float(event["notional"]) + float(event["fee"]); positions[oid] = event
            elif event.get("event") == "exit_fill":
                cash += float(event["notional"]) - float(event["fee"]); positions.pop(oid, None)
        market = 0.0
        for oid, entry in positions.items():
            mark = (evidence.get(oid, {}).get("marks") or {}).get(session)
            if mark is None:
                failures.append("EQUITY_RECONCILIATION_FAILED"); market = math.nan; break
            market += float(entry["quantity"]) * float(mark)
        expected = cash + market
        difference = abs(expected - float(row["total_equity"]))
        maximum = max(maximum, difference)
        if difference > TOLERANCE or abs(cash - float(row["cash"])) > TOLERANCE:
            mismatches += 1; first_mismatch = first_mismatch or session
    if mismatches:
        failures.append("EQUITY_RECONCILIATION_FAILED")

    rows = equity.get("rows") or []
    known = [r for r in rows if r.get("total_equity") is not None]
    peak_value = -math.inf; peak_session = None; worst = None
    for row in known:
        value = float(row["total_equity"])
        if value > peak_value: peak_value, peak_session = value, row["session"]
        dd = value / peak_value - 1
        if worst is None or dd < worst[0]: worst = (dd, peak_session, row["session"], peak_value)
    recovery = None
    if worst:
        trough_index = next(i for i, r in enumerate(known) if r["session"] == worst[2])
        recovery = next((r["session"] for r in known[trough_index + 1:] if float(r["total_equity"]) >= worst[3]), None)
    expected_drawdown = {
        "max_drawdown": worst[0] if worst else None,
        "drawdown_peak_session": worst[1] if worst else None,
        "drawdown_trough_session": worst[2] if worst else None,
        "drawdown_recovery_session": recovery,
    }
    if any(metrics.get(k) != v and not (isinstance(v, float) and abs(float(metrics.get(k, math.inf)) - v) <= TOLERANCE) for k, v in expected_drawdown.items()):
        failures.append("DRAWDOWN_RECONSTRUCTION_FAILED")

    selected = next((c for c in backtest.get("challengers", []) if c.get("model_version") == candidate), {})
    required_gate_fields = ("promotion_gates", "backtest_metrics")
    if any(not isinstance(selected.get(k), dict) for k in required_gate_fields) or not isinstance(backtest.get("benchmark"), dict):
        failures.append("ECONOMIC_GATE_STATUS_MISSING")
    if summary.get("automatic_promotion") is not False or certificate.get("automatic_promotion") is True:
        failures.append("AUTOMATIC_PROMOTION_ENABLED")
    final_summary = backtest.get("final_summary") or {}
    if summary.get("ready_for_live_routing") is not False or final_summary.get("ready_for_live_routing") is not False:
        failures.append("LIVE_ROUTING_ENABLED")

    counts = {
        "orders": len({e.get("canonical_observation_id") for e in events}),
        "fills": sum(e.get("event") in ("entry_fill", "exit_fill") for e in events),
        "rejections": sum(e.get("event") == "order_rejected" for e in events),
        "unique_securities": len({e.get("security_id") for e in evidence.values()} - {None, ""}),
        "duplicate_violations": duplicate,
        "capital_limit_rejections": rejection_reasons.get("insufficient_cash", 0),
        "exposure_limit_rejections": rejection_reasons.get("maximum_positions_reached", 0),
        "other_rejection_reasons": {k: v for k, v in rejection_reasons.items() if k not in ("insufficient_cash", "maximum_positions_reached")},
    }
    result = _result(identity, actual_hashes, failures)
    result.update({"phase0": {"rows": phase0.get("rows_checked"), "valuation_required_rows": len(valuation_rows)}, "candidate_id": candidate, "ledger_summary": counts, "equity_reconciliation": {"sessions": len(rows), "reconciled_sessions": len(rows) - mismatches, "mismatched_sessions": mismatches, "maximum_absolute_difference": maximum, "tolerance": TOLERANCE, "first_mismatch": first_mismatch}, "drawdown_reconstruction": expected_drawdown, "economic_gates_preserved": "ECONOMIC_GATE_STATUS_MISSING" not in failures, "automatic_promotion": summary.get("automatic_promotion"), "ready_for_live_routing": summary.get("ready_for_live_routing")})
    return result


def _result(identity: dict[str, Any], hashes: dict[str, str], failures: list[str]) -> dict[str, Any]:
    unique = sorted(set(failures))
    return {"schema_version": "alpha-atlas-v4-hosted-portfolio-verification.v1", "status": "VERIFIED" if not unique else "FAILED", "hosted_source": identity, "artifact_sha256": hashes, "failures": unique, "evidence_only": True, "model_fitting_performed": False, "holdout_access_performed": False, "automatic_promotion_performed": False, "live_routing_performed": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = verify(args.run_root, _load(args.source_manifest))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": result["status"], "failures": result["failures"]}, sort_keys=True))
    return 0 if result["status"] == "VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
