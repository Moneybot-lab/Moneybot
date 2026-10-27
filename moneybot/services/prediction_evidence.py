"""Prediction contracts, independent of portfolio actions and narrative advice."""
from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime, timezone

SCHEMA = "model_forecast.v1"
OUTCOME_CONTRACT = "xnys_next_available_close_plus_5_sessions_split_adjusted.v1"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def probability(value):
    # This contract is a numeric fraction, never a percentage or confidence label.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and 0 <= value <= 1 else None


def timestamp(value):
    try:
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.timestamp() if parsed.tzinfo else None
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return float(value) if value > 0 else None
    except (ValueError, OverflowError):
        pass
    return None


def quote_provenance(quote):
    diagnostics = quote.get("diagnostics") or {}
    return {key: quote.get(key) for key in (
        "event_timestamp", "received_timestamp", "market_session", "is_stale",
        "quality_flags", "is_delayed", "delay_seconds", "price_reason",
    )} | {
        "source": quote.get("source") or quote.get("quote_source"),
        "source_mode": quote.get("source_mode") or diagnostics.get("source_mode"),
    }


def make_forecast(*, symbol, artifact, features, quote, recommendation, probability_up,
                  raw_probability_up, horizon, calibration, shadow_only=False, now=None, input_kind="model_features", outcome_contract=OUTCOME_CONTRACT):
    """Called ONLY at model inference. Identical inputs share one prediction ID.

    Receipt time, request/user IDs and personalized actions are not identity inputs.
    A missing source timestamp leaves identity unverifiable; it is never invented.
    """
    issued = float(time.time() if now is None else now)
    market = quote_provenance(quote)
    source_ts = timestamp(market["event_timestamp"])
    contract = {
        "schema_version": SCHEMA, "symbol": str(symbol or "").upper(),
        "model_version": artifact.get("version"), "model_artifact_sha256": digest(artifact),
        "forecast_horizon": horizon, "outcome_contract": outcome_contract, "input_kind": input_kind,
        "probability_up": probability_up, "raw_probability_up": raw_probability_up,
        "recommendation": recommendation, "calibration": calibration,
        "input_sha256": digest({"features": features, "quote_timestamp": source_ts,
                                "quote_price": quote.get("price"), "quote_source": market["source"]}),
    }
    identity = digest(contract) if source_ts is not None and source_ts <= issued else None
    return contract | {"prediction_id": identity, "decision_timestamp": issued,
                       "market_data_provenance": market, "shadow_only": bool(shadow_only)}


def classify_event(endpoint, source, forecast):
    if endpoint == "user_watchlist":
        return "portfolio_advice"
    if isinstance(forecast, dict) and forecast.get("schema_version") == SCHEMA:
        return "shadow_prediction" if forecast.get("shadow_only") else "probabilistic_model_forecast"
    if source == "portfolio_quote_only":
        return "quote_observation"
    if source == "ai_enhanced":
        return "ai_narrative"
    if source == "deterministic_model":
        return "unidentified_model_prediction"
    return "rule_based_recommendation"
