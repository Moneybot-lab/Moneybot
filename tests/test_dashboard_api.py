import os
import sys
import json
from io import BytesIO
from datetime import datetime, timedelta, timezone

from flask import current_app
from moneybot.app_factory import create_app
from moneybot import api as api_module




class StubAIAdvisorService:
    def enhance_quick_decision(self, *, symbol, quick_decision, signal_data, quote_data):
        return {
            "mode": "ai_enhanced",
            "narrative": f"Aggressive signal for {symbol}: {quick_decision['recommendation']}",
            "risk_notes": ["Use strict stops.", "Expect high volatility."],
            "next_checks": ["Watch volume.", "Re-check sentiment."],
            "provider": "stub",
            "model": "stub-fast",
        }

    def enhance_portfolio_position(self, *, symbol, entry_price, current_price, shares, signal_data):
        return {
            "mode": "ai_enhanced",
            "advice": "SELL",
            "advice_reason": f"{symbol}: Above your buy-in; trim into strength and protect gains.",
            "risk_notes": ["Momentum can reverse quickly.", "Size exits in tranches."],
            "next_checks": ["Watch RSI and volume.", "Reassess after earnings."],
            "provider": "stub",
            "model": "stub-fast",
        }


class StubDeterministicQuickAdvisor:
    def predict_quick_decision(self, *, signal_data, quote_data, symbol=None):
        return {
            "recommendation": "STRONG BUY",
            "rationale": "Deterministic model says upside probability is high.",
            "current_price": quote_data.get("price"),
            "change_percent": quote_data.get("change_percent"),
            "quote_source": quote_data.get("quote_source"),
            "quote_diagnostics": quote_data.get("diagnostics"),
            "decision_source": "deterministic_model",
            "model_version": "alpha-atlas-v1",
            "probability_up": 0.78,
            "decision_threshold": 0.55,
            "confidence": 78.0,
            "imputed_features": [],
        }

    def predict_portfolio_position(self, *, symbol, entry_price, current_price, shares, signal_data, quote_data):
        return {
            "mode": "deterministic_model",
            "symbol": symbol,
            "advice": "BUY",
            "advice_reason": f"Deterministic portfolio signal for {symbol}.",
            "decision_source": "deterministic_model",
            "model_version": "alpha-atlas-v1",
            "probability_up": 0.71,
            "confidence": 71.0,
            "position_shares": float(shares),
            "pnl_percent": -7.2,
        }

class StubMarketService:
    def get_market_indices(self):
        return [{"name": "Dow Jones", "symbol": "^DJI", "price": 39000.0, "change_percent": 0.4, "series": [1, 2, 3]}]

    def get_stable_watchlist(self):
        return [{"symbol": "MSFT", "company": "Microsoft", "price": 420.12, "signal_score": 8.0}]

    def get_hot_momentum_buys(self):
        return [{"symbol": "NVDA", "price": 900.33, "score": 9.4, "rationale": "Strong breakout"}]

    def get_breakout_radar(self, **_kwargs):
        return [{"symbol": "ASTC", "price": 5.43, "score": 9.8, "decision_source": "scanner:small_cap_gainers", "rationale": "Live breakout scanner candidate."}]

    def get_wells_picks(self):
        return [{"investor": "Warren Buffett", "stocks": [{"ticker": "AAPL", "price": 190.0, "performance": 1.2}]}]

    def get_quote(self, symbol):
        return {"symbol": symbol, "price": 150.25, "change_percent": 1.2, "quote_source": "finnhub", "diagnostics": {"provider": "finnhub", "error": None}}

    def get_signal(self, symbol):
        return {
            "symbol": symbol,
            "action": "HOLD",
            "technical": {"rsi": 52, "macd_histogram": 0.18},
            "sentiment": {"label": "positive", "score": 0.62},
            "quote": self.get_quote(symbol),
        }

    def get_price_history(self, symbol, days=30):
        return [150.25, 151.5, 152.0]

    def get_price_history_data(self, symbol, days=30):
        return {
            "symbol": symbol.upper(),
            "closes": [150.25, 151.5, 152.0],
            "bars": [
                {"open": 149.5, "high": 151.0, "low": 149.0, "close": 150.25},
                {"open": 150.25, "high": 152.0, "low": 150.0, "close": 151.5},
                {"open": 151.5, "high": 152.5, "low": 151.0, "close": 152.0},
            ],
            "source": "stub",
        }

    def get_company_snapshot(self, symbol):
        return {"symbol": symbol, "company_name": f"{symbol} Corp", "summary": "Test summary."}


def _client():
    os.environ["MONEYBOT_SECRET_KEY"] = "test-secret"
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    os.environ.pop("SMTP_HOST", None)
    os.environ.pop("SMTP_USER", None)
    os.environ.pop("PASSWORD_RESET_FROM_EMAIL", None)
    app = create_app()
    app.extensions["market_data_service"] = StubMarketService()
    return app.test_client()


def _signup_payload(email: str, password: str = "pw", password_confirmation: str | None = None):
    local = email.split("@")[0]
    return {
        "name": f"{local.title()} User",
        "username": local.replace(".", "_"),
        "email": email,
        "password": password,
        "password_confirmation": password if password_confirmation is None else password_confirmation,
    }


def test_market_overview_endpoint_returns_items():
    client = _client()
    res = client.get("/api/market-overview")
    assert res.status_code == 200
    data = res.get_json()
    assert data["items"][0]["symbol"] == "^DJI"


def test_tab_data_endpoints_return_items():
    client = _client()

    stable = client.get("/api/stable-watchlist")
    momentum = client.get("/api/hot-momentum-buys")
    breakout = client.get("/api/breakout-radar")
    wells = client.get("/api/wells-picks")

    assert stable.status_code == 200
    assert momentum.status_code == 200
    assert breakout.status_code == 200
    assert wells.status_code == 200

    assert stable.get_json()["items"][0]["symbol"] == "MSFT"
    assert momentum.get_json()["items"][0]["symbol"] == "NVDA"
    assert isinstance(breakout.get_json()["items"], list)
    assert wells.get_json()["items"][0]["investor"] == "Warren Buffett"
    assert wells.get_json()["items"][0]["stocks"][0]["ticker"] == "AAPL"


def test_quick_ask_returns_shopping_friendly_recommendation_scale():
    client = _client()
    client.application.extensions["deterministic_quick_advisor"] = None
    res = client.get("/api/quick-ask?symbol=AAPL")
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["recommendation"] in {"STRONG BUY", "BUY", "HOLD", "HOLD OFF FOR NOW"}
    assert "momentum" in data["rationale"].lower() or "signal" in data["rationale"].lower()
    assert data["quote_source"] == "finnhub"
    assert data["quote_diagnostics"]["provider"] == "finnhub"


def test_quick_ask_normalizes_symbol_from_url_like_input():
    client = _client()
    res = client.get('/api/quick-ask?symbol=%2Fapi%2Fquote%3Fsymbol%3DTSLA')
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["symbol"] == "TSLA"


class FailingMarketService(StubMarketService):
    def get_signal(self, symbol):
        raise RuntimeError("provider unavailable")

    def get_quote(self, symbol):
        raise RuntimeError("quote unavailable")

    def get_price_history(self, symbol, days=30):
        raise RuntimeError("history unavailable")

    def get_price_history_data(self, symbol, days=30):
        raise RuntimeError("history unavailable")


def test_quick_ask_returns_json_fallback_when_market_provider_fails():
    client = _client()
    client.application.extensions["market_data_service"] = FailingMarketService()
    client.application.extensions["deterministic_quick_advisor"] = None
    client.application.extensions["ai_advisor_service"] = None

    res = client.get("/api/quick-ask?symbol=aapl")

    assert res.status_code == 200
    assert res.content_type.startswith("application/json")
    data = res.get_json()["data"]
    assert data["symbol"] == "AAPL"
    assert data["recommendation"] == "HOLD OFF FOR NOW"
    assert data["history30"] == []
    assert data["current_price"] is None


def test_quick_ask_includes_ai_fallback_payload_when_ai_not_configured():
    client = _client()
    res = client.get("/api/quick-ask?symbol=AAPL")
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["ai"]["mode"] == "rule_based"
    assert data["ai_status"] == "fallback"
    assert data["ai_mode"] == "rule_based"
    assert data["ai"]["reason"] == "disabled_or_missing_api_key"
    assert "not financial advice" in data["ai"]["risk_notes"][1].lower()


def test_quick_ask_uses_ai_extension_when_present():
    client = _client()
    client.application.extensions["ai_advisor_service"] = StubAIAdvisorService()

    res = client.get("/api/quick-ask?symbol=TSLA")
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["ai"]["mode"] == "ai_enhanced"
    assert data["ai_status"] == "working"
    assert data["ai_mode"] == "ai_enhanced"
    assert "reason" not in data["ai"]
    assert data["ai"]["provider"] == "stub"
    assert "TSLA" in data["ai"]["narrative"]


def test_quick_ask_uses_deterministic_model_extension_when_present():
    client = _client()
    client.application.extensions["deterministic_quick_advisor"] = StubDeterministicQuickAdvisor()
    client.application.extensions["ai_advisor_service"] = None

    res = client.get("/api/quick-ask?symbol=AAPL")
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["recommendation"] == "STRONG BUY"
    assert data["decision_source"] == "deterministic_model"
    assert data["model_version"] == "alpha-atlas-v1"
    assert data["confidence"] == 78.0
    assert data["score"] == 7.8
    assert data["model_score"] == 7.8
    assert data["score_basis"] == "deterministic_model_probability"


def test_model_health_reports_deterministic_and_logging_status():
    client = _client()

    res = client.get("/api/model-health")
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["schema_version"] == "model_health.v1"
    assert "deterministic_quick_enabled" in data
    assert "deterministic_momentum_enabled" in data
    assert "model_loaded" in data
    assert "artifact_metadata" in data
    assert "artifact_history" in data
    assert "decision_logging" in data
    assert "rollout_percentage" in data
    assert "calibration_enabled" in data
    assert "enabled" in data["decision_logging"]
    assert "source_counts" in data["decision_logging"]
    assert "endpoint_counts" in data["decision_logging"]


def test_model_health_includes_artifact_metadata_history(tmp_path, monkeypatch):
    model_path = tmp_path / "day1_baseline_model.json"
    metadata_path = tmp_path / "day1_baseline_model.json.meta.json"
    history_path = tmp_path / "day1_baseline_model.json.history.json"
    metadata = {"model_version": "alpha-atlas-v1", "train_rows": 100}
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    history_path.write_text(json.dumps([metadata]), encoding="utf-8")
    monkeypatch.setenv("DETERMINISTIC_MODEL_PATH", str(model_path))

    client = _client()
    res = client.get("/api/model-health")

    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["artifact_metadata"]["model_version"] == "alpha-atlas-v1"
    assert data["artifact_history"][0]["train_rows"] == 100


def test_model_health_includes_fresh_calibration_report(tmp_path, monkeypatch):
    report_path = tmp_path / "day13_calibration_report.json"
    plan_path = tmp_path / "day13_recalibration_plan.json"
    report_path.write_text(
        json.dumps(
            {
                "schema_version": "calibration_report.v1",
                "computed_at_utc": datetime.now(timezone.utc).isoformat(),
                "brier_score": 0.19,
                "rows": 80,
            }
        ),
        encoding="utf-8",
    )
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": "calibration_recalibration_plan.v1",
                "computed_at_utc": datetime.now(timezone.utc).isoformat(),
                "apply_change": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MONEYBOT_PERSISTENT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DETERMINISTIC_CALIBRATION_REPORT_MAX_AGE_SECONDS", "3600")

    client = _client()
    res = client.get("/api/model-health")
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["calibration_report"] is not None
    assert data["calibration_report"]["brier_score"] == 0.19
    assert data["calibration_report_path"] == str(report_path)
    assert data["calibration_report_exists"] is True
    assert data["recalibration_plan_path"] == str(plan_path)
    assert data["recalibration_plan_exists"] is True
    assert data["calibration_report_mtime_utc"] is not None
    assert data["recalibration_plan_mtime_utc"] is not None


def test_model_health_reports_missing_calibration_and_plan(monkeypatch, tmp_path):
    monkeypatch.setenv("MONEYBOT_PERSISTENT_DATA_DIR", str(tmp_path))
    client = _client()

    res = client.get("/api/model-health")

    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["calibration_report_path"] == str(tmp_path / "day13_calibration_report.json")
    assert data["calibration_report_exists"] is False
    assert data["recalibration_plan_path"] == str(tmp_path / "day13_recalibration_plan.json")
    assert data["recalibration_plan_exists"] is False
    assert data["calibration_report_mtime_utc"] is None
    assert data["recalibration_plan_mtime_utc"] is None


def test_quick_ask_logs_shadow_decision_in_rollout_dry_run():
    class DryRunAdvisor:
        rollout_dry_run = True

        def predict_quick_decision(self, *, signal_data, quote_data, symbol=None):
            return None

        def predict_shadow_decision(self, *, signal_data, quote_data):
            return {
                "recommendation": "BUY",
                "decision_source": "deterministic_model",
                "model_version": "alpha-atlas-v1",
                "probability_up": 0.66,
                "confidence": 66.0,
            }

    client = _client()
    client.application.extensions["deterministic_quick_advisor"] = DryRunAdvisor()

    res = client.get("/api/quick-ask?symbol=AAPL")
    assert res.status_code == 200
    summary = client.get("/api/decision-log-summary?limit=20").get_json()["data"]
    assert summary["endpoint_counts"]["quick_ask_shadow"] >= 1


def test_run_daily_ops_requires_token():
    client = _client()
    client.application.config["DAILY_OPS_TOKEN"] = "secret-token"

    res = client.post("/api/run-daily-ops")

    assert res.status_code == 401
    assert res.get_json()["error"] == "unauthorized"


def test_run_weekly_model_refresh_requires_token():
    client = _client()
    client.application.config["DAILY_OPS_TOKEN"] = "secret-token"

    res = client.post("/api/run-weekly-model-refresh")

    assert res.status_code == 401
    assert res.get_json()["error"] == "unauthorized"


def test_run_daily_ops_executes_and_returns_output(monkeypatch, tmp_path):
    class Completed:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(command, cwd, capture_output, text, check):
        assert command[:3] == ["python3", "scripts/run_daily_ops.py", "--input-log"]
        assert capture_output is True
        assert text is True
        assert check is False
        assert cwd.endswith("/Moneybot")
        return Completed()

    monkeypatch.setattr(api_module.subprocess, "run", fake_run)
    monkeypatch.setenv("MONEYBOT_PERSISTENT_DATA_DIR", str(tmp_path))

    client = _client()
    client.application.config["DAILY_OPS_TOKEN"] = "secret-token"
    res = client.post("/api/run-daily-ops", headers={"X-Daily-Ops-Token": "secret-token"})

    assert res.status_code == 200
    payload = res.get_json()["data"]
    assert payload["success"] is True
    assert payload["returncode"] == 0
    assert payload["stdout"] == "ok"
    assert payload["stderr"] == ""
    assert payload["calibration_report_path"] == str(tmp_path / "day13_calibration_report.json")
    assert payload["calibration_report_exists"] is False
    assert payload["recalibration_plan_path"] == str(tmp_path / "day13_recalibration_plan.json")
    assert payload["recalibration_plan_exists"] is False
    assert payload["day13_stderr"] == ""


def test_run_weekly_model_refresh_executes_and_returns_output(monkeypatch, tmp_path):
    class Completed:
        returncode = 0
        stdout = "weekly ok"
        stderr = ""

    def fake_run(command, cwd, capture_output, text, check):
        assert command == ["python3", "scripts/run_weekly_model_refresh.py"]
        assert capture_output is True
        assert text is True
        assert check is False
        assert cwd.endswith("/Moneybot")
        return Completed()

    monkeypatch.setattr(api_module.subprocess, "run", fake_run)
    monkeypatch.setenv("MONEYBOT_PERSISTENT_DATA_DIR", str(tmp_path))

    client = _client()
    client.application.config["DAILY_OPS_TOKEN"] = "secret-token"
    res = client.post("/api/run-weekly-model-refresh", headers={"X-Daily-Ops-Token": "secret-token"})

    assert res.status_code == 200
    payload = res.get_json()["data"]
    assert payload["success"] is True
    assert payload["command"] == ["python3", "scripts/run_weekly_model_refresh.py"]
    assert payload["exit_code"] == 0
    assert payload["returncode"] == 0
    assert payload["stdout"] == "weekly ok"
    assert payload["stderr"] == ""
    assert payload["runtime_dir"] == str(tmp_path)
    assert payload["calibration_report_path"] == str(tmp_path / "day13_calibration_report.json")
    assert payload["calibration_report_exists"] is False
    assert payload["recalibration_plan_path"] == str(tmp_path / "day13_recalibration_plan.json")
    assert payload["recalibration_plan_exists"] is False


def test_run_daily_ops_reports_day13_paths_in_runtime_dir_when_files_exist(monkeypatch, tmp_path):
    report_path = tmp_path / "day13_calibration_report.json"
    plan_path = tmp_path / "day13_recalibration_plan.json"
    report_path.write_text("{}", encoding="utf-8")
    plan_path.write_text("{}", encoding="utf-8")

    class Completed:
        returncode = 1
        stdout = ""
        stderr = "Script stderr (day13_calibration_report.py): boom"

    def fake_run(command, cwd, capture_output, text, check):
        assert command[-1] == str(tmp_path / "decision_events.jsonl")
        return Completed()

    monkeypatch.setattr(api_module.subprocess, "run", fake_run)
    monkeypatch.setenv("MONEYBOT_PERSISTENT_DATA_DIR", str(tmp_path))

    client = _client()
    client.application.config["DAILY_OPS_TOKEN"] = "secret-token"
    res = client.post("/api/run-daily-ops", headers={"X-Daily-Ops-Token": "secret-token"})

    assert res.status_code == 200
    payload = res.get_json()["data"]
    assert payload["success"] is False
    assert payload["calibration_report_path"] == str(report_path)
    assert payload["calibration_report_exists"] is True
    assert payload["recalibration_plan_path"] == str(plan_path)
    assert payload["recalibration_plan_exists"] is True
    assert "day13_calibration_report.py" in payload["day13_stderr"]


def test_run_weekly_model_refresh_reports_diagnostics_when_files_exist(monkeypatch, tmp_path):
    report_path = tmp_path / "day13_calibration_report.json"
    plan_path = tmp_path / "day13_recalibration_plan.json"
    report_path.write_text("{}", encoding="utf-8")
    plan_path.write_text("{}", encoding="utf-8")

    class Completed:
        returncode = 1
        stdout = ""
        stderr = "weekly failed"

    def fake_run(command, cwd, capture_output, text, check):
        return Completed()

    monkeypatch.setattr(api_module.subprocess, "run", fake_run)
    monkeypatch.setenv("MONEYBOT_PERSISTENT_DATA_DIR", str(tmp_path))

    client = _client()
    client.application.config["DAILY_OPS_TOKEN"] = "secret-token"
    res = client.post("/api/run-weekly-model-refresh", headers={"X-Daily-Ops-Token": "secret-token"})

    assert res.status_code == 200
    payload = res.get_json()["data"]
    assert payload["success"] is False
    assert payload["exit_code"] == 1
    assert payload["runtime_dir"] == str(tmp_path)
    assert payload["calibration_report_path"] == str(report_path)
    assert payload["calibration_report_exists"] is True
    assert payload["recalibration_plan_path"] == str(plan_path)
    assert payload["recalibration_plan_exists"] is True


def test_decision_log_summary_reports_recent_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_LOG_PATH", str(tmp_path / "decision_events.jsonl"))
    client = _client()
    logger = client.application.extensions["decision_logger"]
    logger.log(endpoint="quick_ask", symbol="AAPL", decision_source="deterministic_model", payload={"recommendation": "BUY"})
    logger.log(endpoint="hot_momentum_buys", symbol="SOFI", decision_source="rule_based", payload={"score": 7.8})

    res = client.get("/api/decision-log-summary?limit=10")

    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["schema_version"] == "decision_log_summary.v1"
    assert data["events_considered"] == 2
    assert data["source_counts"]["deterministic_model"] == 1
    assert data["source_counts"]["rule_based"] == 1
    assert data["endpoint_counts"]["quick_ask"] == 1
    assert data["endpoint_counts"]["hot_momentum_buys"] == 1
    assert data["latest_event"]["symbol"] == "SOFI"
    assert data["logging_enabled"] is True


def test_decision_log_summary_rejects_invalid_limit():
    client = _client()

    res = client.get("/api/decision-log-summary?limit=bad")

    assert res.status_code == 400
    assert res.get_json()["error"] == "limit must be an integer"


def test_decision_outcomes_returns_rows_and_summaries(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_LOG_PATH", str(tmp_path / "decision_events.jsonl"))
    client = _client()
    logger = client.application.extensions["decision_logger"]
    logger.log(endpoint="quick_ask", symbol="AAPL", decision_source="deterministic_model", payload={"recommendation": "BUY", "model_version": "alpha-atlas-v1"})
    logger.log(endpoint="user_watchlist", symbol="TSLA", decision_source="rule_based", payload={"advice": "SELL"})

    monkeypatch.setattr(
        api_module,
        "_future_return_for_outcomes",
        lambda symbol, ts, days: {("AAPL", 1): 0.02, ("AAPL", 5): 0.04, ("TSLA", 1): -0.01, ("TSLA", 5): -0.03}[(symbol, days)],
    )

    res = client.get("/api/decision-outcomes?limit=10")

    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["schema_version"] == "decision_outcomes.v1"
    assert data["summary_1d"]["rows"] == 2
    assert data["summary_1d"]["accuracy"] == 1.0
    assert data["rows"][0]["outcome_1d"] == "correct"
    assert data["rows"][0]["model_version"] == "alpha-atlas-v1"
    assert data["include_skipped"] is False
    assert data["rows_scanned"] >= 2
    assert data["evaluated_rows_available"] == 2


def test_decision_outcomes_filters_skipped_rows_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_LOG_PATH", str(tmp_path / "decision_events.jsonl"))
    client = _client()
    logger = client.application.extensions["decision_logger"]
    logger.log(endpoint="quick_ask", symbol="AAPL", decision_source="deterministic_model", payload={"recommendation": "BUY"})
    logger.log(endpoint="quick_ask", symbol="TSLA", decision_source="rule_based", payload={"recommendation": "BUY"})

    monkeypatch.setattr(
        api_module,
        "_future_return_for_outcomes",
        lambda symbol, ts, days: {("AAPL", 1): None, ("AAPL", 5): None, ("TSLA", 1): 0.03, ("TSLA", 5): None}[(symbol, days)],
    )

    filtered = client.get("/api/decision-outcomes?limit=10")
    assert filtered.status_code == 200
    filtered_data = filtered.get_json()["data"]
    assert len(filtered_data["rows"]) == 1
    assert filtered_data["rows"][0]["symbol"] == "TSLA"
    assert filtered_data["summary_1d"]["rows"] == 1
    assert filtered_data["used_unevaluated_fallback"] is False

    include_skipped = client.get("/api/decision-outcomes?limit=10&include_skipped=true")
    assert include_skipped.status_code == 200
    include_skipped_data = include_skipped.get_json()["data"]
    assert len(include_skipped_data["rows"]) == 2
    assert include_skipped_data["include_skipped"] is True


def test_decision_outcomes_can_filter_by_decision_source(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_LOG_PATH", str(tmp_path / "decision_events.jsonl"))
    client = _client()
    logger = client.application.extensions["decision_logger"]
    logger.log(endpoint="quick_ask", symbol="AAPL", decision_source="deterministic_model", payload={"recommendation": "BUY"})
    logger.log(endpoint="quick_ask", symbol="TSLA", decision_source="rule_based", payload={"recommendation": "SELL"})

    monkeypatch.setattr(
        api_module,
        "_future_return_for_outcomes",
        lambda symbol, ts, days: {("AAPL", 1): 0.02, ("AAPL", 5): 0.05, ("TSLA", 1): -0.01, ("TSLA", 5): -0.03}[(symbol, days)],
    )

    res = client.get("/api/decision-outcomes?limit=10&decision_source=deterministic_model")
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["decision_source_filter"] == "deterministic_model"
    assert len(data["rows"]) == 1
    assert data["rows"][0]["decision_source"] == "deterministic_model"


def test_decision_outcomes_keeps_1d_and_5d_rows_separate_for_default_view(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_LOG_PATH", str(tmp_path / "decision_events.jsonl"))
    client = _client()
    logger = client.application.extensions["decision_logger"]
    logger.log(endpoint="quick_ask", symbol="AAPL", decision_source="deterministic_model", payload={"recommendation": "BUY"})
    logger.log(endpoint="quick_ask", symbol="TSLA", decision_source="rule_based", payload={"recommendation": "SELL"})
    logger.log(endpoint="quick_ask", symbol="MSFT", decision_source="rule_based", payload={"recommendation": "BUY"})

    monkeypatch.setattr(
        api_module,
        "_future_return_for_outcomes",
        lambda symbol, ts, days: {
            ("AAPL", 1): 0.03,
            ("AAPL", 5): None,
            ("TSLA", 1): -0.01,
            ("TSLA", 5): -0.04,
            ("MSFT", 1): 0.02,
            ("MSFT", 5): None,
        }[(symbol, days)],
    )

    res = client.get("/api/decision-outcomes?limit=10")
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert len(data["rows"]) == 3
    assert [row["symbol"] for row in data["rows_1d"]] == ["AAPL", "TSLA", "MSFT"]
    assert [row["symbol"] for row in data["rows_5d"]] == ["TSLA"]
    assert data["rows_5d"][0]["return_5d"] == -0.04
    assert data["summary_1d"]["evaluated_rows"] == 3
    assert data["summary_5d"]["evaluated_rows"] == 1
    assert data["evaluated_rows_available"] == 3
    assert data["evaluated_rows_1d_available"] == 3
    assert data["evaluated_rows_5d_available"] == 1


def test_decision_outcomes_falls_back_to_recent_rows_when_nothing_is_evaluable(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_LOG_PATH", str(tmp_path / "decision_events.jsonl"))
    client = _client()
    logger = client.application.extensions["decision_logger"]
    logger.log(endpoint="quick_ask", symbol="AAPL", decision_source="deterministic_model", payload={"recommendation": "BUY"})
    logger.log(endpoint="quick_ask", symbol="TSLA", decision_source="rule_based", payload={"recommendation": "SELL"})

    monkeypatch.setattr(api_module, "_future_return_for_outcomes", lambda symbol, ts, days: None)

    res = client.get("/api/decision-outcomes?limit=10")
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert len(data["rows"]) == 2
    assert data["summary_1d"]["evaluated_rows"] == 0
    assert data["used_unevaluated_fallback"] is True


def test_decision_outcomes_expands_scan_window_to_find_older_evaluated_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_LOG_PATH", str(tmp_path / "decision_events.jsonl"))
    client = _client()
    logger = client.application.extensions["decision_logger"]

    for idx in range(260):
        symbol = f"SYM{idx}"
        logger.log(endpoint="quick_ask", symbol=symbol, decision_source="deterministic_model", payload={"recommendation": "BUY"})

    monkeypatch.setattr(
        api_module,
        "_future_return_for_outcomes",
        lambda symbol, ts, days: 0.02 if (symbol == "SYM0" and days == 1) else None,
    )

    res = client.get("/api/decision-outcomes?limit=1")
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert len(data["rows"]) == 1
    assert data["rows"][0]["symbol"] == "SYM0"
    assert data["rows_scanned"] == 260


def test_decision_outcomes_returns_200_when_lookup_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_LOG_PATH", str(tmp_path / "decision_events.jsonl"))
    client = _client()
    logger = client.application.extensions["decision_logger"]
    logger.log(endpoint="quick_ask", symbol="MSFT", decision_source="deterministic_model", payload={"recommendation": "BUY"})

    monkeypatch.setattr(api_module, "_future_return_for_outcomes", lambda symbol, ts, days: (_ for _ in ()).throw(ValueError("No objects to concatenate")))

    res = client.get("/api/decision-outcomes?limit=10&include_skipped=true")
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert len(data["rows"]) == 1
    assert data["rows"][0]["outcome_1d"] == "skipped"
    assert data["rows"][0]["outcome_5d"] == "skipped"


def test_decision_outcomes_uses_lookup_cache_for_duplicate_events(tmp_path, monkeypatch):
    events_path = tmp_path / "decision_events.jsonl"
    event = {
        "ts": 1700000000,
        "endpoint": "quick_ask",
        "symbol": "AAPL",
        "decision_source": "deterministic_model",
        "payload": {"recommendation": "BUY"},
    }
    events_path.write_text("\n".join(json.dumps(event) for _ in range(3)) + "\n", encoding="utf-8")
    monkeypatch.setenv("DECISION_LOG_PATH", str(events_path))

    client = _client()
    calls = {"count": 0}

    def fake_lookup(symbol, ts, days):
        calls["count"] += 1
        return 0.02 if days == 1 else 0.04

    monkeypatch.setattr(api_module, "_future_return_for_outcomes", fake_lookup)

    res = client.get("/api/decision-outcomes?limit=10&include_skipped=true")
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert len(data["rows"]) == 3
    assert calls["count"] == 5
    assert data["lookup_cache_misses"] >= 5
    assert data["lookup_cache_hits"] >= 8
    assert data["lookup_cache_size"] >= 5


def test_decision_outcomes_includes_visible_paper_pnl_summary(tmp_path, monkeypatch):
    events_path = tmp_path / "decision_events.jsonl"
    base_event = {
        "ts": 1700000000,
        "endpoint": "quick_ask",
        "decision_source": "deterministic_model",
        "payload": {"recommendation": "BUY"},
    }
    events_path.write_text(
        "\n".join(
            json.dumps({**base_event, "symbol": symbol})
            for symbol in ["AAPL", "MSFT"]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DECISION_LOG_PATH", str(events_path))
    client = _client()

    monkeypatch.setattr(
        api_module,
        "_future_return_for_outcomes",
        lambda symbol, ts, days: {"AAPL": 0.01, "MSFT": 0.02}[symbol],
    )

    res = client.get("/api/decision-outcomes?limit=1&force_live=true")

    assert res.status_code == 200
    data = res.get_json()["data"]
    assert len(data["rows"]) == 1
    assert data["rows"][0]["symbol"] == "MSFT"
    assert data["paper_pnl_by_recommendation"]["BUY"]["rows"] == 2
    assert data["paper_pnl_by_recommendation"]["BUY"]["avg_paper_return_1d"] == 0.015
    assert data["visible_paper_pnl_by_recommendation"]["BUY"]["rows"] == 1
    assert data["visible_paper_pnl_by_recommendation"]["BUY"]["avg_paper_return_1d"] == 0.02


def test_decision_outcomes_snapshot_keeps_aggregate_and_visible_paper_pnl(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "decision_outcomes_snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "computed_at_utc": datetime.now(timezone.utc).isoformat(),
                "data": {
                    "rows": [
                        {"symbol": "AAPL", "action": "BUY", "return_1d": 0.01, "paper_return_1d": 0.01},
                        {"symbol": "MSFT", "action": "BUY", "return_1d": 0.02, "paper_return_1d": 0.02},
                    ],
                    "rows_1d": [
                        {"symbol": "AAPL", "action": "BUY", "return_1d": 0.01, "paper_return_1d": 0.01},
                        {"symbol": "MSFT", "action": "BUY", "return_1d": 0.02, "paper_return_1d": 0.02},
                    ],
                    "summary_1d": {"rows": 2},
                    "summary_5d": {"rows": 0},
                    "paper_pnl_by_recommendation": {
                        "BUY": {"rows": 2, "avg_paper_return_1d": 0.015}
                    },
                    "visible_paper_pnl_by_recommendation": {
                        "BUY": {"rows": 1, "avg_paper_return_1d": 0.02}
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DECISION_OUTCOMES_SNAPSHOT_PATH", str(snapshot_path))
    monkeypatch.setenv("DECISION_OUTCOMES_SNAPSHOT_MAX_AGE_SECONDS", "900")
    client = _client()

    res = client.get("/api/decision-outcomes?limit=1")

    assert res.status_code == 200
    data = res.get_json()["data"]
    assert len(data["rows"]) == 2
    assert data["rows"][1]["symbol"] == "MSFT"
    assert data["summary_1d"]["rows"] == 2
    assert data["paper_pnl_by_recommendation"]["BUY"]["rows"] == 2
    assert data["paper_pnl_by_recommendation"]["BUY"]["avg_paper_return_1d"] == 0.015
    assert data["visible_paper_pnl_by_recommendation"]["BUY"]["rows"] == 1
    assert data["visible_paper_pnl_by_recommendation"]["BUY"]["avg_paper_return_1d"] == 0.02


def test_decision_outcomes_widens_beyond_5000_to_find_5d_rows(tmp_path, monkeypatch):
    events_path = tmp_path / "decision_events.jsonl"
    old_event = {
        "ts": 1700000000,
        "endpoint": "quick_ask",
        "symbol": "OLD5D",
        "decision_source": "deterministic_model",
        "payload": {"recommendation": "BUY"},
    }
    recent_events = [
        {
            "ts": 1701000000 + idx,
            "endpoint": "quick_ask",
            "symbol": f"RECENT{idx}",
            "decision_source": "deterministic_model",
            "payload": {"recommendation": "BUY"},
        }
        for idx in range(5200)
    ]
    events_path.write_text(
        "\n".join(json.dumps(event) for event in [old_event, *recent_events]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DECISION_LOG_PATH", str(events_path))
    client = _client()
    client.application.config["DECISION_OUTCOMES_READ_CAP"] = 6000

    def fake_lookup(symbol, ts, days):
        if symbol == "OLD5D" and days in {1, 5}:
            return 0.05
        if days == 1:
            return 0.01
        return None

    monkeypatch.setattr(api_module, "_future_return_for_outcomes", fake_lookup)
    monkeypatch.setattr(api_module, "_price_path_for_outcomes", lambda *args, **kwargs: [])

    res = client.get("/api/decision-outcomes?limit=1&force_live=true")

    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["events_read"] == 5201
    assert data["evaluated_rows_5d_available"] > 0
    assert data["rows_5d"][0]["symbol"] == "OLD5D"
    assert data["all_available_events_read"] is True


def test_decision_outcomes_visible_pnl_uses_union_of_1d_and_5d_tables(tmp_path, monkeypatch):
    events_path = tmp_path / "decision_events.jsonl"
    events = [
        {"ts": 1, "endpoint": "quick_ask", "symbol": "OLD", "decision_source": "deterministic_model", "payload": {"recommendation": "BUY"}},
        {"ts": 2, "endpoint": "quick_ask", "symbol": "MID", "decision_source": "deterministic_model", "payload": {"recommendation": "SELL"}},
        {"ts": 3, "endpoint": "quick_ask", "symbol": "NEW", "decision_source": "deterministic_model", "payload": {"recommendation": "BUY"}},
    ]
    events_path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
    monkeypatch.setenv("DECISION_LOG_PATH", str(events_path))
    client = _client()

    def fake_lookup(symbol, ts, days):
        if days == 1 and symbol in {"MID", "NEW"}:
            return 0.01
        if days == 5 and symbol in {"OLD", "MID"}:
            return -0.02 if symbol == "MID" else 0.05
        return None

    monkeypatch.setattr(api_module, "_future_return_for_outcomes", fake_lookup)
    monkeypatch.setattr(api_module, "_price_path_for_outcomes", lambda *args, **kwargs: [])

    res = client.get("/api/decision-outcomes?limit=1&force_live=true")

    assert res.status_code == 200
    data = res.get_json()["data"]
    assert [row["symbol"] for row in data["rows_1d"]] == ["NEW"]
    assert [row["symbol"] for row in data["rows_5d"]] == ["MID"]
    visible = data["visible_paper_pnl_by_recommendation"]
    assert visible["BUY"]["rows"] == 1
    assert visible["SELL"]["rows"] == 1


def test_decision_outcomes_aggregate_scan_reaches_older_buy_after_recent_5d_hold_rows(tmp_path, monkeypatch):
    events_path = tmp_path / "decision_events.jsonl"
    old_buy_events = [
        {"ts": 1700000000 + idx, "endpoint": "quick_ask", "symbol": f"BUYOLD{idx}", "decision_source": "deterministic_model", "payload": {"recommendation": "BUY"}}
        for idx in range(3)
    ]
    recent_hold_events = [
        {"ts": 1701000000 + idx, "endpoint": "quick_ask", "symbol": f"HOLDNEW{idx}", "decision_source": "deterministic_model", "payload": {"recommendation": "HOLD"}}
        for idx in range(150)
    ]
    events_path.write_text(
        "\n".join(json.dumps(event) for event in [*old_buy_events, *recent_hold_events]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DECISION_LOG_PATH", str(events_path))
    client = _client()
    client.application.config["DECISION_OUTCOMES_READ_CAP"] = 1000

    def fake_lookup(symbol, ts, days):
        if days == 5:
            return 0.05
        if days == 1:
            return 0.01
        return None

    monkeypatch.setattr(api_module, "_future_return_for_outcomes", fake_lookup)
    monkeypatch.setattr(api_module, "_price_path_for_outcomes", lambda *args, **kwargs: [])

    res = client.get("/api/decision-outcomes?limit=100&force_live=true")

    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["events_read"] == 153
    assert data["aggregate_complete"] is True
    assert data["paper_pnl_by_recommendation"]["BUY"]["evaluated_rows_5d"] == 3
    assert len(data["rows_5d"]) == 100


def test_decision_outcomes_marks_partial_aggregate_when_read_cap_reached(tmp_path, monkeypatch):
    events_path = tmp_path / "decision_events.jsonl"
    events = [
        {"ts": 1700000000 + idx, "endpoint": "quick_ask", "symbol": f"SYM{idx}", "decision_source": "deterministic_model", "payload": {"recommendation": "BUY"}}
        for idx in range(20)
    ]
    events_path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
    monkeypatch.setenv("DECISION_LOG_PATH", str(events_path))
    client = _client()
    client.application.config["DECISION_OUTCOMES_READ_CAP"] = 5
    monkeypatch.setattr(api_module, "_future_return_for_outcomes", lambda *args, **kwargs: 0.01)
    monkeypatch.setattr(api_module, "_price_path_for_outcomes", lambda *args, **kwargs: [])

    res = client.get("/api/decision-outcomes?limit=1&force_live=true")

    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["events_read"] == 5
    assert data["aggregate_events_available"] == 20
    assert data["aggregate_events_scanned"] == 5
    assert data["aggregate_complete"] is False
    assert data["aggregate_scan_cap_reached"] is True
    assert len(data["rows"]) == 1


def test_decision_outcomes_allows_stale_snapshot_only_when_requested(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "decision_outcomes_snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "computed_at_utc": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
                "data": {
                    "rows": [{"symbol": "STALE", "action": "BUY", "return_1d": 0.01}],
                    "summary_1d": {"rows": 1, "evaluated_rows": 1, "accuracy": 1.0},
                    "summary_5d": {"rows": 0, "evaluated_rows": 0, "accuracy": None},
                },
            }
        ),
        encoding="utf-8",
    )
    log_path = tmp_path / "decision_events.jsonl"
    log_path.write_text(
        json.dumps({"ts": 1, "endpoint": "quick_ask", "symbol": "LIVE", "decision_source": "deterministic_model", "payload": {"recommendation": "BUY"}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DECISION_OUTCOMES_SNAPSHOT_PATH", str(snapshot_path))
    monkeypatch.setenv("DECISION_LOG_PATH", str(log_path))
    monkeypatch.setenv("DECISION_OUTCOMES_SNAPSHOT_MAX_AGE_SECONDS", "900")
    client = _client()
    monkeypatch.setattr(api_module, "_future_return_for_outcomes", lambda *args, **kwargs: 0.01)
    monkeypatch.setattr(api_module, "_price_path_for_outcomes", lambda *args, **kwargs: [])

    live = client.get("/api/decision-outcomes?limit=10")
    stale = client.get("/api/decision-outcomes?limit=10&allow_stale_snapshot=true")
    forced = client.get("/api/decision-outcomes?limit=10&force_live=true")

    assert live.get_json()["data"]["snapshot_source"] == "live"
    assert stale.get_json()["data"]["snapshot_source"] == "materialized_stale"
    assert stale.get_json()["data"]["snapshot_stale"] is True
    assert forced.get_json()["data"]["snapshot_source"] == "live"


def test_decision_outcomes_uses_materialized_snapshot_when_fresh(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "decision_outcomes_snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "computed_at_utc": datetime.now(timezone.utc).isoformat(),
                "data": {
                    "rows": [{"symbol": "AAPL", "action": "BUY", "return_1d": 0.02, "return_5d": 0.04}],
                    "summary_1d": {"rows": 1, "evaluated_rows": 1, "accuracy": 1.0, "counts": {"correct": 1, "incorrect": 0, "neutral": 0, "skipped": 0}, "avg_return_1d": 0.02, "avg_return_5d": 0.04},
                    "summary_5d": {"rows": 1, "evaluated_rows": 1, "accuracy": 1.0, "counts": {"correct": 1, "incorrect": 0, "neutral": 0, "skipped": 0}, "avg_return_1d": 0.04, "avg_return_5d": 0.04},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DECISION_OUTCOMES_SNAPSHOT_PATH", str(snapshot_path))
    monkeypatch.setenv("DECISION_OUTCOMES_SNAPSHOT_MAX_AGE_SECONDS", "900")
    client = _client()

    monkeypatch.setattr(api_module, "_future_return_for_outcomes", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("should not run live lookup")))

    res = client.get("/api/decision-outcomes?limit=10")
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["snapshot_source"] == "materialized"
    assert data["rows"][0]["symbol"] == "AAPL"
    assert data["paper_pnl_by_recommendation"]["BUY"]["rows"] == 1
    assert data["snapshot_age_seconds"] >= 0


def test_decision_outcomes_uses_daily_snapshot_for_default_ttl(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "decision_outcomes_snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "computed_at_utc": (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(),
                "data": {
                    "rows": [{"symbol": "SNAP", "action": "BUY", "return_1d": 0.01}],
                    "summary_1d": {"rows": 1, "evaluated_rows": 1, "accuracy": 1.0},
                    "summary_5d": {"rows": 0, "evaluated_rows": 0, "accuracy": None},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DECISION_OUTCOMES_SNAPSHOT_PATH", str(snapshot_path))
    monkeypatch.delenv("DECISION_OUTCOMES_SNAPSHOT_MAX_AGE_SECONDS", raising=False)
    client = _client()

    monkeypatch.setattr(api_module, "_future_return_for_outcomes", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("should use daily snapshot")))

    res = client.get("/api/decision-outcomes?limit=10")

    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["snapshot_source"] == "materialized"
    assert data["rows"][0]["symbol"] == "SNAP"
    assert data["snapshot_age_seconds"] >= 24 * 60 * 60


def test_decision_outcomes_serves_stale_snapshot_when_allowed(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "decision_outcomes_snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "computed_at_utc": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
                "data": {
                    "rows": [{"symbol": "STALE", "action": "BUY", "return_1d": 0.01}],
                    "summary_1d": {"rows": 1, "evaluated_rows": 1, "accuracy": 1.0},
                    "summary_5d": {"rows": 0, "evaluated_rows": 0, "accuracy": None},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DECISION_OUTCOMES_SNAPSHOT_PATH", str(snapshot_path))
    monkeypatch.setenv("DECISION_OUTCOMES_SNAPSHOT_MAX_AGE_SECONDS", "900")
    client = _client()

    monkeypatch.setattr(api_module, "_future_return_for_outcomes", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("should not fan out live lookups")))

    res = client.get("/api/decision-outcomes?limit=10&allow_stale_snapshot=true")

    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["snapshot_source"] == "materialized_stale"
    assert data["snapshot_stale"] is True
    assert data["rows"][0]["symbol"] == "STALE"
    assert data["paper_pnl_by_recommendation"]["BUY"]["rows"] == 1


def test_decision_outcomes_force_live_bypasses_materialized_snapshot(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "decision_outcomes_snapshot.json"
    snapshot_path.write_text(
        json.dumps({"computed_at_utc": datetime.now(timezone.utc).isoformat(), "data": {"rows": [{"symbol": "OLD"}], "summary_1d": {}, "summary_5d": {}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("DECISION_OUTCOMES_SNAPSHOT_PATH", str(snapshot_path))
    monkeypatch.setenv("DECISION_LOG_PATH", str(tmp_path / "decision_events.jsonl"))

    client = _client()
    logger = client.application.extensions["decision_logger"]
    logger.log(endpoint="quick_ask", symbol="MSFT", decision_source="deterministic_model", payload={"recommendation": "BUY"})
    monkeypatch.setattr(api_module, "_future_return_for_outcomes", lambda symbol, ts, days: 0.01)

    res = client.get("/api/decision-outcomes?limit=10&force_live=true")
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["snapshot_source"] == "live"
    assert data["rows"][0]["symbol"] == "MSFT"


def test_decision_outcomes_rejects_invalid_limit():
    client = _client()

    res = client.get("/api/decision-outcomes?limit=bad")

    assert res.status_code == 400
    assert res.get_json()["error"] == "limit must be an integer"


def test_explain_recommendation_returns_plain_english_text():
    client = _client()
    res = client.post(
        "/api/explain-recommendation",
        json={"recommendation": "BUY", "reason": "Momentum and sentiment both look positive."},
    )
    assert res.status_code == 200
    explanation = res.get_json()["data"]["explanation"]
    assert "reasonable to buy" in explanation.lower()
    assert "plain english" in explanation.lower()




def test_explain_recommendation_humanizes_jargon_reason():
    client = _client()
    res = client.post(
        "/api/explain-recommendation",
        json={"recommendation": "HOLD", "reason": "MACD hist positive (+3)"},
    )
    assert res.status_code == 200
    explanation = res.get_json()["data"]["explanation"].lower()
    assert "trend momentum" in explanation
    assert "macd" not in explanation

def test_explain_recommendation_humanizes_lowercase_jargon_reason():
    client = _client()
    res = client.post(
        "/api/explain-recommendation",
        json={"recommendation": "HOLD", "reason": "macd hist positive (+3 pts)"},
    )
    assert res.status_code == 200
    explanation = res.get_json()["data"]["explanation"].lower()
    assert "trend momentum" in explanation
    assert "points" in explanation
    assert "macd" not in explanation


def test_company_details_is_accessible_without_authentication():
    client = _client()
    res = client.get("/api/company-details?symbol=AAPL")
    assert res.status_code == 200
    payload = res.get_json()["data"]
    assert payload["symbol"] == "AAPL"


def test_signup_rejects_mismatched_password_confirmation():
    client = _client()
    signup = client.post(
        "/api/auth/signup",
        json=_signup_payload("mismatch@b.com", password="pw1", password_confirmation="pw2"),
    )
    assert signup.status_code == 400
    assert signup.get_json()["error"] == "passwords do not match"


def test_signup_requires_name_and_username():
    client = _client()
    signup = client.post(
        "/api/auth/signup",
        json={"email": "noname@b.com", "password": "pw", "password_confirmation": "pw"},
    )
    assert signup.status_code == 400
    assert "name, username" in signup.get_json()["error"]


def test_signup_rejects_duplicate_username():
    client = _client()
    first = client.post("/api/auth/signup", json=_signup_payload("alpha@b.com"))
    assert first.status_code == 201
    second_payload = _signup_payload("beta@b.com")
    second_payload["username"] = first.get_json()["user"]["username"]
    second = client.post("/api/auth/signup", json=second_payload)
    assert second.status_code == 409
    assert second.get_json()["error"] == "username already exists"


def test_me_profile_update_reflects_name_username_and_initials():
    client = _client()
    signup = client.post("/api/auth/signup", json=_signup_payload("profile@b.com"))
    assert signup.status_code == 201
    update = client.put("/api/me/profile", json={"name": "John Smith", "username": "johnsmith", "profile_image_url": None})
    assert update.status_code == 200
    payload = update.get_json()["user"]
    assert payload["name"] == "John Smith"
    assert payload["username"] == "johnsmith"
    assert payload["initials"] == "JS"
    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.get_json()["user"]["username"] == "johnsmith"


def test_me_security_requires_current_password():
    client = _client()
    signup = client.post("/api/auth/signup", json=_signup_payload("security@b.com", password="pw"))
    assert signup.status_code == 201
    res = client.put(
        "/api/me/security",
        json={"email": "security-new@b.com", "current_password": "wrong"},
    )
    assert res.status_code == 401
    assert res.get_json()["error"] == "current password is incorrect"


def test_me_security_updates_email_and_password():
    client = _client()
    signup = client.post("/api/auth/signup", json=_signup_payload("secure-update@b.com", password="oldpw"))
    assert signup.status_code == 201
    update = client.put(
        "/api/me/security",
        json={
            "email": "secure-new@b.com",
            "current_password": "oldpw",
            "new_password": "newpw",
            "confirm_new_password": "newpw",
        },
    )
    assert update.status_code == 200
    old_login = client.post("/api/auth/login", json={"email": "secure-update@b.com", "password": "oldpw"})
    assert old_login.status_code == 401
    new_login = client.post("/api/auth/login", json={"email": "secure-new@b.com", "password": "newpw"})
    assert new_login.status_code == 200


def test_login_accepts_username_identifier():
    client = _client()
    signup = client.post("/api/auth/signup", json=_signup_payload("identifier@b.com", password="pw123"))
    assert signup.status_code == 201

    login = client.post("/api/auth/login", json={"email": "testuser", "password": "pw123"})
    assert login.status_code == 200


def test_user_watchlist_exposes_quote_source_diagnostics():
    client = _client()
    signup = client.post("/api/auth/signup", json=_signup_payload("a@b.com"))
    assert signup.status_code == 201
    add = client.post("/api/user-watchlist", json={"symbol": "AAPL", "buy_price": 100, "shares": 1})
    assert add.status_code == 201

    res = client.get("/api/user-watchlist")
    assert res.status_code == 200
    enriched = res.get_json()["enriched_items"][0]
    assert enriched["quote_source"] == "finnhub"
    assert enriched["quote_diagnostics"]["provider"] == "finnhub"


def test_user_watchlist_returns_rows_with_quote_and_history_enrichment():
    client = _client()

    class QuoteOnlyPortfolioService(StubMarketService):
        def get_quote(self, symbol):
            return {
                "symbol": symbol,
                "price": 42.5,
                "change_percent": 2.0,
                "live_data_available": True,
                "quote_source": "test_quote",
                "diagnostics": {"provider": "test_quote", "error": None},
            }

        def get_signal(self, symbol, include_company_snapshot=True):
            raise AssertionError("portfolio endpoint should not call slow signal enrichment")

        def get_price_history_data(self, symbol, days=30):
            return {
                "symbol": symbol.upper(),
                "closes": [40.0, 41.0, 42.5],
                "bars": [
                    {"open": 39.5, "high": 40.5, "low": 39.0, "close": 40.0},
                    {"open": 40.0, "high": 41.5, "low": 39.8, "close": 41.0},
                    {"open": 41.0, "high": 43.0, "low": 40.8, "close": 42.5},
                ],
                "source": "test_history",
            }

    client.application.extensions["market_data_service"] = QuoteOnlyPortfolioService()
    signup = client.post("/api/auth/signup", json=_signup_payload("quote-only@b.com"))
    assert signup.status_code == 201
    assert client.post("/api/user-watchlist", json={"symbol": "AAPL", "buy_price": 40, "shares": 1}).status_code == 201
    assert client.post("/api/user-watchlist", json={"symbol": "TSLA", "buy_price": 50, "shares": 2}).status_code == 201

    res = client.get("/api/user-watchlist")

    assert res.status_code == 200
    payload = res.get_json()
    assert len(payload["items"]) == 2
    assert len(payload["enriched_items"]) == 2
    assert {item["current_price"] for item in payload["enriched_items"]} == {42.5}
    assert {item["quote_source"] for item in payload["enriched_items"]} == {"test_quote"}
    assert all(item["history30"] == [40.0, 41.0, 42.5] for item in payload["enriched_items"])
    assert all(len(item["history30_bars"]) == 3 for item in payload["enriched_items"])
    assert {item["history30_source"] for item in payload["enriched_items"]} == {"test_history"}


def test_user_watchlist_duplicate_symbol_points_to_buy_action():
    client = _client()
    signup = client.post("/api/auth/signup", json=_signup_payload("duplicate@b.com"))
    assert signup.status_code == 201
    add = client.post("/api/user-watchlist", json={"symbol": "AAPL", "buy_price": 100, "shares": 1})
    assert add.status_code == 201

    duplicate = client.post("/api/user-watchlist", json={"symbol": "AAPL", "buy_price": 110, "shares": 2})
    assert duplicate.status_code == 409
    assert duplicate.get_json()["error"] == (
        "Symbol already exists in portfolio. Click Buy in the Action column to add more shares."
    )


def test_forgot_password_returns_generic_success_message():
    client = _client()
    signup = client.post("/api/auth/signup", json=_signup_payload("recover@b.com"))
    assert signup.status_code == 201

    res = client.post("/api/auth/forgot-password", json={"email": "recover@b.com"})
    assert res.status_code == 200
    payload = res.get_json()
    assert payload["ok"] is True
    assert "If an account exists" in payload["message"]
    assert payload["email_delivery_configured"] is False


def test_forgot_password_requires_email():
    client = _client()
    res = client.post("/api/auth/forgot-password", json={})
    assert res.status_code == 400
    assert res.get_json()["error"] == "email required"


def test_sell_watchlist_item_records_realized_gain_and_reduces_shares():
    client = _client()
    signup = client.post("/api/auth/signup", json=_signup_payload("sell@b.com"))
    assert signup.status_code == 201

    add = client.post("/api/user-watchlist", json={"symbol": "AAPL", "buy_price": 100, "shares": 10})
    assert add.status_code == 201
    item_id = add.get_json()["item"]["id"]

    sell = client.post(f"/api/user-watchlist/{item_id}/sell", json={"sold_price": 120, "shares_sold": 4})
    assert sell.status_code == 200
    payload = sell.get_json()
    assert payload["removed"] is False
    assert payload["remaining_item"]["shares"] == 6.0
    assert payload["sold_trade"]["realized_amount"] == 80.0

    watchlist = client.get("/api/user-watchlist")
    assert watchlist.status_code == 200
    assert watchlist.get_json()["items"][0]["shares"] == 6.0

    sold_trades = client.get("/api/sold-trades")
    assert sold_trades.status_code == 200
    sold_payload = sold_trades.get_json()
    assert sold_payload["total_realized"] == 80.0
    assert sold_payload["items"][0]["symbol"] == "AAPL"


def test_sell_watchlist_item_removes_position_when_all_shares_are_sold():
    client = _client()
    signup = client.post("/api/auth/signup", json=_signup_payload("sellall@b.com"))
    assert signup.status_code == 201

    add = client.post("/api/user-watchlist", json={"symbol": "TSLA", "buy_price": 200, "shares": 2})
    assert add.status_code == 201
    item_id = add.get_json()["item"]["id"]

    sell = client.post(f"/api/user-watchlist/{item_id}/sell", json={"sold_price": 180, "shares_sold": 2})
    assert sell.status_code == 200
    payload = sell.get_json()
    assert payload["removed"] is True
    assert payload["remaining_item"] is None
    assert payload["sold_trade"]["realized_amount"] == -40.0

    watchlist = client.get("/api/user-watchlist")
    assert watchlist.status_code == 200
    assert watchlist.get_json()["items"] == []


def test_sell_watchlist_item_rejects_selling_more_than_owned():
    client = _client()
    signup = client.post("/api/auth/signup", json=_signup_payload("over@b.com"))
    assert signup.status_code == 201

    add = client.post("/api/user-watchlist", json={"symbol": "MSFT", "buy_price": 50, "shares": 1})
    assert add.status_code == 201
    item_id = add.get_json()["item"]["id"]

    sell = client.post(f"/api/user-watchlist/{item_id}/sell", json={"sold_price": 55, "shares_sold": 2})
    assert sell.status_code == 400
    assert sell.get_json()["error"] == "shares_sold cannot exceed current shares"


def test_update_sold_trade_recalculates_realized_gain_and_restores_shares():
    client = _client()
    signup = client.post("/api/auth/signup", json=_signup_payload("edit-sold@b.com"))
    assert signup.status_code == 201

    add = client.post("/api/user-watchlist", json={"symbol": "AAPL", "buy_price": 100, "shares": 10})
    assert add.status_code == 201
    item_id = add.get_json()["item"]["id"]

    sell = client.post(f"/api/user-watchlist/{item_id}/sell", json={"sold_price": 120, "shares_sold": 4})
    assert sell.status_code == 200
    trade_id = sell.get_json()["sold_trade"]["id"]

    update = client.patch(f"/api/sold-trades/{trade_id}", json={"sold_price": 125, "shares_sold": 2})
    assert update.status_code == 200
    payload = update.get_json()
    assert payload["sold_trade"]["sold_price"] == 125.0
    assert payload["sold_trade"]["shares_sold"] == 2.0
    assert payload["sold_trade"]["realized_amount"] == 50.0
    assert payload["remaining_item"]["shares"] == 8.0
    assert payload["total_realized"] == 50.0

    watchlist = client.get("/api/user-watchlist")
    assert watchlist.status_code == 200
    assert watchlist.get_json()["items"][0]["shares"] == 8.0

    sold_trades = client.get("/api/sold-trades")
    assert sold_trades.status_code == 200
    sold_payload = sold_trades.get_json()
    assert sold_payload["total_realized"] == 50.0
    assert sold_payload["items"][0]["shares_sold"] == 2.0


def test_update_sold_trade_allows_correction_when_portfolio_shares_are_already_fixed():
    client = _client()
    signup = client.post("/api/auth/signup", json=_signup_payload("edit-sold-fixed@b.com"))
    assert signup.status_code == 201

    add = client.post("/api/user-watchlist", json={"symbol": "AAPL", "buy_price": 1, "shares": 575})
    assert add.status_code == 201
    item_id = add.get_json()["item"]["id"]

    sell = client.post(f"/api/user-watchlist/{item_id}/sell", json={"sold_price": 415, "shares_sold": 3.59})
    assert sell.status_code == 200
    trade_id = sell.get_json()["sold_trade"]["id"]

    manual_fix = client.patch(f"/api/user-watchlist/{item_id}", json={"shares": 160})
    assert manual_fix.status_code == 200

    update = client.patch(f"/api/sold-trades/{trade_id}", json={"sold_price": 3.59, "shares_sold": 415})
    assert update.status_code == 200
    payload = update.get_json()
    assert payload["sold_trade"]["sold_price"] == 3.59
    assert payload["sold_trade"]["shares_sold"] == 415.0
    assert payload["sold_trade"]["realized_amount"] == 1074.85
    assert payload["portfolio_adjustment_skipped"] is True
    assert "already been corrected" in payload["portfolio_adjustment_note"]

    watchlist = client.get("/api/user-watchlist")
    assert watchlist.status_code == 200
    assert watchlist.get_json()["items"][0]["shares"] == 160.0


def test_update_sold_trade_restores_position_after_all_shares_were_sold():
    client = _client()
    signup = client.post("/api/auth/signup", json=_signup_payload("edit-sold-all@b.com"))
    assert signup.status_code == 201

    add = client.post("/api/user-watchlist", json={"symbol": "TSLA", "buy_price": 200, "shares": 5})
    assert add.status_code == 201
    item_id = add.get_json()["item"]["id"]

    sell = client.post(f"/api/user-watchlist/{item_id}/sell", json={"sold_price": 220, "shares_sold": 5})
    assert sell.status_code == 200
    assert sell.get_json()["removed"] is True
    trade_id = sell.get_json()["sold_trade"]["id"]

    update = client.patch(f"/api/sold-trades/{trade_id}", json={"sold_price": 220, "shares_sold": 3})
    assert update.status_code == 200
    payload = update.get_json()
    assert payload["sold_trade"]["realized_amount"] == 60.0
    assert payload["remaining_item"]["symbol"] == "TSLA"
    assert payload["remaining_item"]["entry_price"] == 200.0
    assert payload["remaining_item"]["shares"] == 2.0

    watchlist = client.get("/api/user-watchlist")
    assert watchlist.status_code == 200
    assert watchlist.get_json()["items"][0]["shares"] == 2.0

def test_buy_watchlist_item_increases_shares_and_recalculates_entry_price():
    client = _client()
    signup = client.post("/api/auth/signup", json=_signup_payload("buy@b.com"))
    assert signup.status_code == 201

    add = client.post("/api/user-watchlist", json={"symbol": "AAPL", "buy_price": 100, "shares": 10})
    assert add.status_code == 201
    item_id = add.get_json()["item"]["id"]

    buy = client.post(f"/api/user-watchlist/{item_id}/buy", json={"bought_price": 130, "shares_bought": 5})
    assert buy.status_code == 200
    payload = buy.get_json()
    assert payload["item"]["shares"] == 15.0
    assert payload["item"]["entry_price"] == 110.0
    assert payload["added"]["new_entry_price"] == 110.0


def test_buy_watchlist_item_validates_positive_inputs():
    client = _client()
    signup = client.post("/api/auth/signup", json=_signup_payload("buy-invalid@b.com"))
    assert signup.status_code == 201

    add = client.post("/api/user-watchlist", json={"symbol": "TSLA", "buy_price": 200, "shares": 2})
    assert add.status_code == 201
    item_id = add.get_json()["item"]["id"]

    bad_price = client.post(f"/api/user-watchlist/{item_id}/buy", json={"bought_price": 0, "shares_bought": 1})
    assert bad_price.status_code == 400
    assert bad_price.get_json()["error"] == "bought_price must be > 0"

    bad_shares = client.post(f"/api/user-watchlist/{item_id}/buy", json={"bought_price": 210, "shares_bought": 0})
    assert bad_shares.status_code == 400
    assert bad_shares.get_json()["error"] == "shares_bought must be > 0"


def test_forgot_password_reports_delivery_error_when_email_send_fails(monkeypatch):
    client = _client()
    signup = client.post("/api/auth/signup", json=_signup_payload("mailer-fail@b.com"))
    assert signup.status_code == 201

    def fake_send_reset_email(email, reset_link):
        return False

    monkeypatch.setattr(api_module, "_send_reset_email", fake_send_reset_email)

    os.environ["SMTP_HOST"] = "smtp.example.com"
    os.environ["PASSWORD_RESET_FROM_EMAIL"] = "noreply@example.com"

    with client.application.app_context():
        current_app.config["SMTP_HOST"] = "smtp.example.com"
        current_app.config["PASSWORD_RESET_FROM_EMAIL"] = "noreply@example.com"

    res = client.post("/api/auth/forgot-password", json={"email": "mailer-fail@b.com"})
    assert res.status_code == 200
    payload = res.get_json()
    assert payload["ok"] is True
    assert payload["email_delivery_configured"] is True
    assert payload["email_delivery_error"] is True


def test_forgot_password_sends_reset_email_for_existing_user(monkeypatch):
    client = _client()
    signup = client.post("/api/auth/signup", json=_signup_payload("mailer@b.com"))
    assert signup.status_code == 201

    captured = {}

    def fake_send_reset_email(email, reset_link):
        captured["email"] = email
        captured["reset_link"] = reset_link
        return True

    monkeypatch.setattr(api_module, "_send_reset_email", fake_send_reset_email)

    res = client.post("/api/auth/forgot-password", json={"email": "mailer@b.com"})
    assert res.status_code == 200
    assert captured["email"] == "mailer@b.com"
    assert "reset-password" in captured["reset_link"] and "token=" in captured["reset_link"]
    payload = res.get_json()
    assert payload["email_delivery_error"] is False


def test_reset_password_updates_credentials_and_allows_login():
    client = _client()
    signup = client.post("/api/auth/signup", json=_signup_payload("reset@b.com", password="oldpw"))
    assert signup.status_code == 201

    with client.application.app_context():
        user = api_module.User.query.filter_by(email="reset@b.com").first()
        token = api_module._password_reset_serializer().dumps({"user_id": user.id})

    reset = client.post("/api/auth/reset-password", json={"token": token, "password": "newpw"})
    assert reset.status_code == 200

    old_login = client.post("/api/auth/login", json={"email": "reset@b.com", "password": "oldpw"})
    assert old_login.status_code == 401

    new_login = client.post("/api/auth/login", json={"email": "reset@b.com", "password": "newpw"})
    assert new_login.status_code == 200


def test_send_reset_email_sets_deliverability_headers(monkeypatch):
    os.environ["MONEYBOT_SECRET_KEY"] = "test-secret"
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["SMTP_HOST"] = "smtp.example.com"
    os.environ["SMTP_PORT"] = "587"
    os.environ["SMTP_USER"] = "support@moneybotlabs.com"
    os.environ["SMTP_PASSWORD"] = "pw"
    os.environ["SMTP_USE_TLS"] = "false"
    os.environ["PASSWORD_RESET_FROM_EMAIL"] = "support@moneybotlabs.com"
    os.environ["PASSWORD_RESET_FROM_NAME"] = "Moneybot Labs Support"
    app = create_app()

    captured = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            captured["host"] = host
            captured["port"] = port
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def starttls(self):
            captured["starttls_called"] = True

        def login(self, user, password):
            captured["login"] = (user, password)

        def send_message(self, message):
            captured["message"] = message

    monkeypatch.setattr(api_module.smtplib, "SMTP", FakeSMTP)

    with app.app_context():
        sent = api_module._send_reset_email("user@example.com", "https://moneybotlabs.com/reset-password?token=abc")

    assert sent is True
    msg = captured["message"]
    assert msg["From"] == "Moneybot Labs Support <support@moneybotlabs.com>"
    assert msg["Reply-To"] == "support@moneybotlabs.com"
    assert msg["Date"]
    assert msg["Message-ID"]


def test_password_reset_email_config_helper_reads_runtime_config():
    os.environ["MONEYBOT_SECRET_KEY"] = "test-secret"
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["SMTP_HOST"] = "smtp.example.com"
    os.environ["PASSWORD_RESET_FROM_EMAIL"] = "noreply@example.com"
    app = create_app()

    with app.app_context():
        assert api_module._password_reset_email_configured() is True


def test_user_watchlist_uses_ai_portfolio_advice_when_available():
    client = _client()
    client.application.extensions["ai_advisor_service"] = StubAIAdvisorService()

    signup = client.post("/api/auth/signup", json=_signup_payload("portfolio-ai@b.com"))
    assert signup.status_code == 201
    add = client.post("/api/user-watchlist", json={"symbol": "AAPL", "buy_price": 100, "shares": 1})
    assert add.status_code == 201

    res = client.get("/api/user-watchlist")
    assert res.status_code == 200
    enriched = res.get_json()["enriched_items"][0]
    assert enriched["advice"] == "HOLD"
    assert enriched["quick_alignment_recommendation"] in {"BUY", "STRONG BUY"}
    assert enriched["ai_portfolio"]["mode"] == "ai_enhanced"
    assert enriched["ai_portfolio"]["provider"] == "stub"


def test_user_watchlist_includes_deterministic_portfolio_advice_when_available():
    client = _client()
    client.application.extensions["deterministic_quick_advisor"] = StubDeterministicQuickAdvisor()
    client.application.extensions["ai_advisor_service"] = None

    signup = client.post("/api/auth/signup", json=_signup_payload("portfolio-det@b.com"))
    assert signup.status_code == 201
    add = client.post("/api/user-watchlist", json={"symbol": "AAPL", "buy_price": 100, "shares": 1})
    assert add.status_code == 201

    res = client.get("/api/user-watchlist")
    assert res.status_code == 200
    enriched = res.get_json()["enriched_items"][0]
    assert enriched["advice"] == "BUY"
    assert enriched["deterministic_portfolio"]["mode"] == "deterministic_model"
    assert enriched["deterministic_portfolio"]["decision_source"] == "deterministic_model"


def test_user_watchlist_keeps_deterministic_portfolio_advice_when_ai_is_enabled():
    client = _client()
    client.application.extensions["deterministic_quick_advisor"] = StubDeterministicQuickAdvisor()
    client.application.extensions["ai_advisor_service"] = StubAIAdvisorService()

    signup = client.post("/api/auth/signup", json=_signup_payload("portfolio-det-ai@b.com"))
    assert signup.status_code == 201
    add = client.post("/api/user-watchlist", json={"symbol": "AAPL", "buy_price": 100, "shares": 1})
    assert add.status_code == 201

    res = client.get("/api/user-watchlist")
    assert res.status_code == 200
    enriched = res.get_json()["enriched_items"][0]
    assert enriched["advice"] == "BUY"
    assert enriched["deterministic_portfolio"]["decision_source"] == "deterministic_model"
    assert enriched["ai_portfolio"]["mode"] == "ai_enhanced"


def test_export_decision_log_requires_token():
    client = _client()
    res = client.get('/api/export-decision-log')
    assert res.status_code == 401
    assert res.get_json()['error'] == 'unauthorized'


def test_export_decision_log_returns_ndjson_with_token(tmp_path):
    client = _client()
    log_path = tmp_path / 'decision_events.jsonl'
    log_path.write_text(
        '{"ts": 1, "endpoint": "quick_ask", "symbol": "AAPL", "decision_source": "deterministic_model", "payload": {"recommendation": "BUY"}}\n'
        '{"ts": 2, "endpoint": "hot_momentum_buys", "symbol": "TSLA", "decision_source": "rule_based", "payload": {"recommendation": "HOLD"}}\n',
        encoding='utf-8',
    )

    with client.application.app_context():
        current_app.config['DAILY_OPS_TOKEN'] = 'secret-token'
        current_app.config['DECISION_LOG_PATH'] = str(log_path)

    res = client.get('/api/export-decision-log?limit=1', headers={'X-Daily-Ops-Token': 'secret-token'})
    assert res.status_code == 200
    assert 'application/x-ndjson' in (res.headers.get('Content-Type') or '')
    assert res.headers.get('X-Decision-Log-Lines') == '1'
    lines = [line for line in res.get_data(as_text=True).splitlines() if line.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload['symbol'] == 'TSLA'


def test_model_health_includes_safe_historical_validation_when_default_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("MONEYBOT_PERSISTENT_DATA_DIR", str(tmp_path))
    client = _client()

    res = client.get("/api/model-health")

    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["historical_validation"]["available"] is False
    assert data["historical_validation"]["configured"] is True
    assert data["historical_validation"]["path"] == str(tmp_path / "historical_validation_report.json")
    assert data["historical_validation"]["exists"] is False
    assert data["historical_validation"]["summary"] is None
    assert data["historical_validation"]["error"] is None


def test_model_health_loads_historical_validation_summary(tmp_path):
    report_path = tmp_path / "historical_validation.json"
    report_path.write_text(
        json.dumps({"generated_at_utc": "2026-06-21T00:00:00+00:00", "rows": 42, "accuracy": 0.72, "ignored": "large"}),
        encoding="utf-8",
    )
    client = _client()
    client.application.config["HISTORICAL_VALIDATION_REPORT_PATH"] = str(report_path)

    res = client.get("/api/model-health")

    assert res.status_code == 200
    historical = res.get_json()["data"]["historical_validation"]
    assert historical["available"] is True
    assert historical["configured"] is True
    assert historical["path"] == str(report_path)
    assert historical["exists"] is True
    assert historical["summary"] == {"generated_at_utc": "2026-06-21T00:00:00+00:00", "rows": 42, "accuracy": 0.72}




def test_day14_promotion_metadata_uses_candidate_version(tmp_path):
    from scripts import day14_promote_candidate

    comparison_path = tmp_path / "comparison.json"
    candidate_path = tmp_path / "candidate.json"
    production_path = tmp_path / "production.json"
    comparison_path.write_text(json.dumps({"candidate_win": True, "candidate_metrics": {"rows": 12}, "production_metrics": {"rows": 8}}), encoding="utf-8")
    candidate_path.write_text(json.dumps({"version": "candidate-logreg-v1-20260710T225011Z", "feature_columns": []}), encoding="utf-8")

    old_argv = sys.argv
    try:
        sys.argv = [
            "day14_promote_candidate.py",
            "--comparison-report",
            str(comparison_path),
            "--candidate-model",
            str(candidate_path),
            "--production-model",
            str(production_path),
        ]
        day14_promote_candidate.main()
    finally:
        sys.argv = old_argv

    metadata = json.loads(production_path.with_suffix(production_path.suffix + ".meta.json").read_text(encoding="utf-8"))
    assert metadata["model_version"] == "candidate-logreg-v1-20260710T225011Z"

def test_promote_track_b_candidate_requires_token():
    client = _client()
    client.application.config["TRACK_B_PROMOTION_TOKEN"] = "promote-token"

    res = client.post("/api/promote-track-b-candidate")

    assert res.status_code == 401
    assert res.get_json()["error"] == "unauthorized"


def test_promote_track_b_candidate_rejects_losing_report(tmp_path, monkeypatch):
    monkeypatch.setenv("MONEYBOT_PERSISTENT_DATA_DIR", str(tmp_path))
    client = _client()
    client.application.config["TRACK_B_PROMOTION_TOKEN"] = "promote-token"

    res = client.post(
        "/api/promote-track-b-candidate",
        headers={"X-Track-B-Promotion-Token": "promote-token"},
        data={
            "comparison_report": (BytesIO(json.dumps({"candidate_win": False, "reasons": ["not enough"]}).encode()), "model_comparison_track_b.json"),
            "candidate_model": (BytesIO(json.dumps({"version": "candidate"}).encode()), "candidate_model_track_b.json"),
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 409
    payload = res.get_json()["data"]
    assert payload["success"] is False
    assert payload["promoted"] is False
    assert payload["reasons"] == ["not enough"]


def test_promote_track_b_candidate_rejects_no_promotable_placeholder(tmp_path, monkeypatch):
    monkeypatch.setenv("MONEYBOT_PERSISTENT_DATA_DIR", str(tmp_path))
    client = _client()
    client.application.config["TRACK_B_PROMOTION_TOKEN"] = "promote-token"

    res = client.post(
        "/api/promote-track-b-candidate",
        headers={"X-Track-B-Promotion-Token": "promote-token"},
        data={
            "comparison_report": (BytesIO(json.dumps({"candidate_win": True, "reasons": ["approved"]}).encode()), "model_comparison_track_b.json"),
            "candidate_model": (
                BytesIO(json.dumps({"promotion_ready": False, "version": "no-promotable-challenger"}).encode()),
                "candidate_model_track_b.json",
            ),
            "force": "true",
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 409
    payload = res.get_json()["data"]
    assert payload["success"] is False
    assert payload["promoted"] is False
    assert payload["candidate_model_version"] == "no-promotable-challenger"
    assert "cannot be promoted" in payload["message"]


def test_promote_track_b_candidate_uploads_and_runs_promotion(monkeypatch, tmp_path):
    class Completed:
        returncode = 0
        stdout = "promoted candidate -> /var/data/moneybot/day1_baseline_model.json\n"
        stderr = ""

    captured = {}

    def fake_run(command, cwd, capture_output, text, check):
        captured["command"] = command
        captured["cwd"] = cwd
        assert capture_output is True
        assert text is True
        assert check is False
        return Completed()

    monkeypatch.setattr(api_module.subprocess, "run", fake_run)
    monkeypatch.setenv("MONEYBOT_PERSISTENT_DATA_DIR", str(tmp_path))

    client = _client()
    client.application.config["TRACK_B_PROMOTION_TOKEN"] = "promote-token"
    client.application.config["DETERMINISTIC_MODEL_PATH"] = str(tmp_path / "day1_baseline_model.json")

    report = {"candidate_win": True, "reasons": ["candidate accuracy exceeds production by at least 0.02"]}
    candidate = {"version": "candidate-promoted-v1"}
    res = client.post(
        "/api/promote-track-b-candidate",
        headers={"X-Track-B-Promotion-Token": "promote-token"},
        data={
            "comparison_report": (BytesIO(json.dumps(report).encode()), "model_comparison_track_b.json"),
            "candidate_model": (BytesIO(json.dumps(candidate).encode()), "candidate_model_track_b.json"),
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 200
    payload = res.get_json()["data"]
    assert payload["success"] is True
    assert payload["promoted"] is True
    assert payload["candidate_win"] is True
    assert payload["comparison_report_path"] == str(tmp_path / "track_b" / "model_comparison_track_b.json")
    assert payload["candidate_model_path"] == str(tmp_path / "track_b" / "candidate_model_track_b.json")
    assert json.loads((tmp_path / "track_b" / "model_comparison_track_b.json").read_text()) == report
    assert json.loads((tmp_path / "track_b" / "candidate_model_track_b.json").read_text()) == candidate
    assert captured["command"] == [
        "python3",
        "scripts/day14_promote_candidate.py",
        "--comparison-report",
        str(tmp_path / "track_b" / "model_comparison_track_b.json"),
        "--candidate-model",
        str(tmp_path / "track_b" / "candidate_model_track_b.json"),
        "--production-model",
        str(tmp_path / "day1_baseline_model.json"),
    ]
