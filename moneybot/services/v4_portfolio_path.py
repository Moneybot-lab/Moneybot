"""Deterministic research-only V4 execution ledger and daily equity path."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping

from moneybot.services.market_data_providers import ExchangeCalendar

EXECUTION_POLICY_VERSION = "alpha-atlas-v4-unlevered-long-cash.v1"
PORTFOLIO_PATH_SCHEMA = "alpha-atlas-v4-portfolio-path.v1"
PORTFOLIO_VALUATION_CERTIFICATION_SCHEMA = (
    "alpha-atlas-v4-selected-portfolio-valuation-certification.v1"
)
VALUATION_PATH_POLICY_VERSION = "alpha-atlas-v4-daily-close-valuation.v1"


@dataclass(frozen=True)
class V4ExecutionPolicy:
    starting_capital: float = 100_000.0
    max_positions: int = 5
    gross_exposure_limit: float = 1.0
    fractional_shares: bool = True
    transaction_cost_bps: float = 5.0
    slippage_bps: float = 5.0
    repeated_symbol_policy: str = "reject_while_open"
    duplicate_policy: str = "one_order_per_canonical_observation_id"
    event_order: str = "entries_at_open_then_exits_and_marks_at_close"
    dividends: str = "excluded_price_return_only"
    split_policy: str = "backward_adjusted_price_basis_no_quantity_readjustment"
    version: str = EXECUTION_POLICY_VERSION

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_selected_portfolio_valuation_certification(
    certification: Mapping[str, Any], portfolio: Mapping[str, Any]
) -> None:
    """Reject stale, wrong-scope, or non-verified portfolio certificates."""
    if certification.get("schema_version") != PORTFOLIO_VALUATION_CERTIFICATION_SCHEMA:
        raise ValueError("unsupported selected-portfolio valuation certification")
    if certification.get("scope") != "SELECTED_PORTFOLIO_ACTUAL_HOLDINGS":
        raise ValueError("wrong certification scope for selected portfolio")
    if certification.get("status") != "VERIFIED":
        raise ValueError("selected portfolio lacks complete valuation certification")
    expected = {
        "candidate_id": portfolio.get("candidate_id"),
        "input_sha256": portfolio.get("input_sha256"),
        "policy_sha256": portfolio.get("policy_sha256"),
        "core_certification_sha256": portfolio.get("core_certification_sha256"),
        "execution_events_sha256": _hash(portfolio.get("orders_and_position_events")),
        "holding_valuation_evidence_sha256": _hash(
            (portfolio.get("valuation_evidence_manifest") or {}).get("evidence")
        ),
    }
    if any(certification.get(key) != value for key, value in expected.items()):
        raise ValueError(
            "selected-portfolio valuation certification is stale or forged"
        )


def reconstruct_v4_portfolio_path(
    rows: Iterable[Mapping[str, Any]],
    *,
    candidate_id: str,
    input_sha256: str,
    policy: V4ExecutionPolicy,
    candidate_scope: str = "decision",
    valuation_verifications: Mapping[str, Mapping[str, Any]] | None = None,
    core_certification_sha256: str | None = None,
) -> dict[str, Any]:
    """Execute positive decision-lane signals and mark every XNYS close.

    Slippage changes the fill price once at entry and exit; fees are separate
    cash charges once per fill.  Positions use fixed fractional notional equal
    to starting capital / max_positions and are never leveraged.
    """
    calendar = ExchangeCalendar()
    source = [dict(row) for row in rows]
    reasons: list[str] = []
    if candidate_scope != "decision":
        reasons.append(f"unsupported_candidate_scope:{candidate_scope}")
    seen: set[str] = set()
    orders: list[dict[str, Any]] = []
    interval_entries: list[date] = []
    interval_exits: list[date] = []
    for row in source:
        identifier = str(row.get("canonical_observation_id") or "")
        if not identifier:
            reasons.append("missing_canonical_observation_id")
            continue
        if identifier in seen:
            continue
        seen.add(identifier)
        try:
            interval_entries.append(
                calendar.local_date(datetime.fromisoformat(str(row["entry_at"])))
            )
            interval_exits.append(
                calendar.local_date(datetime.fromisoformat(str(row["exit_at"])))
            )
        except (KeyError, TypeError, ValueError):
            reasons.append(f"missing_evaluation_interval_evidence:{identifier}")
        if int(row.get("prediction", 0)) != 1:
            continue
        try:
            decision = datetime.fromisoformat(str(row["decision_at"]))
            entry = datetime.fromisoformat(str(row["entry_at"]))
            exit_at = datetime.fromisoformat(str(row["exit_at"]))
            entry_price = float(row["adjusted_entry_price"])
            exit_price = float(row["adjusted_exit_price"])
        except (KeyError, TypeError, ValueError):
            reasons.append(f"missing_execution_evidence:{identifier}")
            continue
        marks = {}
        try:
            marks = {
                str(mark["session"]): float(mark["adjusted_close"])
                for mark in row.get("valuation_path") or []
            }
        except (TypeError, ValueError, KeyError):
            # This cannot affect order creation; an accepted holding will fail
            # valuation certification below.
            marks = {}
        entry_session = calendar.local_date(entry)
        exit_session = calendar.local_date(exit_at)
        expected: list[str] = []
        current = entry_session
        while current <= exit_session:
            if calendar.is_trading_day(current):
                expected.append(current.isoformat())
            current = calendar.next_session(current)
        if entry != calendar.session_open(
            entry_session
        ) or exit_at != calendar.session_close(exit_session):
            reasons.append(f"invalid_execution_timestamps:{identifier}")
        orders.append(
            {
                "canonical_observation_id": identifier,
                "symbol": str(row.get("symbol") or ""),
                "security_id": str(row.get("point_in_time_symbol_id") or ""),
                "decision_at": decision.isoformat(),
                "entry_at": entry.isoformat(),
                "exit_at": exit_at.isoformat(),
                "entry_session": entry_session.isoformat(),
                "exit_session": exit_session.isoformat(),
                "score": float(row.get("score", 0.0)),
                "entry_price": entry_price,
                "exit_price": exit_price,
                "marks": marks,
                "valuation_policy_version": row.get("valuation_path_policy_version"),
                "required_valuation_sessions": expected,
            }
        )
    orders.sort(
        key=lambda row: (
            row["entry_at"],
            row["decision_at"],
            -row["score"],
            row["canonical_observation_id"],
        )
    )
    if (
        policy.starting_capital <= 0
        or policy.max_positions <= 0
        or not 0 < policy.gross_exposure_limit <= 1
    ):
        reasons.append("invalid_execution_policy")

    first = min(interval_entries, default=None)
    last = max(interval_exits, default=None)
    sessions: list[date] = []
    if first and last:
        current = first
        while current <= last:
            sessions.append(current)
            current = calendar.next_session(current)
    cash = float(policy.starting_capital)
    positions: dict[str, dict[str, Any]] = {}
    ledger: list[dict[str, Any]] = []
    equity: list[dict[str, Any]] = []
    fees = slippage = realized = turnover_notional = 0.0
    rejected = 0
    previous_equity = policy.starting_capital
    if first:
        equity.append(
            {
                "session": calendar.previous_session(first).isoformat(),
                "cash": cash,
                "market_value": 0.0,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "cumulative_fees": 0.0,
                "cumulative_slippage": 0.0,
                "total_equity": cash,
                "high_water_mark": cash,
                "drawdown": 0.0,
                "daily_return": 0.0,
                "gross_exposure": 0.0,
                "open_positions": 0,
                "observation_type": "initial_capital",
            }
        )
    allocation_budget = (
        policy.starting_capital
        * policy.gross_exposure_limit
        / max(1, policy.max_positions)
    )
    for session in sessions:
        key = session.isoformat()
        for order in (row for row in orders if row["entry_session"] == key):
            reason = None
            if order["symbol"] in positions or any(
                position["security_id"]
                and position["security_id"] == order["security_id"]
                for position in positions.values()
            ):
                reason = "repeated_symbol_while_open"
            elif len(positions) >= policy.max_positions:
                reason = "maximum_positions_reached"
            entry_fill = order["entry_price"] * (1 + policy.slippage_bps / 10_000)
            fee_rate = policy.transaction_cost_bps / 10_000
            entry_notional = allocation_budget / (1 + fee_rate)
            quantity = entry_notional / entry_fill
            fee = entry_notional * fee_rate
            if reason is None and cash + 1e-9 < entry_notional + fee:
                reason = "insufficient_cash"
            if reason:
                rejected += 1
                ledger.append(
                    {
                        **order,
                        "event": "order_rejected",
                        "session": key,
                        "reason": reason,
                    }
                )
                continue
            cash -= entry_notional + fee
            fees += fee
            slippage += quantity * (entry_fill - order["entry_price"])
            turnover_notional += entry_notional
            positions[order["symbol"]] = {
                **order,
                "quantity": quantity,
                "entry_cash": entry_notional + fee,
            }
            missing = sorted(
                set(order["required_valuation_sessions"]) - set(order["marks"])
            )
            if order["valuation_policy_version"] != VALUATION_PATH_POLICY_VERSION:
                reasons.append(
                    f"uncertified_valuation_policy:{order['canonical_observation_id']}"
                )
            if missing:
                reasons.append(
                    f"missing_holding_valuation_sessions:{order['canonical_observation_id']}:{','.join(missing)}"
                )
            if valuation_verifications is not None:
                verification = valuation_verifications.get(
                    order["canonical_observation_id"], {}
                )
                if verification.get("status") != "VERIFIED":
                    failures = verification.get("failures") or ["missing_verification"]
                    reasons.extend(
                        f"holding_valuation_verification_failed:{order['canonical_observation_id']}:{reason}"
                        for reason in failures
                    )
            ledger.append(
                {
                    "event": "entry_fill",
                    "session": key,
                    "canonical_observation_id": order["canonical_observation_id"],
                    "symbol": order["symbol"],
                    "quantity": quantity,
                    "reference_price": order["entry_price"],
                    "fill_price": entry_fill,
                    "notional": entry_notional,
                    "fee": fee,
                }
            )
        closing = [
            position
            for position in positions.values()
            if position["exit_session"] == key
        ]
        for position in sorted(
            closing, key=lambda row: (row["exit_at"], row["canonical_observation_id"])
        ):
            exit_fill = position["exit_price"] * (1 - policy.slippage_bps / 10_000)
            gross = position["quantity"] * exit_fill
            fee = gross * policy.transaction_cost_bps / 10_000
            cash += gross - fee
            fees += fee
            slippage += position["quantity"] * (position["exit_price"] - exit_fill)
            turnover_notional += gross
            trade_pnl = gross - fee - position["entry_cash"]
            realized += trade_pnl
            ledger.append(
                {
                    "event": "exit_fill",
                    "session": key,
                    "canonical_observation_id": position["canonical_observation_id"],
                    "symbol": position["symbol"],
                    "quantity": position["quantity"],
                    "reference_price": position["exit_price"],
                    "fill_price": exit_fill,
                    "notional": gross,
                    "fee": fee,
                    "realized_pnl": trade_pnl,
                }
            )
            del positions[position["symbol"]]
        market_value: float | None = 0.0
        open_cost = 0.0
        missing_open_marks: list[dict[str, str]] = []
        for position in positions.values():
            mark = position["marks"].get(key)
            if mark is None:
                missing_open_marks.append(
                    {
                        "canonical_observation_id": position[
                            "canonical_observation_id"
                        ],
                        "symbol": position["symbol"],
                        "session": key,
                    }
                )
                market_value = None
                continue
            if market_value is not None:
                market_value += position["quantity"] * mark
            open_cost += position["entry_cash"]
        total = cash + market_value if market_value is not None else None
        known_peaks = [
            row["high_water_mark"]
            for row in equity
            if row["high_water_mark"] is not None
        ]
        peak = max(known_peaks + [total]) if total is not None else None
        equity.append(
            {
                "session": key,
                "cash": cash,
                "market_value": market_value,
                "realized_pnl": realized,
                "unrealized_pnl": market_value - open_cost
                if market_value is not None
                else None,
                "cumulative_fees": fees,
                "cumulative_slippage": slippage,
                "total_equity": total,
                "high_water_mark": peak,
                "drawdown": total / peak - 1 if total is not None and peak else None,
                "daily_return": total / previous_equity - 1
                if total is not None and previous_equity is not None and previous_equity
                else None,
                "gross_exposure": market_value / total
                if market_value is not None and total
                else None,
                "open_positions": len(positions),
                "missing_position_marks": missing_open_marks,
                "observation_type": "session_close",
            }
        )
        previous_equity = total
    if positions:
        reasons.append("open_positions_at_terminal_session")
    if (
        equity
        and not positions
        and equity[-1]["total_equity"] is not None
        and abs(equity[-1]["total_equity"] - (policy.starting_capital + realized))
        > 1e-6
    ):
        reasons.append("ledger_equity_reconciliation_failed")
    if any(
        row["gross_exposure"] is not None
        and row["gross_exposure"] > policy.gross_exposure_limit + 1e-9
        for row in equity
    ):
        reasons.append("gross_exposure_limit_exceeded")
    unique_reasons = sorted(set(reasons))
    valid = not unique_reasons
    known_equity = [row for row in equity if row["drawdown"] is not None]
    trough = min(known_equity, key=lambda row: row["drawdown"], default=None)
    peak_date = None
    recovery_session = None
    drawdown_duration_sessions = None
    if trough:
        prior = [row for row in known_equity if row["session"] <= trough["session"]]
        peak_date = max(prior, key=lambda row: row["total_equity"])["session"]
        peak_index = next(
            index for index, row in enumerate(equity) if row["session"] == peak_date
        )
        trough_index = equity.index(trough)
        recovery = next(
            (
                row
                for row in equity[trough_index + 1 :]
                if row["total_equity"] is not None
                and row["total_equity"] >= trough["high_water_mark"]
            ),
            None,
        )
        recovery_session = recovery["session"] if recovery else None
        recovery_index = next(
            (index for index, row in enumerate(equity) if row is recovery),
            len(equity) - 1,
        )
        drawdown_duration_sessions = recovery_index - peak_index
    metrics = {
        "path_valid": valid,
        "evidence_sufficient": valid,
        "max_drawdown_evaluable": valid,
        "invalid_reasons": unique_reasons,
        "starting_capital": policy.starting_capital,
        "ending_equity": equity[-1]["total_equity"] if equity else None,
        "cumulative_return": equity[-1]["total_equity"] / policy.starting_capital - 1
        if equity and equity[-1]["total_equity"] is not None
        else None,
        "max_drawdown": trough["drawdown"] if valid and trough else None,
        "drawdown_sign_convention": "negative_fraction_from_prior_close_high_water_mark; close_to_close_not_intraday",
        "drawdown_peak_session": peak_date if valid else None,
        "drawdown_trough_session": trough["session"] if valid and trough else None,
        "drawdown_recovered": bool(
            valid and trough and equity[-1]["total_equity"] >= trough["high_water_mark"]
        ),
        "drawdown_recovery_session": recovery_session if valid else None,
        "drawdown_duration_sessions": drawdown_duration_sessions if valid else None,
        "trade_count": sum(row["event"] == "exit_fill" for row in ledger),
        "rejected_order_count": rejected,
        "turnover": turnover_notional / policy.starting_capital,
        "cumulative_fees": fees,
        "cumulative_slippage": slippage,
        "extended_valuation_end_session": last.isoformat() if last else None,
    }
    policy_payload = policy.payload()
    accepted_ids = {
        row["canonical_observation_id"]
        for row in ledger
        if row["event"] == "entry_fill"
    }
    evidence = [
        {
            key: row.get(key)
            for key in (
                "canonical_observation_id",
                "security_id",
                "entry_at",
                "exit_at",
                "entry_price",
                "exit_price",
                "marks",
            )
        }
        for row in orders
        if row["canonical_observation_id"] in accepted_ids
    ]
    ledger_hash = _hash(ledger)
    evidence_hash = _hash(evidence)
    valuation_certificate = {
        "schema_version": PORTFOLIO_VALUATION_CERTIFICATION_SCHEMA,
        "scope": "SELECTED_PORTFOLIO_ACTUAL_HOLDINGS",
        "status": "VERIFIED" if valid else "INSUFFICIENT_EVIDENCE",
        "candidate_id": candidate_id,
        "input_sha256": input_sha256,
        "core_certification_sha256": core_certification_sha256,
        "policy_sha256": _hash(policy.payload()),
        "execution_events_sha256": ledger_hash,
        "holding_valuation_evidence_sha256": evidence_hash,
        "invalid_reasons": unique_reasons,
        "all_required_holding_marks_verified": valid,
    }
    return {
        "schema_version": PORTFOLIO_PATH_SCHEMA,
        "candidate_id": candidate_id,
        "evaluated_split": "frozen_final_holdout",
        "input_sha256": input_sha256,
        "core_certification_sha256": core_certification_sha256,
        "candidate_scope": candidate_scope,
        "policy": policy_payload,
        "policy_sha256": _hash(policy_payload),
        "orders_and_position_events": ledger,
        "daily_equity": equity,
        "valuation_evidence_manifest": {
            "basis": policy.split_policy,
            "dividends": policy.dividends,
            "calendar": calendar.identifier,
            "required_price_fields": [
                "adjusted_entry_price",
                "adjusted_exit_price",
                "valuation_path[].adjusted_close",
            ],
            "source_rows": len(source),
            "unique_canonical_ids": len(seen),
            "valuation_evidence_sha256": evidence_hash,
            "evidence": evidence,
        },
        "portfolio_valuation_certification": valuation_certificate,
        "metrics": metrics,
        "automatic_promotion": False,
        "research_only": True,
    }
