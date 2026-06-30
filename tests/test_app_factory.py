import json

import pytest

from moneybot.app_factory import _database_engine_options, _resolve_database_url, create_app


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


def test_database_engine_options_configure_postgres_pool(monkeypatch):
    monkeypatch.delenv("SQLALCHEMY_POOL_SIZE", raising=False)
    monkeypatch.delenv("SQLALCHEMY_MAX_OVERFLOW", raising=False)
    monkeypatch.delenv("SQLALCHEMY_POOL_TIMEOUT", raising=False)
    monkeypatch.delenv("SQLALCHEMY_POOL_RECYCLE", raising=False)
    monkeypatch.delenv("SQLALCHEMY_POOL_PRE_PING", raising=False)

    options = _database_engine_options("postgresql+psycopg://user:pw@db:5432/moneybot")

    assert options == {
        "pool_size": 10,
        "max_overflow": 20,
        "pool_timeout": 10,
        "pool_recycle": 300,
        "pool_pre_ping": True,
    }


def test_database_engine_options_are_env_overridable_and_skip_sqlite(monkeypatch):
    monkeypatch.setenv("SQLALCHEMY_POOL_SIZE", "4")
    monkeypatch.setenv("SQLALCHEMY_MAX_OVERFLOW", "8")
    monkeypatch.setenv("SQLALCHEMY_POOL_TIMEOUT", "12")
    monkeypatch.setenv("SQLALCHEMY_POOL_RECYCLE", "600")
    monkeypatch.setenv("SQLALCHEMY_POOL_PRE_PING", "false")

    assert _database_engine_options("sqlite:///:memory:") == {}
    assert _database_engine_options("postgresql://user:pw@db:5432/moneybot") == {
        "pool_size": 4,
        "max_overflow": 8,
        "pool_timeout": 12,
        "pool_recycle": 600,
        "pool_pre_ping": False,
    }


def test_create_app_reads_api_rate_limit_settings(monkeypatch):
    monkeypatch.setenv("MONEYBOT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("API_RATE_LIMIT_WINDOW_SECONDS", "30")
    monkeypatch.setenv("API_RATE_LIMIT_MAX_REQUESTS", "500")

    app = create_app()

    assert app.config["API_RATE_LIMIT_WINDOW_SECONDS"] == 30
    assert app.config["API_RATE_LIMIT_MAX_REQUESTS"] == 500


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
    monkeypatch.setenv("DETERMINISTIC_PORTFOLIO_ROLLOUT_PERCENTAGE", "20")
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
    assert app.config["DETERMINISTIC_PORTFOLIO_ROLLOUT_PERCENTAGE"] == 20.0
    assert app.config["DETERMINISTIC_ROLLOUT_SEED"] == "day12"
    assert app.config["DETERMINISTIC_ROLLOUT_ALLOWLIST"] == {"AAPL", "MSFT"}
    assert app.config["DETERMINISTIC_ROLLOUT_BLOCKLIST"] == {"TSLA"}
    assert app.config["DETERMINISTIC_ROLLOUT_DRY_RUN"] is True
    assert app.config["DETERMINISTIC_CALIBRATION_REPORT_PATH"] == "data/custom_calibration_report.json"
    assert app.config["DETERMINISTIC_CALIBRATION_REPORT_MAX_AGE_SECONDS"] == 1234
    assert svc.calibration_enabled is True
    assert svc.rollout_percentage == 35.0
    assert svc.portfolio_rollout_percentage == 20.0
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


def test_create_app_parses_calibration_report_age_when_assignment_string_is_pasted(monkeypatch):
    monkeypatch.setenv("MONEYBOT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv(
        "DETERMINISTIC_CALIBRATION_REPORT_MAX_AGE_SECONDS",
        "DETERMINISTIC_CALIBRATION_REPORT_MAX_AGE_SECONDS=43200",
    )

    app = create_app()

    assert app.config["DETERMINISTIC_CALIBRATION_REPORT_MAX_AGE_SECONDS"] == 43200


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
    assert "/notifications" in html


def test_decision_outcomes_snapshot_default_ttl_matches_daily_ops(monkeypatch):
    monkeypatch.setenv("MONEYBOT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.delenv("DECISION_OUTCOMES_SNAPSHOT_MAX_AGE_SECONDS", raising=False)

    app = create_app()

    assert app.config["DECISION_OUTCOMES_SNAPSHOT_MAX_AGE_SECONDS"] == 129600


def test_performance_page_uses_empty_outcomes_fallback(monkeypatch):
    monkeypatch.setenv("MONEYBOT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")

    app = create_app()
    client = app.test_client()

    res = client.get("/performance")

    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "Live decision outcomes unavailable" in html
    assert "summary_1d:{accuracy:null,evaluated_rows:0}" in html
    assert "rows:[{symbol:'AAPL'" not in html
    assert "evaluated_rows:13" not in html


def test_notifications_page_renders_push_toggle(monkeypatch):
    monkeypatch.setenv("MONEYBOT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")

    app = create_app()
    client = app.test_client()

    res = client.get("/notifications")

    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "Enable push notifications" in html
    assert "pushEnabledToggle" in html
    assert "Alert Triggers" in html
    assert "triggerPortfolioSell" in html
    assert "triggerPortfolioBuy" in html
    assert "triggerMomentum8" in html
    assert "triggerWhaleAdded" in html
    assert "/static/js/notifications.js" in html

    js_res = client.get("/static/js/home.js")
    assert js_res.status_code == 200
    js = js_res.get_data(as_text=True)
    assert "/api/decision-log-summary?limit=50" in js
    assert "/api/decision-outcomes?limit=20" in js


def test_run_notification_triggers_alias_redirects_to_api(monkeypatch):
    monkeypatch.setenv("MONEYBOT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")

    app = create_app()
    client = app.test_client()

    res = client.post("/run-notification-triggers")

    assert res.status_code == 307
    assert res.headers["Location"].endswith("/api/run-notification-triggers")


def test_create_app_auto_applies_recalibration_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("MONEYBOT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MONEYBOT_PERSISTENT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DETERMINISTIC_CALIBRATION_ENABLED", raising=False)
    plan_path = tmp_path / "day13_recalibration_plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "apply_change": True,
                "effective_brier_score": 0.245,
                "next": {"slope": 0.646989, "intercept": 0.666503},
            }
        ),
        encoding="utf-8",
    )

    app = create_app()

    advisor = app.extensions["deterministic_quick_advisor"]
    assert app.config["DETERMINISTIC_CALIBRATION_ENABLED"] is True
    assert advisor.calibration_enabled is True
    assert advisor.calibration_slope == 0.646989
    assert advisor.calibration_intercept == 0.666503


def test_portfolio_page_uses_base_items_when_enrichment_is_empty(monkeypatch):
    monkeypatch.setenv("MONEYBOT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")

    app = create_app()
    client = app.test_client()

    res = client.get("/portfolio")

    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "function selectPortfolioRows(data)" in body
    assert "return enriched.length ? enriched : base;" in body
    assert "Portfolio data did not load completely. Please refresh in a moment." in body


def test_create_app_reads_personalization_rollout_settings(monkeypatch):
    monkeypatch.setenv("MONEYBOT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("INVESTOR_PROFILE_ENABLED", "true")
    monkeypatch.setenv("SUITABILITY_POLICY_ENABLED", "true")
    monkeypatch.setenv("SUITABILITY_POLICY_MODE", "shadow")
    monkeypatch.setenv("SUITABILITY_ROLLOUT_PERCENTAGE", "25")
    monkeypatch.setenv("SUITABILITY_ROLLOUT_ALLOWLIST", "3,8")

    app = create_app()
    runtime = app.extensions["personalization_runtime"]

    assert app.config["INVESTOR_PROFILE_ENABLED"] is True
    assert app.config["SUITABILITY_POLICY_MODE"] == "shadow"
    assert runtime.mode == "shadow"
    assert runtime.rollout_percentage == 25
    assert runtime.allowlist == {3, 8}


def test_create_app_reads_market_stream_shadow_configuration(monkeypatch):
    monkeypatch.setenv("MONEYBOT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MASSIVE_STREAM_ENABLED", "true")
    monkeypatch.setenv("MASSIVE_STREAM_SHADOW_MODE", "true")
    monkeypatch.setenv("MASSIVE_STREAM_SYMBOL_CAP", "125")
    monkeypatch.setenv("MASSIVE_STREAM_QUOTE_CAP", "40")
    monkeypatch.setenv("MASSIVE_STREAM_TRADE_CAP", "10")
    monkeypatch.setenv("MASSIVE_STREAM_SERVER_SYMBOLS", "SPY,QQQ,IWM,*")
    monkeypatch.delenv("REDIS_URL", raising=False)

    app = create_app()
    config = app.config["MASSIVE_STREAM_CONFIG"]

    assert config.enabled is True
    assert config.shadow_mode is True
    assert config.symbol_cap == 125
    assert config.quote_cap == 40
    assert config.trade_cap == 10
    assert config.server_symbols == ("SPY", "QQQ", "IWM")
    assert app.extensions["market_stream_state"].get_health() == {}
