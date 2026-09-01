"""Research-only Alpha Atlas V4 Phase 0 evidence primitives."""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from moneybot.services.alpha_atlas_v4_canonical_observations import (
    CanonicalizationError,
    canonical_observation_id,
)
from moneybot.services.market_data_providers import ExchangeCalendar
from moneybot.services.corporate_actions import (
    CORPORATE_ACTION_AVAILABILITY_POLICY_VERSION,
)

FEATURE_CONTRACT_VERSION = "alpha-atlas-v4-features.v2"
FEATURE_REGISTRY_VERSION = "alpha-atlas-v4-feature-registry.v2"
FILL_POLICY_VERSION = "alpha-atlas-v4-feature-fill-policy.v1"
RECONSTRUCTION_VERSION = "alpha-atlas-v4-reconstructability.v1"
TEMPORAL_CERTIFICATION_VERSION = "alpha-atlas-v4-temporal-safety-certification.v1"
RECONSTRUCTION_LINEAGE_VERSION = "alpha-atlas-v4-reconstruction-lineage.v1"

MODEL_FEATURES = (
    "feature_above_vwap",
    "feature_atr_14",
    "feature_close",
    "feature_distance_from_20d_low",
    "feature_dollar_volume",
    "feature_drawdown_from_20d_high",
    "feature_ema_10",
    "feature_ema_20",
    "feature_gap_percent",
    "feature_macd",
    "feature_macd_hist",
    "feature_macd_signal",
    "feature_market_regime_risk_on",
    "feature_market_volatility_proxy",
    "feature_momentum_5d_vs_20d",
    "feature_price_vs_sma_20",
    "feature_price_vs_sma_50",
    "feature_price_vs_vwap",
    "feature_relative_volume_5d",
    "feature_return_10d_lagged",
    "feature_return_1d_lagged",
    "feature_return_20d_lagged",
    "feature_return_5d_lagged",
    "feature_rsi_14",
    "feature_sector_relative_return_5d",
    "feature_sma_10",
    "feature_sma_10_over_20",
    "feature_sma_20",
    "feature_sma_20_over_50",
    "feature_sma_50",
    "feature_spy_return_1d",
    "feature_spy_return_5d",
    "feature_symbol_beta_20d",
    "feature_symbol_minus_spy_5d",
    "feature_trend_slope_10d",
    "feature_trend_slope_20d",
    "feature_volatility_20d",
    "feature_volatility_5d",
    "feature_volume",
    "feature_volume_ratio_20d",
    "feature_volume_zscore_20d",
    "feature_vwap",
    "feature_vwap_slope",
)
FEATURE_STORE_PROVENANCE_COLUMNS = (
    "feature_cutoff_at",
    "feature_family_available_at",
    "feature_family_source_at",
    "feature_market_asof_date",
    "feature_split_ids",
)
PRIOR_REQUEST_FEATURES = ("feature_probability_up_delta_from_last_signal",)


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    ).encode()


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _date_extent(frame: pd.DataFrame) -> tuple[str | None, str | None]:
    for field in ("feature_cutoff_at", "decision_at", "event_date", "ts"):
        if field not in frame:
            continue
        values = pd.to_datetime(frame[field], utc=True, errors="coerce")
        values = values.dropna()
        if not values.empty:
            return values.min().isoformat(), values.max().isoformat()
    return None, None


def fit_feature_fill_policy(
    fit_frame: pd.DataFrame,
    feature_columns: Iterable[str],
    *,
    feature_contract_version: str = FEATURE_CONTRACT_VERSION,
) -> dict[str, Any]:
    """Fit deterministic medians using only the supplied fit frame."""
    columns = sorted(dict.fromkeys(str(column) for column in feature_columns))
    rows_for_hash: list[dict[str, Any]] = [
        {
            "row": int(position),
            "canonical_observation_id": row.get("canonical_observation_id"),
            "feature_cutoff_at": row.get("feature_cutoff_at"),
        }
        for position, row in fit_frame.reset_index(drop=True).iterrows()
    ]
    fitted: dict[str, dict[str, Any]] = {}
    for column in columns:
        if column not in fit_frame:
            raise ValueError(f"fit frame missing feature: {column}")
        numeric = pd.to_numeric(fit_frame[column], errors="coerce").replace(
            [np.inf, -np.inf], np.nan
        )
        present = numeric.dropna()
        value = float(present.median()) if not present.empty else 0.0
        fitted[column] = {
            "fill_method": (
                "fit_period_median"
                if not present.empty
                else "constant_zero_no_fit_values"
            ),
            "fitted_value": value,
            "fit_non_missing_rows": int(len(present)),
        }
        rows_for_hash.append(
            {
                "feature": column,
                "values": [None if pd.isna(v) else float(v) for v in numeric],
            }
        )
    start, end = _date_extent(fit_frame)
    core = {
        "schema_version": FILL_POLICY_VERSION,
        "feature_contract_version": feature_contract_version,
        "fit_period_rows": int(len(fit_frame)),
        "fit_period_start": start,
        "fit_period_end": end,
        "fit_input_sha256": sha256_value(rows_for_hash),
        "features": fitted,
    }
    return {**core, "policy_sha256": sha256_value(core)}


def validate_fill_policy(
    policy: Mapping[str, Any], *, expected_feature_contract_version: str | None = None
) -> None:
    supplied = str(policy.get("policy_sha256") or "")
    core = {key: value for key, value in policy.items() if key != "policy_sha256"}
    if policy.get("schema_version") != FILL_POLICY_VERSION or supplied != sha256_value(
        core
    ):
        raise ValueError("invalid or stale V4 feature-fill policy")
    if (
        expected_feature_contract_version
        and policy.get("feature_contract_version") != expected_feature_contract_version
    ):
        raise ValueError("feature-fill policy contract mismatch")


def apply_feature_fill_policy(
    frame: pd.DataFrame,
    policy: Mapping[str, Any],
    *,
    expected_feature_contract_version: str | None = None,
) -> pd.DataFrame:
    validate_fill_policy(
        policy, expected_feature_contract_version=expected_feature_contract_version
    )
    out = frame.copy()
    for column, spec in sorted(policy["features"].items()):
        if column not in out:
            raise ValueError(f"apply frame missing feature: {column}")
        numeric = pd.to_numeric(out[column], errors="coerce").replace(
            [np.inf, -np.inf], np.nan
        )
        out[column] = numeric.fillna(float(spec["fitted_value"])).astype(float)
    return out


def feature_registry() -> dict[str, Any]:
    calculations = {
        "feature_close": "split-basis close(T)",
        "feature_volume": "split-basis volume(T)",
        "feature_sma_10": "mean(close T-9..T)",
        "feature_sma_20": "mean(close T-19..T)",
        "feature_sma_50": "mean(close T-49..T)",
        "feature_sma_10_over_20": "SMA10/SMA20",
        "feature_sma_20_over_50": "SMA20/SMA50",
        "feature_ema_10": "EMA10(close through T)",
        "feature_ema_20": "EMA20(close through T)",
        "feature_price_vs_sma_20": "close(T)/SMA20-1",
        "feature_price_vs_sma_50": "close(T)/SMA50-1",
        "feature_return_1d_lagged": "close(T)/close(T-1)-1",
        "feature_return_5d_lagged": "close(T)/close(T-5)-1",
        "feature_return_10d_lagged": "close(T)/close(T-10)-1",
        "feature_return_20d_lagged": "close(T)/close(T-20)-1",
        "feature_spy_return_1d": "SPY close(T)/SPY close(T-1)-1",
        "feature_spy_return_5d": "SPY close(T)/SPY close(T-5)-1",
        "feature_symbol_minus_spy_5d": "symbol return5-SPY return5",
        "feature_sector_relative_return_5d": "symbol return5-sector return5",
        "feature_momentum_5d_vs_20d": "return5-return20",
        "feature_volume_ratio_20d": "volume(T)/mean(volume T-19..T)",
        "feature_relative_volume_5d": "volume(T)/mean(volume T-4..T)",
        "feature_dollar_volume": "close(T)*volume(T)",
        "feature_price_vs_vwap": "close(T)/VWAP(T)-1",
        "feature_above_vwap": "1 iff close(T)>VWAP(T)",
        "feature_gap_percent": "open(T)/close(T-1)-1",
        "feature_drawdown_from_20d_high": "close(T)/max(high T-19..T)-1",
        "feature_distance_from_20d_low": "close(T)/min(low T-19..T)-1",
        "feature_rsi_14": "simple-average RSI14 through T",
        "feature_macd": "EMA12(close)-EMA26(close)",
        "feature_macd_signal": "EMA9(MACD)",
        "feature_macd_hist": "MACD-MACD signal",
        "feature_atr_14": "mean true range over T-13..T",
        "feature_symbol_beta_20d": "cov(symbol,SPY)/var(SPY) over 20 returns",
        "feature_market_regime_risk_on": "1 iff SPY return5>0 and SPY close>=SMA20",
        "feature_market_volatility_proxy": "population stddev of SPY backward returns",
        "feature_volatility_5d": "population stddev of 5 backward symbol returns",
        "feature_volatility_20d": "population stddev of 20 backward symbol returns",
        "feature_volume_zscore_20d": "(volume(T)-mean20)/population_stddev20",
        "feature_trend_slope_10d": "OLS slope of close over 10 sessions",
        "feature_trend_slope_20d": "OLS slope of close over 20 sessions",
        "feature_vwap_slope": "OLS slope of VWAP over configured trailing window",
        "feature_vwap": "sum(close*volume T-19..T)/sum(volume T-19..T)",
    }
    lookbacks = {name: 1 for name in MODEL_FEATURES}
    for name in MODEL_FEATURES:
        for token, sessions in (
            ("50", 50),
            ("20", 20),
            ("10", 10),
            ("5d", 5),
            ("14", 14),
            ("macd", 35),
            ("beta", 21),
            ("rsi", 15),
        ):
            if token in name:
                lookbacks[name] = max(lookbacks[name], sessions)
    lookbacks.update(
        {
            "feature_above_vwap": 20,
            "feature_gap_percent": 2,
            "feature_market_regime_risk_on": 20,
            "feature_market_volatility_proxy": 21,
            "feature_price_vs_vwap": 20,
            "feature_vwap": 20,
            "feature_vwap_slope": 29,
        }
    )
    records = []
    for name in MODEL_FEATURES:
        family = "symbol"
        dependency = None
        if "spy" in name or name in {
            "feature_symbol_beta_20d",
            "feature_market_regime_risk_on",
            "feature_market_volatility_proxy",
        }:
            family, dependency = "spy_context", "SPY"
        elif "sector" in name:
            family, dependency = "sector_context", "point_in_time_sector_benchmark"
        alignment_policy = "independent_family_latest_completed_session"
        if name in {
            "feature_symbol_minus_spy_5d",
            "feature_sector_relative_return_5d",
        }:
            alignment_policy = "exact_session_inner_join_common_return_endpoint"
        elif name == "feature_symbol_beta_20d":
            alignment_policy = "exact_matching_start_and_end_session_return_pairs"
        records.append(
            {
                "name": name,
                "classification": "model_input",
                "model_input": True,
                "source_family": family,
                "source_fields": ["daily_ohlcv"],
                "calculation": calculations.get(
                    name, "builder-defined deterministic trailing daily-bar calculation"
                ),
                "calculation_version": FEATURE_CONTRACT_VERSION,
                "required_lookback_sessions": lookbacks[name],
                "source_event_timestamp": "official_session_close_at_or_before_feature_cutoff",
                "source_availability_timestamp": "provider availability must be at_or_before_feature_cutoff",
                "cutoff_rule": "source_at_and_available_at <= feature_cutoff_at",
                "missing_data_policy": "fit_period_median_after_required-family fail_closed checks",
                "fill_policy": FILL_POLICY_VERSION,
                "adjustment": "event_time_split_adjusted",
                "context_dependency": dependency,
                "source_alignment_policy": alignment_policy,
                "point_in_time_security_identity_required": True,
                "reconstructability_status": "REQUIRES_IMMUTABLE_SOURCE_LINEAGE",
                "serving_equivalent_source": "daily aggregate history; parity not certified",
                "feature_contract_version": FEATURE_CONTRACT_VERSION,
            }
        )
    for name in FEATURE_STORE_PROVENANCE_COLUMNS:
        records.append(
            {
                "name": name,
                "classification": "provenance",
                "model_input": False,
                "source_family": "lineage",
                "missing_data_policy": "required_for_reconstruction",
                "feature_contract_version": FEATURE_CONTRACT_VERSION,
            }
        )
    return {
        "schema_version": FEATURE_REGISTRY_VERSION,
        "source_alignment_policy_version": "alpha-atlas-v4-source-alignment.v1",
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
        "feature_store_feature_columns": len(records),
        "model_input_count": len(MODEL_FEATURES),
        "provenance_count": len(FEATURE_STORE_PROVENANCE_COLUMNS),
        "reconciliation": "48 feature-store columns = 43 model inputs + 5 provenance columns",
        "columns": sorted(records, key=lambda item: item["name"]),
        "registry_sha256": sha256_value(sorted(records, key=lambda item: item["name"])),
    }


def validate_feature_registry(
    columns: Iterable[str], registry: Mapping[str, Any] | None = None
) -> None:
    registry = registry or feature_registry()
    expected = {item["name"] for item in registry["columns"]}
    actual = set(map(str, columns))
    if actual != expected:
        raise ValueError(
            f"V4 feature registry mismatch missing={sorted(expected-actual)} unexpected={sorted(actual-expected)}"
        )
    if any(name in actual for name in PRIOR_REQUEST_FEATURES):
        raise ValueError("prior-request state cannot be V4 model evidence")


def _source_rows(source: Any) -> list[dict[str, Any]]:
    rows = source if isinstance(source, list) else source.get("rows")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("source does not contain normalized rows")
    return rows


def _replay_v4_features(
    loaded: Mapping[str, Any], lineage: Mapping[str, Any]
) -> dict[str, float | int | None]:
    """Replay the production V4 builder formulas from hash-verified bar histories."""
    if lineage.get("replay_engine_version") != "massive-v4-feature-replay.v2":
        raise ValueError("unsupported feature replay engine")
    from scripts import build_massive_decision_training_rows as builder

    symbol = _source_rows(loaded["symbol"])
    spy = _source_rows(loaded["spy"])
    sector = _source_rows(loaded["sector"])
    indices = lineage.get("source_indices") or {}
    idx = int(indices["symbol"])
    spy_idx = int(indices["spy"])
    sector_idx = int(indices["sector"])
    if idx < 5 or spy_idx < 5 or sector_idx < 5:
        raise ValueError("insufficient source lookback for builder eligibility")
    asof = symbol[idx]
    close = float(asof["close"])
    sma10 = builder._rolling_close_mean(symbol, idx, 10)
    sma20 = builder._rolling_close_mean(symbol, idx, 20)
    sma50 = builder._rolling_close_mean(symbol, idx, 50)
    return5 = builder._lagged_return(symbol, idx, 5)
    return20 = builder._lagged_return(symbol, idx, 20)
    spy_return5 = builder._lagged_return(spy, spy_idx, 5)
    sector_return5 = builder._lagged_return(sector, sector_idx, 5)
    aligned_symbol_spy_5d, aligned_spy_5d, _ = builder._aligned_relative_returns(
        symbol, spy, idx, spy_idx, 5
    )
    aligned_symbol_sector_5d, aligned_sector_5d, _ = builder._aligned_relative_returns(
        symbol, sector, idx, sector_idx, 5
    )
    volume = builder._coerce_float(asof.get("volume"))
    volume5 = builder._rolling_numeric_mean(symbol, idx, 5, "volume")
    volume20 = builder._rolling_numeric_mean(symbol, idx, 20, "volume")
    high20 = builder._rolling_extreme(symbol, idx, 20, "high", high=True)
    low20 = builder._rolling_extreme(symbol, idx, 20, "low", high=False)
    vwap = builder._rolling_vwap(symbol, idx, 20)
    macd, macd_signal, macd_hist = builder._macd_components_at(symbol, idx)
    replayed = {
        "feature_close": close,
        "feature_sma_10": sma10,
        "feature_sma_20": sma20,
        "feature_sma_50": sma50,
        "feature_sma_10_over_20": builder._ratio(sma10, sma20),
        "feature_sma_20_over_50": builder._ratio(sma20, sma50),
        "feature_trend_slope_10d": builder._trend_slope(symbol, idx, 10),
        "feature_trend_slope_20d": builder._trend_slope(symbol, idx, 20),
        "feature_volatility_5d": builder._return_volatility(symbol, idx, 5),
        "feature_volatility_20d": builder._return_volatility(symbol, idx, 20),
        "feature_drawdown_from_20d_high": builder._pct(close, high20),
        "feature_distance_from_20d_low": builder._pct(close, low20),
        "feature_gap_percent": builder._pct(
            asof.get("open"), symbol[idx - 1].get("close")
        ),
        "feature_ema_10": builder._ema_at(symbol, idx, 10),
        "feature_ema_20": builder._ema_at(symbol, idx, 20),
        "feature_price_vs_sma_20": builder._pct(close, sma20),
        "feature_price_vs_sma_50": builder._pct(close, sma50),
        "feature_rsi_14": builder._rsi_at(symbol, idx, 14),
        "feature_macd": macd,
        "feature_macd_signal": macd_signal,
        "feature_macd_hist": macd_hist,
        "feature_atr_14": builder._atr_at(symbol, idx, 14),
        "feature_spy_return_1d": builder._lagged_return(spy, spy_idx, 1),
        "feature_spy_return_5d": spy_return5,
        "feature_symbol_minus_spy_5d": (
            round(aligned_symbol_spy_5d - aligned_spy_5d, 6)
            if aligned_symbol_spy_5d is not None and aligned_spy_5d is not None
            else None
        ),
        "feature_symbol_beta_20d": builder._date_aligned_beta(
            symbol, spy, idx, spy_idx, 20
        ),
        "feature_sector_relative_return_5d": (
            round(aligned_symbol_sector_5d - aligned_sector_5d, 6)
            if aligned_symbol_sector_5d is not None and aligned_sector_5d is not None
            else None
        ),
        "feature_market_regime_risk_on": builder._market_regime_risk_on(spy, spy_idx),
        "feature_market_volatility_proxy": builder._return_volatility(spy, spy_idx, 20),
        "feature_return_1d_lagged": builder._lagged_return(symbol, idx, 1),
        "feature_return_5d_lagged": return5,
        "feature_return_10d_lagged": builder._lagged_return(symbol, idx, 10),
        "feature_return_20d_lagged": return20,
        "feature_momentum_5d_vs_20d": (
            round(return5 - return20, 6)
            if return5 is not None and return20 is not None
            else None
        ),
        "feature_volume": volume,
        "feature_volume_ratio_20d": builder._ratio(volume, volume20),
        "feature_relative_volume_5d": builder._ratio(volume, volume5),
        "feature_volume_zscore_20d": builder._rolling_zscore(symbol, idx, 20, "volume"),
        "feature_vwap": vwap,
        "feature_price_vs_vwap": builder._pct(close, vwap),
        "feature_vwap_slope": builder._vwap_slope(symbol, idx, 10, 20),
        "feature_above_vwap": int(close > vwap) if vwap is not None else None,
        "feature_dollar_volume": (
            round(close * volume, 6) if volume is not None else None
        ),
    }
    # The builder deliberately overlays the shared V3 train/serve calculations.
    # Replay that same final write, including its exact insufficient-history None.
    from moneybot.services.alpha_atlas_v3_features import build_alpha_atlas_v3_features

    replayed.update(
        build_alpha_atlas_v3_features(
            symbol_bars=symbol[: idx + 1],
            spy_bars=spy[: spy_idx + 1],
            asof_date=None,
        )
    )
    # The shared overlay owns standalone SPY features; cross-family features use
    # the V4 exact-session alignment semantics above.
    replayed.update(
        {
            "feature_symbol_minus_spy_5d": (
                round(aligned_symbol_spy_5d - aligned_spy_5d, 6)
                if aligned_symbol_spy_5d is not None and aligned_spy_5d is not None
                else None
            ),
            "feature_sector_relative_return_5d": (
                round(aligned_symbol_sector_5d - aligned_sector_5d, 6)
                if aligned_symbol_sector_5d is not None
                and aligned_sector_5d is not None
                else None
            ),
            "feature_symbol_beta_20d": builder._date_aligned_beta(
                symbol, spy, idx, spy_idx, 20
            ),
        }
    )
    return replayed


def verify_observation(
    row: Mapping[str, Any],
    *,
    root: Path,
    cache: dict[str, Any] | None = None,
    tolerance: float = 1e-6,
) -> dict[str, Any]:
    """Independently replay a lineage-bearing observation; fail closed otherwise."""
    cache = cache if cache is not None else {}
    failures: list[str] = []
    mismatches: list[dict[str, Any]] = []
    lineage = row.get("reconstruction_lineage")
    if not isinstance(lineage, dict):
        return {
            "canonical_observation_id": row.get("canonical_observation_id"),
            "status": "NOT_RECONSTRUCTABLE",
            "failures": ["missing_reconstruction_lineage"],
        }
    try:
        if canonical_observation_id(row) != row.get("canonical_observation_id"):
            failures.append("canonical_observation_id_mismatch")
    except (CanonicalizationError, TypeError, ValueError):
        failures.append("canonical_observation_id_replay_failed")
    cutoff = pd.to_datetime(row.get("feature_cutoff_at"), utc=True, errors="coerce")
    if pd.isna(cutoff):
        failures.append("missing_feature_cutoff_at")
    loaded: dict[str, Any] = {}
    bundle_mode = lineage.get("schema_version") == RECONSTRUCTION_LINEAGE_VERSION
    if bundle_mode:
        bundle_loaded, resolved, bundle_failures = _resolve_evidence_bundle(
            row=row, lineage=lineage, root=root, cache=cache, cutoff=cutoff
        )
        loaded.update(bundle_loaded)
        failures.extend(bundle_failures)
        lineage = resolved
    required = {"symbol", "spy", "sector", "reference"}
    for source_spec in ([] if bundle_mode else lineage.get("sources") or []):
        family = str(source_spec.get("family") or "")
        required.discard(family)
        relative = str(source_spec.get("path") or "")
        path = (root / relative).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            failures.append(f"source_path_outside_root:{family}")
            continue
        if not path.is_file():
            failures.append(f"missing_source:{family}")
            continue
        actual_hash = sha256_file(path)
        if actual_hash != source_spec.get("sha256"):
            failures.append(f"source_hash_mismatch:{family}")
            continue
        available = pd.to_datetime(
            source_spec.get("available_at"), utc=True, errors="coerce"
        )
        event_at = pd.to_datetime(
            source_spec.get("event_at"), utc=True, errors="coerce"
        )
        if pd.isna(available) or pd.isna(event_at):
            failures.append(f"missing_source_timestamp:{family}")
        elif not pd.isna(cutoff) and available > cutoff:
            failures.append(f"future_source_availability:{family}")
        if source_spec.get("staleness_status") != "fresh":
            failures.append(f"stale_or_unproven_source:{family}")
        if relative not in cache:
            cache[relative] = json.loads(path.read_text())
        loaded[family] = cache[relative]
    if bundle_mode:
        required.difference_update(loaded)
    failures.extend(f"missing_required_context:{family}" for family in sorted(required))
    if lineage.get("feature_contract_version") != FEATURE_CONTRACT_VERSION:
        failures.append("feature_contract_mismatch")
    if lineage.get("calendar_contract_version") != row.get("exchange_calendar"):
        failures.append("calendar_contract_mismatch")
    fill_policy = lineage.get("fill_policy")
    if fill_policy:
        try:
            validate_fill_policy(
                fill_policy,
                expected_feature_contract_version=FEATURE_CONTRACT_VERSION,
            )
        except ValueError:
            failures.append("fill_policy_mismatch")
    try:
        replayed_features = _replay_v4_features(loaded, lineage)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        replayed_features = {}
        failures.append("feature_replay_engine_failed")
    for feature in MODEL_FEATURES:
        replay = replayed_features.get(feature)
        observed = row.get(feature)
        if replay is None and observed is None:
            continue
        if not isinstance(replay, (int, float)) or not isinstance(
            observed, (int, float)
        ):
            failures.append(f"feature_replay_failed:{feature}")
        elif not np.isclose(
            float(replay), float(observed), rtol=tolerance, atol=tolerance
        ):
            failures.append(f"feature_mismatch:{feature}")
            absolute = abs(float(replay) - float(observed))
            mismatches.append(
                {
                    "feature": feature,
                    "stored_value": observed,
                    "replayed_value": replay,
                    "absolute_difference": absolute,
                    "relative_difference": (
                        absolute / abs(float(observed))
                        if float(observed) != 0.0
                        else None
                    ),
                    "accepted_rtol": tolerance,
                    "accepted_atol": tolerance,
                    "symbol_source_row_ids": lineage.get("symbol_row_ids") or [],
                    "spy_source_row_ids": lineage.get("spy_row_ids") or [],
                    "sector_source_row_ids": lineage.get("sector_row_ids") or [],
                    "calculation_engine_version": lineage.get(
                        "calculation_engine_version"
                    ),
                }
            )
    execution = lineage.get("execution") or {}
    try:
        entry = float(execution["entry_price"])
        exit_price = float(execution["exit_price"])
        factor = float(execution.get("split_factor", 1.0))
        replay_return = exit_price / (entry * factor) - 1.0
        if str(execution.get("entry_at")) != str(row.get("entry_at")):
            failures.append("entry_mismatch")
        if str(execution.get("exit_at")) != str(row.get("exit_at")):
            failures.append("exit_mismatch")
        calendar = ExchangeCalendar()
        entry_session = datetime.fromisoformat(str(execution["entry_session"])).date()
        exit_session = datetime.fromisoformat(str(execution["exit_session"])).date()
        if calendar.session_open(entry_session).isoformat() != str(row.get("entry_at")):
            failures.append("entry_calendar_mismatch")
        if calendar.session_close(exit_session).isoformat() != str(row.get("exit_at")):
            failures.append("exit_calendar_mismatch")
        if not np.isclose(
            replay_return, float(row["return_5d"]), rtol=tolerance, atol=tolerance
        ):
            failures.append("target_mismatch")
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        failures.append("missing_executable_label_lineage")
    action_source = lineage.get("corporate_action_source") or {}
    action_relative = str(action_source.get("path") or "")
    action_path = (root / action_relative).resolve()
    if bundle_mode:
        action_payload = loaded.get("corporate_actions")
        if not isinstance(action_payload, dict):
            failures.append("missing_corporate_action_source")
        else:
            actions = action_payload.get("actions") or []
            by_id = {
                str(item.get("id") or ""): item
                for item in actions
                if isinstance(item, dict)
            }
            for action_id in row.get("feature_split_ids") or []:
                action = by_id.get(str(action_id))
                if action is None:
                    failures.append(f"missing_feature_action:{action_id}")
                    continue
                evidence = action.get("availability_evidence") or {}
                kind = evidence.get("kind")
                available = pd.NaT
                if kind == "EXPLICIT_PROVIDER_AVAILABILITY":
                    available = pd.to_datetime(
                        evidence.get("available_at"), utc=True, errors="coerce"
                    )
                elif kind == "EXECUTED_ACTION_SESSION_CLOSE":
                    try:
                        execution_day = date.fromisoformat(
                            str(action["execution_date"])
                        )
                        computed = pd.Timestamp(calendar.session_close(execution_day))
                        claimed = pd.to_datetime(
                            evidence.get("available_at"), utc=True, errors="coerce"
                        )
                        if (
                            evidence.get("execution_date") == execution_day.isoformat()
                            and evidence.get("policy_version")
                            == CORPORATE_ACTION_AVAILABILITY_POLICY_VERSION
                            and evidence.get("calendar_contract_version")
                            == calendar.identifier
                            and claimed == computed
                        ):
                            available = computed
                    except (KeyError, TypeError, ValueError):
                        available = pd.NaT
                elif action.get("available_at"):
                    # Backward-compatible immutable bundles with provider timestamps.
                    available = pd.to_datetime(
                        action.get("available_at"), utc=True, errors="coerce"
                    )
                if pd.isna(available) or (not pd.isna(cutoff) and available > cutoff):
                    failures.append(f"unproven_feature_action_availability:{action_id}")
            for action_id in row.get("label_split_ids") or []:
                if str(action_id) not in by_id:
                    failures.append(f"missing_label_action:{action_id}")
    elif not action_relative or not action_path.is_file():
        failures.append("missing_corporate_action_source")
    else:
        action_hash = sha256_file(action_path)
        if action_hash != action_source.get("sha256"):
            failures.append("corporate_action_source_hash_mismatch")
        if row.get("corporate_action_manifest_sha256") != action_hash:
            failures.append("corporate_action_manifest_hash_mismatch")
        try:
            action_payload = json.loads(action_path.read_text())
            actions = action_payload.get("actions", action_payload)
            if not isinstance(actions, list):
                raise ValueError("actions are not a list")
            by_id = {
                str(action.get("id") or ""): action
                for action in actions
                if isinstance(action, dict) and action.get("id")
            }
            for action_id in row.get("feature_split_ids") or []:
                action = by_id.get(str(action_id))
                if action is None:
                    failures.append(f"missing_feature_action:{action_id}")
                    continue
                available = pd.to_datetime(
                    action.get("available_at"), utc=True, errors="coerce"
                )
                if pd.isna(available) or (not pd.isna(cutoff) and available > cutoff):
                    failures.append(f"unproven_feature_action_availability:{action_id}")
            for action_id in row.get("label_split_ids") or []:
                if str(action_id) not in by_id:
                    failures.append(f"missing_label_action:{action_id}")
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            failures.append("corporate_action_source_invalid")
    if lineage.get("corporate_action_manifest_sha256") != row.get(
        "corporate_action_manifest_sha256"
    ):
        failures.append("corporate_action_lineage_mismatch")
    status = "RECONSTRUCTABLE" if not failures else "NOT_RECONSTRUCTABLE"
    return {
        "canonical_observation_id": row.get("canonical_observation_id"),
        "status": status,
        "failures": sorted(set(failures)),
        "feature_mismatches": mismatches,
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    text = (
        gzip.decompress(path.read_bytes()).decode("utf-8")
        if path.name.endswith(".gz")
        else path.read_text(encoding="utf-8")
    )
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _resolve_evidence_bundle(
    *,
    row: Mapping[str, Any],
    lineage: Mapping[str, Any],
    root: Path,
    cache: dict[str, Any],
    cutoff: Any,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Resolve and verify the normalized v1 evidence bundle without row-order joins."""
    failures: list[str] = []
    manifest_relative = str(lineage.get("evidence_manifest_path") or "")
    manifest_path = (root / manifest_relative).resolve()
    try:
        manifest_path.relative_to(root.resolve())
    except ValueError:
        return {}, dict(lineage), ["evidence_manifest_outside_root"]
    if not manifest_path.is_file():
        return {}, dict(lineage), ["missing_source_evidence_manifest"]
    if sha256_file(manifest_path) != lineage.get("source_evidence_manifest_sha256"):
        return {}, dict(lineage), ["source_evidence_manifest_hash_mismatch"]
    cache_key = f"bundle:{manifest_path}"
    if cache_key not in cache:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != RECONSTRUCTION_LINEAGE_VERSION:
            return {}, dict(lineage), ["source_evidence_manifest_schema_mismatch"]
        tables = {}
        if manifest.get("sections"):
            if (
                manifest.get("partition_contract_version")
                != "alpha-atlas-v4-evidence-partitions.v1"
            ):
                failures.append("evidence_partition_contract_mismatch")
            required_sections = {
                "source_objects",
                "selected_market_rows",
                "corporate_action_evidence",
                "security_identity_evidence",
                "observation_lineage",
            }
            failures.extend(
                f"missing_evidence_section:{section}"
                for section in sorted(
                    required_sections.difference(manifest["sections"])
                )
            )
            seen_partition_paths: set[str] = set()
            for section, partitions in sorted(manifest["sections"].items()):
                records = []
                expected_offset = 0
                for spec in partitions:
                    name = str(spec.get("path") or "")
                    if name in seen_partition_paths:
                        failures.append(f"duplicate_evidence_partition_path:{name}")
                    seen_partition_paths.add(name)
                    path = (manifest_path.parent / name).resolve()
                    try:
                        path.relative_to(manifest_path.parent.resolve())
                    except ValueError:
                        failures.append(
                            f"evidence_partition_path_outside_root:{section}"
                        )
                        continue
                    if not path.is_file():
                        failures.append(f"missing_evidence_partition:{section}:{name}")
                        continue
                    if sha256_file(path) != spec.get("sha256"):
                        failures.append(
                            f"evidence_partition_hash_mismatch:{section}:{name}"
                        )
                        continue
                    if path.stat().st_size != int(spec.get("compressed_bytes", -1)):
                        failures.append(
                            f"evidence_partition_compressed_size_mismatch:{section}:{name}"
                        )
                    if int(spec.get("record_offset_start", -1)) != expected_offset:
                        failures.append(
                            f"evidence_partition_offset_mismatch:{section}:{name}"
                        )
                    try:
                        raw_partition = gzip.decompress(path.read_bytes())
                        if len(raw_partition) != int(
                            spec.get("uncompressed_bytes", -1)
                        ):
                            failures.append(
                                f"evidence_partition_uncompressed_size_mismatch:{section}:{name}"
                            )
                        partition_records = [
                            json.loads(line)
                            for line in raw_partition.decode("utf-8").splitlines()
                            if line.strip()
                        ]
                    except (
                        OSError,
                        UnicodeDecodeError,
                        gzip.BadGzipFile,
                        json.JSONDecodeError,
                    ):
                        failures.append(f"evidence_partition_invalid:{section}:{name}")
                        continue
                    if len(partition_records) != int(spec.get("records", -1)):
                        failures.append(
                            f"evidence_partition_record_count_mismatch:{section}:{name}"
                        )
                    records.extend(partition_records)
                    expected_offset += len(partition_records)
                    if (
                        int(spec.get("record_offset_end_exclusive", -1))
                        != expected_offset
                    ):
                        failures.append(
                            f"evidence_partition_offset_mismatch:{section}:{name}"
                        )
                tables[section] = records
        else:
            for name, spec in sorted((manifest.get("files") or {}).items()):
                path = manifest_path.parent / name
                if not path.is_file() or sha256_file(path) != spec.get("sha256"):
                    failures.append(f"evidence_ledger_hash_mismatch:{name}")
                    continue
                records = _read_jsonl(path)
                if len(records) != int(spec.get("records", -1)):
                    failures.append(f"evidence_ledger_record_count_mismatch:{name}")
                tables[name.removesuffix(".jsonl")] = records
        source_root = (
            manifest_path.parent / str(manifest.get("source_root_relative_path") or ".")
        ).resolve()
        action_source = (
            manifest_path.parent
            / str(manifest.get("corporate_action_source_relative_path") or "")
        ).resolve()
        if not action_source.is_file():
            failures.append("missing_corporate_action_source")
        elif sha256_file(action_source) != manifest.get(
            "corporate_action_source_sha256"
        ):
            failures.append("corporate_action_source_hash_mismatch")
        for source in tables.get("source_objects", []):
            source_path = (
                source_root / str(source.get("relative_source_path") or "")
            ).resolve()
            try:
                source_path.relative_to(source_root)
            except ValueError:
                failures.append("source_object_path_outside_root")
                continue
            if not source_path.is_file():
                failures.append(
                    f"missing_source_object:{source.get('source_object_id')}"
                )
            elif sha256_file(source_path) != source.get("sha256"):
                failures.append(
                    f"source_object_hash_mismatch:{source.get('source_object_id')}"
                )
        cache[cache_key] = (manifest, tables, list(failures))
        failures = []
    manifest, tables, cached_failures = cache[cache_key]
    failures.extend(cached_failures)
    indexes_key = f"bundle-indexes:{manifest_path}"
    if indexes_key not in cache:
        cache[indexes_key] = {
            "lineage": {
                item.get("lineage_id"): item
                for item in tables.get("observation_lineage", [])
            },
            "rows": {
                item.get("source_row_id"): item
                for item in tables.get("selected_market_rows", [])
            },
            "identities": {
                item.get("security_identity_evidence_id"): item
                for item in tables.get("security_identity_evidence", [])
            },
            "actions": {
                item.get("corporate_action_evidence_id"): item
                for item in tables.get("corporate_action_evidence", [])
            },
        }
    indexes = cache[indexes_key]
    by_lineage = indexes["lineage"]
    record = by_lineage.get(lineage.get("lineage_id"))
    if not isinstance(record, dict):
        return {}, dict(lineage), failures + ["missing_observation_lineage_record"]
    if record.get("canonical_observation_id") != row.get("canonical_observation_id"):
        failures.append("lineage_canonical_observation_id_mismatch")
    rows_by_id = indexes["rows"]

    family_ids = {
        "symbol": list(record.get("symbol_row_ids") or []),
        "spy": list(record.get("spy_row_ids") or []),
        "sector": list(record.get("sector_row_ids") or []),
    }
    effective = record.get("source_family_effective_asof") or {}
    for family, ids in family_ids.items():
        endpoint = (rows_by_id.get(ids[-1]) or {}).get("date") if ids else None
        if effective.get(family) != endpoint:
            failures.append(f"source_family_effective_asof_mismatch:{family}")

    for label, left_family, right_family, endpoint_key in (
        ("symbol_spy", "symbol", "spy", "symbol_spy_common"),
        ("symbol_sector", "symbol", "sector", "symbol_sector_common"),
    ):
        left_by_day = {
            str((rows_by_id.get(source_id) or {}).get("date")): source_id
            for source_id in family_ids[left_family]
        }
        right_by_day = {
            str((rows_by_id.get(source_id) or {}).get("date")): source_id
            for source_id in family_ids[right_family]
        }
        common = sorted(left_by_day.keys() & right_by_day.keys())
        expected_alignment = {
            "symbol": [left_by_day[day] for day in common],
            "comparison": [right_by_day[day] for day in common],
        }
        if (record.get("aligned_source_row_ids") or {}).get(
            label
        ) != expected_alignment:
            failures.append(f"aligned_source_row_ids_mismatch:{label}")
        if effective.get(endpoint_key) != (common[-1] if common else None):
            failures.append(f"common_source_endpoint_mismatch:{label}")

    family_ids = {
        "symbol": list(record.get("symbol_row_ids") or []),
        "spy": list(record.get("spy_row_ids") or []),
        "sector": list(record.get("sector_row_ids") or []),
    }
    effective = record.get("source_family_effective_asof") or {}
    for family, ids in family_ids.items():
        endpoint = (rows_by_id.get(ids[-1]) or {}).get("date") if ids else None
        if effective.get(family) != endpoint:
            failures.append(f"source_family_effective_asof_mismatch:{family}")

    for label, left_family, right_family, endpoint_key in (
        ("symbol_spy", "symbol", "spy", "symbol_spy_common"),
        ("symbol_sector", "symbol", "sector", "symbol_sector_common"),
    ):
        left_by_day = {
            str((rows_by_id.get(source_id) or {}).get("date")): source_id
            for source_id in family_ids[left_family]
        }
        right_by_day = {
            str((rows_by_id.get(source_id) or {}).get("date")): source_id
            for source_id in family_ids[right_family]
        }
        common = sorted(left_by_day.keys() & right_by_day.keys())
        expected_alignment = {
            "symbol": [left_by_day[day] for day in common],
            "comparison": [right_by_day[day] for day in common],
        }
        if (record.get("aligned_source_row_ids") or {}).get(
            label
        ) != expected_alignment:
            failures.append(f"aligned_source_row_ids_mismatch:{label}")
        if effective.get(endpoint_key) != (common[-1] if common else None):
            failures.append(f"common_source_endpoint_mismatch:{label}")

    calendar = ExchangeCalendar()

    def resolve_rows(ids: Iterable[str], family: str) -> list[dict[str, Any]]:
        output = []
        for source_row_id in ids:
            item = rows_by_id.get(source_row_id)
            if not isinstance(item, dict):
                failures.append(f"missing_selected_source_row:{family}:{source_row_id}")
                continue
            content = {
                key: item.get(key)
                for key in ("symbol", "date", "open", "high", "low", "close", "volume")
            }
            expected = sha256_value(
                {"values": content, "adjustment": item.get("adjustment")}
            )
            if expected != item.get("row_content_sha256"):
                failures.append(
                    f"selected_source_row_hash_mismatch:{family}:{source_row_id}"
                )
            availability = item.get("availability_evidence") or {}
            if availability.get("kind") == "OFFICIAL_SESSION_CLOSE_POLICY":
                try:
                    available = pd.Timestamp(
                        calendar.session_close(date.fromisoformat(str(item["date"])))
                    )
                except (KeyError, TypeError, ValueError):
                    failures.append(
                        f"invalid_semantic_availability:{family}:{source_row_id}"
                    )
                    available = pd.NaT
            elif availability.get("kind") == "EXPLICIT_PROVIDER_AVAILABILITY":
                available = pd.to_datetime(
                    availability.get("available_at"), utc=True, errors="coerce"
                )
            else:
                available = pd.NaT
            if pd.isna(available):
                failures.append(f"missing_source_timestamp:{family}")
            elif (
                not pd.isna(cutoff)
                and available > cutoff
                and source_row_id
                not in {record.get("entry_row_id"), record.get("exit_row_id")}
            ):
                failures.append(f"future_source_availability:{family}")
            output.append(content)
        return output

    loaded = {
        "symbol": resolve_rows(record.get("symbol_row_ids") or [], "symbol"),
        "spy": resolve_rows(record.get("spy_row_ids") or [], "spy"),
        "sector": resolve_rows(record.get("sector_row_ids") or [], "sector"),
        "reference": {},
    }
    identity_by_id = indexes["identities"]
    identity = identity_by_id.get(record.get("security_identity_evidence_id"))
    if not isinstance(identity, dict):
        failures.append("missing_security_identity_evidence")
    elif identity.get("ticker") != row.get("symbol") or identity.get(
        "point_in_time_symbol_id"
    ) != row.get("point_in_time_symbol_id"):
        failures.append("security_identity_mismatch")
    action_by_id = indexes["actions"]
    loaded["corporate_actions"] = action_by_id.get(
        record.get("corporate_action_evidence_id")
    )
    entry = rows_by_id.get(record.get("entry_row_id")) or {}
    exit_row = rows_by_id.get(record.get("exit_row_id")) or {}
    resolved = {
        **record,
        "replay_engine_version": record.get("calculation_engine_version"),
        "source_indices": {
            "symbol": int(record.get("symbol_source_index", -1)),
            "spy": int(record.get("spy_source_index", -1)),
            "sector": int(record.get("sector_source_index", -1)),
        },
        "execution": {
            "entry_price": entry.get("open"),
            "exit_price": exit_row.get("close"),
            "split_factor": row.get("label_split_adjustment_factor", 1.0),
            "entry_at": row.get("entry_at"),
            "exit_at": row.get("exit_at"),
            "entry_session": row.get("entry_session_date"),
            "exit_session": row.get("exit_session_date"),
        },
        "corporate_action_manifest_sha256": row.get("corporate_action_manifest_sha256"),
    }
    return loaded, resolved, failures


def build_temporal_safety_certification(
    *,
    artifact_path: Path,
    verification_report: Mapping[str, Any],
    timing_contract_version: str,
) -> dict[str, Any]:
    artifact_hash = sha256_file(artifact_path)
    report_core = dict(verification_report)
    report_hash = sha256_value(report_core)
    rows = int(verification_report.get("rows_total") or 0)
    checked = int(verification_report.get("rows_checked") or 0)
    results = verification_report.get("results")
    report_integrity_failures: list[str] = []
    if verification_report.get("schema_version") != RECONSTRUCTION_VERSION:
        report_integrity_failures.append("verification_schema_mismatch")
    if verification_report.get("artifact_sha256") != artifact_hash:
        report_integrity_failures.append("verification_artifact_hash_mismatch")
    if verification_report.get("verification_scope") != "FULL_ARTIFACT":
        report_integrity_failures.append("verification_scope_not_full")
    if not isinstance(results, list) or len(results) != checked:
        report_integrity_failures.append("verification_result_count_mismatch")
        results = []
    identifiers = [str(item.get("canonical_observation_id") or "") for item in results]
    if not all(identifiers) or len(identifiers) != len(set(identifiers)):
        report_integrity_failures.append("verification_result_identity_invalid")
    computed_failures = sum(
        item.get("status") != "RECONSTRUCTABLE" or bool(item.get("failures"))
        for item in results
    )
    failures = int(verification_report.get("failure_count") or 0)
    if computed_failures != failures:
        report_integrity_failures.append("verification_failure_count_mismatch")
    if verification_report.get("results_sha256") != sha256_value(results):
        report_integrity_failures.append("verification_results_hash_mismatch")
    reasons = [reason for item in results for reason in item.get("failures", [])]
    timestamp_failures = sum(
        reason.startswith(("missing_feature_cutoff", "entry_", "exit_", "calendar_"))
        for reason in reasons
    )
    future_feature_failures = sum(reason.startswith("future_") for reason in reasons)
    availability_failures = sum(
        reason.startswith(
            (
                "missing_source",
                "source_hash",
                "stale_or_unproven_source",
                "missing_required_context",
            )
        )
        for reason in reasons
    )
    execution_label_failures = sum(
        reason.startswith(("entry_", "exit_", "target_", "missing_executable"))
        for reason in reasons
    )
    verified = (
        rows > 0
        and checked == rows
        and failures == 0
        and verification_report.get("status") == "RECONSTRUCTABLE"
        and not report_integrity_failures
    )
    return {
        "schema_version": TEMPORAL_CERTIFICATION_VERSION,
        "status": (
            "VERIFIED_FOR_THIS_ARTIFACT"
            if verified
            else (
                "FAILED"
                if failures or report_integrity_failures
                else "PARTIALLY_VERIFIED"
            )
        ),
        "artifact_sha256": artifact_hash,
        "timing_contract_version": timing_contract_version,
        "validator_version": RECONSTRUCTION_VERSION,
        "rows_total": rows,
        "rows_checked": checked,
        "reconstructability_failures": failures,
        "timestamp_invariant_failures": timestamp_failures,
        "future_feature_failures": future_feature_failures,
        "availability_failures": availability_failures,
        "execution_label_failures": execution_label_failures,
        "report_integrity_failures": report_integrity_failures,
        "verification_report_sha256": report_hash,
        "legacy_leakage_safe_accepted": False,
    }


def validate_temporal_safety_certification(
    certification: Mapping[str, Any],
    *,
    artifact_path: Path,
    verification_report: Mapping[str, Any],
) -> None:
    if certification.get("schema_version") != TEMPORAL_CERTIFICATION_VERSION:
        raise ValueError("unsupported temporal-safety certification")
    if certification.get("status") != "VERIFIED_FOR_THIS_ARTIFACT":
        raise ValueError("artifact lacks full temporal-safety certification")
    if certification.get("artifact_sha256") != sha256_file(artifact_path):
        raise ValueError("temporal-safety certification artifact hash mismatch")
    if certification.get("verification_report_sha256") != sha256_value(
        dict(verification_report)
    ):
        raise ValueError("temporal-safety verification report hash mismatch")
    rebuilt = build_temporal_safety_certification(
        artifact_path=artifact_path,
        verification_report=verification_report,
        timing_contract_version=str(certification.get("timing_contract_version") or ""),
    )
    if dict(certification) != rebuilt:
        raise ValueError("temporal-safety certification contents are stale or forged")
    if (
        not certification.get("rows_total")
        or certification.get("rows_checked") != certification.get("rows_total")
        or certification.get("reconstructability_failures")
    ):
        raise ValueError("partial or failed checks cannot certify Phase 0")
