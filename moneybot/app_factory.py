from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from datetime import timedelta
from pathlib import Path

from flask import Flask, abort, redirect, render_template, render_template_string, request, send_from_directory, url_for
from flask_cors import CORS
from .api import api_bp
from .extensions import db, migrate
from .services.ai_advisor import AIAdvisorService
from .services.decision_log import DecisionLogger
from .services.deterministic_advisor import DeterministicQuickAdvisor
from .services.market_data import MarketDataService
from .services.suitability_policy import PersonalizationRuntime
from .models import WaitlistSignup
from .services.runtime_paths import (
    day1_baseline_model_path,
    day13_calibration_report_path,
    day13_recalibration_plan_path,
    decision_events_log_path,
    decision_outcomes_snapshot_path,
    is_durable_runtime_configured,
    resolve_runtime_dir,
)


def _slug_username(raw: str) -> str:
    value = re.sub(r"[^a-z0-9_]+", "_", (raw or "").strip().lower())
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "user"


def _ensure_user_profile_schema() -> None:
    inspector = db.inspect(db.engine)
    if "users" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("users")}
    if "name" not in columns:
        db.session.execute(db.text("ALTER TABLE users ADD COLUMN name VARCHAR(255)"))
    if "username" not in columns:
        db.session.execute(db.text("ALTER TABLE users ADD COLUMN username VARCHAR(80)"))
    if "profile_image_url" not in columns:
        db.session.execute(db.text("ALTER TABLE users ADD COLUMN profile_image_url TEXT"))
    if "updated_at" not in columns:
        db.session.execute(db.text("ALTER TABLE users ADD COLUMN updated_at TIMESTAMP"))
    db.session.commit()

    rows = db.session.execute(
        db.text("SELECT id, email, name, username, created_at, updated_at FROM users ORDER BY id ASC"),
    ).mappings().all()
    assigned_usernames: set[str] = set()
    for row in rows:
        existing_username = _slug_username(str(row["username"] or ""))
        if existing_username:
            assigned_usernames.add(existing_username)

    for row in rows:
        email = str(row["email"] or "")
        local_part = email.split("@")[0] if "@" in email else email
        resolved_name = str(row["name"] or "").strip() or local_part.replace(".", " ").replace("_", " ").title() or "User"

        base_username = _slug_username(str(row["username"] or "") or local_part)
        candidate = base_username
        suffix = 1
        while candidate in assigned_usernames and candidate != str(row["username"] or ""):
            suffix += 1
            candidate = f"{base_username}{suffix}"
        assigned_usernames.add(candidate)
        db.session.execute(
            db.text(
                "UPDATE users SET name=:name, username=:username, updated_at=COALESCE(updated_at, created_at, CURRENT_TIMESTAMP) WHERE id=:user_id",
            ),
            {"name": resolved_name, "username": candidate, "user_id": row["id"]},
        )
    db.session.commit()
    db.session.execute(
        db.text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users (username)"),
    )
    db.session.commit()


def _ensure_notification_trigger_schema() -> None:
    inspector = db.inspect(db.engine)
    if "notification_trigger_preferences" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("notification_trigger_preferences")}
    if "portfolio_sell_advice_change" not in columns:
        db.session.execute(
            db.text(
                "ALTER TABLE notification_trigger_preferences ADD COLUMN portfolio_sell_advice_change BOOLEAN DEFAULT TRUE",
            ),
        )
    if "portfolio_buy_advice_change" not in columns:
        db.session.execute(
            db.text(
                "ALTER TABLE notification_trigger_preferences ADD COLUMN portfolio_buy_advice_change BOOLEAN DEFAULT TRUE",
            ),
        )
    if "hot_momentum_score_crosses_8" not in columns:
        db.session.execute(
            db.text(
                "ALTER TABLE notification_trigger_preferences ADD COLUMN hot_momentum_score_crosses_8 BOOLEAN DEFAULT TRUE",
            ),
        )
    if "whale_top_investor_added" not in columns:
        db.session.execute(
            db.text(
                "ALTER TABLE notification_trigger_preferences ADD COLUMN whale_top_investor_added BOOLEAN DEFAULT TRUE",
            ),
        )
    if "whales_top_stock_list_changes" not in columns:
        db.session.execute(
            db.text(
                "ALTER TABLE notification_trigger_preferences ADD COLUMN whales_top_stock_list_changes BOOLEAN DEFAULT TRUE",
            ),
        )
    if "push_notifications_enabled" not in columns:
        db.session.execute(
            db.text(
                "ALTER TABLE notification_trigger_preferences ADD COLUMN push_notifications_enabled BOOLEAN DEFAULT FALSE",
            ),
        )
    if "clearview_symbols_csv" not in columns:
        db.session.execute(
            db.text(
                "ALTER TABLE notification_trigger_preferences ADD COLUMN clearview_symbols_csv TEXT DEFAULT ''",
            ),
        )
    db.session.commit()

    db.session.execute(
        db.text(
            "UPDATE notification_trigger_preferences SET "
            "portfolio_sell_advice_change=COALESCE(portfolio_sell_advice_change, TRUE), "
            "portfolio_buy_advice_change=COALESCE(portfolio_buy_advice_change, TRUE), "
            "hot_momentum_score_crosses_8=COALESCE(hot_momentum_score_crosses_8, TRUE), "
            "whale_top_investor_added=COALESCE(whale_top_investor_added, TRUE), "
            "whales_top_stock_list_changes=COALESCE(whales_top_stock_list_changes, TRUE), "
            "push_notifications_enabled=COALESCE(push_notifications_enabled, FALSE), "
            "clearview_symbols_csv=COALESCE(clearview_symbols_csv, '')",
        ),
    )
    db.session.commit()


def _parse_symbol_set(raw: str | None) -> set[str]:
    return {token.strip().upper() for token in str(raw or "").split(",") if token.strip()}


def _parse_int_env(name: str, default: int) -> int:
    raw = str(os.environ.get(name, default)).strip()
    normalized = raw.replace(",", "").replace("_", "").strip()
    try:
        return int(normalized)
    except ValueError:
        if "=" in raw:
            maybe_value = raw.rsplit("=", 1)[-1].strip().replace(",", "").replace("_", "")
            if maybe_value:
                try:
                    return int(maybe_value)
                except ValueError:
                    pass
        raise RuntimeError(f"{name} must be an integer value, got: {raw!r}")




def _load_recalibration_plan(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _num_or_none(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _runtime_data_path(filename: str) -> str:
    base_dir = os.getenv("MONEYBOT_PERSISTENT_DATA_DIR", "data")
    os.makedirs(base_dir, exist_ok=True)
    return os.path.join(base_dir, filename)


def _resolve_database_url() -> str:
    # Prefer explicit DATABASE_URL, but support common provider aliases used on hosted platforms.
    raw_database_url = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("POSTGRES_INTERNAL_URL")
        or os.environ.get("POSTGRES_URL")
        or os.environ.get("POSTGRESQL_URL")
    )
    database_url = (raw_database_url or "").strip() or "sqlite:///moneybot.db"

    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    # Fail fast on hosted deployments so we do not silently deploy with non-persistent auth/portfolio storage.
    is_hosted = os.environ.get("RENDER") == "true" or os.environ.get("FLASK_ENV") == "production"

    # Pick an installed PostgreSQL DBAPI when the URL does not pin one.
    if database_url.startswith("postgresql://") and "+" not in database_url.split("://", 1)[0]:
        has_psycopg = importlib.util.find_spec("psycopg") is not None
        has_psycopg2 = importlib.util.find_spec("psycopg2") is not None
        if has_psycopg:
            database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        elif not has_psycopg2:
            msg = (
                "DATABASE_URL points to PostgreSQL but no PostgreSQL driver is installed. "
                "Install psycopg[binary] or psycopg2-binary in the build command."
            )
            if is_hosted:
                raise RuntimeError(msg)
            logging.error(
                "%s Falling back to local SQLite for local/dev only; data will not persist.",
                msg,
            )
            database_url = "sqlite:///moneybot.db"

    if database_url.startswith("sqlite") and is_hosted:
        raise RuntimeError(
            "No persistent PostgreSQL database is configured for production. "
            "Set DATABASE_URL (or POSTGRES_INTERNAL_URL/POSTGRES_URL) and ensure a PostgreSQL driver is installed."
        )

    if " " in database_url or "://" not in database_url:
        raise RuntimeError(
            "DATABASE_URL is not a valid database URL. "
            "Set DATABASE_URL to a valid value such as "
            "postgresql://user:password@host:5432/dbname."
        )

    return database_url


def _waitlist_email_configured(app: Flask) -> bool:
    smtp_host = (app.config.get("SMTP_HOST") or "").strip()
    from_email = (app.config.get("PASSWORD_RESET_FROM_EMAIL") or app.config.get("SMTP_USER") or "").strip()
    return bool(smtp_host and from_email)


def _send_waitlist_welcome_email(app: Flask, email: str) -> bool:
    smtp_host = (app.config.get("SMTP_HOST") or "").strip()
    smtp_port = int(app.config.get("SMTP_PORT") or 587)
    smtp_user = (app.config.get("SMTP_USER") or "").strip()
    smtp_password = app.config.get("SMTP_PASSWORD") or ""
    smtp_use_tls = bool(app.config.get("SMTP_USE_TLS", True))
    smtp_use_ssl = bool(app.config.get("SMTP_USE_SSL", False))
    from_email = (app.config.get("PASSWORD_RESET_FROM_EMAIL") or smtp_user or "").strip()
    from_name = (app.config.get("PASSWORD_RESET_FROM_NAME") or "Moneybot Labs").strip()

    if not _waitlist_email_configured(app):
        return False

    msg = EmailMessage()
    msg["Subject"] = "Welcome to the Moneybot waitlist"
    msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
    msg["Reply-To"] = from_email
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=(from_email.split("@", 1)[1] if "@" in from_email else None))
    msg["To"] = email
    msg["List-Unsubscribe"] = f"<mailto:{from_email}?subject=Unsubscribe>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(
        "\n".join(
            [
                "Thanks for joining the Moneybot waitlist.",
                "",
                "You signed up on moneybotlabs.us to receive early-access launch updates.",
                "",
                "If you would like to stop these updates, reply with 'unsubscribe' and we'll remove you.",
                "",
                "— Moneybot Labs",
                f"Contact: {from_email}",
            ]
        )
    )

    try:
        if smtp_use_ssl:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20) as smtp:
                if smtp_user and smtp_password:
                    smtp.login(smtp_user, smtp_password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as smtp:
                if smtp_use_tls:
                    smtp.starttls()
                if smtp_user and smtp_password:
                    smtp.login(smtp_user, smtp_password)
                smtp.send_message(msg)
        return True
    except Exception:
        logging.exception("Failed to send waitlist welcome email.")
        return False


def _resolve_runtime_file_path(runtime_dir, env_name: str, default_filename: str) -> str:
    raw = os.environ.get(env_name)
    if not raw:
        return str(runtime_dir / default_filename)

    candidate = raw.strip()
    if not candidate:
        return str(runtime_dir / default_filename)

    candidate_path = Path(candidate).expanduser()
    if candidate_path.is_absolute():
        return str(candidate_path)

    parts = list(candidate_path.parts)
    if parts and parts[0] == "data":
        parts = parts[1:]
    relative = Path(*parts) if parts else Path(default_filename)
    return str(runtime_dir / relative)


def create_app() -> Flask:
    secret = os.environ.get("MONEYBOT_SECRET_KEY")
    if not secret:
        logging.warning(
            "MONEYBOT_SECRET_KEY is not set. Using an insecure fallback key; set MONEYBOT_SECRET_KEY in production."
        )
        secret = "moneybot-insecure-fallback-key"

    database_url = _resolve_database_url()

    runtime_dir = resolve_runtime_dir()
    configured_model_path = str(os.environ.get("DETERMINISTIC_MODEL_PATH", "") or "").strip()
    runtime_model_path = str(day1_baseline_model_path())
    legacy_model_path = "data/day1_baseline_model.json"
    runtime_model_exists = Path(runtime_model_path).exists()
    legacy_model_exists = Path(legacy_model_path).exists()

    default_model_path = runtime_model_path
    if configured_model_path:
        if configured_model_path == legacy_model_path:
            # Keep legacy path if that's where the artifact currently exists; otherwise prefer runtime path.
            default_model_path = legacy_model_path if legacy_model_exists else runtime_model_path
        else:
            default_model_path = configured_model_path
    elif not runtime_model_exists and legacy_model_exists:
        # Safety fallback for existing installs that still write to legacy relative path.
        default_model_path = legacy_model_path

    app = Flask(__name__)
    app.url_map.strict_slashes = False
    app.config.update(
        SECRET_KEY=secret,
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        SESSION_REFRESH_EACH_REQUEST=True,
        SQLALCHEMY_DATABASE_URI=database_url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        DATA_PROVIDER=os.environ.get("DATA_PROVIDER", "yfinance"),
        PUBLIC_BASE_URL=os.environ.get("PUBLIC_BASE_URL", ""),
        SMTP_HOST=os.environ.get("SMTP_HOST", ""),
        SMTP_PORT=int(os.environ.get("SMTP_PORT", "587")),
        SMTP_USER=os.environ.get("SMTP_USER", ""),
        SMTP_PASSWORD=os.environ.get("SMTP_PASSWORD", ""),
        SMTP_USE_TLS=(os.environ.get("SMTP_USE_TLS", "true").lower() == "true"),
        SMTP_USE_SSL=(os.environ.get("SMTP_USE_SSL", "false").lower() == "true"),
        PASSWORD_RESET_FROM_EMAIL=os.environ.get("PASSWORD_RESET_FROM_EMAIL", os.environ.get("SMTP_USER", "")),
        PASSWORD_RESET_FROM_NAME=os.environ.get("PASSWORD_RESET_FROM_NAME", "Moneybot Labs"),
        PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS=int(os.environ.get("PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS", "3600")),
        DAILY_OPS_TOKEN=os.environ.get("DAILY_OPS_TOKEN", ""),
        AI_ENABLED=(os.environ.get("AI_ENABLED", "false").lower() == "true"),
        AI_PROVIDER=os.environ.get("AI_PROVIDER", "openai"),
        AI_MODEL=os.environ.get("AI_MODEL", "gpt-5-mini"),
        AI_API_KEY=os.environ.get("AI_API_KEY", ""),
        AI_TIMEOUT_SECONDS=float(os.environ.get("AI_TIMEOUT_SECONDS", "6.0")),
        AI_FAILURE_COOLDOWN_SECONDS=int(os.environ.get("AI_FAILURE_COOLDOWN_SECONDS", "120")),
        AI_RESPONSE_CACHE_TTL_SECONDS=int(os.environ.get("AI_RESPONSE_CACHE_TTL_SECONDS", "300")),
        EXPERIMENT_ID=os.environ.get("EXPERIMENT_ID", "default"),
        EXPERIMENT_COHORT_DEFAULT=os.environ.get("EXPERIMENT_COHORT_DEFAULT", "control"),
        INVESTOR_PROFILE_ENABLED=(os.environ.get("INVESTOR_PROFILE_ENABLED", "true").lower() == "true"),
        SUITABILITY_POLICY_ENABLED=(os.environ.get("SUITABILITY_POLICY_ENABLED", "true").lower() == "true"),
        SUITABILITY_POLICY_MODE=os.environ.get("SUITABILITY_POLICY_MODE", "enforce").lower(),
        SUITABILITY_ROLLOUT_PERCENTAGE=float(os.environ.get("SUITABILITY_ROLLOUT_PERCENTAGE", "100.0")),
        SUITABILITY_ROLLOUT_SEED=os.environ.get("SUITABILITY_ROLLOUT_SEED", "moneybot-profile"),
        SUITABILITY_ROLLOUT_ALLOWLIST={int(value) for value in os.environ.get("SUITABILITY_ROLLOUT_ALLOWLIST", "").split(",") if value.strip().isdigit()},
        INVESTOR_PROFILE_REVISION_RETENTION_DAYS=int(os.environ.get("INVESTOR_PROFILE_REVISION_RETENTION_DAYS", "2555")),
        DETERMINISTIC_QUICK_ENABLED=(os.environ.get("DETERMINISTIC_QUICK_ENABLED", "true").lower() == "true"),
        DETERMINISTIC_MODEL_PATH=default_model_path,
        DETERMINISTIC_MOMENTUM_ENABLED=(os.environ.get("DETERMINISTIC_MOMENTUM_ENABLED", "true").lower() == "true"),
        DETERMINISTIC_QUICK_BUY_THRESHOLD=(float(os.environ.get("DETERMINISTIC_QUICK_BUY_THRESHOLD", "0.0")) or None),
        DETERMINISTIC_QUICK_STRONG_BUY_THRESHOLD=float(os.environ.get("DETERMINISTIC_QUICK_STRONG_BUY_THRESHOLD", "0.70")),
        DETERMINISTIC_PORTFOLIO_BUY_PROB_THRESHOLD=float(os.environ.get("DETERMINISTIC_PORTFOLIO_BUY_PROB_THRESHOLD", "0.62")),
        DETERMINISTIC_PORTFOLIO_SELL_PROB_THRESHOLD=float(os.environ.get("DETERMINISTIC_PORTFOLIO_SELL_PROB_THRESHOLD", "0.45")),
        DETERMINISTIC_PORTFOLIO_BUY_DIP_THRESHOLD_PCT=float(os.environ.get("DETERMINISTIC_PORTFOLIO_BUY_DIP_THRESHOLD_PCT", "-4.0")),
        DETERMINISTIC_PORTFOLIO_SELL_PROFIT_THRESHOLD_PCT=float(os.environ.get("DETERMINISTIC_PORTFOLIO_SELL_PROFIT_THRESHOLD_PCT", "6.0")),
        DETERMINISTIC_CALIBRATION_ENABLED=(os.environ.get("DETERMINISTIC_CALIBRATION_ENABLED", "false").lower() == "true"),
        DETERMINISTIC_CALIBRATION_SLOPE=float(os.environ.get("DETERMINISTIC_CALIBRATION_SLOPE", "1.0")),
        DETERMINISTIC_CALIBRATION_INTERCEPT=float(os.environ.get("DETERMINISTIC_CALIBRATION_INTERCEPT", "0.0")),
        DETERMINISTIC_CALIBRATION_AUTO_APPLY_PLAN=(os.environ.get("DETERMINISTIC_CALIBRATION_AUTO_APPLY_PLAN", "true").lower() == "true"),
        DETERMINISTIC_ROLLOUT_PERCENTAGE=float(os.environ.get("DETERMINISTIC_ROLLOUT_PERCENTAGE", "100.0")),
        DETERMINISTIC_PORTFOLIO_ROLLOUT_PERCENTAGE=float(
            os.environ.get(
                "DETERMINISTIC_PORTFOLIO_ROLLOUT_PERCENTAGE",
                os.environ.get("DETERMINISTIC_ROLLOUT_PERCENTAGE", "100.0"),
            ),
        ),
        DETERMINISTIC_ROLLOUT_SEED=os.environ.get("DETERMINISTIC_ROLLOUT_SEED", "moneybot"),
        DETERMINISTIC_ROLLOUT_ALLOWLIST=_parse_symbol_set(os.environ.get("DETERMINISTIC_ROLLOUT_ALLOWLIST", "")),
        DETERMINISTIC_ROLLOUT_BLOCKLIST=_parse_symbol_set(os.environ.get("DETERMINISTIC_ROLLOUT_BLOCKLIST", "")),
        DETERMINISTIC_ROLLOUT_DRY_RUN=(os.environ.get("DETERMINISTIC_ROLLOUT_DRY_RUN", "false").lower() == "true"),
        DECISION_LOGGING_ENABLED=(os.environ.get("DECISION_LOGGING_ENABLED", "true").lower() == "true"),
        DECISION_LOG_PATH=os.environ.get("DECISION_LOG_PATH", str(decision_events_log_path())),
        DECISION_OUTCOMES_SNAPSHOT_PATH=os.environ.get(
            "DECISION_OUTCOMES_SNAPSHOT_PATH",
            str(decision_outcomes_snapshot_path()),
        ),
        DECISION_OUTCOMES_SNAPSHOT_MAX_AGE_SECONDS=int(os.environ.get("DECISION_OUTCOMES_SNAPSHOT_MAX_AGE_SECONDS", "129600")),
        DETERMINISTIC_CALIBRATION_REPORT_PATH=os.environ.get(
            "DETERMINISTIC_CALIBRATION_REPORT_PATH",
            str(day13_calibration_report_path()),
        ),
        DETERMINISTIC_CALIBRATION_REPORT_MAX_AGE_SECONDS=_parse_int_env("DETERMINISTIC_CALIBRATION_REPORT_MAX_AGE_SECONDS", 604800),
        DETERMINISTIC_TRAINING_MAX_AGE_HOURS=_parse_int_env("DETERMINISTIC_TRAINING_MAX_AGE_HOURS", 36),
        FIREBASE_API_KEY=os.environ.get("FIREBASE_API_KEY", ""),
        FIREBASE_AUTH_DOMAIN=os.environ.get("FIREBASE_AUTH_DOMAIN", ""),
        FIREBASE_PROJECT_ID=os.environ.get("FIREBASE_PROJECT_ID", ""),
        FIREBASE_STORAGE_BUCKET=os.environ.get("FIREBASE_STORAGE_BUCKET", ""),
        FIREBASE_MESSAGING_SENDER_ID=os.environ.get("FIREBASE_MESSAGING_SENDER_ID", ""),
        FIREBASE_APP_ID=os.environ.get("FIREBASE_APP_ID", ""),
        FIREBASE_MEASUREMENT_ID=os.environ.get("FIREBASE_MEASUREMENT_ID", ""),
        FIREBASE_VAPID_KEY=os.environ.get("FIREBASE_VAPID_KEY", ""),
        WAITLIST_WELCOME_EMAIL_ENABLED=(os.environ.get("WAITLIST_WELCOME_EMAIL_ENABLED", "false").lower() == "true"),
        LANDING_ONLY_HOSTS={
            host.strip().lower()
            for host in os.environ.get("LANDING_ONLY_HOSTS", "moneybotlabs.us,www.moneybotlabs.us").split(",")
            if host.strip()
        },
    )
    calibration_report = day13_calibration_report_path()
    recalibration_plan = day13_recalibration_plan_path()
    logging.info(
        "Resolved model-ops diagnostics paths calibration_report_path=%s exists=%s recalibration_plan_path=%s exists=%s",
        calibration_report,
        calibration_report.exists(),
        recalibration_plan,
        recalibration_plan.exists(),
    )

    calibration_enabled_env = os.environ.get("DETERMINISTIC_CALIBRATION_ENABLED")
    if app.config["DETERMINISTIC_CALIBRATION_AUTO_APPLY_PLAN"] and calibration_enabled_env is None:
        plan = _load_recalibration_plan(recalibration_plan)
        next_plan = plan.get("next") if isinstance(plan, dict) and isinstance(plan.get("next"), dict) else {}
        plan_slope = _num_or_none(next_plan.get("slope"))
        plan_intercept = _num_or_none(next_plan.get("intercept"))
        if plan and plan.get("apply_change") is True and plan_slope is not None and plan_intercept is not None:
            app.config["DETERMINISTIC_CALIBRATION_ENABLED"] = True
            app.config["DETERMINISTIC_CALIBRATION_SLOPE"] = plan_slope
            app.config["DETERMINISTIC_CALIBRATION_INTERCEPT"] = plan_intercept
            logging.info(
                "Applied deterministic calibration plan slope=%s intercept=%s effective_brier_score=%s",
                plan_slope,
                plan_intercept,
                plan.get("effective_brier_score"),
            )

    app.extensions["ai_advisor_service"] = AIAdvisorService(
        enabled=app.config["AI_ENABLED"],
        provider=app.config["AI_PROVIDER"],
        model=app.config["AI_MODEL"],
        api_key=app.config["AI_API_KEY"],
        timeout_s=app.config["AI_TIMEOUT_SECONDS"],
        failure_cooldown_s=app.config["AI_FAILURE_COOLDOWN_SECONDS"],
        cache_ttl_s=app.config["AI_RESPONSE_CACHE_TTL_SECONDS"],
    )
    app.extensions["deterministic_quick_advisor"] = DeterministicQuickAdvisor(
        enabled=app.config["DETERMINISTIC_QUICK_ENABLED"],
        artifact_path=app.config["DETERMINISTIC_MODEL_PATH"],
        quick_buy_threshold=app.config["DETERMINISTIC_QUICK_BUY_THRESHOLD"],
        quick_strong_buy_threshold=app.config["DETERMINISTIC_QUICK_STRONG_BUY_THRESHOLD"],
        portfolio_buy_prob_threshold=app.config["DETERMINISTIC_PORTFOLIO_BUY_PROB_THRESHOLD"],
        portfolio_sell_prob_threshold=app.config["DETERMINISTIC_PORTFOLIO_SELL_PROB_THRESHOLD"],
        portfolio_buy_dip_threshold_pct=app.config["DETERMINISTIC_PORTFOLIO_BUY_DIP_THRESHOLD_PCT"],
        portfolio_sell_profit_threshold_pct=app.config["DETERMINISTIC_PORTFOLIO_SELL_PROFIT_THRESHOLD_PCT"],
        calibration_enabled=app.config["DETERMINISTIC_CALIBRATION_ENABLED"],
        calibration_slope=app.config["DETERMINISTIC_CALIBRATION_SLOPE"],
        calibration_intercept=app.config["DETERMINISTIC_CALIBRATION_INTERCEPT"],
        rollout_percentage=app.config["DETERMINISTIC_ROLLOUT_PERCENTAGE"],
        portfolio_rollout_percentage=app.config["DETERMINISTIC_PORTFOLIO_ROLLOUT_PERCENTAGE"],
        rollout_seed=app.config["DETERMINISTIC_ROLLOUT_SEED"],
        rollout_allowlist=app.config["DETERMINISTIC_ROLLOUT_ALLOWLIST"],
        rollout_blocklist=app.config["DETERMINISTIC_ROLLOUT_BLOCKLIST"],
        rollout_dry_run=app.config["DETERMINISTIC_ROLLOUT_DRY_RUN"],
    )
    app.extensions["decision_logger"] = DecisionLogger(
        enabled=app.config["DECISION_LOGGING_ENABLED"],
        output_path=app.config["DECISION_LOG_PATH"],
    )
    app.extensions["personalization_runtime"] = PersonalizationRuntime(
        profile_enabled=app.config["INVESTOR_PROFILE_ENABLED"],
        policy_enabled=app.config["SUITABILITY_POLICY_ENABLED"],
        mode=app.config["SUITABILITY_POLICY_MODE"],
        rollout_percentage=app.config["SUITABILITY_ROLLOUT_PERCENTAGE"],
        rollout_seed=app.config["SUITABILITY_ROLLOUT_SEED"],
        allowlist=app.config["SUITABILITY_ROLLOUT_ALLOWLIST"],
    )

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    CORS(app)
    db.init_app(app)
    migrate.init_app(app, db)

    from . import models  # noqa: F401

    app.register_blueprint(api_bp)
    app.extensions["market_data_service"] = MarketDataService(
        deterministic_quick_advisor=app.extensions["deterministic_quick_advisor"],
        deterministic_momentum_enabled=app.config["DETERMINISTIC_MOMENTUM_ENABLED"],
    )

    with app.app_context():
        db.create_all()
        _ensure_user_profile_schema()
        _ensure_notification_trigger_schema()

    @app.get("/")
    @app.get("/index.html")
    @app.get("/home")
    def home():
        def _firebase_template_context():
            firebase_config = {
                "apiKey": app.config["FIREBASE_API_KEY"],
                "authDomain": app.config["FIREBASE_AUTH_DOMAIN"],
                "projectId": app.config["FIREBASE_PROJECT_ID"],
                "storageBucket": app.config["FIREBASE_STORAGE_BUCKET"],
                "messagingSenderId": app.config["FIREBASE_MESSAGING_SENDER_ID"],
                "appId": app.config["FIREBASE_APP_ID"],
                "measurementId": app.config["FIREBASE_MEASUREMENT_ID"],
            }
            enabled = all(
                firebase_config[key]
                for key in ("apiKey", "authDomain", "projectId", "messagingSenderId", "appId")
            ) and bool(app.config["FIREBASE_VAPID_KEY"])
            return {
                "firebase_enabled": enabled,
                "firebase_config_json": json.dumps(firebase_config),
                "firebase_vapid_key": app.config["FIREBASE_VAPID_KEY"],
            }

        return render_template("home.html", **_firebase_template_context())

    @app.before_request
    def enforce_landing_only_hosts():
        host = str(request.host or "").split(":", 1)[0].strip().lower()
        landing_only_hosts = app.config.get("LANDING_ONLY_HOSTS") or set()
        if host not in landing_only_hosts:
            return None
        if request.path in {"/landing", "/landing/"}:
            return None
        if request.path == "/":
            return redirect(url_for("landing_page"), code=302)
        abort(404)

    @app.get("/landing")
    @app.get("/landing/")
    def landing_page():
        success = request.args.get("success") == "1"
        submitted_email = str(request.args.get("email", "") or "").strip()
        return render_template("landing.html", signup_success=success, submitted_email=submitted_email)

    @app.post("/landing")
    @app.post("/landing/")
    def landing_signup():
        email = str(request.form.get("email", "") or "").strip().lower()
        if not email or "@" not in email or "." not in email.split("@")[-1]:
            return render_template(
                "landing.html",
                signup_success=False,
                signup_error="Please enter a valid email address.",
                submitted_email=email,
            ), 400

        existing = WaitlistSignup.query.filter_by(email=email).first()
        if existing is None:
            signup = WaitlistSignup(email=email, source="landing")
            db.session.add(signup)
            db.session.commit()
            if app.config.get("WAITLIST_WELCOME_EMAIL_ENABLED", False):
                sent = _send_waitlist_welcome_email(app, email)
                signup.welcome_email_sent = bool(sent)
                db.session.commit()

        return redirect(url_for("landing_page", success="1", email=email), code=303)

    @app.get("/notifications")
    def notifications_page():
        firebase_config = {
            "apiKey": app.config["FIREBASE_API_KEY"],
            "authDomain": app.config["FIREBASE_AUTH_DOMAIN"],
            "projectId": app.config["FIREBASE_PROJECT_ID"],
            "storageBucket": app.config["FIREBASE_STORAGE_BUCKET"],
            "messagingSenderId": app.config["FIREBASE_MESSAGING_SENDER_ID"],
            "appId": app.config["FIREBASE_APP_ID"],
            "measurementId": app.config["FIREBASE_MEASUREMENT_ID"],
        }
        enabled = all(
            firebase_config[key]
            for key in ("apiKey", "authDomain", "projectId", "messagingSenderId", "appId")
        ) and bool(app.config["FIREBASE_VAPID_KEY"])
        return render_template(
            "notifications.html",
            firebase_enabled=enabled,
            firebase_config_json=json.dumps(firebase_config),
            firebase_vapid_key=app.config["FIREBASE_VAPID_KEY"],
        )

    @app.get("/performance")
    @app.get("/performance/")
    def performance_page():
        return render_template("performance.html")

    @app.get("/firebase-messaging-sw.js")
    def firebase_messaging_service_worker():
        return send_from_directory(app.static_folder, "firebase-messaging-sw.js")

    @app.post("/run-notification-triggers")
    @app.post("/run-notification-triggers/")
    def run_notification_triggers_alias():
        return redirect("/api/run-notification-triggers", code=307)

    @app.get("/user-profile")
    def user_profile_page():
        return render_template_string(
            """
            <html><body style="font-family:Inter,sans-serif;padding:24px;background:#f7fee7;max-width:760px;margin:0 auto">
              <h2>User Profile</h2>
              <p><a href="/" style="color:#166534;font-weight:700">← Back Home</a></p>
              <p>This page will be expanded soon.</p>
            </body></html>
            """
        )

    def _simple_page(title: str):
        return render_template_string(
            f"""
            <html><body style=\"font-family:Inter,sans-serif;padding:24px;background:#f7fee7;max-width:760px;margin:0 auto\">
              <h2>{title}</h2>
              <p><a href=\"/\" style=\"color:#166534;font-weight:700\">← Back Home</a></p>
              <p>Content coming soon.</p>
            </body></html>
            """
        )

    @app.get("/security")
    def security_page():
        return render_template_string(
            """
            <html><body style="font-family:Inter,sans-serif;padding:24px;background:#f7fee7;max-width:760px;margin:0 auto">
              <h2 style="margin:0 0 12px">Security</h2>
              <p style="margin:0 0 14px"><a href="/" style="color:#166534;font-weight:700">← Back Home</a></p>
              <section style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:12px;padding:14px">
                <h3 style="margin:0 0 8px;color:#14532d">Update email and password</h3>
                <p style="margin:0 0 12px;color:#166534">For security, enter your current password before any change.</p>
                <form id="securityForm" style="display:grid;gap:10px">
                  <input id="email" type="email" placeholder="New email (optional)" style="font-size:1.02rem;padding:10px;border:1px solid #bbf7d0;border-radius:10px" />
                  <input id="currentPassword" type="password" placeholder="Current password (required for changes)" style="font-size:1.02rem;padding:10px;border:1px solid #bbf7d0;border-radius:10px" />
                  <input id="newPassword" type="password" placeholder="New password" style="font-size:1.02rem;padding:10px;border:1px solid #bbf7d0;border-radius:10px" />
                  <input id="confirmNewPassword" type="password" placeholder="Confirm new password" style="font-size:1.02rem;padding:10px;border:1px solid #bbf7d0;border-radius:10px" />
                  <button type="submit" style="justify-self:start;border:none;background:#14532d;color:#f0fdf4;padding:10px 14px;border-radius:999px;font-weight:700;cursor:pointer">Save security changes</button>
                </form>
                <div id="out" style="margin-top:10px;color:#166534"></div>
              </section>
              <script>
                const TAB_SESSION_KEY = 'moneybot_tab_session_id';
                function getTabSessionId(){ return sessionStorage.getItem(TAB_SESSION_KEY) || localStorage.getItem(TAB_SESSION_KEY) || ''; }
                async function apiFetch(url, options = {}){
                  const headers = Object.assign({'Content-Type':'application/json', 'X-Tab-Session-Id': getTabSessionId()}, options.headers || {});
                  const res = await fetch(url, Object.assign({}, options, { headers }));
                  if(res.status === 401){ location.href = '/login'; throw new Error('authentication required'); }
                  return res;
                }
                async function loadSecurityDefaults(){
                  const res = await apiFetch('/api/me');
                  const payload = await res.json();
                  const user = payload.user || {};
                  document.getElementById('email').value = user.email || '';
                }
                document.getElementById('securityForm').addEventListener('submit', async (event) => {
                  event.preventDefault();
                  const outEl = document.getElementById('out');
              const trustedDeviceEl = document.getElementById('trustedDevice');
                  outEl.textContent = 'Saving...';
                  const body = {
                    email: document.getElementById('email').value,
                    current_password: document.getElementById('currentPassword').value,
                    new_password: document.getElementById('newPassword').value,
                    confirm_new_password: document.getElementById('confirmNewPassword').value,
                  };
                  const res = await apiFetch('/api/me/security', { method: 'PUT', body: JSON.stringify(body) });
                  const payload = await res.json();
                  if(!res.ok){ outEl.textContent = payload.error || 'Unable to save changes.'; return; }
                  document.getElementById('currentPassword').value = '';
                  document.getElementById('newPassword').value = '';
                  document.getElementById('confirmNewPassword').value = '';
                  outEl.textContent = 'Security settings updated.';
                });
                loadSecurityDefaults();
              </script>
            </body></html>
            """
        )

    @app.get("/account")
    def account_page():
        return _simple_page("Account")

    @app.get("/privacy")
    def privacy_page():
        return render_template_string(
            """
            <html><body style="font-family:Inter,sans-serif;min-height:100vh;margin:0;background:#f7fee7;padding:24px;box-sizing:border-box;color:#14532d">
              <main style="max-width:900px;margin:0 auto;background:#f0fdf4;padding:28px;border-radius:14px;box-shadow:0 10px 28px rgba(15,23,42,.08);line-height:1.6">
                <p style="margin:0 0 12px"><a href="/" style="display:inline-block;text-decoration:none;background:#dcfce7;color:#14532d;padding:8px 12px;border-radius:999px;font-weight:700">← Back</a></p>
                <h1 style="margin-top:0">Privacy Policy</h1>
                <p><strong>Effective Date: April 30, 2026</strong></p>
                <p>MoneyBot Labs (“MoneyBot Labs,” “we,” “our,” or “us”) respects your privacy. This Privacy Policy explains how we collect, use, disclose, and protect your information when you use our website, platform, and related services.</p>
                <p>By using MoneyBot Labs, you agree to this Privacy Policy.</p>

                <h2>1. Information We Collect</h2>
                <p>We may collect the following types of information:</p>
                <h3>Personal Information</h3>
                <p>When you create an account or contact us, we may collect your name, username, email address, password, profile image, and other information you choose to provide.</p>
                <h3>Portfolio and Investment Information</h3>
                <p>If you use portfolio or stock-related features, we may collect information you enter, such as stock tickers, watchlists, share quantities, purchase prices, and investment preferences.</p>
                <h3>Usage Information</h3>
                <p>We may automatically collect information about your use of the Services, including IP address, browser type, device information, operating system, referral pages, pages visited, session activity, clicks, and feature usage.</p>
                <h3>Cookies and Similar Technologies</h3>
                <p>We may use cookies, local storage, pixels, and similar technologies to remember preferences, maintain sessions, analyze usage, improve performance, and support security.</p>
                <h3>Communications</h3>
                <p>If you contact us, we may collect and retain your messages, support requests, feedback, and related contact details.</p>

                <h2>2. How We Use Information</h2>
                <p>We may use your information to:</p>
                <ul>
                  <li>provide, maintain, and improve the Services</li>
                  <li>create and manage accounts</li>
                  <li>deliver stock analysis, AI insights, watchlists, alerts, and portfolio-related features</li>
                  <li>personalize user experience</li>
                  <li>monitor performance, reliability, and security</li>
                  <li>respond to support requests and communications</li>
                  <li>send service-related messages such as login alerts, password resets, and account notices</li>
                  <li>develop new features and analyze platform usage</li>
                  <li>detect fraud, abuse, misuse, and unauthorized access</li>
                  <li>comply with legal obligations and enforce our policies</li>
                </ul>

                <h2>3. How We Share Information</h2>
                <p>We do not sell your personal information.</p>
                <p>We may share information in the following circumstances:</p>
                <h3>Service Providers</h3>
                <p>We may share information with vendors and contractors that help us host, maintain, secure, analyze, or operate the Services.</p>
                <h3>Legal Requirements</h3>
                <p>We may disclose information if required by law, regulation, court order, subpoena, or governmental request, or if necessary to protect rights, safety, or property.</p>
                <h3>Business Transfers</h3>
                <p>We may share or transfer information in connection with a merger, financing, acquisition, reorganization, or sale of assets.</p>
                <h3>Platform Protection</h3>
                <p>We may share information when necessary to investigate fraud, abuse, security incidents, or violations of our Terms.</p>
                <h3>With Your Direction</h3>
                <p>We may share information when you direct us to do so or use features that inherently involve sharing.</p>

                <h2>4. Data Retention</h2>
                <p>We retain information for as long as reasonably necessary to provide the Services, comply with legal obligations, resolve disputes, enforce agreements, maintain records, and support security and operational needs.</p>

                <h2>5. Data Security</h2>
                <p>We use reasonable administrative, technical, and organizational safeguards designed to protect your information. However, no system is completely secure, and we cannot guarantee absolute security.</p>

                <h2>6. Your Choices and Rights</h2>
                <p>Depending on your location, you may have rights to access, correct, update, delete, or request a copy of certain personal information. You may also have the right to object to or restrict certain processing.</p>
                <p>You may also:</p>
                <ul>
                  <li>update certain account information through your settings, if available</li>
                  <li>disable cookies through your browser settings</li>
                  <li>unsubscribe from non-essential emails through the unsubscribe link, where applicable</li>
                </ul>
                <p>To make a privacy-related request, contact us at the email below.</p>

                <h2>7. Children’s Privacy</h2>
                <p>MoneyBot Labs is not intended for children under 13, and we do not knowingly collect personal information from children under 13. If we learn that we collected such information, we will take reasonable steps to delete it.</p>

                <h2>8. Third-Party Services</h2>
                <p>Our Services may link to or rely on third-party websites, services, APIs, payment processors, analytics tools, market data providers, or integrations. We are not responsible for the privacy practices of third parties.</p>

                <h2>9. AI and Financial Information</h2>
                <p>MoneyBot Labs may use automated systems and AI-generated outputs to provide stock analysis and related insights. These features are informational only and are not personalized financial advice. You are responsible for evaluating and using any information provided by the platform.</p>

                <h2>10. State Privacy Rights</h2>
                <p>If you live in a state with applicable privacy laws, including California, you may have additional legal rights regarding your personal information. We will honor applicable rights as required by law.</p>

                <h2>11. International Users</h2>
                <p>If you access the Services from outside the United States, you understand that your information may be processed and stored in the United States or other jurisdictions where our providers operate.</p>

                <h2>12. Changes to This Privacy Policy</h2>
                <p>We may update this Privacy Policy from time to time. When we do, we will revise the Effective Date above. Your continued use of the Services after changes become effective means you accept the updated Privacy Policy.</p>

                <h2>13. Contact</h2>
                <p>If you have questions about this Privacy Policy or our data practices, contact:</p>
                <p>MoneyBot Labs<br />Email: <a href="mailto:support@moneybotlabs.com">support@moneybotlabs.com</a></p>
              </main>
            </body></html>
            """
        )

    @app.get("/terms")
    def terms_page():
        return render_template_string(
            """
            <html><body style="font-family:Inter,sans-serif;min-height:100vh;margin:0;background:#f7fee7;padding:24px;box-sizing:border-box;color:#14532d">
              <main style="max-width:900px;margin:0 auto;background:#f0fdf4;padding:28px;border-radius:14px;box-shadow:0 10px 28px rgba(15,23,42,.08);line-height:1.6">
                <p style="margin:0 0 12px"><a href="/" style="display:inline-block;text-decoration:none;background:#dcfce7;color:#14532d;padding:8px 12px;border-radius:999px;font-weight:700">← Back</a></p>
                <h1 style="margin-top:0">Terms of Service</h1>
                <p><strong>Effective Date: April 30, 2026</strong></p>
                <p>Welcome to MoneyBot Labs. These Terms of Service (“Terms”) govern your access to and use of the MoneyBot Labs website, platform, tools, features, and services (collectively, the “Services”). By using our Services, you agree to these Terms. If you do not agree, do not use the Services.</p>

                <h2>1. Who We Are</h2>
                <p>MoneyBot Labs (“MoneyBot Labs,” “we,” “our,” or “us”) provides AI-powered stock analysis, market insights, watchlist tools, portfolio-related features, and related informational services.</p>

                <h2>2. Eligibility</h2>
                <p>You must be at least 18 years old, or the age of majority in your jurisdiction, to use our Services. By using MoneyBot Labs, you represent that you have the legal capacity to enter into these Terms.</p>

                <h2>3. Informational Use Only</h2>
                <p>MoneyBot Labs provides informational and educational content only. Our Services do not provide personalized investment advice, legal advice, tax advice, or financial planning services. Nothing on the platform should be interpreted as a recommendation to buy, sell, or hold any security for your specific situation.</p>
                <p>You remain solely responsible for your investment decisions, trades, research, and evaluation of risk.</p>

                <h2>4. No Broker, Dealer, or Investment Adviser Relationship</h2>
                <p>MoneyBot Labs is not a registered broker-dealer, investment adviser, or financial institution unless expressly stated otherwise. Use of the Services does not create any fiduciary, advisory, or client relationship between you and MoneyBot Labs.</p>

                <h2>5. User Accounts</h2>
                <p>To access some features, you may need to create an account. You agree to provide accurate information and keep it updated. You are responsible for maintaining the confidentiality of your login credentials and for all activity under your account.</p>
                <p>You agree to notify us promptly of any unauthorized use of your account.</p>

                <h2>6. User Content</h2>
                <p>You may submit information such as portfolio holdings, watchlists, stock symbols, purchase prices, profile information, and other content (“User Content”). You retain ownership of your User Content, but you grant MoneyBot Labs a non-exclusive, worldwide, royalty-free license to use, host, store, process, and display that content solely to operate, improve, and provide the Services.</p>
                <p>You are responsible for ensuring that your User Content is accurate and that you have the rights to submit it.</p>

                <h2>7. Acceptable Use</h2>
                <p>You agree not to:</p>
                <ul>
                  <li>use the Services for unlawful, fraudulent, or misleading purposes</li>
                  <li>interfere with platform security or operations</li>
                  <li>attempt to gain unauthorized access to accounts, systems, or data</li>
                  <li>scrape, copy, reverse engineer, or exploit the Services except as allowed by law</li>
                  <li>upload malicious code, bots, or harmful material</li>
                  <li>misuse AI outputs as guaranteed facts or professional advice</li>
                  <li>use the Services in a way that infringes another party’s rights</li>
                </ul>
                <p>We may suspend or terminate access for conduct that violates these Terms or harms the platform or other users.</p>

                <h2>8. AI-Generated Content</h2>
                <p>MoneyBot Labs may generate stock ratings, commentary, summaries, forecasts, alerts, or similar outputs using automated systems and AI models. These outputs may be incomplete, inaccurate, delayed, or wrong. Market conditions can change rapidly, and data sources may contain errors or interruptions.</p>
                <p>You should independently verify all information before acting on it.</p>

                <h2>9. Market Data and Third-Party Sources</h2>
                <p>Our Services may rely on third-party market data providers, news sources, analytics vendors, hosting providers, and other external services. We do not guarantee the accuracy, completeness, timeliness, or availability of third-party data.</p>
                <p>We are not responsible for losses or damages arising from delayed quotes, incomplete news coverage, inaccurate data feeds, outages, or third-party service failures.</p>

                <h2>10. Payments and Paid Features</h2>
                <p>Some features may require payment or subscription. If paid plans are offered, pricing, billing frequency, renewal terms, and cancellation details will be presented at the time of purchase. Unless otherwise stated, fees are non-refundable to the fullest extent allowed by law.</p>
                <p>We may change pricing or features at any time, but changes will apply prospectively.</p>

                <h2>11. Intellectual Property</h2>
                <p>The Services, including software, branding, logos, design, text, graphics, AI workflows, and platform content created by MoneyBot Labs, are owned by or licensed to MoneyBot Labs and are protected by applicable intellectual property laws.</p>
                <p>Except for limited personal use of the Services, you may not copy, distribute, modify, sell, or create derivative works from our content without prior written permission.</p>

                <h2>12. Disclaimer of Warranties</h2>
                <p>The Services are provided on an “as is” and “as available” basis. To the fullest extent permitted by law, MoneyBot Labs disclaims all warranties, express or implied, including warranties of merchantability, fitness for a particular purpose, title, non-infringement, accuracy, and availability.</p>
                <p>We do not guarantee that the Services will be uninterrupted, error-free, secure, or suitable for your needs.</p>

                <h2>13. Limitation of Liability</h2>
                <p>To the fullest extent permitted by law, MoneyBot Labs and its officers, owners, employees, contractors, affiliates, and service providers will not be liable for any indirect, incidental, special, consequential, exemplary, or punitive damages, or for any loss of profits, trading losses, lost data, business interruption, or loss of goodwill arising out of or related to your use of the Services.</p>
                <p>Our total liability for any claim arising out of or relating to the Services will not exceed the amount you paid us, if any, in the 12 months before the event giving rise to the claim.</p>

                <h2>14. Indemnification</h2>
                <p>You agree to defend, indemnify, and hold harmless MoneyBot Labs and its affiliates, officers, employees, and service providers from any claims, liabilities, damages, losses, and expenses arising out of your use of the Services, your User Content, your violation of these Terms, or your violation of any rights of another person or entity.</p>

                <h2>15. Termination</h2>
                <p>We may suspend or terminate your access to the Services at any time, with or without notice, if we believe you violated these Terms, created risk for the platform, or if continued service is no longer commercially or legally feasible.</p>

                <h2>16. Changes to the Services or Terms</h2>
                <p>We may update the Services or these Terms from time to time. When we do, we will update the Effective Date above. Your continued use of the Services after changes become effective means you accept the revised Terms.</p>

                <h2>17. Governing Law</h2>
                <p>These Terms will be governed by and construed in accordance with the laws of the applicable jurisdiction in which MoneyBot Labs operates, without regard to conflict of law principles.</p>

                <h2>18. Contact</h2>
                <p>If you have questions about these Terms, contact:</p>
                <p>MoneyBot Labs<br />Email: <a href="mailto:support@moneybotlabs.com">support@moneybotlabs.com</a></p>
              </main>
            </body></html>
            """
        )

    @app.get("/help")
    def help_page():
        return render_template_string(
            """
            <html><body style="font-family:Inter,sans-serif;min-height:100vh;margin:0;background:#f7fee7;padding:24px;box-sizing:border-box;color:#14532d">
              <main style="max-width:980px;margin:0 auto;background:#f0fdf4;padding:28px;border-radius:14px;box-shadow:0 10px 28px rgba(15,23,42,.08);line-height:1.65">
                <p style="margin:0 0 12px"><a href="/" style="display:inline-block;text-decoration:none;background:#dcfce7;color:#14532d;padding:8px 12px;border-radius:999px;font-weight:700">← Back</a></p>
                <h1 style="margin-top:0">MoneyBot Labs Help Center</h1>
                <h2>Welcome to MoneyBot Labs</h2>
                <p>MoneyBot Labs is an AI-assisted stock research website built to help users review market data, compare stock signals, track a personal portfolio, and understand why a recommendation was generated.</p>
                <p>MoneyBot Labs does not place trades for you. It provides research, signal summaries, AI explanations, market context, and portfolio tracking tools so you can make better-informed decisions.</p>
                <h2>Main Menu</h2>
                <p>Use the Menu button in the top-right corner of the homepage to open the navigation sidebar.</p>
                <p>From the menu, you can access:</p>
                <table style="width:100%;border-collapse:collapse"><tr><th align="left">Menu Item</th><th align="left">What It Does</th></tr>
                <tr><td>User Portfolio</td><td>Track stocks you own, entry price, shares, current value, gains/losses, and AI advice.</td></tr>
                <tr><td>Security</td><td>Update your email address or password.</td></tr>
                <tr><td>Notifications</td><td>Manage push alert settings for portfolio and stock signal changes.</td></tr>
                <tr><td>AI Performance</td><td>View model health, decision tracking, outcome testing, and backtested result summaries.</td></tr>
                <tr><td>Account</td><td>General account area.</td></tr>
                <tr><td>Privacy</td><td>Read how user data is handled.</td></tr>
                <tr><td>Terms</td><td>Review the website terms of use.</td></tr>
                <tr><td>Help</td><td>Learn how to use the website features.</td></tr>
                <tr><td>Disclaimer</td><td>Review the financial disclaimer and risk notice.</td></tr></table>
                <h2>Quick Ask</h2><p>The Quick Ask tool lets you enter a stock ticker and receive an instant AI-assisted signal.</p>
                <h3>How to use Quick Ask</h3><ol><li>Go to the homepage.</li><li>Find the Quick Ask section.</li><li>Enter a ticker symbol, such as AAPL, TSLA, NVDA, or SOFI.</li><li>Click Analyze.</li><li>Review the recommendation, current price, short rationale, trend chart, and AI key points.</li></ol>
                <h3>What the recommendation means</h3>
                <table style="width:100%;border-collapse:collapse"><tr><th align="left">Signal</th><th align="left">Meaning</th></tr>
                <tr><td>Strong Buy</td><td>The system sees a stronger buying setup based on current indicators.</td></tr>
                <tr><td>Buy</td><td>The stock may be reasonable to consider, but you should still review risk.</td></tr>
                <tr><td>Hold</td><td>The system does not see a strong reason to buy or sell immediately.</td></tr>
                <tr><td>Hold Off For Now</td><td>The system suggests waiting instead of buying right now.</td></tr>
                <tr><td>Sell</td><td>The system may see conditions where reducing or exiting the position could be worth considering.</td></tr></table>
                <h3>AI Key Points</h3><ul><li>A short explanation of the signal.</li><li>Risk notes.</li><li>Next checks to review before making a decision.</li><li>Whether the signal came from the AI model, deterministic model, or rule-based logic.</li></ul>
                <h2>Market Indices</h2><p>The homepage includes a Market Indices section that shows broad market context.</p><p>This section may include: Dow, S&amp;P 500, Nasdaq, Gold, Bitcoin.</p><p>Each market card may show the latest price, daily change percentage, and a small trend chart. This helps users understand whether the broader market is moving up, down, or sideways before reviewing individual stock picks.</p>
                <h2>Buyer’s Guide</h2><p>The Buyer’s Guide explains the main stock research categories on the homepage.</p>
                <h3>Stable Watchlist</h3><p>The Stable Watchlist focuses on lower-risk, long-term style stocks. These are generally meant for users who want steadier companies instead of highly speculative trades.</p><p>Stable Watchlist rows may include: Ticker, Price, Signal score, Transparency note explaining why the stock appears on the list.</p>
                <h3>Hot Momentum Buys</h3><p>The Hot Momentum Buys section focuses on higher-risk stocks that may have stronger short-term momentum.</p><p>These stocks can move fast in either direction. The section is designed for users who want to review aggressive opportunities, not guaranteed winners.</p><p>Hot Momentum rows may include: Ticker, Price, Score, Signal source, Transparency or rationale.</p>
                <h3>Whales of Wall Street</h3><p>The Whales of Wall Street section shows stock ideas connected to well-known investors or large investor-style portfolios.</p><p>This section helps users see what major investors are associated with certain holdings. It is not a recommendation to copy them blindly. It is a research starting point.</p>
                <h2>Clicking a Ticker Symbol</h2><p>Many ticker symbols on MoneyBot Labs are clickable.</p><p>When you click a ticker, a Company Details window opens.</p><p>The Company Details window may show: Company name, Ticker symbol, Short business summary, Recent headlines (when available), and Basic company context.</p><p>Use this feature when you want to understand what the business actually does before looking at the AI signal.</p>
                <h3>Example</h3><p>Instead of only seeing AAPL, you can click the ticker and see more context about Apple as a business, including a short company overview and available news context.</p><p>This helps users avoid buying a stock based only on a score or ticker name.</p>
                <h2>User Portfolio</h2><p>The User Portfolio page helps you track stocks you own or want to monitor closely.</p><p>To open it, click User Portfolio from the homepage or menu.</p><h3>What you can enter</h3><p>For each stock, you may enter: Stock symbol, Entry price, Number of shares.</p><p>The portfolio then uses that information to help calculate your position performance.</p>
                <h3>Portfolio table columns</h3><table style="width:100%;border-collapse:collapse"><tr><th align="left">Column</th><th align="left">What It Means</th></tr><tr><td>Symbol</td><td>The ticker for the stock.</td></tr><tr><td>Entry</td><td>The price you paid or entered for the position.</td></tr><tr><td>Shares</td><td>The number of shares you own.</td></tr><tr><td>Current Price</td><td>The latest available price.</td></tr><tr><td>Today’s Gain/Loss</td><td>The estimated daily movement for your position.</td></tr><tr><td>Performance</td><td>How the position is performing compared with your entry price.</td></tr><tr><td>Trend Score</td><td>A signal score based on recent movement and indicators.</td></tr><tr><td>Advice</td><td>AI-assisted guidance for that position.</td></tr><tr><td>Action</td><td>Portfolio actions such as selling or removing a position.</td></tr></table>
                <h3>Clicking Portfolio Advice for an Explanation</h3><p>In the User Portfolio, the advice field is clickable. When you click the advice, MoneyBot Labs opens an Advice Reasoning window.</p><p>This window is designed to explain the recommendation in plain English.</p><p>It may include: Why the system gave that advice; whether the position looks strong, weak, or mixed; risk notes; what to check next; recent headlines; and a button to explain the recommendation in simpler language.</p><p>Why this matters: a basic label like Buy, Hold, or Sell is not enough by itself. The explanation window helps users understand the reasoning behind the signal so they are not blindly following a recommendation.</p>
                <h3>Lifetime Gains and Losses</h3><p>The User Portfolio page includes a Show Lifetime Gains/Losses option. This area is used to track sold trades and realized gains or losses.</p><p>When available, it may show sold symbol, entry price, sold price, shares sold, realized gain/loss, and lifetime total.</p><p>This helps users separate unrealized gains/losses from stocks they still hold and realized gains/losses from positions they already sold.</p>
                <h2>Account Creation and Login</h2><p>Users can create an account using the Sign Up page.</p><p>During signup, users may be asked for display name, username, email address, password, and optional profile picture.</p><p>If a profile picture is not added, MoneyBot Labs can display initials inside a basic profile circle.</p><p>Users can log in with their account credentials and access profile, portfolio, notification, and security features.</p>
                <h2>Profile and Account Settings</h2><p>The Profile / Account Settings area lets users update their display name, username, profile picture, and investor preferences.</p><p>The investor profile records goals, time horizon, risk tolerance, loss capacity, liquidity needs, experience, account context, portfolio concentration limits, and security preferences. Incomplete profiles use conservative defaults until all required questions are answered.</p><p>The profile image tool includes crop-style controls such as zoom, horizontal position, and vertical position before saving the picture.</p>
                <h2>Security</h2><p>The Security page lets users update sensitive account information, including email address and password.</p><p>For protection, MoneyBot Labs may require the current password before saving security changes.</p>
                <h2>Password Recovery</h2><p>If you forget your password, use the Forgot Password option on the login page.</p><p>MoneyBot Labs may send password recovery instructions to the email address on the account, if email delivery is configured.</p><p>For security, the website may show the same general message whether or not an email exists. This helps protect user privacy.</p>
                <h2>Notifications</h2><p>The Notifications page is used to manage push alerts.</p><p>Depending on browser and device support, users may enable push notifications for certain MoneyBot activity.</p><p>Possible notification types include portfolio advice changes, buy advice changes, sell advice changes, hot momentum score changes, whale investor list changes, and new whale-related stock activity.</p><p>To receive notifications, the browser may ask for permission. If permission is blocked, users may need to adjust browser or device notification settings.</p>
                <h2>AI Performance</h2><p>The AI Performance page is designed to show how the system is performing over time.</p><p>This section may include model health, whether the model is loaded, decision logging status, recent decision counts, one-day and five-day outcome tracking, calibration status, and backtested/historical performance summaries.</p>
                <h3>What backtested results mean</h3><p>Backtested results compare past AI signals against later market outcomes. Backtesting helps show whether the system is improving, but it does not guarantee future results.</p>
                <h2>Understanding AI Recommendations</h2><table style="width:100%;border-collapse:collapse"><tr><th align="left">Signal Source</th><th align="left">Meaning</th></tr><tr><td>AI-assisted explanation</td><td>A plain-English explanation generated from available stock context.</td></tr><tr><td>Deterministic model</td><td>A structured model that applies repeatable thresholds and learned signal behavior.</td></tr><tr><td>Rule-based logic</td><td>A fallback system based on indicators, price movement, and simple signal rules.</td></tr><tr><td>Market data checks</td><td>Current price, recent history, trend movement, and available company context.</td></tr><tr><td>News context</td><td>Recent headlines or company-related news when available.</td></tr></table><p>The goal is to make the stock signal easier to understand, not to guarantee a profitable trade.</p>
                <h2>Important Risk Reminder</h2><p>MoneyBot Labs provides AI-assisted stock research and educational information only. It is not a licensed financial advisor, broker, or investment manager.</p><p>Before making any trade, users should do their own research, review company fundamentals, consider risk tolerance, avoid investing money they cannot afford to lose, understand that stocks can lose value quickly, and treat AI signals as research support—not guaranteed instructions.</p>
                <h2>Common Questions</h2><h3>Does MoneyBot Labs buy or sell stocks for me?</h3><p>No. MoneyBot Labs does not place trades. It only provides research, signals, explanations, and tracking tools.</p><h3>Are the AI recommendations guaranteed?</h3><p>No. No AI stock prediction is guaranteed. Market conditions can change quickly.</p><h3>Why does a stock show Hold Off For Now?</h3><p>Hold Off For Now means the system does not currently see a strong enough setup to support buying. It may be due to weak trend, mixed indicators, risk, price pressure, or lack of confirmation.</p><h3>Why should I click the ticker?</h3><p>Clicking the ticker helps you understand the company behind the stock. A stock score is more useful when you also know what the business does.</p><h3>Why should I click the portfolio advice?</h3><p>Clicking the advice opens the reasoning window. This explains why the system gave the recommendation and what risk factors or next checks matter.</p><h3>Why do some items say data is unavailable?</h3><p>Market data, company summaries, or news may occasionally be unavailable because of provider limits, temporary API issues, unsupported tickers, or market data delays.</p><h3>Is this financial advice?</h3><p>No. MoneyBot Labs provides AI-assisted research and educational information only.</p>
                <h2>Best Way to Use MoneyBot Labs</h2><p>A good workflow is:</p><ol><li>Start with Market Indices to understand the broader market.</li><li>Use Quick Ask to analyze a ticker.</li><li>Click the ticker symbol to learn about the company.</li><li>Review Stable Watchlist, Hot Momentum Buys, or Whales of Wall Street for ideas.</li><li>Add stocks you own to User Portfolio.</li><li>Click the Advice field in your portfolio to understand the recommendation.</li><li>Review risk before making any decision.</li><li>Track results over time instead of relying on one signal.</li></ol>
              </main>
            </body></html>
            """
        )

    @app.get("/disclaimer")
    def disclaimer_page():
        return render_template_string(
            """
            <html><body style="font-family:Inter,sans-serif;min-height:100vh;margin:0;background:#f7fee7;padding:24px;box-sizing:border-box;color:#14532d">
              <main style="max-width:900px;margin:0 auto;background:#f0fdf4;padding:28px;border-radius:14px;box-shadow:0 10px 28px rgba(15,23,42,.08);line-height:1.6">
                <p style="margin:0 0 12px"><a href="/" style="display:inline-block;text-decoration:none;background:#dcfce7;color:#14532d;padding:8px 12px;border-radius:999px;font-weight:700">← Back</a></p>
                <h1 style="margin-top:0">Disclaimer</h1>
                <p><strong>Effective Date: April 30, 2026</strong></p>
                <p>The information provided by MoneyBot Labs is for informational and educational purposes only.</p>

                <h2>1. Not Investment Advice</h2>
                <p>MoneyBot Labs does not provide personalized investment advice, financial advice, legal advice, tax advice, or other professional advice. Nothing on our platform, website, reports, alerts, AI outputs, commentary, stock ratings, or related materials should be interpreted as a recommendation or solicitation to buy, sell, or hold any security.</p>
                <p>All investment decisions involve risk, and you are solely responsible for your own decisions.</p>

                <h2>2. No Guarantee of Results</h2>
                <p>Past performance does not guarantee future results. Any projections, forecasts, probabilities, backtests, model outputs, or forward-looking statements are inherently uncertain and may not reflect actual future performance.</p>
                <p>MoneyBot Labs makes no guarantee that any stock, strategy, signal, alert, or AI-generated insight will be profitable or successful.</p>

                <h2>3. AI and Automated Analysis Limitations</h2>
                <p>Our platform may use automated models, scoring systems, external data feeds, and AI-generated analysis. These systems can produce incomplete, outdated, inaccurate, or misleading results. Outputs may change as data changes, models are updated, or market conditions shift.</p>
                <p>You should not rely solely on automated outputs when making financial decisions.</p>

                <h2>4. Third-Party Data</h2>
                <p>MoneyBot Labs may use third-party data sources such as market data providers, financial statement sources, news services, and analytics providers. We do not warrant the accuracy, timeliness, completeness, or availability of any third-party information.</p>

                <h2>5. No Professional Relationship</h2>
                <p>Your use of MoneyBot Labs does not create an adviser-client, fiduciary, brokerage, agency, or other professional relationship between you and MoneyBot Labs.</p>

                <h2>6. Use at Your Own Risk</h2>
                <p>By using MoneyBot Labs, you acknowledge that you do so at your own risk. You are responsible for conducting your own due diligence and consulting qualified professionals before making financial, legal, or tax decisions.</p>

                <h2>7. Contact</h2>
                <p>If you have questions about this Disclaimer, contact:</p>
                <p>MoneyBot Labs<br />Email: <a href="mailto:support@moneybotlabs.com">support@moneybotlabs.com</a></p>
              </main>
            </body></html>
            """
        )

    @app.get("/login")
    @app.get("/login/")
    def login_page():
        return render_template_string(
            """
            <html><body style="font-family:Inter,sans-serif;min-height:100vh;margin:0;display:flex;align-items:center;justify-content:center;background:#f7fee7;padding:24px;box-sizing:border-box">
              <div style="width:100%;max-width:520px;background:#f0fdf4;padding:34px;border-radius:14px;box-shadow:0 10px 28px rgba(15,23,42,.08)">
                <h2 style="font-size:2.2rem;margin:0 0 18px;text-align:center">Login</h2>
                <p style="display:flex;justify-content:center;gap:10px;margin:0 0 18px">
                  <a href="/" style="text-decoration:none;background:#dcfce7;color:#14532d;padding:10px 16px;border-radius:999px;font-size:1.05rem;font-weight:600">Home</a>
                  <a href="/signup" style="text-decoration:none;background:#d1fae5;color:#0f172a;padding:10px 16px;border-radius:999px;font-size:1.05rem;font-weight:600">Create account</a>
                </p>
                <form id="loginForm" style="display:flex;flex-direction:column;gap:12px">
                  <input id="email" name="email" type="text" autocomplete="username" placeholder="email or username" required style="font-size:1.08rem;padding:12px;border:1px solid #bbf7d0;border-radius:10px" />
                  <input id="password" name="password" type="password" autocomplete="current-password" placeholder="password" required style="font-size:1.08rem;padding:12px;border:1px solid #bbf7d0;border-radius:10px" />
                  <label style="display:flex;align-items:center;gap:10px;color:#14532d;font-size:0.98rem">
                    <input id="trustedDevice" name="trustedDevice" type="checkbox" style="width:18px;height:18px" />
                    Stay signed in on this device
                  </label>
                  <button type="button" onclick="forgotPassword()" style="align-self:flex-start;border:none;background:none;color:#15803d;padding:0 2px;font-size:0.95rem;font-weight:600;cursor:pointer;text-decoration:underline">Forgot Password?</button>
                  <button type="submit" style="font-size:1.08rem;padding:12px;border:none;border-radius:10px;background:#16a34a;color:#f0fdf4;font-weight:700;cursor:pointer">Login</button>
                </form>
                <div id="out" style="margin-top:12px;color:#166534;text-align:center;font-size:1.02rem"></div>
              </div>
              <script>
              const emailEl = document.getElementById('email');
              const passwordEl = document.getElementById('password');
              const outEl = document.getElementById('out');
              const trustedDeviceEl = document.getElementById('trustedDevice');
              const TAB_SESSION_KEY = 'moneybot_tab_session_id';
              function getOrCreateTabSessionId(){
                let tabSessionId = sessionStorage.getItem(TAB_SESSION_KEY) || localStorage.getItem(TAB_SESSION_KEY);
                if(!tabSessionId){
                  tabSessionId = (typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2);
                  sessionStorage.setItem(TAB_SESSION_KEY, tabSessionId);
                }
                return tabSessionId;
              }
              document.getElementById('loginForm').addEventListener('submit', go);

              async function go(event){
                if (event) event.preventDefault();
                outEl.textContent = 'Logging in...';
                try {
                  const res = await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:emailEl.value,password:passwordEl.value,tab_session_id:getOrCreateTabSessionId(),trusted_device:Boolean(trustedDeviceEl && trustedDeviceEl.checked)})});
                  const data = await res.json();
                  if(res.ok){ if(Boolean(trustedDeviceEl && trustedDeviceEl.checked)){ localStorage.setItem(TAB_SESSION_KEY, getOrCreateTabSessionId()); } else { localStorage.removeItem(TAB_SESSION_KEY); } outEl.textContent='Login successful. Redirecting...'; location.href='/'; }
                  else { outEl.textContent = data.error || 'Login failed. Please verify your credentials.'; }
                } catch (err) {
                  outEl.textContent = 'Unable to login right now. Please retry.';
                }
              }

              async function forgotPassword(){
                const email = (emailEl.value || '').trim();
                if(!email){
                  outEl.textContent = 'Enter your email first, then click Forgot Password.';
                  return;
                }
                outEl.textContent = 'Sending password recovery instructions...';
                try {
                  const res = await fetch('/api/auth/forgot-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email})});
                  const data = await res.json();
                  if (!res.ok) {
                    outEl.textContent = data.error || 'Unable to start password recovery right now.';
                    return;
                  }
                  if (data && data.email_delivery_configured === false) {
                    outEl.textContent = 'Password recovery email service is not configured yet. Please contact support or try again later.';
                    return;
                  }
                  if (data && data.email_delivery_error === true) {
                    outEl.textContent = 'Password recovery request received, but there was a temporary email delivery issue. Please try again shortly or contact support.';
                    return;
                  }
                  outEl.textContent = data.message || 'If an account exists for that email, password recovery instructions have been sent.';
                } catch (err) {
                  outEl.textContent = 'Unable to start password recovery right now. Please retry.';
                }
              }
              </script>
            </body></html>
            """
        )

    @app.get("/signup")
    @app.get("/signup/")
    def signup_page():
        return render_template_string(
            """
            <html><body style="font-family:Inter,sans-serif;min-height:100vh;margin:0;display:flex;align-items:center;justify-content:center;background:#f7fee7;padding:24px;box-sizing:border-box">
              <div style="width:100%;max-width:520px;background:#f0fdf4;padding:34px;border-radius:14px;box-shadow:0 10px 28px rgba(15,23,42,.08)">
                <h2 style="font-size:2.2rem;margin:0 0 18px;text-align:center">Sign Up</h2>
                <p style="display:flex;justify-content:center;gap:10px;margin:0 0 18px">
                  <a href="/" style="text-decoration:none;background:#dcfce7;color:#14532d;padding:10px 16px;border-radius:999px;font-size:1.05rem;font-weight:600">Home</a>
                  <a href="/login" style="text-decoration:none;background:#d1fae5;color:#0f172a;padding:10px 16px;border-radius:999px;font-size:1.05rem;font-weight:600">Login</a>
                </p>
                <form id="signupForm" style="display:flex;flex-direction:column;gap:12px">
                  <div style="display:flex;justify-content:center;margin-bottom:4px">
                    <button id="avatarPickerBtn" type="button" style="position:relative;width:86px;height:86px;border-radius:999px;border:2px dashed #86efac;background:#dcfce7;color:#166534;font-weight:700;cursor:pointer">
                      <span id="avatarPickerText" style="font-size:.82rem;line-height:1.1">Add<br/>Picture</span>
                      <img id="avatarPreview" alt="Profile preview" style="display:none;width:100%;height:100%;border-radius:999px;object-fit:cover" />
                      <span style="position:absolute;right:-3px;bottom:-3px;background:#16a34a;color:#f0fdf4;width:24px;height:24px;border-radius:999px;display:flex;align-items:center;justify-content:center;font-size:13px">✎</span>
                    </button>
                    <input id="profileImage" type="file" accept="image/*" style="display:none" />
                  </div>
                  <input id="name" placeholder="full name" required style="font-size:1.08rem;padding:12px;border:1px solid #bbf7d0;border-radius:10px" />
                  <input id="username" name="username" autocomplete="off" placeholder="username" required style="font-size:1.08rem;padding:12px;border:1px solid #bbf7d0;border-radius:10px" />
                  <input id="email" name="email" type="email" autocomplete="username" placeholder="email" required style="font-size:1.08rem;padding:12px;border:1px solid #bbf7d0;border-radius:10px" />
                  <label style="font-size:.95rem;color:#166534;font-weight:600">Profile picture (optional)</label>
                  <input id="profileImage" type="file" accept="image/*" style="font-size:1rem;padding:8px;border:1px solid #bbf7d0;border-radius:10px;background:#fff" />
                  <input id="password" name="password" type="password" autocomplete="new-password" placeholder="password" required style="font-size:1.08rem;padding:12px;border:1px solid #bbf7d0;border-radius:10px" />
                  <input id="confirmPassword" name="confirmPassword" type="password" autocomplete="new-password" placeholder="confirm password" required style="font-size:1.08rem;padding:12px;border:1px solid #bbf7d0;border-radius:10px" />
                  <button type="submit" style="font-size:1.08rem;padding:12px;border:none;border-radius:10px;background:#16a34a;color:#f0fdf4;font-weight:700;cursor:pointer">Create</button>
                </form>
                <div id="avatarEditorModal" style="display:none;position:fixed;inset:0;background:rgba(2,6,23,.5);align-items:center;justify-content:center;padding:14px">
                  <div style="background:#f8fafc;border-radius:12px;padding:14px;width:min(96vw,420px)">
                    <h3 style="margin:0 0 8px;color:#0f172a">Adjust picture</h3>
                    <div style="display:flex;justify-content:center;margin-bottom:10px">
                      <div id="avatarEditorViewport" style="width:170px;height:170px;border-radius:999px;overflow:hidden;background:#e2e8f0;position:relative;border:1px solid #cbd5e1">
                        <img id="avatarEditorImage" alt="Adjust profile image" style="position:absolute;left:50%;top:50%;width:100%;height:100%;object-fit:cover;transform:translate(-50%,-50%);transform-origin:center center" />
                      </div>
                    </div>
                    <label style="display:block;font-size:.9rem;color:#166534;font-weight:700">Zoom</label>
                    <input id="avatarZoom" type="range" min="1" max="4" step="0.01" value="1.35" style="width:100%" />
                    <label style="display:block;font-size:.9rem;color:#166534;font-weight:700;margin-top:8px">Horizontal</label>
                    <input id="avatarOffsetX" type="range" min="-120" max="120" step="1" value="0" style="width:100%" />
                    <label style="display:block;font-size:.9rem;color:#166534;font-weight:700;margin-top:8px">Vertical</label>
                    <input id="avatarOffsetY" type="range" min="-120" max="120" step="1" value="0" style="width:100%" />
                    <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:10px">
                      <button id="cancelAvatarEditBtn" type="button" style="border:none;background:#e2e8f0;color:#0f172a;padding:8px 12px;border-radius:8px;cursor:pointer">Cancel</button>
                      <button id="saveAvatarEditBtn" type="button" style="border:none;background:#16a34a;color:#f0fdf4;padding:8px 12px;border-radius:8px;cursor:pointer;font-weight:700">Use picture</button>
                    </div>
                  </div>
                </div>
                <div id="out" style="margin-top:12px;color:#166534;text-align:center;font-size:1.02rem"></div>
              </div>
              <script>
              const nameEl = document.getElementById('name');
              const usernameEl = document.getElementById('username');
              const emailEl = document.getElementById('email');
              const profileImageEl = document.getElementById('profileImage');
              const avatarPickerBtn = document.getElementById('avatarPickerBtn');
              const avatarPreview = document.getElementById('avatarPreview');
              const avatarPickerText = document.getElementById('avatarPickerText');
              const avatarEditorModal = document.getElementById('avatarEditorModal');
              const avatarEditorImage = document.getElementById('avatarEditorImage');
              const avatarZoomEl = document.getElementById('avatarZoom');
              const avatarOffsetXEl = document.getElementById('avatarOffsetX');
              const avatarOffsetYEl = document.getElementById('avatarOffsetY');
              const passwordEl = document.getElementById('password');
              const confirmPasswordEl = document.getElementById('confirmPassword');
              const outEl = document.getElementById('out');
              const trustedDeviceEl = document.getElementById('trustedDevice');
              let profileImageDataUrl = null;
              let rawSelectedAvatarUrl = null;
              const TAB_SESSION_KEY = 'moneybot_tab_session_id';
              function getOrCreateTabSessionId(){
                let tabSessionId = sessionStorage.getItem(TAB_SESSION_KEY) || localStorage.getItem(TAB_SESSION_KEY);
                if(!tabSessionId){
                  tabSessionId = (typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2);
                  sessionStorage.setItem(TAB_SESSION_KEY, tabSessionId);
                }
                return tabSessionId;
              }
              function readFileAsDataUrl(file){
                return new Promise((resolve, reject) => {
                  if(!file){ resolve(null); return; }
                  const reader = new FileReader();
                  reader.onload = () => resolve(reader.result);
                  reader.onerror = () => reject(new Error('Unable to read selected file.'));
                  reader.readAsDataURL(file);
                });
              }
              function applyEditorTransform(){
                avatarEditorImage.style.transform = `translate(calc(-50% + ${avatarOffsetXEl.value}px), calc(-50% + ${avatarOffsetYEl.value}px)) scale(${avatarZoomEl.value})`;
              }
              function openAvatarEditor(dataUrl){
                rawSelectedAvatarUrl = dataUrl;
                avatarEditorImage.src = dataUrl;
                avatarZoomEl.value = '1.35';
                avatarOffsetXEl.value = '0';
                avatarOffsetYEl.value = '0';
                applyEditorTransform();
                avatarEditorModal.style.display = 'flex';
              }
              function closeAvatarEditor(){ avatarEditorModal.style.display = 'none'; }
              function buildCroppedAvatarDataUrl(){
                const canvas = document.createElement('canvas');
                canvas.width = 240;
                canvas.height = 240;
                const ctx = canvas.getContext('2d');
                const img = avatarEditorImage;
                const zoom = Number(avatarZoomEl.value || 1);
                const offsetX = Number(avatarOffsetXEl.value || 0);
                const offsetY = Number(avatarOffsetYEl.value || 0);
                const width = Number(img.naturalWidth || canvas.width);
                const height = Number(img.naturalHeight || canvas.height);
                const fitScale = Math.max(canvas.width / width, canvas.height / height);
                const drawWidth = width * fitScale * zoom;
                const drawHeight = height * fitScale * zoom;
                const x = (canvas.width - drawWidth) / 2 + (offsetX * (canvas.width / 170));
                const y = (canvas.height - drawHeight) / 2 + (offsetY * (canvas.height / 170));
                ctx.save();
                ctx.beginPath();
                ctx.arc(canvas.width / 2, canvas.height / 2, canvas.width / 2, 0, Math.PI * 2);
                ctx.clip();
                ctx.drawImage(img, x, y, drawWidth, drawHeight);
                ctx.restore();
                return canvas.toDataURL('image/png');
              }
              avatarPickerBtn.addEventListener('click', () => profileImageEl.click());
              profileImageEl.addEventListener('change', async () => {
                const file = profileImageEl.files && profileImageEl.files[0];
                if(!file){ return; }
                const dataUrl = await readFileAsDataUrl(file);
                openAvatarEditor(dataUrl);
              });
              [avatarZoomEl, avatarOffsetXEl, avatarOffsetYEl].forEach((el) => el.addEventListener('input', applyEditorTransform));
              document.getElementById('cancelAvatarEditBtn').addEventListener('click', closeAvatarEditor);
              document.getElementById('saveAvatarEditBtn').addEventListener('click', () => {
                profileImageDataUrl = buildCroppedAvatarDataUrl() || rawSelectedAvatarUrl;
                avatarPreview.src = profileImageDataUrl;
                avatarPreview.style.display = 'block';
                avatarPickerText.style.display = 'none';
                closeAvatarEditor();
              });
              document.getElementById('signupForm').addEventListener('submit', go);

              async function go(event){
                if (event) event.preventDefault();
                if(passwordEl.value !== confirmPasswordEl.value){
                  outEl.textContent = 'Passwords do not match.';
                  return;
                }
                const res = await fetch('/api/auth/signup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:nameEl.value,username:usernameEl.value,email:emailEl.value,password:passwordEl.value,profile_image_url:profileImageDataUrl,password_confirmation:confirmPasswordEl.value,tab_session_id:getOrCreateTabSessionId()})});
                const data = await res.json();
                if(res.ok){ outEl.textContent='Account created. Redirecting...'; location.href='/portfolio'; }
                else { outEl.textContent = data.error || 'Sign-up failed. Please try again.'; }
              }
              </script>
            </body></html>
            """
        )



    @app.get("/reset-password")
    @app.get("/reset-password/")
    def reset_password_page():
        return render_template_string(
            """
            <html><body style="font-family:Inter,sans-serif;min-height:100vh;margin:0;display:flex;align-items:center;justify-content:center;background:#f7fee7;padding:24px;box-sizing:border-box">
              <div style="width:100%;max-width:520px;background:#f0fdf4;padding:34px;border-radius:14px;box-shadow:0 10px 28px rgba(15,23,42,.08)">
                <h2 style="font-size:2rem;margin:0 0 18px;text-align:center">Reset Password</h2>
                <form id="resetForm" style="display:flex;flex-direction:column;gap:12px">
                  <input id="password" type="password" placeholder="new password" required style="font-size:1.08rem;padding:12px;border:1px solid #bbf7d0;border-radius:10px" />
                  <input id="confirmPassword" type="password" placeholder="confirm new password" required style="font-size:1.08rem;padding:12px;border:1px solid #bbf7d0;border-radius:10px" />
                  <button type="submit" style="font-size:1.08rem;padding:12px;border:none;border-radius:10px;background:#16a34a;color:#f0fdf4;font-weight:700;cursor:pointer">Update Password</button>
                </form>
                <div id="out" style="margin-top:12px;color:#166534;text-align:center;font-size:1.02rem"></div>
                <p style="margin-top:14px;text-align:center"><a href="/login" style="color:#15803d;font-weight:600">Back to login</a></p>
              </div>
              <script>
                const passwordEl = document.getElementById('password');
                const confirmPasswordEl = document.getElementById('confirmPassword');
                const outEl = document.getElementById('out');
              const trustedDeviceEl = document.getElementById('trustedDevice');
                const params = new URLSearchParams(window.location.search);
                const token = params.get('token') || '';
                document.getElementById('resetForm').addEventListener('submit', async (event) => {
                  event.preventDefault();
                  if(!token){ outEl.textContent='Reset link is invalid.'; return; }
                  if(passwordEl.value !== confirmPasswordEl.value){ outEl.textContent='Passwords do not match.'; return; }
                  const res = await fetch('/api/auth/reset-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token, password:passwordEl.value})});
                  const data = await res.json();
                  if(res.ok){ outEl.textContent='Password updated. Redirecting to login...'; setTimeout(()=>{ location.href='/login'; }, 900); }
                  else { outEl.textContent = data.error || 'Unable to reset password.'; }
                });
              </script>
            </body></html>
            """
        )

    @app.get("/settings")
    @app.get("/settings/")
    def settings_page():
        return render_template("settings.html")

    @app.get("/portfolio")
    @app.get("/portfolio/")
    def portfolio_page():
        return render_template_string(
            """
            <html><body style="font-family:Inter,sans-serif;padding:24px;background:#f7fee7;max-width:1100px;margin:0 auto">
              <h2>User Portfolio</h2>
              <p style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
                <a href="/" style="text-decoration:none;background:#dcfce7;color:#14532d;padding:12px 18px;border-radius:999px;font-size:1.08rem;font-weight:700">Home</a>
                <button id="refreshPortfolioBtn" onclick="refreshPortfolio()" style="border:none;background:#65a30d;color:#f7fee7;padding:12px 18px;border-radius:999px;font-size:1.08rem;font-weight:700;cursor:pointer">Refresh Portfolio</button>
                <button onclick="logout()" style="border:none;background:#166534;color:#f0fdf4;padding:12px 18px;border-radius:999px;font-size:1.08rem;font-weight:700;cursor:pointer">Logout</button>
              </p>
              <form id="addForm">
                <input id="symbol" placeholder="AAPL" required autocapitalize="characters" style="text-transform:uppercase" />
                <input id="buy_price" type="number" step="0.01" placeholder="buy price"/>
                <input id="shares" type="number" step="0.0001" placeholder="shares"/>
                <button type="submit" style="border:none;background:#16a34a;color:#f0fdf4;padding:9px 14px;border-radius:8px;font-weight:700;cursor:pointer">Add</button>
              </form>
              <div id="out" style="margin:10px 0;color:#166534"></div>
              <div id="loadingState" style="display:none;align-items:center;justify-content:center;gap:10px;position:fixed;top:16px;right:16px;background:rgba(236,252,203,.95);border:1px solid #bef264;border-radius:999px;padding:10px 14px;z-index:40;color:#14532d;font-weight:700;font-size:.95rem;pointer-events:none">
                <span style="width:34px;height:34px;border:4px solid #86efac;border-top-color:#16a34a;border-radius:999px;display:inline-block;animation:spin .8s linear infinite"></span>
                Loading latest portfolio stock data...
              </div>
              <button id="toggleLifetimeBtn" onclick="toggleLifetime()" style="border:none;background:#14532d;color:#f0fdf4;padding:9px 14px;border-radius:8px;font-weight:700;cursor:pointer;margin-bottom:10px">Show Lifetime Gains/Losses</button>
              <div id="lifetimePanel" style="display:none;background:#ecfccb;border:1px solid #d9f99d;border-radius:10px;padding:12px;margin-bottom:12px">
                <div style="font-weight:700;margin-bottom:8px">Lifetime Realized Gains/Losses: <span id="lifetimeTotal">$0.00</span></div>
                <div style="overflow-x:auto"><table style="width:100%;background:#f0fdf4;border-collapse:collapse;min-width:640px">
                  <thead><tr><th style="border:1px solid #e5e7eb;padding:8px">Sold At</th><th style="border:1px solid #e5e7eb;padding:8px">Symbol</th><th style="border:1px solid #e5e7eb;padding:8px">Entry</th><th style="border:1px solid #e5e7eb;padding:8px">Sold Price</th><th style="border:1px solid #e5e7eb;padding:8px">Shares Sold</th><th style="border:1px solid #e5e7eb;padding:8px">Realized</th><th style="border:1px solid #e5e7eb;padding:8px">Action</th></tr></thead>
                  <tbody id="soldRows"><tr><td colspan="7" style="padding:8px;color:#3f6212">No sold trades yet.</td></tr></tbody>
                </table></div>
              </div>
              <div style="overflow-x:auto"><table style="width:100%;background:#f0fdf4;border-collapse:collapse;min-width:980px">
                <thead><tr><th style="border:1px solid #e5e7eb;padding:8px">Symbol</th><th style="border:1px solid #e5e7eb;padding:8px">Entry</th><th style="border:1px solid #e5e7eb;padding:8px">Shares</th><th style="border:1px solid #e5e7eb;padding:8px">Current Price</th><th style="border:1px solid #e5e7eb;padding:8px">Today's Gain/Loss</th><th style="border:1px solid #e5e7eb;padding:8px">Performance</th><th style="border:1px solid #e5e7eb;padding:8px">Trend</th><th style="border:1px solid #e5e7eb;padding:8px">Score</th><th style="border:1px solid #e5e7eb;padding:8px">Advice</th><th style="border:1px solid #e5e7eb;padding:8px">Action</th></tr></thead>
                <tbody id="rows"></tbody>
              </table></div>
              <div id="tickerModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:50;align-items:center;justify-content:center;padding:14px">
                <div style="background:#f0fdf4;border-radius:12px;max-width:680px;width:100%;max-height:80vh;overflow:auto;padding:14px">
                  <div style="display:flex;justify-content:space-between;gap:8px;align-items:center">
                    <h3 id="modalTitle" style="margin:0">Company Details</h3>
                    <button onclick="closeModal()" style="border:none;background:#d1fae5;border-radius:8px;padding:6px 10px">Close</button>
                  </div>
                  <p id="modalSummary" style="color:#166534"></p>
                  <div id="modalNews" style="display:grid;gap:8px"></div>
                </div>
              </div>
              <div id="adviceModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:51;align-items:center;justify-content:center;padding:14px">
                <div style="background:#f0fdf4;border-radius:12px;max-width:520px;width:100%;padding:14px">
                  <div style="display:flex;justify-content:space-between;gap:8px;align-items:center">
                    <h3 id="adviceTitle" style="margin:0">Advice Reasoning</h3>
                    <button onclick="closeAdviceModal()" style="border:none;background:#d1fae5;border-radius:8px;padding:6px 10px">Close</button>
                  </div>
                  <div id="adviceReason" style="color:#dcfce7;margin-top:10px;background:#14532d;border:1px solid #166534;border-radius:10px;padding:10px">
                    <strong style="display:block;color:#bbf7d0;margin-bottom:6px">AI key points</strong>
                    <ul style="margin:0;padding-left:18px;display:grid;gap:4px">
                      <li style="color:#dcfce7">No reasoning available.</li>
                    </ul>
                  </div>
                  <div style="margin-top:8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
                    <button id="plainEnglishBtn" onclick="explainAdviceInPlainEnglish()" style="border:none;background:#16a34a;color:#f0fdf4;padding:7px 10px;border-radius:8px;font-weight:700;cursor:pointer">Explain this recommendation in plain English</button>
                    <span id="plainEnglishLoading" style="display:none;color:#3f6212;font-size:13px">Explaining...</span>
                  </div>
                  <p id="plainEnglishExplanation" style="display:none;color:#14532d;margin-top:10px;background:#ecfccb;border:1px solid #bef264;border-radius:8px;padding:8px"></p>
                  <div style="margin-top:12px">
                    <div style="font-size:12px;color:#3f6212;font-weight:700;letter-spacing:.02em;text-transform:uppercase">Latest Headlines</div>
                    <div id="adviceHeadlines" style="display:grid;gap:8px;margin-top:8px"></div>
                  </div>
                </div>
              </div>
              <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
              <script>
              const styleTag = document.createElement('style');
              styleTag.textContent = '@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }';
              document.head.appendChild(styleTag);

              const TAB_SESSION_KEY = 'moneybot_tab_session_id';
              function getTabSessionId(){
                return sessionStorage.getItem(TAB_SESSION_KEY) || localStorage.getItem(TAB_SESSION_KEY) || '';
              }
              async function apiFetch(url, options = {}){
                const tabSessionId = getTabSessionId();
                if(!tabSessionId){
                  location.href = '/login';
                  throw new Error('missing tab session');
                }
                const headers = Object.assign({}, options.headers || {}, {'X-Tab-Session-Id': tabSessionId});
                const response = await fetch(url, Object.assign({}, options, {headers}));
                if(response.status === 401){
                  sessionStorage.removeItem(TAB_SESSION_KEY);
                  localStorage.removeItem(TAB_SESSION_KEY);
                  location.href = '/login';
                }
                return response;
              }

              function normalizeTickerInputValue(inputEl){
                if(!inputEl) return '';
                const normalized = String(inputEl.value || '').toUpperCase();
                if(inputEl.value !== normalized){
                  inputEl.value = normalized;
                }
                return normalized.trim();
              }

              const rowsEl = document.getElementById('rows');
              const soldRowsEl = document.getElementById('soldRows');
              const lifetimePanelEl = document.getElementById('lifetimePanel');
              const lifetimeTotalEl = document.getElementById('lifetimeTotal');
              const toggleLifetimeBtnEl = document.getElementById('toggleLifetimeBtn');
              const outEl = document.getElementById('out');
              const trustedDeviceEl = document.getElementById('trustedDevice');
              const loadingStateEl = document.getElementById('loadingState');
              const symbolEl = document.getElementById('symbol');
              const buyPriceEl = document.getElementById('buy_price');
              const sharesEl = document.getElementById('shares');
              symbolEl.addEventListener('input', (event) => normalizeTickerInputValue(event.target));
              let currentPortfolioItems = [];
              let currentAdviceContext = null;
              document.getElementById('addForm').addEventListener('submit', addItem);

              async function logout(){ await apiFetch('/api/auth/logout',{method:'POST'}); sessionStorage.removeItem(TAB_SESSION_KEY); localStorage.removeItem(TAB_SESSION_KEY); location.href='/'; }
              function setLoading(isLoading){ loadingStateEl.style.display = isLoading ? 'flex' : 'none'; }
              async function refreshPortfolio(){
                const refreshBtn = document.getElementById('refreshPortfolioBtn');
                if(refreshBtn){
                  refreshBtn.disabled = true;
                  refreshBtn.style.opacity = '.75';
                  refreshBtn.textContent = 'Refreshing...';
                }
                outEl.textContent = 'Refreshing portfolio data...';
                try {
                  await load();
                  outEl.textContent = 'Portfolio refreshed.';
                } finally {
                  if(refreshBtn){
                    refreshBtn.disabled = false;
                    refreshBtn.style.opacity = '1';
                    refreshBtn.textContent = 'Refresh Portfolio';
                  }
                }
              }
              function displayValue(value){
                return (value === null || value === undefined || value === '') ? 'n/a' : value;
              }
              function formatMoney(v){
                return (typeof v === 'number' && isFinite(v)) ? ('$' + v.toLocaleString(undefined,{maximumFractionDigits:2})) : 'n/a';
              }
              function adviceBadge(value){
                const advice = String(value || 'HOLD').toUpperCase();
                const color = advice === 'BUY' ? '#166534' : (advice === 'SELL' ? '#4d7c0f' : '#3f3f46');
                return `<span style="display:inline-block;padding:4px 8px;border-radius:999px;background:${color};color:#f0fdf4;font-weight:700;font-size:12px">${advice}</span>`;
              }
              function adviceButton(item, idx){
                return `<button onclick="showAdvice(${idx})" title="Click to see why this advice was generated" style="border:none;background:none;padding:0;cursor:pointer">${adviceBadge(item.advice)}</button>`;
              }
              function escapeHtml(value){
                return String(value || '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch] || ch));
              }
              function openAdviceModal(){ document.getElementById('adviceModal').style.display='flex'; }
              function closeAdviceModal(){ document.getElementById('adviceModal').style.display='none'; }
              async function showAdvice(idx){
                const item = currentPortfolioItems[idx] || {};
                const symbol = item.symbol || '';
                const advice = String(item.advice || 'HOLD').toUpperCase();
                const reason = item.advice_reason || 'Rule-based recommendation from technical momentum and sentiment checks.';
                const aiPortfolio = (item && typeof item.ai_portfolio === 'object' && item.ai_portfolio) ? item.ai_portfolio : {};
                const suitability = (item && typeof item.suitability === 'object' && item.suitability) ? item.suitability : {};
                const profileRules = Array.isArray(suitability.applied_rules) ? suitability.applied_rules : [];
                const mode = String(aiPortfolio.mode || 'rule_based').replaceAll('_', ' ');
                const riskNotes = Array.isArray(aiPortfolio.risk_notes) ? aiPortfolio.risk_notes : [];
                const nextChecks = Array.isArray(aiPortfolio.next_checks) ? aiPortfolio.next_checks : [];
                const topRisk = riskNotes[0] || 'Keep strict risk controls and position sizing.';
                const topCheck = nextChecks[0] || 'Recheck trend and sentiment before changing your position size.';
                currentAdviceContext = { symbol, advice, reason };
                document.getElementById('adviceTitle').textContent = `${symbol} · ${advice} rationale`;
                document.getElementById('adviceReason').innerHTML = `
                  <strong style="display:block;color:#bbf7d0;margin-bottom:6px">${escapeHtml(symbol)} · ${escapeHtml(mode)}</strong>
                  <ul style="margin:0;padding-left:18px;display:grid;gap:4px;color:#dcfce7">
                    <li>${escapeHtml(reason)}</li>
                    ${item.base_advice && item.base_advice !== item.advice ? `<li><strong>Base market action:</strong> ${escapeHtml(item.base_advice)} → <strong>Profile-aware action:</strong> ${escapeHtml(item.advice)}</li>` : ''}
                    ${profileRules.map((rule) => `<li><strong>Profile rule:</strong> ${escapeHtml(rule.message || rule.code)}</li>`).join('')}
                    ${profileRules.length ? '<li><a href="/settings" style="color:#bbf7d0;font-weight:800">Review investor profile settings</a></li>' : ''}
                    <li><strong>Risk:</strong> ${escapeHtml(topRisk)}</li>
                    <li><strong>Next:</strong> ${escapeHtml(topCheck)}</li>
                  </ul>`;
                const plainEnglishEl = document.getElementById('plainEnglishExplanation');
                plainEnglishEl.style.display = 'block';
                plainEnglishEl.textContent = buildPlainEnglishExplanation(advice, reason);
                const headlinesEl = document.getElementById('adviceHeadlines');
                headlinesEl.innerHTML = '<div style="color:#3f6212">Loading latest headlines...</div>';
                openAdviceModal();
                if(!symbol){
                  headlinesEl.innerHTML = '<div style="color:#3f6212">No recent headlines available.</div>';
                  return;
                }
                try {
                  const res = await apiFetch('/api/company-details?symbol=' + encodeURIComponent(symbol));
                  const payload = await res.json();
                  if(!res.ok){
                    if (res.status === 401) { location.href='/login'; return; }
                    headlinesEl.innerHTML = '<div style="color:#3f6212">No recent headlines available.</div>';
                    return;
                  }
                  const news = (payload.data && payload.data.latest_news) || [];
                  headlinesEl.innerHTML = news.length ? news.map(n => `<a href="${n.link || '#'}" target="_blank" rel="noopener" style="display:block;padding:8px;border:1px solid #d1fae5;border-radius:8px;text-decoration:none;color:#0f172a"><div style="font-weight:600">${n.title || 'Story'}</div><div style="font-size:12px;color:#3f6212">${n.publisher || 'Source unavailable'}</div></a>`).join('') : '<div style="color:#3f6212">No recent headlines available.</div>';
                } catch (err) {
                  headlinesEl.innerHTML = '<div style="color:#3f6212">Unable to load headlines right now.</div>';
                }
              }
              function performanceCell(amount, pct){
                if(typeof amount !== 'number' || typeof pct !== 'number') return '<span style="color:#3f6212">n/a</span>';
                const up = amount >= 0;
                const color = up ? '#166534' : '#dc2626';
                const sign = up ? '+' : '';
                return `<div style="color:${color};font-weight:700">${sign}${formatMoney(amount)}</div><div style="color:${color};font-size:12px">(${sign}${pct.toFixed(2)}%)</div>`;
              }
              function amountCell(amount){
                if(typeof amount !== 'number') return '<span style="color:#3f6212">n/a</span>';
                const up = amount >= 0;
                const color = up ? '#166534' : '#dc2626';
                const sign = up ? '+' : '';
                return `<div style="color:${color};font-weight:700">${sign}${formatMoney(amount)}</div>`;
              }
              function renderTrend(divId, series){
                if(!window.Plotly) return;
                if(!Array.isArray(series) || series.length < 2){
                  const el = document.getElementById(divId); if(el) el.innerHTML='<span style="color:#94a3b8">No trend data</span>'; return;
                }
                const up = series[series.length-1] >= series[0];
                Plotly.newPlot(divId,[{y:series,mode:'lines',type:'scatter',line:{color:up?'#16a34a':'#dc2626',width:2},hoverinfo:'skip'}],{margin:{l:2,r:2,t:2,b:2},height:30,width:100,showlegend:false,xaxis:{visible:false,fixedrange:true},yaxis:{visible:false,fixedrange:true},paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)'},{displayModeBar:false,responsive:true,staticPlot:true});
              }


              function tickerButton(symbol){
                return `<button onclick="showCompanyDetails('${symbol}')" style="border:none;background:none;color:#15803d;font-weight:700;cursor:pointer;font-size:15px;padding:0">${symbol}</button>`;
              }
              function openModal(){ document.getElementById('tickerModal').style.display='flex'; }
              function closeModal(){ document.getElementById('tickerModal').style.display='none'; }
              async function showCompanyDetails(symbol){
                const titleEl = document.getElementById('modalTitle');
                const summaryEl = document.getElementById('modalSummary');
                const newsEl = document.getElementById('modalNews');
                titleEl.textContent = `${symbol} · Loading...`;
                summaryEl.textContent = 'Fetching company profile...';
                newsEl.innerHTML = '';
                openModal();
                try {
                  const res = await apiFetch('/api/company-details?symbol=' + encodeURIComponent(symbol));
                  const payload = await res.json();
                  if(!res.ok){
                    if (res.status === 401) { location.href='/login'; return; }
                    titleEl.textContent = symbol;
                    const err = String(payload.error || '');
                    summaryEl.textContent = err === 'authentication required' ? 'Please log in to view company details.' : (payload.error || 'Unable to load company details.');
                    return;
                  }
                  const data = payload.data || {};
                  titleEl.textContent = `${data.company_name || symbol} (${symbol})`;
                  summaryEl.textContent = data.summary || 'No summary available.';
                  const news = data.latest_news || [];
                  newsEl.innerHTML = news.length ? news.map(n => `<a href="${n.link || '#'}" target="_blank" rel="noopener" style="display:block;padding:8px;border:1px solid #d1fae5;border-radius:8px;text-decoration:none;color:#0f172a"><div style="font-weight:600">${n.title || 'Story'}</div><div style="font-size:12px;color:#3f6212">${n.publisher || 'Source unavailable'}</div></a>`).join('') : '<div style="color:#3f6212">No recent news available.</div>';
                } catch (err) {
                  titleEl.textContent = symbol;
                  summaryEl.textContent = 'Unable to load company details right now.';
                }
              }

              function humanizeReason(reason){
                const text = String(reason || 'signals are mixed').trim();
                return text
                  .replace(/MACD/gi, 'trend momentum')
                  .replace(/RSI/gi, 'price pressure')
                  .replace(/hist/gi, 'trend strength')
                  .replace(/\bpts\b/gi, 'points')
                  .replace(/bullish/gi, 'positive')
                  .replace(/bearish/gi, 'negative');
              }

              function buildPlainEnglishExplanation(advice, reason){
                const rec = String(advice || 'HOLD').toUpperCase();
                const friendlyReason = humanizeReason(reason).toLowerCase();
                let action = 'There is no clear edge right now, so holding is safer';
                if(rec === 'STRONG BUY') action = 'This looks like a strong buying setup';
                else if(rec === 'BUY') action = 'This looks reasonable to buy';
                else if(rec === 'SELL') action = 'This looks like a good time to trim or sell';
                else if(rec === 'HOLD OFF FOR NOW') action = 'It is better to wait instead of buying right now';
                return `${action}. The system saw ${friendlyReason}. This is guidance only, not financial advice.`;
              }

              function explainAdviceInPlainEnglish(){
                const loadingEl = document.getElementById('plainEnglishLoading');
                const explanationEl = document.getElementById('plainEnglishExplanation');
                if(!currentAdviceContext){
                  explanationEl.style.display = 'block';
                  explanationEl.textContent = 'Open an advice card first.';
                  return;
                }
                loadingEl.style.display = 'inline';
                explanationEl.style.display = 'block';
                explanationEl.textContent = buildPlainEnglishExplanation(currentAdviceContext.advice, currentAdviceContext.reason);
                loadingEl.style.display = 'none';
              }

              function selectPortfolioRows(data){
                const enriched = Array.isArray(data && data.enriched_items) ? data.enriched_items : [];
                const base = Array.isArray(data && data.items) ? data.items : [];
                return enriched.length ? enriched : base;
              }

              function renderRows(items){
                const safeItems = Array.isArray(items) ? items : [];
                if(!safeItems.length){
                  rowsEl.innerHTML = '<tr><td colspan="10" style="padding:8px;color:#3f6212">No watchlist entries yet.</td></tr>';
                  currentPortfolioItems = [];
                  return;
                }
                currentPortfolioItems = safeItems;
                items = safeItems;
                const totalValue = items.reduce((sum, item) => {
                  const price = typeof item.current_price === 'number' ? item.current_price : 0;
                  const shares = typeof item.shares === 'number' ? item.shares : 1;
                  return sum + (price * shares);
                }, 0);
                const totalTodayChange = items.reduce((sum, item) => sum + (typeof item.today_change_amount === 'number' ? item.today_change_amount : 0), 0);
                const totalPerformance = items.reduce((sum, item) => sum + (typeof item.performance_amount === 'number' ? item.performance_amount : 0), 0);

                rowsEl.innerHTML = items.map((i,idx)=>`<tr><td style="border:1px solid #e5e7eb;padding:8px;font-size:15px">${tickerButton(i.symbol)}</td><td style="border:1px solid #e5e7eb;padding:8px">${formatMoney(i.entry_price)}</td><td style="border:1px solid #e5e7eb;padding:8px">${displayValue(i.shares)}</td><td style="border:1px solid #e5e7eb;padding:8px">${formatMoney(i.current_price)}</td><td style="border:1px solid #e5e7eb;padding:8px">${performanceCell(i.today_change_amount, i.today_change_percent)}</td><td style="border:1px solid #e5e7eb;padding:8px">${performanceCell(i.performance_amount, i.performance_percent)}</td><td style="border:1px solid #e5e7eb;padding:8px"><div id="trend-${idx}" style="width:100px;height:30px"></div></td><td style="border:1px solid #e5e7eb;padding:8px">${displayValue(i.score)}</td><td style="border:1px solid #e5e7eb;padding:8px">${adviceButton(i, idx)}</td><td style="border:1px solid #e5e7eb;padding:8px"><div style="display:flex;gap:6px;flex-wrap:wrap"><button onclick="markBought(${i.id})" style="border:none;background:#16a34a;color:#f0fdf4;padding:6px 10px;border-radius:8px;font-weight:600;cursor:pointer">Buy</button><button onclick="markSold(${i.id})" style="border:none;background:#15803d;color:#f0fdf4;padding:6px 10px;border-radius:8px;font-weight:600;cursor:pointer">Sold</button><button onclick="editRow(${i.id})" style="border:none;background:#65a30d;color:#f0fdf4;padding:6px 10px;border-radius:8px;font-weight:600;cursor:pointer">Edit</button></div></td></tr>`).join('')
                + `<tr style="background:#f7fee7;font-weight:700"><td style="border:1px solid #e5e7eb;padding:8px">Totals</td><td style="border:1px solid #e5e7eb;padding:8px"></td><td style="border:1px solid #e5e7eb;padding:8px">${formatMoney(totalValue)}</td><td style="border:1px solid #e5e7eb;padding:8px"></td><td style="border:1px solid #e5e7eb;padding:8px">${amountCell(totalTodayChange)}</td><td style="border:1px solid #e5e7eb;padding:8px">${amountCell(totalPerformance)}</td><td style="border:1px solid #e5e7eb;padding:8px"></td><td style="border:1px solid #e5e7eb;padding:8px"></td><td style="border:1px solid #e5e7eb;padding:8px;color:#3f3f46;font-size:12px">Click advice badges to see why.</td><td style="border:1px solid #e5e7eb;padding:8px"></td></tr>`;
                items.forEach((item, idx)=> renderTrend(`trend-${idx}`, item.history30 || []));
              }

              async function load(){
                setLoading(true);
                try {
                  const res = await apiFetch('/api/user-watchlist');
                  let data = {};
                  try {
                    data = await res.json();
                  } catch (jsonErr) {
                    data = {};
                  }
                  if(!res.ok){
                    if (res.status === 401) { location.href='/login'; return; }
                    rowsEl.innerHTML = '<tr><td colspan="10" style="padding:8px;color:#4d7c0f">Unable to load watchlist right now.</td></tr>';
                    outEl.textContent = data.error || 'Please try again in a moment.';
                    return;
                  }
                  renderRows(selectPortfolioRows(data));
                  if (lifetimePanelEl.style.display !== 'none') {
                    await loadSoldTrades();
                  }
                } catch (err) {
                  rowsEl.innerHTML = '<tr><td colspan="10" style="padding:8px;color:#4d7c0f">Unable to load portfolio rows right now. Please refresh.</td></tr>';
                  outEl.textContent = 'Portfolio data did not load completely. Please refresh in a moment.';
                } finally {
                  setLoading(false);
                }
              }
              async function addItem(event){
                if (event) event.preventDefault();
                const payload = { symbol:normalizeTickerInputValue(symbolEl), buy_price:buyPriceEl.value||null, shares:sharesEl.value||null };
                const res = await apiFetch('/api/user-watchlist',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
                const data = await res.json();
                if (res.ok) {
                  outEl.textContent = 'Watchlist item added.';
                  symbolEl.value=''; buyPriceEl.value=''; sharesEl.value='';
                  await load();
                } else {
                  outEl.textContent = data.error || 'Unable to add item.';
                }
              }


              function amountColor(v){
                if (typeof v !== 'number') return '#3f3f46';
                if (v > 0) return '#166534';
                if (v < 0) return '#b91c1c';
                return '#3f3f46';
              }

              function formatDate(iso){
                if(!iso) return 'n/a';
                const d = new Date(iso);
                return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString();
              }

              function renderSoldTrades(items, totalRealized){
                lifetimeTotalEl.textContent = formatMoney(totalRealized || 0);
                lifetimeTotalEl.style.color = amountColor(totalRealized);
                if(!items || !items.length){
                  soldRowsEl.innerHTML = '<tr><td colspan="7" style="padding:8px;color:#3f6212">No sold trades yet.</td></tr>';
                  return;
                }
                soldRowsEl.innerHTML = items.map((item)=>`<tr><td style="border:1px solid #e5e7eb;padding:8px">${formatDate(item.sold_at)}</td><td style="border:1px solid #e5e7eb;padding:8px">${displayValue(item.symbol)}</td><td style="border:1px solid #e5e7eb;padding:8px">${formatMoney(item.entry_price)}</td><td style="border:1px solid #e5e7eb;padding:8px">${formatMoney(item.sold_price)}</td><td style="border:1px solid #e5e7eb;padding:8px">${displayValue(item.shares_sold)}</td><td style="border:1px solid #e5e7eb;padding:8px;color:${amountColor(item.realized_amount)}">${formatMoney(item.realized_amount)}</td><td style="border:1px solid #e5e7eb;padding:8px"><button onclick="editSoldTrade(${item.id})" style="border:none;background:#16a34a;color:#f0fdf4;padding:6px 10px;border-radius:8px;font-weight:700;cursor:pointer">Edit</button></td></tr>`).join('');
              }

              async function editSoldTrade(id){
                const res = await apiFetch('/api/sold-trades');
                const soldData = await res.json();
                if(!res.ok){
                  outEl.textContent = soldData.error || 'Unable to load sold trade.';
                  return;
                }
                const item = (soldData.items || []).find((entry)=> entry.id === id);
                if(!item){
                  outEl.textContent = 'Unable to find sold trade.';
                  return;
                }
                const soldPriceRaw = prompt(`Correct sold price for ${item.symbol}:`, item.sold_price ?? '');
                if(soldPriceRaw === null) return;
                const soldPrice = Number(soldPriceRaw);
                if(!Number.isFinite(soldPrice) || soldPrice <= 0){
                  outEl.textContent = 'Sold price must be a positive number.';
                  return;
                }
                const sharesRaw = prompt(`Correct shares sold for ${item.symbol}:`, item.shares_sold ?? '');
                if(sharesRaw === null) return;
                const sharesSold = Number(sharesRaw);
                if(!Number.isFinite(sharesSold) || sharesSold <= 0){
                  outEl.textContent = 'Shares sold must be a positive number.';
                  return;
                }
                const updateRes = await apiFetch('/api/sold-trades/' + id, {
                  method:'PATCH',
                  headers:{'Content-Type':'application/json'},
                  body:JSON.stringify({ sold_price:soldPrice, shares_sold:sharesSold })
                });
                const updateData = await updateRes.json();
                if(!updateRes.ok){
                  outEl.textContent = updateData.error || 'Unable to update sold trade.';
                  return;
                }
                const realized = updateData.sold_trade && typeof updateData.sold_trade.realized_amount === 'number' ? updateData.sold_trade.realized_amount : 0;
                const adjustmentNote = updateData.portfolio_adjustment_note ? ` ${updateData.portfolio_adjustment_note}` : '';
                outEl.textContent = `Sold trade updated (${formatMoney(realized)} realized).${adjustmentNote}`;
                await load();
                await loadSoldTrades();
              }

              async function loadSoldTrades(){
                const res = await apiFetch('/api/sold-trades');
                const data = await res.json();
                if(!res.ok){
                  if (res.status === 401) { location.href='/login'; return; }
                  outEl.textContent = data.error || 'Unable to load sold trades.';
                  return;
                }
                renderSoldTrades(data.items || [], data.total_realized || 0);
              }

              function toggleLifetime(){
                const isOpen = lifetimePanelEl.style.display !== 'none';
                if(isOpen){
                  lifetimePanelEl.style.display = 'none';
                  toggleLifetimeBtnEl.textContent = 'Show Lifetime Gains/Losses';
                } else {
                  lifetimePanelEl.style.display = 'block';
                  toggleLifetimeBtnEl.textContent = 'Hide Lifetime Gains/Losses';
                  loadSoldTrades();
                }
              }

              async function markSold(id){
                const item = currentPortfolioItems.find((entry)=> entry.id === id);
                if(!item){
                  outEl.textContent = 'Unable to find portfolio item.';
                  return;
                }
                const soldPriceRaw = prompt(`What price did you sell ${item.symbol} at?`);
                if(soldPriceRaw === null) return;
                const soldPrice = Number(soldPriceRaw);
                if(!Number.isFinite(soldPrice) || soldPrice <= 0){
                  outEl.textContent = 'Sold price must be a positive number.';
                  return;
                }

                const sharesRaw = prompt(`How many shares of ${item.symbol} did you sell? (Current: ${displayValue(item.shares)})`);
                if(sharesRaw === null) return;
                const sharesSold = Number(sharesRaw);
                if(!Number.isFinite(sharesSold) || sharesSold <= 0){
                  outEl.textContent = 'Shares sold must be a positive number.';
                  return;
                }

                const res = await apiFetch('/api/user-watchlist/' + id + '/sell', {
                  method:'POST',
                  headers:{'Content-Type':'application/json'},
                  body:JSON.stringify({ sold_price:soldPrice, shares_sold:sharesSold })
                });
                const data = await res.json();
                if(!res.ok){
                  outEl.textContent = data.error || 'Unable to record sold trade.';
                  return;
                }
                const realized = data.sold_trade && typeof data.sold_trade.realized_amount === 'number' ? data.sold_trade.realized_amount : 0;
                outEl.textContent = `Sold trade recorded (${formatMoney(realized)} realized).`;
                await load();
                if (lifetimePanelEl.style.display !== 'none') {
                  await loadSoldTrades();
                }
              }

              async function markBought(id){
                const item = currentPortfolioItems.find((entry)=> entry.id === id);
                if(!item){
                  outEl.textContent = 'Unable to find portfolio item.';
                  return;
                }
                const boughtPriceRaw = prompt(`What price did you buy more ${item.symbol} at?`);
                if(boughtPriceRaw === null) return;
                const boughtPrice = Number(boughtPriceRaw);
                if(!Number.isFinite(boughtPrice) || boughtPrice <= 0){
                  outEl.textContent = 'Bought price must be a positive number.';
                  return;
                }

                const sharesRaw = prompt(`How many shares of ${item.symbol} did you buy? (Current: ${displayValue(item.shares)})`);
                if(sharesRaw === null) return;
                const sharesBought = Number(sharesRaw);
                if(!Number.isFinite(sharesBought) || sharesBought <= 0){
                  outEl.textContent = 'Shares bought must be a positive number.';
                  return;
                }

                const res = await apiFetch('/api/user-watchlist/' + id + '/buy', {
                  method:'POST',
                  headers:{'Content-Type':'application/json'},
                  body:JSON.stringify({ bought_price:boughtPrice, shares_bought:sharesBought })
                });
                const data = await res.json();
                if(!res.ok){
                  outEl.textContent = data.error || 'Unable to record buy trade.';
                  return;
                }
                const newEntry = data.added && typeof data.added.new_entry_price === 'number' ? data.added.new_entry_price : null;
                outEl.textContent = newEntry === null
                  ? 'Buy trade recorded.'
                  : `Buy trade recorded (new avg entry ${formatMoney(newEntry)}).`;
                await load();
              }

              async function del(id){
                const res = await apiFetch('/api/user-watchlist/'+id,{method:'DELETE'});
                if(!res.ok){
                  const data = await res.json();
                  outEl.textContent = data.error || 'Unable to remove this row.';
                  return;
                }
                outEl.textContent = 'Row deleted.';
                await load();
              }
              async function editRow(id){
                const item = currentPortfolioItems.find((entry)=> entry.id === id);
                if(!item){
                  outEl.textContent = 'Unable to find portfolio item.';
                  return;
                }
                const mode = (prompt('Type UPDATE to edit row, or DELETE to remove it.', 'UPDATE') || '').trim().toUpperCase();
                if(!mode) return;
                if(mode === 'DELETE'){
                  if(!confirm(`Delete ${item.symbol} from your portfolio?`)) return;
                  await del(id);
                  return;
                }
                if(mode !== 'UPDATE'){
                  outEl.textContent = 'No changes made.';
                  return;
                }
                const nextSymbol = (prompt('Ticker symbol:', String(item.symbol || '')) || '').trim().toUpperCase();
                if(!nextSymbol){
                  outEl.textContent = 'Ticker symbol is required.';
                  return;
                }
                const nextEntryRaw = (prompt('Entry price (blank clears value):', item.entry_price ?? '') || '').trim();
                const nextSharesRaw = (prompt('Shares (blank clears value):', item.shares ?? '') || '').trim();
                const nextEntry = nextEntryRaw === '' ? null : Number(nextEntryRaw);
                const nextShares = nextSharesRaw === '' ? null : Number(nextSharesRaw);
                if((nextEntryRaw !== '' && (!Number.isFinite(nextEntry) || nextEntry <= 0)) || (nextSharesRaw !== '' && (!Number.isFinite(nextShares) || nextShares <= 0))){
                  outEl.textContent = 'Entry price and shares must be positive numbers when provided.';
                  return;
                }
                const res = await apiFetch('/api/user-watchlist/' + id, {
                  method:'PATCH',
                  headers:{'Content-Type':'application/json'},
                  body:JSON.stringify({ symbol: nextSymbol, buy_price: nextEntry, shares: nextShares })
                });
                const data = await res.json();
                if(!res.ok){
                  outEl.textContent = data.error || 'Unable to update row.';
                  return;
                }
                outEl.textContent = 'Row updated.';
                await load();
              }
              document.getElementById('tickerModal').addEventListener('click', (event) => { if(event.target.id==='tickerModal'){ closeModal(); }});
              document.getElementById('adviceModal').addEventListener('click', (event) => { if(event.target.id==='adviceModal'){ closeAdviceModal(); }});
              load();
              </script>
            </body></html>
            """
        )

    return app
