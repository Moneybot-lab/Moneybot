"""Read-only Phase 1 historical-coverage audit primitives for private V4 research."""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from moneybot.services.alpha_atlas_v4_phase0 import (
    FEATURE_CONTRACT_VERSION,
    FEATURE_STORE_PROVENANCE_COLUMNS,
    MODEL_FEATURES,
    feature_registry,
)

SOURCE_INVENTORY_VERSION = "alpha-atlas-v4-phase1-source-inventory.v1"
PREFLIGHT_VERSION = "alpha-atlas-v4-phase1-historical-preflight.v1"
FEATURE_MAPPING_VERSION = "alpha-atlas-v4-phase1-feature-mapping.v1"
BACKFILL_PLAN_VERSION = "alpha-atlas-v4-phase1-controlled-backfill-plan.v1"
DUPLICATE_REPORT_VERSION = "alpha-atlas-v4-phase1-duplicate-comparison.v1"
REFERENCE_POLICY_VERSION = "alpha-atlas-v4-phase1-reference-policy.v1"
PHASE1_REPORT_VERSION = "alpha-atlas-v4-phase1-readiness.v1"
MAX_PROBES = 16
MAX_RESPONSE_BYTES = 1_048_576


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def sanitize(value: Any) -> Any:
    """Remove credentials from arbitrary report-safe values."""
    if isinstance(value, dict):
        return {
            str(k): sanitize(v)
            for k, v in sorted(value.items())
            if str(k).lower() not in {"authorization", "api_key", "apikey", "token"}
        }
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)(api[_-]?key|token)=([^&\s]+)", r"\1=[REDACTED]", value)
        value = re.sub(r"(?i)bearer\s+[^\s]+", "Bearer [REDACTED]", value)
    return value


def source_inventory() -> dict[str, Any]:
    """Return the fail-closed inventory; dates are not inferred from marketing claims."""
    common = {
        "provider": "Massive",
        "configured_bucket_or_prefix": "data/raw/massive_flatfiles (configured run-scoped download root)",
        "earliest_available_date": None,
        "latest_available_date": None,
        "expected_historical_depth": "20 years requested; technically unverified",
        "observed_technical_accessibility": "REQUIRES_OPTIONAL_PREFLIGHT",
        "point_in_time_capability": "UNVERIFIED",
        "known_gaps": [],
        "backfill_readiness": "BLOCKED_PENDING_TECHNICAL_PROBE",
    }
    specs = [
        (
            "daily_bars",
            "Stocks Day Aggregates flat files; /v2/aggs/ticker/{ticker}/range/1/day",
            "raw OHLCV/VWAP",
            "session date; exchange calendar; UTC-derived event time",
            "all 43 model inputs; entry/exit labels",
        ),
        (
            "active_and_inactive_security_reference",
            "/v3/reference/tickers with effective date",
            "reference",
            "effective date",
            "historical universe, security type, exchange, listing/delisting",
        ),
        (
            "ticker_events",
            "/v3/reference/tickers and ticker events/details where available",
            "reference events",
            "effective date",
            "ticker changes, mergers, acquisitions, bankruptcies",
        ),
        (
            "splits",
            "/stocks/v1/splits",
            "event records",
            "execution date plus availability timestamp",
            "split-normalized features and economic labels",
        ),
        (
            "dividends",
            "/v3/reference/dividends",
            "event records",
            "declaration/ex/pay dates",
            "not in current 43 features; required only if total-return labels are introduced",
        ),
        (
            "spy_context",
            "Stocks Day Aggregates for SPY",
            "raw OHLCV/VWAP",
            "session date and official close",
            "SPY returns, beta, regime, volatility",
        ),
        (
            "sector_etf_context",
            "Stocks Day Aggregates for effective-dated sector ETF",
            "raw OHLCV/VWAP",
            "session date and official close",
            "sector-relative return",
        ),
    ]
    sources = []
    for source_id, endpoint, adjusted, resolution, required in specs:
        item = {
            "source_id": source_id,
            **common,
            "endpoint_or_dataset": endpoint,
            "raw_or_adjusted": adjusted,
            "timestamp_resolution_timezone": resolution,
            "delisted_security_inclusion": "UNVERIFIED",
            "required_features": required,
            "technical_blocker": "bounded preflight has not demonstrated historical completeness",
        }
        if source_id in {"daily_bars", "spy_context", "sector_etf_context"}:
            item["point_in_time_capability"] = (
                "NATIVE_SESSION_DATED_IF_RAW_PARTITIONS_PRESERVED"
            )
        if source_id == "dividends":
            item["backfill_readiness"] = "NOT_REQUIRED_CURRENT_CONTRACT"
            item["technical_blocker"] = None
        sources.append(item)
    sources.extend(
        [
            {
                "source_id": "exchange_calendar",
                "provider": "repository ExchangeCalendar",
                "endpoint_or_dataset": "rule-based XNYS calendar",
                "configured_bucket_or_prefix": None,
                "earliest_available_date": "2000-01-01",
                "latest_available_date": "2099-12-31",
                "expected_historical_depth": "contract range",
                "observed_technical_accessibility": "ACCESSIBLE_REPOSITORY",
                "raw_or_adjusted": "rules",
                "timestamp_resolution_timezone": "session open/close converted to UTC",
                "point_in_time_capability": "NATIVE_EFFECTIVE_DATED_RULES",
                "delisted_security_inclusion": "NOT_APPLICABLE",
                "known_gaps": ["not an authoritative exchange feed"],
                "required_features": "all timing, holidays, early closes",
                "backfill_readiness": "READY_WITH_CONTRACT_LIMIT",
                "technical_blocker": None,
            },
            {
                "source_id": "terminal_price_policy",
                "provider": "repository contract",
                "endpoint_or_dataset": "derived from raw bars and effective-dated corporate events",
                "configured_bucket_or_prefix": None,
                "earliest_available_date": None,
                "latest_available_date": None,
                "expected_historical_depth": "same as universe",
                "observed_technical_accessibility": "NOT_DEMONSTRATED",
                "raw_or_adjusted": "economic outcome policy",
                "timestamp_resolution_timezone": "session",
                "point_in_time_capability": "REQUIRES_EFFECTIVE_DATED_EVENTS",
                "delisted_security_inclusion": "REQUIRED",
                "known_gaps": ["no approved terminal-price fallback"],
                "required_features": "executable labels",
                "backfill_readiness": "BLOCKED",
                "technical_blocker": "missing exits must reject; no invented terminal price",
            },
        ]
    )
    core = {
        "schema_version": SOURCE_INVENTORY_VERSION,
        "scope": "private_personal_use_research_only",
        "sources": sorted(sources, key=lambda x: x["source_id"]),
    }
    return {**core, "inventory_sha256": _hash(core)}


def probe_plan() -> list[dict[str, str]]:
    cases = [
        ("active_security", "AAPL", "2024-06-03", "aggregate"),
        ("delisted_security", "TWTR", "2022-10-27", "aggregate"),
        ("ticker_change", "META", "2022-06-09", "reference"),
        ("split_case", "AAPL", "2020-08-31", "split"),
        ("recent_listing", "ARM", "2023-09-14", "aggregate"),
        ("spy_context", "SPY", "2006-01-03", "aggregate"),
        ("sector_etf", "XLK", "2010-01-04", "aggregate"),
        ("regular_session", "SPY", "2024-06-03", "aggregate"),
        ("early_close", "SPY", "2024-11-29", "aggregate"),
        ("old_period", "AAPL", "2006-01-03", "aggregate"),
    ]
    return [{"case": c, "symbol": s, "date": d, "family": f} for c, s, d, f in cases]


def _probe_url(p: Mapping[str, str]) -> str:
    if p["family"] == "reference":
        return f"https://api.massive.com/v3/reference/tickers/{p['symbol']}?date={p['date']}"
    if p["family"] == "split":
        return f"https://api.massive.com/stocks/v1/splits?ticker={p['symbol']}&execution_date={p['date']}&limit=10"
    return f"https://api.massive.com/v2/aggs/ticker/{p['symbol']}/range/1/day/{p['date']}/{p['date']}?adjusted=false&limit=10"


def _result_date(value: Mapping[str, Any]) -> str | None:
    raw = value.get("execution_date") or value.get("date")
    if raw:
        return str(raw)[:10]
    timestamp = value.get("t") or value.get("timestamp")
    if isinstance(timestamp, (int, float)):
        seconds = float(timestamp) / (
            1_000_000_000 if timestamp > 10**16 else 1000 if timestamp > 10**11 else 1
        )
        return datetime.fromtimestamp(seconds, tz=timezone.utc).date().isoformat()
    return None


def classify_probe_payload(probe: Mapping[str, str], payload: Any) -> dict[str, Any]:
    """Validate schema, identity, requested date, and pagination before claiming access."""
    if not isinstance(payload, dict) or payload.get("status") in {
        "ERROR",
        "NOT_AUTHORIZED",
    }:
        return {"status": "AMBIGUOUS", "reason": "invalid_response_schema"}
    if payload.get("next_url") or payload.get("next_page_token"):
        return {
            "status": "INCOMPLETE_RESPONSE",
            "reason": "pagination_indicates_more_results",
        }
    values = payload.get("results")
    if values in (None, [], {}):
        return {"status": "MISSING", "reason": "empty_results", "rows": 0}
    records = (
        values
        if isinstance(values, list)
        else [values] if isinstance(values, dict) else None
    )
    if records is None or not all(isinstance(item, dict) for item in records):
        return {"status": "AMBIGUOUS", "reason": "results_schema_mismatch"}
    declared_count = payload.get("resultsCount")
    if declared_count is not None and int(declared_count) != len(records):
        return {
            "status": "INCOMPLETE_RESPONSE",
            "reason": "declared_result_count_mismatch",
            "rows": len(records),
        }
    expected_symbol = probe["symbol"].upper()
    payload_symbol = str(payload.get("ticker") or "").upper()
    record_symbols = {
        str(item.get("ticker") or item.get("T") or "").upper() for item in records
    }
    observed_symbols = {value for value in record_symbols | {payload_symbol} if value}
    if observed_symbols and observed_symbols != {expected_symbol}:
        return {
            "status": "AMBIGUOUS",
            "reason": "ticker_mismatch",
            "rows": len(records),
        }
    observed_dates = sorted({day for item in records if (day := _result_date(item))})
    requested = probe["date"]
    if probe["family"] in {"aggregate", "split"}:
        if not observed_dates:
            return {
                "status": "AMBIGUOUS",
                "reason": "missing_result_date",
                "rows": len(records),
            }
        if requested not in observed_dates:
            return {
                "status": "AMBIGUOUS",
                "reason": "requested_date_mismatch",
                "rows": len(records),
                "observed_dates": observed_dates,
            }
        date_validation = "RESULT_MATCHED_REQUEST"
    else:
        date_validation = "REQUEST_BOUND_REFERENCE_SNAPSHOT"
    required_fields = {"o", "h", "l", "c", "v"}
    if probe["family"] == "aggregate" and any(
        not required_fields.issubset(item) for item in records
    ):
        return {
            "status": "AMBIGUOUS",
            "reason": "aggregate_fields_incomplete",
            "rows": len(records),
        }
    return {
        "status": "ACCESSIBLE",
        "reason": "schema_identity_and_date_validated",
        "rows": len(records),
        "observed_dates": observed_dates,
        "date_validation": date_validation,
    }


def run_preflight(
    *,
    execute: bool,
    env: Mapping[str, str] | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    source = os.environ if env is None else env
    key = str(
        source.get("MASSIVE_API_KEY") or source.get("POLYGON_API_KEY") or ""
    ).strip()
    report = {
        "schema_version": PREFLIGHT_VERSION,
        "mode": "live_read_only" if execute else "dry_run",
        "write_operations": 0,
        "full_backfill_started": False,
        "caps": {"requests": MAX_PROBES, "response_bytes": MAX_RESPONSE_BYTES},
        "credentials_configured": bool(key),
        "probes": [],
        "requests_attempted": 0,
        "bytes_received": 0,
        "elapsed_seconds": 0.0,
    }
    if not execute:
        report["probes"] = [{**p, "status": "NOT_EVALUATED"} for p in probe_plan()]
        report["overall_status"] = "NOT_EVALUATED"
        return report
    if not key:
        report["overall_status"] = "MISSING_CREDENTIALS"
        return report
    started = time.perf_counter()
    for probe in probe_plan()[:MAX_PROBES]:
        request = urllib.request.Request(
            _probe_url(probe), headers={"Authorization": f"Bearer {key}"}
        )
        result = dict(probe)
        report["requests_attempted"] += 1
        try:
            response = opener(request, timeout=10)
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                result["status"] = "INCOMPLETE_RESPONSE"
                result["reason"] = "response_byte_limit_exceeded"
            else:
                report["bytes_received"] += len(raw)
                payload = json.loads(raw or b"{}")
                result.update(classify_probe_payload(probe, payload))
        except urllib.error.HTTPError as exc:
            result.update(status="INACCESSIBLE", error=f"HTTP {exc.code}")
        except Exception as exc:  # sanitized boundary for provider errors
            result.update(status="AMBIGUOUS", error=str(sanitize(str(exc))))
        report["probes"].append(result)
    report["elapsed_seconds"] = round(time.perf_counter() - started, 6)
    report["overall_status"] = (
        "COMPLETE"
        if all(p["status"] == "ACCESSIBLE" for p in report["probes"])
        else "BLOCKED"
    )
    return sanitize(report)


def summarize_preflight(report: Mapping[str, Any]) -> dict[str, Any]:
    """Derive only claims directly supported by validated live probe records."""
    accessible = [
        p for p in report.get("probes", []) if p.get("status") == "ACCESSIBLE"
    ]
    dates = sorted(
        {
            day
            for probe in accessible
            if probe.get("family") == "aggregate"
            for day in probe.get("observed_dates", [])
        }
    )
    by_case = {
        str(p.get("case")): str(p.get("status")) for p in report.get("probes", [])
    }
    return {
        "earliest_demonstrated_date": dates[0] if dates else None,
        "latest_demonstrated_date": dates[-1] if dates else None,
        "probe_status_by_case": dict(sorted(by_case.items())),
        "inactive_delisted_demonstrated": by_case.get("delisted_security")
        == "ACCESSIBLE",
        "ticker_change_snapshot_demonstrated": by_case.get("ticker_change")
        == "ACCESSIBLE",
        "permanent_identity_support_demonstrated": False,
        "split_history_demonstrated": by_case.get("split_case") == "ACCESSIBLE",
        "spy_history_demonstrated": by_case.get("spy_context") == "ACCESSIBLE",
        "sector_etf_price_demonstrated": by_case.get("sector_etf") == "ACCESSIBLE",
        "effective_dated_sector_mapping_demonstrated": False,
        "common_supported_interval": None,
        "requests_attempted": int(report.get("requests_attempted", 0)),
        "bytes_received": int(report.get("bytes_received", 0)),
        "elapsed_seconds": float(report.get("elapsed_seconds", 0)),
        "backfill_verdict": "BLOCKED_FULL_UNIVERSE_BACKFILL",
    }


def validate_historical_reference(
    record: Mapping[str, Any], *, as_of: str, historical_universe: bool = True
) -> None:
    """Reject current-state metadata and weak identities in historical construction."""
    if record.get("metadata_semantics") == "CURRENT_STATE":
        raise ValueError("CURRENT_STATE_REFERENCE_FORBIDDEN")
    if not record.get("effective_from"):
        raise ValueError("REFERENCE_EFFECTIVE_DATE_REQUIRED")
    if str(record["effective_from"]) > as_of or (
        record.get("effective_to") and str(record["effective_to"]) < as_of
    ):
        raise ValueError("REFERENCE_NOT_EFFECTIVE_AT_DECISION")
    if historical_universe and record.get("identity_class") == "REQUEST_EVENT_IDENTITY":
        raise ValueError("WEAK_IDENTITY_NOT_HISTORICAL_UNIVERSE_ELIGIBLE")


def authoritative_feature_mapping() -> dict[str, Any]:
    registry = feature_registry()
    by_name = {x["name"]: x for x in registry["columns"]}
    records = []
    for position, name in enumerate(
        (*MODEL_FEATURES, *FEATURE_STORE_PROVENANCE_COLUMNS)
    ):
        source = by_name[name]
        records.append(
            {
                "canonical_feature_name": name,
                "registry_position": position,
                "calculation_source": source.get("calculation", "lineage field"),
                "required_historical_inputs": source.get("source_fields", ["lineage"]),
                "point_in_time_status": "REQUIRED",
                "stale_or_missing_policy": source.get("missing_data_policy"),
                "training_inclusion": name in MODEL_FEATURES,
                "inference_inclusion": name in MODEL_FEATURES,
                "production_compatibility": "V4_RESEARCH_ONLY",
                "original_43_feature_membership": name in MODEL_FEATURES,
                "additional_five_feature_membership": name
                in FEATURE_STORE_PROVENANCE_COLUMNS,
                "leakage_risk": "FAIL_CLOSED_CUTOFF_AND_LINEAGE",
                "phase1_readiness": (
                    "READY"
                    if source.get("source_family") != "sector_context"
                    else "BLOCKED_EFFECTIVE_DATED_SECTOR"
                ),
            }
        )
    core = {
        "schema_version": FEATURE_MAPPING_VERSION,
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
        "model_input_count": 43,
        "provenance_count": 5,
        "total_count": 48,
        "features": records,
    }
    if (
        len({r["canonical_feature_name"] for r in records}) != 48
        or tuple(
            r["canonical_feature_name"] for r in records if r["training_inclusion"]
        )
        != MODEL_FEATURES
    ):
        raise ValueError("AUTHORITATIVE_FEATURE_ORDER_MISMATCH")
    return {**core, "mapping_sha256": _hash(core)}


def collapse_exact_records(
    records: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    physical = [dict(r) for r in records]
    by_exact: dict[str, dict[str, Any]] = {}
    immutable: dict[str, str] = {}
    for record in physical:
        exact = _hash(record)
        by_exact.setdefault(exact, record)
        decision_id = str(record.get("decision_id") or "")
        identity = _hash(
            {
                k: record.get(k)
                for k in ("decision_id", "symbol", "decision_at", "feature_cutoff_at")
            }
        )
        if (
            decision_id
            and decision_id in immutable
            and immutable[decision_id] != identity
        ):
            raise ValueError("CONFLICTING_IMMUTABLE_IDENTITY")
        immutable[decision_id] = identity
    unique = [by_exact[k] for k in sorted(by_exact)]
    return unique, {
        "schema_version": DUPLICATE_REPORT_VERSION,
        "physical_records": len(physical),
        "unique_exact_records": len(unique),
        "exact_duplicates_collapsed": len(physical) - len(unique),
        "model_sample_weight": 1.0,
        "historical_records_modified": False,
    }


def comparison_metrics(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, float | int | None]:
    values = list(rows)

    def avg(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 8) if xs else None

    labels = [float(r["label"]) for r in values if r.get("label") is not None]
    eligible = [
        r
        for r in values
        if r.get("label") is not None and r.get("probability") is not None
    ]
    paired = [(float(r["label"]), float(r["probability"])) for r in eligible]
    returns = [float(r.get("return", 0)) for r in values]
    return {
        "rows": len(values),
        "class_balance": avg(labels),
        "directional_accuracy": avg([float((p >= 0.5) == bool(y)) for y, p in paired]),
        "brier_score": avg([(p - y) ** 2 for y, p in paired]),
        "calibration_error": (
            abs((avg([p for _, p in paired]) or 0) - (avg([y for y, _ in paired]) or 0))
            if paired
            else None
        ),
        "positive_prediction_rate": avg([float(p >= 0.5) for _, p in paired]),
        "big_gain_capture": avg(
            [
                float(float(r["probability"]) >= 0.5)
                for r in eligible
                if float(r.get("return", 0)) >= 0.05
            ]
        ),
        "big_loss_rate": avg(
            [
                float(float(r["probability"]) >= 0.5)
                for r in eligible
                if float(r.get("return", 0)) <= -0.05
            ]
        ),
        "average_return": avg(returns),
    }


def controlled_backfill_plan() -> dict[str, Any]:
    core = {
        "schema_version": BACKFILL_PLAN_VERSION,
        "execution_authorized": False,
        "full_backfill_started": False,
        "date_range": {
            "start": None,
            "end": None,
            "status": "BLOCKED_UNTIL_PREFLIGHT_CONFIRMS_COMMON_COVERAGE",
        },
        "universe": "effective-dated active and inactive eligible US equities; never current-active projected backward",
        "reduced_scope_alternative": "fixed, explicitly named contemporaneously tradable cohort with no claim of market-wide results",
        "download_sequence": [
            "effective-dated security reference and events",
            "splits and material corporate actions",
            "raw daily aggregates",
            "SPY and effective-dated sector ETF context",
            "completeness validation",
        ],
        "partition_layout": "data/raw/massive_history/<dataset>/<YYYY>/<MM>/<YYYY-MM-DD>.<format>",
        "manifest": "canonical sorted file paths, byte sizes, SHA-256, request/probe identity",
        "joins": "permanent identity plus effective timestamp; source availability <= feature cutoff",
        "resumability": "atomic .part download then hash-bound rename; skip only exact verified manifest entry",
        "retry": {
            "attempts": 3,
            "backoff": "bounded exponential with jitter excluded from artifacts",
            "rate_limit": "honor Retry-After and stop at configured request/byte budget",
        },
        "partial_recovery": "delete or quarantine unverified .part; never certify partial partitions",
        "estimates": {
            "status": "PROVISIONAL",
            "daily_rows": 50_400_000,
            "compressed_storage_bytes": 1_612_800_000,
            "working_storage_bytes": 6_451_200_000,
            "recommended_headroom_bytes": 19_353_600_000,
            "request_or_object_count": None,
            "runtime_seconds": None,
            "formula": "5,040 sessions * 10,000 symbols * 32 compressed bytes/row; 4x working; 12x backup/headroom",
        },
        "completeness_checks": [
            "every session/universe member classified present, legitimately absent, or rejected",
            "no current-state reference joins",
            "all source and output hashes verified",
            "terminal/missing exit policy applied fail closed",
        ],
        "authorization_conditions": [
            "delisted/inactive inclusion technically demonstrated",
            "ticker-event identity chain demonstrated",
            "common raw-bar and action date range measured",
            "terminal-price policy approved",
            "point-in-time sector mapping available",
        ],
        "blocking_conditions": [
            "delisted coverage unverified",
            "effective-dated ticker/sector identity unverified",
            "terminal-price fallback intentionally absent",
            "common technically supported start date unverified",
        ],
    }
    return {**core, "plan_sha256": _hash(core)}
