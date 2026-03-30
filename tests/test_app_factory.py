import pytest

from moneybot.app_factory import _resolve_database_url, create_app


def test_resolve_database_url_uses_postgres_internal_alias(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_INTERNAL_URL", "postgres://user:pw@localhost:5432/moneybot")
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)

    resolved = _resolve_database_url()

    assert resolved.startswith("postgresql")


def test_resolve_database_url_rejects_sqlite_on_render(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_INTERNAL_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("POSTGRESQL_URL", raising=False)
    monkeypatch.setenv("RENDER", "true")

    with pytest.raises(RuntimeError, match="No persistent PostgreSQL database"):
        _resolve_database_url()


def test_resolve_database_url_rejects_hosted_postgres_without_driver(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@localhost:5432/moneybot")
    monkeypatch.setenv("RENDER", "true")

    import importlib.util

    original_find_spec = importlib.util.find_spec

    def fake_find_spec(name):
        if name in {"psycopg", "psycopg2"}:
            return None
        return original_find_spec(name)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    with pytest.raises(RuntimeError, match="no PostgreSQL driver is installed"):
        _resolve_database_url()


def test_create_app_reads_ai_timeout_and_cooldown(monkeypatch):
    monkeypatch.setenv("MONEYBOT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_API_KEY", "key")
    monkeypatch.setenv("AI_TIMEOUT_SECONDS", "1.7")
    monkeypatch.setenv("AI_FAILURE_COOLDOWN_SECONDS", "45")
    monkeypatch.setenv("AI_RESPONSE_CACHE_TTL_SECONDS", "180")

    app = create_app()
    svc = app.extensions["ai_advisor_service"]

    assert app.config["AI_TIMEOUT_SECONDS"] == 1.7
    assert app.config["AI_FAILURE_COOLDOWN_SECONDS"] == 45
    assert app.config["AI_RESPONSE_CACHE_TTL_SECONDS"] == 180
    assert svc.timeout_s == 1.7
    assert svc.failure_cooldown_s == 45
    assert svc.cache_ttl_s == 180


def test_create_app_uses_new_default_ai_timeout(monkeypatch):
    monkeypatch.setenv("MONEYBOT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.delenv("AI_TIMEOUT_SECONDS", raising=False)

    app = create_app()

    assert app.config["AI_TIMEOUT_SECONDS"] == 6.0
    assert app.extensions["ai_advisor_service"].timeout_s == 6.0


def test_create_app_reads_deterministic_quick_settings(monkeypatch):
    monkeypatch.setenv("MONEYBOT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("DETERMINISTIC_QUICK_ENABLED", "false")
    monkeypatch.setenv("DETERMINISTIC_MODEL_PATH", "data/custom_day1.json")

    app = create_app()
    svc = app.extensions["deterministic_quick_advisor"]

    assert app.config["DETERMINISTIC_QUICK_ENABLED"] is False
    assert app.config["DETERMINISTIC_MODEL_PATH"] == "data/custom_day1.json"
    assert svc.enabled is False
    assert svc.artifact_path == "data/custom_day1.json"


def test_create_app_reads_deterministic_momentum_setting(monkeypatch):
    monkeypatch.setenv("MONEYBOT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("DETERMINISTIC_MOMENTUM_ENABLED", "false")

    app = create_app()
    svc = app.extensions["market_data_service"]

    assert app.config["DETERMINISTIC_MOMENTUM_ENABLED"] is False
    assert svc.deterministic_momentum_enabled is False


def test_create_app_reads_decision_logging_settings(monkeypatch):
    monkeypatch.setenv("MONEYBOT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("DECISION_LOGGING_ENABLED", "false")
    monkeypatch.setenv("DECISION_LOG_PATH", "data/custom_events.jsonl")

    app = create_app()
    logger = app.extensions["decision_logger"]

    assert app.config["DECISION_LOGGING_ENABLED"] is False
    assert app.config["DECISION_LOG_PATH"] == "data/custom_events.jsonl"
    assert logger.enabled is False
    assert logger.output_path == "data/custom_events.jsonl"


def test_create_app_reads_deterministic_threshold_settings(monkeypatch):
    monkeypatch.setenv("MONEYBOT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("DETERMINISTIC_QUICK_BUY_THRESHOLD", "0.61")
    monkeypatch.setenv("DETERMINISTIC_QUICK_STRONG_BUY_THRESHOLD", "0.78")
    monkeypatch.setenv("DETERMINISTIC_PORTFOLIO_BUY_PROB_THRESHOLD", "0.65")
    monkeypatch.setenv("DETERMINISTIC_PORTFOLIO_SELL_PROB_THRESHOLD", "0.42")
    monkeypatch.setenv("DETERMINISTIC_PORTFOLIO_BUY_DIP_THRESHOLD_PCT", "-5.5")
    monkeypatch.setenv("DETERMINISTIC_PORTFOLIO_SELL_PROFIT_THRESHOLD_PCT", "8.0")

    app = create_app()
    svc = app.extensions["deterministic_quick_advisor"]

    assert app.config["DETERMINISTIC_QUICK_BUY_THRESHOLD"] == 0.61
    assert app.config["DETERMINISTIC_QUICK_STRONG_BUY_THRESHOLD"] == 0.78
    assert app.config["DETERMINISTIC_PORTFOLIO_BUY_PROB_THRESHOLD"] == 0.65
    assert app.config["DETERMINISTIC_PORTFOLIO_SELL_PROB_THRESHOLD"] == 0.42
    assert app.config["DETERMINISTIC_PORTFOLIO_BUY_DIP_THRESHOLD_PCT"] == -5.5
    assert app.config["DETERMINISTIC_PORTFOLIO_SELL_PROFIT_THRESHOLD_PCT"] == 8.0
    assert svc.quick_buy_threshold == 0.61
    assert svc.quick_strong_buy_threshold == 0.78
    assert svc.portfolio_buy_prob_threshold == 0.65
    assert svc.portfolio_sell_prob_threshold == 0.42
    assert svc.portfolio_buy_dip_threshold_pct == -5.5
    assert svc.portfolio_sell_profit_threshold_pct == 8.0


def test_create_app_reads_deterministic_calibration_and_rollout_settings(monkeypatch):
    monkeypatch.setenv("MONEYBOT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("DETERMINISTIC_CALIBRATION_ENABLED", "true")
    monkeypatch.setenv("DETERMINISTIC_CALIBRATION_SLOPE", "0.9")
    monkeypatch.setenv("DETERMINISTIC_CALIBRATION_INTERCEPT", "-0.15")
    monkeypatch.setenv("DETERMINISTIC_ROLLOUT_PERCENTAGE", "35")
    monkeypatch.setenv("DETERMINISTIC_ROLLOUT_SEED", "day12")
    monkeypatch.setenv("DETERMINISTIC_ROLLOUT_ALLOWLIST", "AAPL, msft")
    monkeypatch.setenv("DETERMINISTIC_ROLLOUT_BLOCKLIST", "TSLA")
    monkeypatch.setenv("DETERMINISTIC_ROLLOUT_DRY_RUN", "true")
    monkeypatch.setenv("DETERMINISTIC_CALIBRATION_REPORT_PATH", "data/custom_calibration_report.json")
    monkeypatch.setenv("DETERMINISTIC_CALIBRATION_REPORT_MAX_AGE_SECONDS", "1234")

    app = create_app()
    svc = app.extensions["deterministic_quick_advisor"]

    assert app.config["DETERMINISTIC_CALIBRATION_ENABLED"] is True
    assert app.config["DETERMINISTIC_CALIBRATION_SLOPE"] == 0.9
    assert app.config["DETERMINISTIC_CALIBRATION_INTERCEPT"] == -0.15
    assert app.config["DETERMINISTIC_ROLLOUT_PERCENTAGE"] == 35.0
    assert app.config["DETERMINISTIC_ROLLOUT_SEED"] == "day12"
    assert app.config["DETERMINISTIC_ROLLOUT_ALLOWLIST"] == {"AAPL", "MSFT"}
    assert app.config["DETERMINISTIC_ROLLOUT_BLOCKLIST"] == {"TSLA"}
    assert app.config["DETERMINISTIC_ROLLOUT_DRY_RUN"] is True
    assert app.config["DETERMINISTIC_CALIBRATION_REPORT_PATH"] == "data/custom_calibration_report.json"
    assert app.config["DETERMINISTIC_CALIBRATION_REPORT_MAX_AGE_SECONDS"] == 1234
    assert svc.calibration_enabled is True
    assert svc.rollout_percentage == 35.0
    assert svc.rollout_allowlist == {"AAPL", "MSFT"}
    assert svc.rollout_blocklist == {"TSLA"}
    assert svc.rollout_dry_run is True


def test_create_app_reads_outcomes_snapshot_settings(monkeypatch):
    monkeypatch.setenv("MONEYBOT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("DECISION_OUTCOMES_SNAPSHOT_PATH", "data/custom_outcomes_snapshot.json")
    monkeypatch.setenv("DECISION_OUTCOMES_SNAPSHOT_MAX_AGE_SECONDS", "120")

    app = create_app()

    assert app.config["DECISION_OUTCOMES_SNAPSHOT_PATH"] == "data/custom_outcomes_snapshot.json"
    assert app.config["DECISION_OUTCOMES_SNAPSHOT_MAX_AGE_SECONDS"] == 120


def test_home_page_includes_model_ops_snapshot(monkeypatch):
    monkeypatch.setenv("MONEYBOT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")

    app = create_app()
    client = app.test_client()

    res = client.get("/")

    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "Model Ops Snapshot" in html
    assert "Refresh Ops" in html
    assert "Recent Decisions & Outcomes" in html
    assert "/static/js/home.js" in html

    js_res = client.get("/static/js/home.js")
    assert js_res.status_code == 200
    js = js_res.get_data(as_text=True)
    assert "/api/decision-log-summary?limit=50" in js
    assert "/api/decision-outcomes?limit=20" in js
