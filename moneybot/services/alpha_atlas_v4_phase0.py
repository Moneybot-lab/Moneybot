"""Research-only Alpha Atlas V4 Phase 0 evidence primitives."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from moneybot.services.alpha_atlas_v4_canonical_observations import (
    CanonicalizationError,
    canonical_observation_id,
)
from moneybot.services.market_data_providers import ExchangeCalendar

FEATURE_CONTRACT_VERSION = "alpha-atlas-v4-features.v2"
FEATURE_REGISTRY_VERSION = "alpha-atlas-v4-feature-registry.v1"
FILL_POLICY_VERSION = "alpha-atlas-v4-feature-fill-policy.v1"
RECONSTRUCTION_VERSION = "alpha-atlas-v4-reconstructability.v1"
TEMPORAL_CERTIFICATION_VERSION = "alpha-atlas-v4-temporal-safety-certification.v1"

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
    frame: pd.DataFrame, policy: Mapping[str, Any]
) -> pd.DataFrame:
    validate_fill_policy(policy)
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
        "feature_vwap": "provider VWAP(T), else typical-price fallback under builder contract",
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


def _resolve_source_value(source: Any, row_index: int, field: str) -> float:
    rows = source if isinstance(source, list) else source.get("rows")
    if not isinstance(rows, list) or row_index < 0 or row_index >= len(rows):
        raise ValueError("missing selected source row")
    value = rows[row_index].get(field) if isinstance(rows[row_index], dict) else None
    if not isinstance(value, (int, float)):
        raise ValueError(f"missing numeric source field: {field}")
    return float(value)


def verify_observation(
    row: Mapping[str, Any],
    *,
    root: Path,
    cache: dict[str, Any] | None = None,
    tolerance: float = 1e-9,
) -> dict[str, Any]:
    """Independently replay a lineage-bearing observation; fail closed otherwise."""
    cache = cache if cache is not None else {}
    failures: list[str] = []
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
    required = {"symbol", "spy", "sector", "reference"}
    for source_spec in lineage.get("sources") or []:
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
    failures.extend(f"missing_required_context:{family}" for family in sorted(required))
    if lineage.get("feature_contract_version") != FEATURE_CONTRACT_VERSION:
        failures.append("feature_contract_mismatch")
    if lineage.get("calendar_contract_version") != row.get("exchange_calendar"):
        failures.append("calendar_contract_mismatch")
    fill_policy = lineage.get("fill_policy")
    if fill_policy:
        try:
            validate_fill_policy(fill_policy)
        except ValueError:
            failures.append("fill_policy_mismatch")
    calculations = lineage.get("feature_calculations") or {}
    for feature in MODEL_FEATURES:
        spec = calculations.get(feature)
        if not isinstance(spec, dict):
            failures.append(f"missing_feature_calculation:{feature}")
            continue
        try:
            values = [
                _resolve_source_value(
                    loaded[i["family"]], int(i["row_index"]), str(i["field"])
                )
                for i in spec.get("inputs", [])
            ]
            operator = spec.get("operator")
            if operator == "identity" and len(values) == 1:
                replay = values[0]
            elif operator == "ratio" and len(values) == 2:
                replay = values[0] / values[1]
            elif operator == "pct" and len(values) == 2:
                replay = values[0] / values[1] - 1.0
            elif operator == "difference" and len(values) == 2:
                replay = values[0] - values[1]
            elif operator == "constant" and isinstance(spec.get("value"), (int, float)):
                replay = float(spec["value"])
            else:
                raise ValueError("unsupported calculation")
            if not np.isclose(
                replay, float(row[feature]), rtol=tolerance, atol=tolerance
            ):
                failures.append(f"feature_mismatch:{feature}")
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            failures.append(f"feature_replay_failed:{feature}")
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
    if not action_relative or not action_path.is_file():
        failures.append("missing_corporate_action_source")
    elif sha256_file(action_path) != action_source.get("sha256"):
        failures.append("corporate_action_source_hash_mismatch")
    if lineage.get("corporate_action_manifest_sha256") != row.get(
        "corporate_action_manifest_sha256"
    ):
        failures.append("corporate_action_lineage_mismatch")
    status = "RECONSTRUCTABLE" if not failures else "NOT_RECONSTRUCTABLE"
    return {
        "canonical_observation_id": row.get("canonical_observation_id"),
        "status": status,
        "failures": sorted(set(failures)),
    }


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
    failures = int(verification_report.get("failure_count") or 0)
    verified = (
        rows > 0
        and checked == rows
        and failures == 0
        and verification_report.get("status") == "RECONSTRUCTABLE"
    )
    return {
        "schema_version": TEMPORAL_CERTIFICATION_VERSION,
        "status": (
            "VERIFIED_FOR_THIS_ARTIFACT"
            if verified
            else ("FAILED" if failures else "PARTIALLY_VERIFIED")
        ),
        "artifact_sha256": artifact_hash,
        "timing_contract_version": timing_contract_version,
        "validator_version": RECONSTRUCTION_VERSION,
        "rows_total": rows,
        "rows_checked": checked,
        "reconstructability_failures": failures,
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
    if (
        not certification.get("rows_total")
        or certification.get("rows_checked") != certification.get("rows_total")
        or certification.get("reconstructability_failures")
    ):
        raise ValueError("partial or failed checks cannot certify Phase 0")
