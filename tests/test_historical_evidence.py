from __future__ import annotations

import copy
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from moneybot.services.prediction_evidence import make_forecast, probability
from moneybot.services.historical_evidence import build_evidence, EvidenceDataset, EvidenceHistory, project_prediction
from moneybot.services.historical_validation import summarize_validation_rows
from moneybot.services.decision_log import DecisionLogger, read_decision_events
from moneybot.services.market_data_providers import ExchangeCalendar
from scripts.page6_historical_validation_report import report_from_dataset

NOW = datetime(2026, 10, 26, 23, tzinfo=timezone.utc)
ISSUED = datetime(2026, 10, 1, 15, tzinfo=timezone.utc).timestamp()


def forecast_event(index=0, *, p=.8, model="fixture-v1", stale=False, shadow=False, issued=ISSUED, mode="rest"):
    quote = {"price": 100, "source": "fixture", "source_mode": mode,
             "event_timestamp": datetime.fromtimestamp(issued - 1, timezone.utc).isoformat(),
             "is_stale": stale, "quality_flags": ["stale"] if stale else []}
    forecast = make_forecast(symbol="ABC", artifact={"version": model, "weights": [1]},
                             features=[[index]], quote=quote, recommendation="BUY", probability_up=p,
                             raw_probability_up=p, horizon="5d", calibration={"applied": False},
                             shadow_only=shadow, now=issued)
    return {"ts": int(issued), "symbol": "ABC", "decision_source": "deterministic_model",
            "endpoint": "quick_ask", "schema_version": "decision_event.v2",
            "event_category": "shadow_prediction" if shadow else "probabilistic_model_forecast",
            "forecast": forecast, "payload": {"recommendation": "SELL", "probability_up": .01}}


class History:
    def __init__(self, *, bad=None, missing=None, failed=False):
        self.calls = 0
        self.bad, self.missing, self.failed = bad, missing, failed

    def __call__(self, symbol, *, start, end, **kwargs):
        self.calls += 1
        if self.failed:
            raise RuntimeError("fixture provider failure")
        cal = ExchangeCalendar()
        dates = [d.date() for d in pd.date_range(start, end, inclusive="left") if cal.is_trading_day(d.date())]
        values = [100 + i for i in range(len(dates))]
        if self.bad is not None:
            values[2] = self.bad
        if self.missing is not None:
            dates.pop(self.missing)
            values.pop(self.missing)
        frame = pd.DataFrame({"Close": values}, index=pd.to_datetime(dates))
        frame.attrs["provider"] = "fixture"
        return frame


def dataset(tmp_path, events, **kwargs):
    log, output = tmp_path / "events.jsonl", tmp_path / "evidence.sqlite"
    with log.open("w") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")
    manifest = build_evidence(log, output, download=kwargs.pop("download", History()), now=NOW, **kwargs)
    return EvidenceDataset(output), manifest


@pytest.mark.parametrize("value", [-1, 1.001, 80, float("nan"), float("inf"), -float("inf"), True, "0.8", None])
def test_probabilities_are_never_clipped_converted_or_defaulted(value):
    assert probability(value) is None
    metrics = summarize_validation_rows([{"action": "BUY", "return_5d": .1, "probability_up": value}])
    assert metrics["brier_score"] is None
    assert metrics["calibration_status"] == "insufficient_evidence"


@pytest.mark.parametrize("value", [0, 1, .123456])
def test_authentic_probabilities_preserved(value):
    event = forecast_event(p=value)
    row, reasons = project_prediction(event)
    assert not reasons
    assert row["probability_up"] == value
    assert row["action"] == "BUY"  # personalized SELL/.01 cannot replace forecast


@pytest.mark.parametrize("source", ["ai_enhanced", "rule_based", "portfolio_quote_only"])
def test_unrelated_probability_cannot_be_transferred_into_forecast(source):
    event = forecast_event()
    event["decision_source"] = source
    assert project_prediction(event)[0] is None
    event.pop("forecast")
    event["payload"].update(model_version="real-model", probability_up=.8)
    assert "missing_prediction_contract" in project_prediction(event)[1]


@pytest.mark.parametrize("field,value,reason", [
    ("model_version", None, "missing_model_version"), ("model_version", 123, "missing_model_version"),
    ("model_version", "unknown", "missing_model_version"), ("probability_up", None, "missing_probability"),
    ("probability_up", 2, "invalid_probability"), ("probability_up", float("nan"), "invalid_probability"),
    ("forecast_horizon", "1d", "horizon_mismatch"), ("prediction_id", None, "missing_prediction_id"),
    ("decision_timestamp", ISSUED + 100, "invalid_prediction_timestamp"),
])
def test_invalid_forecast_metadata_excluded(field, value, reason):
    event = forecast_event()
    event["forecast"][field] = value
    assert reason in project_prediction(event)[1]


def test_watchlist_history_kept_without_ai_masquerading(tmp_path):
    path = tmp_path / "audit.jsonl"
    logger = DecisionLogger(output_path=str(path))
    for _ in range(3):
        logger.log(endpoint="user_watchlist", symbol="ABC", decision_source="ai_enhanced",
                   payload={"advice": "SELL", "ai_narrative_mode": "ai_enhanced"})
    events = read_decision_events(str(path))
    assert len(events) == 3
    assert all(e["event_category"] == "portfolio_advice" for e in events)
    assert all(project_prediction(e)[0] is None for e in events)


def test_sparse_predictions_outside_latest_window_and_duplicate_polling(tmp_path):
    first, second = forecast_event(1), forecast_event(2)
    duplicate = copy.deepcopy(first)
    duplicate["ts"] += 10
    duplicate["forecast"]["decision_timestamp"] += 10
    duplicate["forecast"]["market_data_provenance"]["received_timestamp"] = "later"
    noise = {"ts": int(ISSUED), "symbol": "ABC", "endpoint": "user_watchlist", "decision_source": "ai_enhanced", "payload": {"advice": "SELL"}}
    provider = History()
    ds, manifest = dataset(tmp_path, [first, duplicate, second] + [noise] * 5001, download=provider)
    try:
        rows = list(ds.rows("fixture-v1"))
        assert len(rows) == 2
        assert {r["prediction_id"] for r in rows} == {first["forecast"]["prediction_id"], second["forecast"]["prediction_id"]}
        assert manifest["counts"]["events_scanned"] == 5004
        assert manifest["counts"]["excluded_duplicates"] == 1
        assert manifest["source_counts"]["ai_enhanced"] == 5001
        assert manifest["complete"] is True
        assert provider.calls == 1
        assert rows[0]["probability_up"] == .8
    finally:
        ds.close()


def test_identity_conflict_excludes_both_versions(tmp_path):
    good = forecast_event()
    bad = copy.deepcopy(good)
    bad["forecast"]["probability_up"] = .2
    ds, manifest = dataset(tmp_path, [good, bad])
    try:
        assert manifest["rows"] == 0
        assert manifest["exclusions"]["conflicting_prediction_identity"] == 1
        assert report_from_dataset(ds)["promotion_ready"] is False
    finally:
        ds.close()


def test_stale_flags_rest_stream_delayed_and_suitability_are_separate(tmp_path):
    events = [forecast_event(0, stale=True), forecast_event(1, mode="websocket"), forecast_event(2)]
    events[2]["forecast"]["market_data_provenance"]["is_delayed"] = True
    ds, manifest = dataset(tmp_path, events)
    try:
        report = report_from_dataset(ds)
        assert report["metrics"]["stale_data_rate"] == .333333
        assert report["metrics"]["delayed_rows"] == 1
        assert report["metrics"]["by_source_mode"] == {"rest": 2, "websocket": 1}
        assert manifest["counts"]["stale_mature_outcomes"] == 1
        assert report["metrics"]["brier_score"] == .04
        assert report["metrics"]["avg_return"] > 0
    finally:
        ds.close()


def test_shadow_and_multiple_models_never_pool_into_promotion_sample(tmp_path):
    ds, manifest = dataset(tmp_path, [forecast_event(1), forecast_event(2, shadow=True), forecast_event(3, model="other")])
    try:
        assert manifest["rows"] == 3
        assert manifest["counts"]["shadow_outcomes"] == 1
        assert report_from_dataset(ds)["metrics"]["evaluated_rows"] == 0
        assert report_from_dataset(ds, model_version="fixture-v1")["metrics"]["evaluated_rows"] == 1
    finally:
        ds.close()


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf")])
def test_invalid_prices_never_become_outcomes(tmp_path, bad):
    ds, manifest = dataset(tmp_path, [forecast_event()], download=History(bad=bad))
    ds.close()
    assert manifest["rows"] == 0
    assert manifest["exclusions"]["invalid_realized_price"] == 1


def test_missing_session_cannot_shift_five_day_horizon(tmp_path):
    ds, manifest = dataset(tmp_path, [forecast_event()], download=History(missing=2))
    ds.close()
    assert manifest["rows"] == 0
    assert manifest["exclusions"]["unavailable_history_session"] == 1


def test_intraday_and_after_close_maturity_has_no_lookahead():
    provider = History()
    row, _ = project_prediction(forecast_event())
    cache = EvidenceHistory(provider, datetime(2026, 10, 8, 19, tzinfo=timezone.utc))
    assert cache.outcome(row)[1] == "immature_outcome"
    cache.now = datetime(2026, 10, 8, 21, tzinfo=timezone.utc)
    outcome, reason = cache.outcome(row)
    assert reason is None
    assert outcome["outcome_entry_session"] == "2026-10-01"
    assert outcome["outcome_exit_session"] == "2026-10-08"
    assert outcome["return_5d"] == pytest.approx(.05)
    after = datetime(2026, 10, 1, 21, tzinfo=timezone.utc).timestamp()
    row, _ = project_prediction(forecast_event(issued=after))
    assert cache.outcome(row)[1] == "immature_outcome"


def test_no_predictions_truncation_provider_failure_and_governance(tmp_path):
    ds, manifest = dataset(tmp_path, [forecast_event(), forecast_event(1)], max_events=1, download=History(failed=True))
    try:
        assert manifest["truncated"] is True
        assert manifest["complete"] is False
        report = report_from_dataset(ds)
        assert report["execution_status"] == "success"
        assert report["validation_status"] == "insufficient_evidence"
        assert report["calibration_status"] == "insufficient_evidence"
        assert report["metrics"]["brier_score"] is None
        assert {"licensing_review", "privacy_review", "historical_provider_integrity", "evidence_complete"} <= set(report["required_next_steps"])
    finally:
        ds.close()


def test_manifest_reproducibility_and_corruption_detection(tmp_path):
    events = [forecast_event(i) for i in range(3)]
    ds, first = dataset(tmp_path, events)
    ds.close()
    ds, second = dataset(tmp_path, events)
    ds.close()
    assert first["checksum"] == second["checksum"]
    assert first["source_sha256"] == second["source_sha256"]
    with sqlite3.connect(tmp_path / "evidence.sqlite") as db:
        db.execute("UPDATE evidence SET row_json='{}'")
    with pytest.raises(ValueError, match="invalid_evidence_manifest"):
        EvidenceDataset(tmp_path / "evidence.sqlite")


def test_full_evidence_still_requires_governance_and_baseline_contract(tmp_path):
    ds, _ = dataset(tmp_path, [forecast_event(i) for i in range(40)])
    try:
        blocked = report_from_dataset(ds)
        assert blocked["validation_status"] == "blocked"
        assert {"licensing_review", "privacy_review"} <= set(blocked["required_next_steps"])
        approved = report_from_dataset(ds, gate_options={"licensing_review_complete": True, "privacy_review_complete": True})
        assert approved["promotion_ready"] is True
        assert approved["validation_status"] == "passed"
        incompatible = report_from_dataset(ds, baseline={"model_version": "other", "metrics": {"brier_score": 0.0}})
        assert "baseline_contract_compatible" in incompatible["required_next_steps"]
    finally:
        ds.close()


def test_failed_build_preserves_previous_atomic_artifact(tmp_path):
    ds, manifest = dataset(tmp_path, [forecast_event()])
    ds.close()
    old = (tmp_path / "evidence.sqlite").read_bytes()
    with pytest.raises(FileNotFoundError):
        build_evidence(tmp_path / "missing", tmp_path / "evidence.sqlite", download=History())
    assert (tmp_path / "evidence.sqlite").read_bytes() == old


def test_history_cache_is_bounded():
    cache = EvidenceHistory(History(), NOW, max_entries=2, max_points=90)
    for i in range(15):
        row, _ = project_prediction(forecast_event(i))
        row["symbol"] = f"SYM{i}"
        cache.outcome(row)
    assert len(cache.cache) <= 2
    assert sum(len(v[0]) for v in cache.cache.values()) <= 90


def test_same_version_with_different_calibration_is_not_a_single_cohort(tmp_path):
    events = [forecast_event(0), forecast_event(1)]
    from moneybot.services.prediction_evidence import digest
    changed = events[1]["forecast"]
    changed["calibration"] = {"applied": True, "slope": 1.1}
    keys = ("schema_version", "symbol", "model_version", "model_artifact_sha256", "forecast_horizon",
            "outcome_contract", "input_kind", "probability_up", "raw_probability_up", "recommendation", "calibration", "input_sha256")
    changed["prediction_id"] = digest({k: changed[k] for k in keys})
    ds, _ = dataset(tmp_path, events)
    try:
        assert report_from_dataset(ds, model_version="fixture-v1")["metrics"]["evaluated_rows"] == 0
        assert "model_cohort_selected" in report_from_dataset(ds)["required_next_steps"]
    finally:
        ds.close()


def test_unknown_quote_quality_does_not_become_fresh(tmp_path):
    ds, _ = dataset(tmp_path, [forecast_event(i, stale=None) for i in range(40)])
    try:
        report = report_from_dataset(ds)
        assert report["metrics"]["unknown_freshness_rows"] == 40
        assert "decision_freshness_known" in report["required_next_steps"]
    finally:
        ds.close()


def test_malformed_and_oversized_logs_are_visible_incomplete_evidence(tmp_path):
    from moneybot.services.historical_evidence import MAX_RECORD_BYTES
    log, output = tmp_path / "raw.jsonl", tmp_path / "evidence.sqlite"
    log.write_bytes(b'{bad}\n' + b'x' * (MAX_RECORD_BYTES + 100) + b'\n' + json.dumps(forecast_event()).encode() + b'\n')
    manifest = build_evidence(log, output, download=History(), now=NOW)
    assert manifest["complete"] is False
    assert manifest["exclusions"]["malformed_record"] == 1
    assert manifest["exclusions"]["oversized_record"] == 1
    assert manifest["rows"] == 1


def test_no_eligible_predictions_is_completed_diagnostic(tmp_path):
    ds, manifest = dataset(tmp_path, [{"endpoint": "user_watchlist", "decision_source": "ai_enhanced", "payload": {"advice": "SELL"}}] * 10)
    try:
        assert manifest["complete"] is True
        assert manifest["history"].get("history_requests", 0) == 0
        report = report_from_dataset(ds)
        assert report["metrics"]["evaluated_rows"] == 0
        assert report["metrics"]["brier_score"] is None
        assert report["promotion_ready"] is False
    finally:
        ds.close()


def test_dashboard_deduplication_cannot_set_validation_sample_size(tmp_path):
    from moneybot.services.outcome_tracking import evaluate_decision_events, select_recent_unique_rows
    events = [forecast_event(i) for i in range(40)]
    dashboard = evaluate_decision_events(events, future_return_lookup=lambda *args: .05)
    assert len(select_recent_unique_rows(dashboard, limit=200, horizon="5d")) == 1
    ds, _ = dataset(tmp_path, events)
    try:
        assert report_from_dataset(ds)["metrics"]["evaluated_rows"] == 40
    finally:
        ds.close()


def test_corrupted_selection_columns_cannot_bypass_shadow_filter(tmp_path):
    ds, _ = dataset(tmp_path, [forecast_event(shadow=True)])
    ds.close()
    with sqlite3.connect(tmp_path / "evidence.sqlite") as db:
        db.execute("UPDATE evidence SET shadow=0")
    with pytest.raises(ValueError, match="invalid_evidence_manifest"):
        EvidenceDataset(tmp_path / "evidence.sqlite")


def test_baseline_regression_limit_applies_to_matching_contract(tmp_path):
    ds, _ = dataset(tmp_path, [forecast_event(i) for i in range(40)])
    try:
        baseline = report_from_dataset(ds)
        baseline["metrics"]["brier_score"] = 0.0
        report = report_from_dataset(ds, baseline=baseline, gate_options={"licensing_review_complete": True, "privacy_review_complete": True})
        assert "calibration_brier" in report["required_next_steps"]
        assert "baseline_contract_compatible" not in report["required_next_steps"]
    finally:
        ds.close()


def test_daily_compact_projection_uses_original_forecast_without_losing_quality():
    from scripts.day13_calibration_report import compact_calibration_event, _signal_completeness
    event = forecast_event()
    event["snapshot"] = {"features": {"return_1d": .01, "rsi_14": 55, "vol_ratio_20d": 1.2}}
    compact = compact_calibration_event(event)
    assert compact["payload"]["probability_up"] == .8
    assert compact["payload"]["model_version"] == "fixture-v1"
    assert compact["payload"]["_signal_completeness"] == _signal_completeness(event["payload"], event["snapshot"], "unknown")
    event["forecast"] = None
    assert compact_calibration_event(event)["payload"]["probability_up"] is None
