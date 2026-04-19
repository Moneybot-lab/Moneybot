import os
import json
from datetime import datetime, timezone

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
            "model_version": "day1-logreg-v1",
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
            "model_version": "day1-logreg-v1",
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
    wells = client.get("/api/wells-picks")

    assert stable.status_code == 200
    assert momentum.status_code == 200
    assert wells.status_code == 200

    assert stable.get_json()["items"][0]["symbol"] == "MSFT"
    assert momentum.get_json()["items"][0]["symbol"] == "NVDA"
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
    assert data["model_version"] == "day1-logreg-v1"
    assert data["confidence"] == 78.0


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
    metadata = {"model_version": "day1-logreg-v1", "train_rows": 100}
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    history_path.write_text(json.dumps([metadata]), encoding="utf-8")
    monkeypatch.setenv("DETERMINISTIC_MODEL_PATH", str(model_path))

    client = _client()
    res = client.get("/api/model-health")

    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["artifact_metadata"]["model_version"] == "day1-logreg-v1"
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
                "model_version": "day1-logreg-v1",
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
    logger.log(endpoint="quick_ask", symbol="AAPL", decision_source="deterministic_model", payload={"recommendation": "BUY", "model_version": "day1-logreg-v1"})
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
    assert data["rows"][0]["model_version"] == "day1-logreg-v1"
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


def test_decision_outcomes_prefers_rows_with_5d_returns_for_default_view(tmp_path, monkeypatch):
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
    assert len(data["rows"]) == 1
    assert data["rows"][0]["symbol"] == "TSLA"
    assert data["rows"][0]["return_5d"] == -0.04
    assert data["summary_5d"]["evaluated_rows"] == 1
    assert data["evaluated_rows_available"] == 3
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
    assert calls["count"] == 2
    assert data["lookup_cache_misses"] == 2
    assert data["lookup_cache_hits"] >= 4
    assert data["lookup_cache_size"] == 2


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
    assert data["snapshot_age_seconds"] >= 0


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
    assert enriched["advice"] == "SELL"
    assert "buy-in" in enriched["advice_reason"].lower()
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
