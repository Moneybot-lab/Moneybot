"""Bounded-memory, atomic historical evidence built from a captured JSONL prefix.

SQLite holds compact candidates, identities, exclusions and matured rows on disk.
No display-row limit or latest-N-event window defines this dataset.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
from collections import Counter, OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .market_data_providers import ExchangeCalendar
from .outcome_tracking import dated_close_values
from .prediction_evidence import SCHEMA, OUTCOME_CONTRACT, canonical, digest, probability, timestamp

EVIDENCE_SCHEMA = "historical_evidence.v1"
MAX_RECORD_BYTES = 2 * 1024 * 1024


def _dict(value):
    return value if isinstance(value, dict) else {}


def project_prediction(event):
    """Never infer a forecast from a recommendation, narrative or legacy score."""
    forecast = _dict(event.get("forecast"))
    payload = _dict(event.get("payload"))
    reasons = []
    if event.get("decision_source") != "deterministic_model":
        reasons.append("non_model_source")
    if forecast.get("schema_version") != SCHEMA:
        reasons.append("missing_prediction_contract")
    if forecast.get("input_kind") != "model_features":
        reasons.append("quote_only_or_unknown_inputs")
    model = forecast.get("model_version")
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", model) or model.lower() in {"unknown", "missing", "none"}:
        reasons.append("missing_model_version")
    p = forecast.get("probability_up")
    if p is None:
        reasons.append("missing_probability")
    elif probability(p) is None:
        reasons.append("invalid_probability")
    for field in ("prediction_id", "model_artifact_sha256", "input_sha256"):
        value = forecast.get(field)
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            reasons.append("missing_" + field)
    if forecast.get("forecast_horizon") != "5d":
        reasons.append("horizon_mismatch")
    if forecast.get("outcome_contract") != OUTCOME_CONTRACT:
        reasons.append("outcome_contract_mismatch")
    ts = timestamp(forecast.get("decision_timestamp"))
    if ts is None or ts > (timestamp(event.get("ts")) or 0) + 1:
        reasons.append("invalid_prediction_timestamp")
    symbol = forecast.get("symbol")
    if not isinstance(symbol, str) or not symbol or symbol != event.get("symbol"):
        reasons.append("symbol_mismatch")
    if forecast.get("recommendation") not in {"BUY", "STRONG BUY", "SELL", "HOLD OFF FOR NOW", "HOLD"}:
        reasons.append("invalid_forecast_action")
    market = _dict(forecast.get("market_data_provenance"))
    quote_ts = timestamp(market.get("event_timestamp"))
    if quote_ts is None or ts is None or quote_ts > ts:
        reasons.append("invalid_quote_timestamp")
    # IDs are bound to the originating contract; a copied probability or altered
    # model/version cannot silently retain another prediction's identity.
    identity_fields = {k: forecast.get(k) for k in (
        "schema_version", "symbol", "model_version", "model_artifact_sha256",
        "forecast_horizon", "outcome_contract", "input_kind", "probability_up", "raw_probability_up",
        "recommendation", "calibration", "input_sha256",
    )}
    try:
        if forecast.get("prediction_id") != digest(identity_fields):
            reasons.append("prediction_identity_mismatch")
    except (ValueError, TypeError):
        reasons.append("prediction_identity_mismatch")
    if reasons:
        return None, reasons
    flags = market.get("quality_flags") if isinstance(market.get("quality_flags"), list) else []
    flags = [v for v in flags if isinstance(v, str)]
    stale_values = [market.get(k) for k in ("is_stale", "quote_is_stale", "quote_stale")]
    stale = True if any(v is True for v in stale_values) or "stale" in flags else (False if any(v is False for v in stale_values) else None)
    shadow = forecast.get("shadow_only") is not False or payload.get("shadow_only") is True or _dict(event.get("experiment")).get("rollout_dry_run") is True
    row = {
        **identity_fields, "prediction_id": forecast["prediction_id"], "ts": ts,
        "decision_timestamp": ts, "action": forecast["recommendation"],
        "decision_source": "deterministic_model", "shadow_only": shadow,
        "model_cohort": digest({k: forecast[k] for k in ("model_version", "model_artifact_sha256", "calibration", "outcome_contract")}),
        "market_data_provenance": market, "is_stale": stale,
        "source_mode": market.get("source_mode") or "unknown",
        "is_delayed": market.get("is_delayed") is True or any("delay" in f for f in flags),
    }
    return row, []


class EvidenceHistory:
    """Month-range cache, capped by both entries and price points.

    Endpoints are exact XNYS sessions; absent/invalid bars never shift the horizon.
    Daily closes are admitted only after the session has closed at the as-of time.
    """
    def __init__(self, download, now, max_entries=128, max_points=20000):
        self.download, self.now = download, now
        self.max_entries, self.max_points = max_entries, max_points
        self.cache = OrderedDict()
        self.diagnostics = Counter()
        self.calendar = ExchangeCalendar()

    def outcome(self, row):
        decision = datetime.fromtimestamp(row["ts"], timezone.utc)
        cal = self.calendar
        entry = cal.local_date(decision)
        if not cal.is_trading_day(entry) or cal.session_close(entry) <= decision:
            entry = cal.next_session(entry)
        sessions = [entry]
        for _ in range(5):
            sessions.append(cal.next_session(sessions[-1]))
        if cal.session_close(sessions[-1]) > self.now:
            return None, "immature_outcome"
        # Fetch a bounded month plus enough sessions for every entry in that month.
        start = entry.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1) + timedelta(days=21)
        end = min(end, self.now.date() + timedelta(days=1))
        key = (row["symbol"], start.isoformat())
        if key in self.cache:
            self.diagnostics["history_cache_hits"] += 1
            history, provenance, error = self.cache.pop(key)
            self.cache[key] = (history, provenance, error)
        else:
            self.diagnostics["history_requests"] += 1
            history, provenance, error = {}, {}, None
            try:
                frame = self.download(row["symbol"], start=start.isoformat(), end=end.isoformat(),
                                      interval="1d", auto_adjust=False, progress=False, threads=False)
                values = dated_close_values(frame)
                if len(values) > 128:
                    raise ValueError("unbounded_history_response")
                for day, close in values:
                    if day in history:
                        error = "duplicate_history_session"
                    history[day] = close
                provider = self.download.provider_for_symbol(row["symbol"]) if hasattr(self.download, "provider_for_symbol") else getattr(frame, "attrs", {}).get("provider", "unknown")
                provenance = {"provider": provider, "adjustment_method": "split_adjusted_close",
                              "fetched_at_utc": self.now.isoformat(), "requested_start": start.isoformat(),
                              "requested_end_exclusive": end.isoformat()}
                if provider == "unknown":
                    error = "unknown_history_provenance"
            except Exception:
                error = "historical_provider_failure"
                self.diagnostics["historical_provider_failures"] += 1
            self.cache[key] = (history, provenance, error)
            while len(self.cache) > self.max_entries or sum(len(v[0]) for v in self.cache.values()) > self.max_points:
                self.cache.popitem(last=False)
        if error:
            return None, error
        if any(day not in history for day in sessions):
            return None, "unavailable_history_session"
        closes = [history[day] for day in sessions]
        if any(not math.isfinite(v) or v <= 0 for v in closes):
            return None, "invalid_realized_price"
        realized = closes[-1] / closes[0] - 1
        exposure = -1 if row["action"] in {"SELL", "HOLD OFF FOR NOW"} else (0 if row["action"] == "HOLD" else 1)
        path = [exposure * (v / closes[0] - 1) for v in closes]
        return {"return_5d": realized, "label_up_5d": int(realized > 0),
                "max_adverse_excursion": min(path), "outcome_entry_session": str(entry),
                "outcome_exit_session": str(sessions[-1]), "outcome_history": provenance,
                "outcome_prices_sha256": digest(list(zip(map(str, sessions), closes)))}, None


def build_evidence(input_path, output_path, *, download, now=None, max_events=None):
    now = now or datetime.now(timezone.utc)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=output_path.name + ".", suffix=".tmp", dir=output_path.parent)
    os.close(fd)
    counts, source_counts, category_counts, endpoint_counts = Counter(), Counter(), Counter(), Counter()
    exclusions = Counter()
    source_hash = hashlib.sha256()
    history = EvidenceHistory(download, now)
    db = None
    try:
        db = sqlite3.connect(temporary)
        db.execute("PRAGMA cache_size=-2048")
        db.execute("PRAGMA temp_store=FILE")
        db.executescript("""
            CREATE TABLE candidates (id TEXT PRIMARY KEY, fingerprint TEXT, row_json TEXT, conflict INTEGER DEFAULT 0);
            CREATE TABLE evidence (id TEXT PRIMARY KEY, model TEXT, cohort TEXT, shadow INTEGER, ts REAL, row_json TEXT);
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE invalid_ids (id TEXT PRIMARY KEY);
            CREATE TABLE exclusions (reason TEXT PRIMARY KEY, count INTEGER);
        """)
        with Path(input_path).open("rb") as fh:
            initial = os.fstat(fh.fileno())
            end = initial.st_size
            while fh.tell() < end and (max_events is None or counts["physical_lines"] < max_events):
                raw = fh.readline(min(MAX_RECORD_BYTES + 1, end - fh.tell()))
                source_hash.update(raw)
                counts["physical_lines"] += 1
                if len(raw) > MAX_RECORD_BYTES:
                    while not raw.endswith(b"\n") and fh.tell() < end:
                        raw = fh.readline(min(MAX_RECORD_BYTES, end - fh.tell()))
                        source_hash.update(raw)
                    exclusions["oversized_record"] += 1
                    continue
                try:
                    event = json.loads(raw)
                    if not isinstance(event, dict):
                        raise ValueError("not_object")
                except (ValueError, UnicodeDecodeError):
                    exclusions["malformed_record"] += 1
                    continue
                counts["events_scanned"] += 1
                # Bounded public taxonomies; arbitrary input strings never grow counters.
                source = event.get("decision_source")
                source_counts[source if source in {"deterministic_model", "ai_enhanced", "rule_based", "portfolio_quote_only"} else "other"] += 1
                endpoint = event.get("endpoint")
                endpoint_counts[endpoint if endpoint in {"user_watchlist", "quick_ask", "quick_ask_shadow", "hot_momentum_buys", "breakout_radar"} else "other"] += 1
                category = event.get("event_category")
                category_counts[category if category in {"probabilistic_model_forecast", "shadow_prediction", "portfolio_advice", "quote_observation", "ai_narrative", "rule_based_recommendation"} else "legacy_or_other"] += 1
                row, reasons = project_prediction(event)
                if reasons:
                    identity = _dict(event.get("forecast")).get("prediction_id")
                    if isinstance(identity, str) and len(identity) == 64:
                        db.execute("INSERT OR IGNORE INTO invalid_ids VALUES (?)", (identity,))
                    exclusions.update(reasons)
                    counts["events_excluded"] += 1
                    continue
                counts["eligible_prediction_events"] += 1
                # Repeated polls may have later receipt/decision time, but the same
                # source observation and model input contract is one forecast.
                fingerprint = digest({k: v for k, v in row.items() if k not in {"ts", "decision_timestamp", "market_data_provenance", "is_stale", "is_delayed", "source_mode"}})
                previous = db.execute("SELECT fingerprint, row_json FROM candidates WHERE id=?", (row["prediction_id"],)).fetchone()
                if previous:
                    counts["excluded_duplicates"] += 1
                    if previous[0] != fingerprint:
                        db.execute("UPDATE candidates SET conflict=1 WHERE id=?", (row["prediction_id"],))
                    elif row["ts"] < json.loads(previous[1])["ts"]:
                        db.execute("UPDATE candidates SET row_json=? WHERE id=?", (canonical(row), row["prediction_id"]))
                else:
                    db.execute("INSERT INTO candidates VALUES (?, ?, ?, 0)", (row["prediction_id"], fingerprint, canonical(row)))
            scanned_bytes = fh.tell()
            final = os.fstat(fh.fileno())
            if final.st_size < end or (final.st_size == end and final.st_mtime_ns != initial.st_mtime_ns):
                raise ValueError("input_changed_during_scan")
        complete = scanned_bytes == end and not any(exclusions[k] for k in ("oversized_record", "malformed_record"))
        db.execute("UPDATE candidates SET conflict=1 WHERE id IN (SELECT id FROM invalid_ids)")
        for _, row_json, conflict in db.execute("SELECT id, row_json, conflict FROM candidates ORDER BY id"):
            if conflict:
                exclusions["conflicting_prediction_identity"] += 1
                continue
            counts["independent_predictions"] += 1
            row = json.loads(row_json)
            outcome, reason = history.outcome(row)
            if reason:
                exclusions[reason] += 1
                continue
            row.update(outcome)
            counts["mature_eligible_outcomes"] += 1
            counts["shadow_outcomes" if row["shadow_only"] else "live_outcomes"] += 1
            if row["is_stale"] is True:
                counts["stale_mature_outcomes"] += 1
            db.execute("INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?)", (row["prediction_id"], row["model_version"], row["model_cohort"], int(row["shadow_only"]), row["ts"], canonical(row)))
        row_hash = hashlib.sha256()
        for (raw,) in db.execute("SELECT row_json FROM evidence ORDER BY id"):
            row_hash.update((raw + "\n").encode())
        manifest = {"schema_version": EVIDENCE_SCHEMA, "dataset_id": row_hash.hexdigest(),
                    "checksum": row_hash.hexdigest(), "checksum_encoding": "canonical_jsonl_ordered_by_prediction_id",
                    "source": "decision_events_captured_prefix", "source_sha256": source_hash.hexdigest(),
                    "source_bytes": end, "scanned_bytes": scanned_bytes, "complete": complete,
                    "truncated": scanned_bytes < end, "as_of_utc": now.isoformat(),
                    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                    "outcome_contract": OUTCOME_CONTRACT, "adjustment_method": "split_adjusted_close",
                    "point_in_time": True, "includes_delisted": False,
                    "counts": dict(counts), "source_counts": dict(source_counts), "category_counts": dict(category_counts), "endpoint_counts": dict(endpoint_counts),
                    "exclusions": dict(exclusions), "history": dict(history.diagnostics),
                    "rows": counts["mature_eligible_outcomes"]}
        if hasattr(download, "diagnostics_payload"):
            manifest["provider_diagnostics"] = {k: v for k, v in download.diagnostics_payload().items()
                                                if isinstance(v, int) and not isinstance(v, bool)}
        manifest["manifest_checksum"] = digest(manifest)
        db.execute("INSERT INTO metadata VALUES ('manifest', ?)", (canonical(manifest),))
        db.executemany("INSERT INTO exclusions VALUES (?, ?)", exclusions.items())
        db.execute("DROP TABLE candidates")
        db.execute("DROP TABLE invalid_ids")
        db.commit()
        db.close()
        db = None
        with open(temporary, "rb") as fh:
            os.fsync(fh.fileno())
        os.replace(temporary, output_path)
        return manifest
    finally:
        if db is not None:
            db.close()
        Path(temporary).unlink(missing_ok=True)


class EvidenceDataset:
    def __init__(self, path):
        self.db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        self.db.execute("PRAGMA cache_size=-2048")
        try:
            self.manifest = json.loads(self.db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])
            if self.manifest.get("manifest_checksum") != digest({k: v for k, v in self.manifest.items() if k != "manifest_checksum"}):
                raise ValueError("invalid_evidence_manifest")
            checksum, count = hashlib.sha256(), 0
            for identity, model, cohort, shadow, ts, raw in self.db.execute("SELECT id, model, cohort, shadow, ts, row_json FROM evidence ORDER BY id"):
                checksum.update((raw + "\n").encode())
                row = json.loads(raw)
                if (identity, model, cohort, shadow, ts) != (row.get("prediction_id"), row.get("model_version"), row.get("model_cohort"), int(row.get("shadow_only", True)), row.get("ts")):
                    raise ValueError("invalid_evidence_manifest")
                count += 1
            if self.manifest.get("schema_version") != EVIDENCE_SCHEMA or self.manifest.get("checksum") != checksum.hexdigest() or self.manifest.get("rows") != count:
                raise ValueError("invalid_evidence_manifest")
        except Exception:
            self.db.close()
            raise

    def rows(self, model=None):
        for (raw,) in self.db.execute("SELECT row_json FROM evidence WHERE shadow=0 AND model=? ORDER BY ts, id", (model,)):
            yield json.loads(raw)

    def cohort_id(self, model):
        row = self.db.execute("SELECT cohort FROM evidence WHERE shadow=0 AND model=? LIMIT 1", (model,)).fetchone()
        return row[0] if row else None

    def cohort_count(self, model):
        return self.db.execute("SELECT COUNT(DISTINCT cohort) FROM evidence WHERE shadow=0 AND model=?", (model,)).fetchone()[0]

    def single_model(self):
        models = self.db.execute("SELECT DISTINCT model FROM evidence WHERE shadow=0 LIMIT 2").fetchall()
        return models[0][0] if len(models) == 1 and self.cohort_count(models[0][0]) == 1 else None

    def close(self):
        self.db.close()
