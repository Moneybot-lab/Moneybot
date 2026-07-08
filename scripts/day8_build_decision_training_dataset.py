#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yfinance as yf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moneybot.services.decision_log import read_decision_events
from moneybot.services.outcome_tracking import classify_outcome, close_values, normalize_action, normalize_unix_ts
from moneybot.services.runtime_paths import bad_symbol_cache_path, decision_events_log_path

RETURN_BIN_EDGES = (-0.03, -0.005, 0.005, 0.03)



COMMON_SYMBOL_NORMALIZATIONS = {
    "NVDIA": "NVDA",
    "NVSIA": "NVDA",
    "APPL": "AAPL",
    "APPL.": "AAPL",
    "TSL": "TSLA",
    "TESLA": "TSLA",
}

KNOWN_BAD_SYMBOLS = {
    "OLFS",
    "REVN",
    "SDNQ",
    "ADLX",
}

KNOWN_FUND_SYMBOLS = {
    "FDRXX",
    "SPAXX",
}

BAD_SYMBOL_FAILURE_THRESHOLD = 2


def _empty_bad_symbol_cache() -> dict[str, Any]:
    return {"symbols": {}}


def _load_bad_symbol_cache(path: str | Path | None = None) -> dict[str, Any]:
    cache_path = Path(path) if path is not None else bad_symbol_cache_path()
    if not cache_path.exists():
        return _empty_bad_symbol_cache()
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_bad_symbol_cache()
    if not isinstance(payload, dict) or not isinstance(payload.get("symbols"), dict):
        return _empty_bad_symbol_cache()
    return payload


def _save_bad_symbol_cache(cache: dict[str, Any], path: str | Path | None = None) -> None:
    cache_path = Path(path) if path is not None else bad_symbol_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


def _record_bad_symbol(cache: dict[str, Any], symbol: str, reason: str) -> None:
    clean_symbol = str(symbol or "").strip().upper()
    if not clean_symbol:
        return
    symbols = cache.setdefault("symbols", {})
    entry = symbols.setdefault(clean_symbol, {"failures": 0, "reason": reason})
    failures = int(entry.get("failures") or 0) + 1
    entry.update(
        {
            "failures": failures,
            "reason": reason,
            "last_seen_utc": datetime.now(timezone.utc).isoformat(),
        }
    )


def _bad_symbol_failures(cache: dict[str, Any], symbol: str) -> int:
    entry = (cache.get("symbols") or {}).get(str(symbol or "").strip().upper())
    if not isinstance(entry, dict):
        return 0
    try:
        return int(entry.get("failures") or 0)
    except (TypeError, ValueError):
        return 0


def _normalize_symbol_for_training(raw_symbol: str, bad_symbol_cache: dict[str, Any]) -> tuple[str | None, str | None, bool]:
    symbol = str(raw_symbol or "").strip().upper()
    if not symbol:
        return None, "empty_symbol", False

    normalized = COMMON_SYMBOL_NORMALIZATIONS.get(symbol, symbol)
    changed = normalized != symbol
    symbol = normalized

    if symbol in KNOWN_BAD_SYMBOLS:
        return None, "known_bad_symbol", changed
    if symbol in KNOWN_FUND_SYMBOLS:
        return None, "fund_or_cash_equivalent_symbol", changed
    if _bad_symbol_failures(bad_symbol_cache, symbol) >= BAD_SYMBOL_FAILURE_THRESHOLD:
        return None, "cached_yfinance_failure", changed
    if "." in symbol:
        return None, "unsupported_foreign_or_share_class_suffix", changed
    if "-" in symbol or "=" in symbol or "/" in symbol or symbol.startswith("^"):
        return None, "unsupported_non_equity_symbol", changed
    if not symbol.isalpha():
        return None, "non_alpha_symbol", changed
    if not (1 <= len(symbol) <= 5):
        return None, "implausible_equity_symbol_length", changed

    return symbol, None, changed


def _future_return(symbol: str, start_ts: int, days: int, bad_symbol_cache: dict[str, Any] | None = None) -> float | None:
    start_dt = datetime.fromtimestamp(int(start_ts), tz=timezone.utc)
    now_utc = datetime.now(timezone.utc)
    if start_dt >= now_utc:
        return None
    if start_dt + timedelta(days=days) > now_utc:
        return None

    end_dt = start_dt + timedelta(days=max(days + 3, 7))
    safe_end_dt = min(end_dt, now_utc + timedelta(days=1))
    try:
        history = yf.download(
            symbol,
            start=start_dt.strftime("%Y-%m-%d"),
            end=safe_end_dt.strftime("%Y-%m-%d"),
            interval="1d",
            progress=False,
            auto_adjust=False,
        )
    except Exception:  # noqa: BLE001
        if bad_symbol_cache is not None:
            _record_bad_symbol(bad_symbol_cache, symbol, "yfinance_exception")
        return None

    closes = close_values(history)
    if len(closes) <= days:
        if bad_symbol_cache is not None:
            _record_bad_symbol(bad_symbol_cache, symbol, "no_price_data")
        return None
    start_price = float(closes[0])
    end_price = float(closes[days])
    if start_price == 0:
        return None
    return round((end_price - start_price) / start_price, 4)


def _extract_probability(snapshot: dict[str, Any], payload: dict[str, Any]) -> float | None:
    value = snapshot.get("probability_up")
    if not isinstance(value, (int, float)):
        value = payload.get("probability_up")
    return float(value) if isinstance(value, (int, float)) else None


def _extract_model_version(snapshot: dict[str, Any], payload: dict[str, Any]) -> str | None:
    value = snapshot.get("model_version") if isinstance(snapshot.get("model_version"), str) else payload.get("model_version")
    return str(value) if isinstance(value, str) and value.strip() else None


def _extract_recommendation(event: dict[str, Any], snapshot: dict[str, Any]) -> str | None:
    rec = snapshot.get("recommendation") if isinstance(snapshot.get("recommendation"), str) else None
    if rec:
        return rec.strip().upper()
    return normalize_action(event)


def _extract_feature_columns(
    *,
    snapshot: dict[str, Any],
    payload: dict[str, Any],
    return_1d: float | None,
    return_5d: float | None,
) -> dict[str, float]:
    features_raw = snapshot.get("features") if isinstance(snapshot.get("features"), dict) else {}
    quote_raw = snapshot.get("quote") if isinstance(snapshot.get("quote"), dict) else {}
    out: dict[str, float] = {}

    for key, value in features_raw.items():
        if isinstance(value, (int, float)):
            clean_key = str(key)
            if not clean_key.startswith("feature_"):
                clean_key = f"feature_{clean_key}"
            out[clean_key] = float(value)

    fallback_map = {
        "feature_price": quote_raw.get("price") if isinstance(quote_raw.get("price"), (int, float)) else payload.get("price"),
        "feature_change_percent": quote_raw.get("change_percent")
        if isinstance(quote_raw.get("change_percent"), (int, float))
        else payload.get("change_percent"),
        "feature_probability_up": payload.get("probability_up"),
    }
    for key, value in fallback_map.items():
        if key not in out and isinstance(value, (int, float)):
            out[key] = float(value)

    if isinstance(return_1d, (int, float)):
        out.setdefault("feature_return_1d", float(return_1d))
    if isinstance(return_5d, (int, float)):
        out.setdefault("feature_return_5d", float(return_5d))

    return out


def _app_signal_feature_columns(*, recommendation: str, probability_up: float | None, endpoint: str, decision_source: str) -> dict[str, float]:
    rec = str(recommendation or "").strip().upper()
    clean_endpoint = str(endpoint or "").strip().lower()
    clean_source = str(decision_source or "").strip().lower()
    out = {
        "feature_rec_buy": float(rec == "BUY"),
        "feature_rec_sell": float(rec == "SELL"),
        "feature_rec_hold": float(rec == "HOLD"),
        "feature_rec_hold_off_for_now": float(rec == "HOLD OFF FOR NOW"),
        "feature_rec_strong_buy": float(rec == "STRONG BUY"),
        "feature_rec_positive": float(rec in {"BUY", "STRONG BUY"}),
        "feature_rec_negative": float(rec in {"SELL", "HOLD OFF FOR NOW"}),
        "feature_endpoint_quick_ask": float(clean_endpoint == "quick_ask"),
        "feature_endpoint_hot_momentum_buys": float(clean_endpoint == "hot_momentum_buys"),
        "feature_endpoint_user_watchlist": float(clean_endpoint == "user_watchlist"),
        "feature_source_ai_enhanced": float(clean_source == "ai_enhanced"),
        "feature_source_deterministic_model": float(clean_source == "deterministic_model"),
        "feature_source_rule_based": float(clean_source == "rule_based"),
    }
    if isinstance(probability_up, (int, float)):
        out["feature_probability_up"] = float(probability_up)
    return out


def _return_bin(value: float | None) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    ret = float(value)
    if ret < RETURN_BIN_EDGES[0]:
        return "big_loss"
    if ret < RETURN_BIN_EDGES[1]:
        return "loss"
    if ret <= RETURN_BIN_EDGES[2]:
        return "flat"
    if ret <= RETURN_BIN_EDGES[3]:
        return "gain"
    return "big_gain"


def build_rows(events: list[dict[str, Any]], *, horizon_days: int, bad_symbol_cache: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    now_utc = datetime.now(timezone.utc)
    mature_cutoff = now_utc - timedelta(days=max(1, horizon_days + 2))

    scanned = 0
    mature = 0
    labeled = 0
    symbols_normalized = 0
    symbols_rejected = 0
    cache = bad_symbol_cache if bad_symbol_cache is not None else _empty_bad_symbol_cache()
    rows: list[dict[str, Any]] = []

    for event in events:
        scanned += 1
        raw_symbol = str(event.get("symbol") or "")
        symbol, reject_reason, normalized = _normalize_symbol_for_training(raw_symbol, cache)
        if normalized:
            symbols_normalized += 1
        if reject_reason is not None:
            symbols_rejected += 1
            continue

        ts = normalize_unix_ts(event.get("ts"))
        if not symbol or ts is None:
            continue

        event_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        if event_dt > mature_cutoff:
            continue
        mature += 1

        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        snapshot = event.get("snapshot") if isinstance(event.get("snapshot"), dict) else {}
        experiment = event.get("experiment") if isinstance(event.get("experiment"), dict) else {}
        recommendation = _extract_recommendation(event, snapshot)
        if not recommendation:
            continue

        ret_1d = _future_return(symbol, ts, 1, cache)
        ret_5d = _future_return(symbol, ts, max(1, horizon_days), cache)
        if ret_1d is None and ret_5d is None:
            continue

        endpoint = str(event.get("endpoint") or "unknown")
        decision_source = str(event.get("decision_source") or "unknown")
        probability_up = _extract_probability(snapshot, payload)
        row: dict[str, Any] = {
            "ts": ts,
            "symbol": symbol,
            "endpoint": endpoint,
            "decision_source": decision_source,
            "recommendation": recommendation,
            "probability_up": probability_up,
            "model_version": _extract_model_version(snapshot, payload),
            "return_1d": ret_1d,
            "return_5d": ret_5d,
            "outcome_1d": classify_outcome(recommendation, ret_1d),
            "outcome_5d": classify_outcome(recommendation, ret_5d),
            "label_up_5d": int(ret_5d > 0) if isinstance(ret_5d, (int, float)) else None,
            "return_bin_5d": _return_bin(ret_5d),
            "label_profit_5d": int(ret_5d > 0) if isinstance(ret_5d, (int, float)) else None,
            "label_drawdown_5d": int(ret_5d <= -0.02) if isinstance(ret_5d, (int, float)) else None,
            "has_snapshot": int(bool(snapshot)),
            "has_feature_map": int(isinstance(snapshot.get("features"), dict) and bool(snapshot.get("features"))),
            "has_model_version": int(bool(_extract_model_version(snapshot, payload))),
            "experiment_id": str(experiment.get("experiment_id") or "default"),
            "cohort_id": str(experiment.get("cohort_id") or "unknown"),
            "rollout_dry_run": bool(experiment.get("rollout_dry_run", False)),
            "rollout_percentage": experiment.get("rollout_percentage"),
            "portfolio_rollout_percentage": experiment.get("portfolio_rollout_percentage"),
        }
        row.update(
            _extract_feature_columns(
                snapshot=snapshot,
                payload=payload,
                return_1d=ret_1d,
                return_5d=ret_5d,
            )
        )
        row.update(
            _app_signal_feature_columns(
                recommendation=recommendation,
                probability_up=probability_up,
                endpoint=endpoint,
                decision_source=decision_source,
            )
        )

        rows.append(row)
        labeled += 1

    yfinance_failures = sum(int((entry or {}).get("failures") or 0) for entry in (cache.get("symbols") or {}).values() if isinstance(entry, dict))
    return rows, {
        "rows_scanned": scanned,
        "mature_rows": mature,
        "labeled_rows": labeled,
        "symbols_normalized": symbols_normalized,
        "symbols_rejected": symbols_rejected,
        "symbol_yfinance_failures": yfinance_failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build decision training dataset from historical decision events.")
    parser.add_argument("--input", default=str(decision_events_log_path()))
    parser.add_argument("--output", default="data/decision_training_snapshot.jsonl")
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--horizon-days", type=int, default=5)
    args = parser.parse_args()

    bad_symbol_cache = _load_bad_symbol_cache()
    events = read_decision_events(args.input, limit=max(1, args.limit))
    rows, summary = build_rows(events, horizon_days=max(1, args.horizon_days), bad_symbol_cache=bad_symbol_cache)
    _save_bad_symbol_cache(bad_symbol_cache)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, default=str) + "\n")

    print(json.dumps({**summary, "output": str(output_path)}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
