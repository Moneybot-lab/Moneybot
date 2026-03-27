from __future__ import annotations

import logging
import smtplib
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit
from collections import defaultdict, deque
from decimal import Decimal
from email.message import EmailMessage
from functools import wraps
from typing import Any, Dict, Tuple

import yfinance as yf
from flask import Blueprint, current_app, g, jsonify, request, session, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

from advice_engine import compute_user_advice

from .extensions import db
from .models import SoldTrade, User, WatchlistItem
from .services.decision_log import read_decision_events, summarize_decision_events
from .services.model_metadata import load_artifact_history, load_artifact_metadata
from .services.outcome_tracking import (
    close_values,
    evaluate_decision_events,
    summarize_outcome_groups,
    summarize_outcome_rows,
)


api_bp = Blueprint("api", __name__, url_prefix="/api")




def _password_reset_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="moneybot-password-reset")


def _build_password_reset_link(user: User) -> str:
    token = _password_reset_serializer().dumps({"user_id": user.id})
    base_url = (current_app.config.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if base_url:
        return f"{base_url}/reset-password?token={token}"
    return url_for("reset_password_page", token=token, _external=True)




def _password_reset_email_configured() -> bool:
    smtp_host = (current_app.config.get("SMTP_HOST") or "").strip()
    from_email = (current_app.config.get("PASSWORD_RESET_FROM_EMAIL") or current_app.config.get("SMTP_USER") or "").strip()
    return bool(smtp_host and from_email)

def _send_reset_email(email: str, reset_link: str) -> bool:
    smtp_host = (current_app.config.get("SMTP_HOST") or "").strip()
    smtp_port = int(current_app.config.get("SMTP_PORT") or 587)
    smtp_user = (current_app.config.get("SMTP_USER") or "").strip()
    smtp_password = current_app.config.get("SMTP_PASSWORD") or ""
    smtp_use_tls = bool(current_app.config.get("SMTP_USE_TLS", True))
    smtp_use_ssl = bool(current_app.config.get("SMTP_USE_SSL", False))
    from_email = (current_app.config.get("PASSWORD_RESET_FROM_EMAIL") or smtp_user or "").strip()

    if not _password_reset_email_configured():
        logging.warning("Password reset email not sent: SMTP_HOST or sender email is not configured.")
        return False

    msg = EmailMessage()
    msg["Subject"] = "Reset your Moneybot password"
    msg["From"] = from_email
    msg["To"] = email
    msg.set_content(
        "We received a request to reset your Moneybot password.\n\n"
        f"Reset it here: {reset_link}\n\n"
        "If you did not request this, you can safely ignore this email."
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
        logging.exception("Failed to send password reset email.")
        return False


def _decode_password_reset_token(token: str) -> int | None:
    max_age = int(current_app.config.get("PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS") or 3600)
    try:
        payload = _password_reset_serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    user_id = payload.get("user_id") if isinstance(payload, dict) else None
    return int(user_id) if isinstance(user_id, int) else None

def _to_decimal(v: Any) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return None


def _normalize_symbol(raw_symbol: str) -> str:
    raw = (raw_symbol or "").strip()
    if not raw:
        return ""

    parsed = urlsplit(raw)
    if parsed.query:
        query = parse_qs(parsed.query, keep_blank_values=True)
        for key, values in query.items():
            if key.lower() == "symbol" and values:
                raw = str(values[0] or "")
                break

    lowered = raw.lower()
    if "symbol=" in lowered:
        idx = lowered.rfind("symbol=")
        raw = raw[idx + len("symbol="):].split("&", 1)[0]

    raw = raw.split("/", 1)[0] if "?" not in raw and "/" in raw else raw
    cleaned = "".join(ch for ch in raw.upper() if ch.isalnum() or ch in {"^", "-", ".", "="})
    return cleaned[:15]


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "authentication required"}), 401
        if session.get("requires_tab_session"):
            tab_session_id = session.get("tab_session_id")
            request_tab_session_id = request.headers.get("X-Tab-Session-Id") or ""
            if not tab_session_id or tab_session_id != request_tab_session_id:
                session.clear()
                return jsonify({"error": "authentication required"}), 401
        return view(*args, **kwargs)

    return wrapped


@api_bp.before_app_request
def _request_context_setup():
    g.request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())


_RATE: dict[Tuple[str, str], deque] = defaultdict(deque)
WINDOW_SECONDS = 60
MAX_REQUESTS_PER_WINDOW = 120


@api_bp.before_request
def _basic_rate_limit():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(","
    )[0].strip()
    key = (ip, request.endpoint or "")
    now = time.time()
    dq = _RATE[key]
    while dq and now - dq[0] > WINDOW_SECONDS:
        dq.popleft()
    if len(dq) >= MAX_REQUESTS_PER_WINDOW:
        return jsonify({"error": "rate limit exceeded", "request_id": g.request_id}), 429
    dq.append(now)


@api_bp.after_request
def _attach_request_id(response):
    response.headers["X-Request-ID"] = g.request_id
    return response


def _user_payload(user: User) -> Dict[str, Any]:
    return {"id": user.id, "email": user.email, "created_at": user.created_at.isoformat()}


def _watchlist_item_payload(item: WatchlistItem) -> Dict[str, Any]:
    return {
        "id": item.id,
        "symbol": item.symbol,
        "company": item.company,
        "entry_price": float(item.buy_price) if item.buy_price is not None else None,
        "shares": float(item.shares) if item.shares is not None else None,
        "created_at": item.created_at.isoformat(),
    }




def _sold_trade_payload(item: SoldTrade) -> Dict[str, Any]:
    return {
        "id": item.id,
        "symbol": item.symbol,
        "shares_sold": float(item.shares_sold),
        "sold_price": float(item.sold_price),
        "entry_price": float(item.entry_price),
        "realized_amount": float(item.realized_amount),
        "sold_at": item.sold_at.isoformat(),
    }


def _quick_decision(signal_data: Dict[str, Any], quote_data: Dict[str, Any]) -> Dict[str, Any]:
    action = (signal_data.get("action") or "HOLD").upper()
    technical = signal_data.get("technical") or {}
    sentiment = signal_data.get("sentiment") or {}

    rsi = technical.get("rsi")
    macd = technical.get("macd_histogram")
    sentiment_score = sentiment.get("score")
    sentiment_label = (sentiment.get("label") or "neutral").lower()

    strong_buy_signal = action == "STRONG BUY" or (
        isinstance(rsi, (int, float))
        and rsi <= 35
        and isinstance(sentiment_score, (int, float))
        and sentiment_score >= 0.7
    )
    buy_signal = action == "BUY" or (
        isinstance(rsi, (int, float))
        and rsi < 55
        and sentiment_label in {"positive", "bullish"}
        and (not isinstance(macd, (int, float)) or macd >= 0)
    )

    if strong_buy_signal:
        recommendation = "STRONG BUY"
    elif buy_signal:
        recommendation = "BUY"
    else:
        recommendation = "HOLD OFF FOR NOW"

    rationale = signal_data.get("reasons") or signal_data.get("rationale") or []
    short_reason = rationale[0] if rationale else "Derived from momentum and signal checks."
    return {
        "recommendation": recommendation,
        "rationale": short_reason,
        "current_price": quote_data.get("price"),
        "change_percent": quote_data.get("change_percent"),
        "quote_source": quote_data.get("quote_source"),
        "quote_diagnostics": quote_data.get("diagnostics"),
        "decision_source": "rule_based",
    }


def _plain_english_recommendation(recommendation: str, reason: str) -> str:
    rec = (recommendation or "HOLD").strip().upper()
    raw_reason = (reason or "Signals are mixed right now.").strip()

    reason_text = raw_reason
    normalized_replacements = [
        ("macd", "trend momentum"),
        ("rsi", "price pressure"),
        ("hist", "trend strength"),
        ("pts", "points"),
        ("bullish", "positive"),
        ("bearish", "negative"),
    ]
    lowered = reason_text.lower()
    for source, target in normalized_replacements:
        lowered = lowered.replace(source, target)
    reason_text = lowered

    if rec == "STRONG BUY":
        action = "This looks like a strong buying setup"
    elif rec == "BUY":
        action = "This looks reasonable to buy"
    elif rec == "SELL":
        action = "This looks like a good time to trim or sell"
    elif rec == "HOLD OFF FOR NOW":
        action = "It is better to wait instead of buying right now"
    else:
        action = "There is no clear edge right now, so holding is safer"

    return (
        f"{action}. Plain English: the system saw {reason_text}. "
        "This is guidance only, not financial advice."
    )


@api_bp.post("/auth/signup")
def signup():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    tab_session_id = (data.get("tab_session_id") or "").strip()
    password_confirmation = data.get("password_confirmation")
    if not email or not password:
        return jsonify({"error": "email and password required", "request_id": g.request_id}), 400
    if password_confirmation is not None and password != password_confirmation:
        return jsonify({"error": "passwords do not match", "request_id": g.request_id}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({"error": "email already exists", "request_id": g.request_id}), 409

    user = User(email=email, password_hash=generate_password_hash(password))
    db.session.add(user)
    db.session.commit()

    session["user_id"] = user.id
    session["tab_session_id"] = tab_session_id
    session["requires_tab_session"] = bool(tab_session_id)
    return jsonify({"user": _user_payload(user), "request_id": g.request_id}), 201


@api_bp.post("/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    tab_session_id = (data.get("tab_session_id") or "").strip()


    user = User.query.filter_by(email=email).first()
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({"error": "invalid credentials", "request_id": g.request_id}), 401

    session["user_id"] = user.id
    session["tab_session_id"] = tab_session_id
    session["requires_tab_session"] = bool(tab_session_id)
    return jsonify({"user": _user_payload(user), "request_id": g.request_id})




@api_bp.post("/auth/forgot-password")
def forgot_password():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"error": "email required", "request_id": g.request_id}), 400

    user = User.query.filter_by(email=email).first()
    if user:
        reset_link = _build_password_reset_link(user)
        _send_reset_email(email, reset_link)

    email_delivery_configured = _password_reset_email_configured()

    # Avoid user-enumeration: always return the same response message.
    return jsonify({
        "ok": True,
        "message": "If an account exists for that email, password recovery instructions have been sent.",
        "email_delivery_configured": email_delivery_configured,
        "request_id": g.request_id,
    })


@api_bp.post("/auth/reset-password")
def reset_password():
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    new_password = data.get("password") or ""

    if not token or not new_password:
        return jsonify({"error": "token and password required", "request_id": g.request_id}), 400

    user_id = _decode_password_reset_token(token)
    if user_id is None:
        return jsonify({"error": "invalid or expired token", "request_id": g.request_id}), 400

    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "invalid or expired token", "request_id": g.request_id}), 400

    user.password_hash = generate_password_hash(new_password)
    db.session.commit()

    return jsonify({"ok": True, "request_id": g.request_id})

@api_bp.post("/auth/logout")
def logout():
    session.clear()
    return jsonify({"ok": True, "request_id": g.request_id})


@api_bp.get("/me")
@login_required
def me():
    user = User.query.get(session["user_id"])
    if not user:
        session.clear()
        return jsonify({"error": "user not found", "request_id": g.request_id}), 404
    return jsonify({"user": _user_payload(user), "request_id": g.request_id})


@api_bp.get("/user-watchlist")
@login_required
def user_watchlist():
    items = (
        WatchlistItem.query.filter_by(user_id=session["user_id"])
        .order_by(WatchlistItem.created_at.desc())
        .all()
    )
    base_items = [_watchlist_item_payload(i) for i in items]

    svc = current_app.extensions.get("market_data_service")
    ai_svc = current_app.extensions.get("ai_advisor_service")
    deterministic_svc = current_app.extensions.get("deterministic_quick_advisor")
    decision_logger = current_app.extensions.get("decision_logger")
    enriched_items: list[Dict[str, Any]] = []
    for item in base_items:
        signal = {}
        quote = {}
        history30: list[float] = []
        if svc is not None:
            try:
                signal = svc.get_signal(item["symbol"]) or {}
            except Exception:  # noqa: BLE001
                signal = {}
            try:
                quote = svc.get_quote(item["symbol"]) or {}
            except Exception:  # noqa: BLE001
                quote = {}
            try:
                history30 = svc.get_price_history(item["symbol"], days=30)
            except Exception:  # noqa: BLE001
                history30 = []

        sentiment_label = str((signal.get("sentiment") or {}).get("label") or "neutral").lower()
        if sentiment_label in {"positive", "bullish"}:
            sentiment = "Bullish"
        elif sentiment_label in {"negative", "bearish"}:
            sentiment = "Bearish"
        else:
            sentiment = "Neutral"

        current_price = quote.get("price")
        if not isinstance(current_price, (int, float)):
            current_price = item.get("entry_price")

        entry_price = item.get("entry_price")
        shares = item.get("shares")
        shares_value = float(shares) if isinstance(shares, (int, float)) and shares > 0 else 1.0
        performance_percent = None
        performance_amount = None
        if isinstance(current_price, (int, float)) and isinstance(entry_price, (int, float)) and entry_price > 0:
            performance_percent = ((current_price - entry_price) / entry_price) * 100
            performance_amount = (current_price - entry_price) * shares_value

        today_change_percent = quote.get("change_percent")
        today_change_amount = None
        if isinstance(current_price, (int, float)) and isinstance(today_change_percent, (int, float)):
            denominator = 1 + (today_change_percent / 100)
            if denominator != 0:
                previous_close = current_price / denominator
                today_change_amount = (current_price - previous_close) * shares_value

        rsi = (signal.get("technical") or {}).get("rsi")
        sentiment_score = (signal.get("sentiment") or {}).get("score")
        advice = "HOLD"
        if isinstance(rsi, (int, float)) and rsi < 30:
            advice = "BUY"
        elif isinstance(sentiment_score, (int, float)) and sentiment_score > 0.7:
            advice = "BUY"
        elif isinstance(rsi, (int, float)) and rsi > 70:
            advice = "SELL"
        elif isinstance(sentiment_score, (int, float)) and sentiment_score < -0.5:
            advice = "SELL"

        reasons = signal.get("reasons")
        rationale = signal.get("rationale")
        advice_reason = "Rule-based recommendation from technical momentum and sentiment checks."
        if isinstance(reasons, list) and reasons:
            advice_reason = str(reasons[0])
        elif isinstance(rationale, list) and rationale:
            advice_reason = str(rationale[0])
        elif isinstance(rationale, str) and rationale.strip():
            advice_reason = rationale.strip()

        deterministic_portfolio = None
        if deterministic_svc is not None:
            try:
                deterministic_portfolio = deterministic_svc.predict_portfolio_position(
                    symbol=item["symbol"],
                    entry_price=entry_price if isinstance(entry_price, (int, float)) else None,
                    current_price=current_price if isinstance(current_price, (int, float)) else None,
                    shares=shares_value,
                    signal_data=signal,
                    quote_data=quote,
                )
                deterministic_advice = str((deterministic_portfolio or {}).get("advice") or "").upper()
                if deterministic_advice in {"BUY", "HOLD", "SELL"}:
                    advice = deterministic_advice
                deterministic_reason = str((deterministic_portfolio or {}).get("advice_reason") or "").strip()
                if deterministic_reason:
                    advice_reason = deterministic_reason
            except Exception:  # noqa: BLE001
                deterministic_portfolio = None

        ai_portfolio = None
        if ai_svc is not None:
            try:
                ai_portfolio = ai_svc.enhance_portfolio_position(
                    symbol=item["symbol"],
                    entry_price=entry_price if isinstance(entry_price, (int, float)) else None,
                    current_price=current_price if isinstance(current_price, (int, float)) else None,
                    shares=shares_value,
                    signal_data=signal,
                )
                ai_advice = str((ai_portfolio or {}).get("advice") or "").upper()
                if ai_advice in {"BUY", "HOLD", "SELL"}:
                    advice = ai_advice
                ai_reason = str((ai_portfolio or {}).get("advice_reason") or "").strip()
                if ai_reason:
                    advice_reason = ai_reason
            except Exception:  # noqa: BLE001
                ai_portfolio = None

        enriched_items.append(
            {
                **item,
                "score": signal.get("score") if signal.get("score") is not None else signal.get("hybrid_score"),
                "sentiment": sentiment,
                "current_price": current_price,
                "today_change_percent": round(today_change_percent, 2) if isinstance(today_change_percent, (int, float)) else None,
                "today_change_amount": round(today_change_amount, 2) if today_change_amount is not None else None,
                "performance_percent": round(performance_percent, 2) if performance_percent is not None else None,
                "performance_amount": round(performance_amount, 2) if performance_amount is not None else None,
                "advice": advice,
                "advice_reason": advice_reason,
                "deterministic_portfolio": deterministic_portfolio,
                "ai_portfolio": ai_portfolio,
                "history30": history30,
                "quote_source": quote.get("quote_source"),
                "quote_diagnostics": quote.get("diagnostics"),
            }
        )
        if decision_logger is not None:
            decision_logger.log(
                endpoint="user_watchlist",
                symbol=item.get("symbol"),
                decision_source=(deterministic_portfolio or {}).get("decision_source")
                or (ai_portfolio or {}).get("mode")
                or "rule_based",
                payload={
                    "advice": advice,
                    "model_version": (deterministic_portfolio or {}).get("model_version"),
                    "confidence": (deterministic_portfolio or {}).get("confidence"),
                },
            )

    return jsonify({"items": base_items, "enriched_items": enriched_items, "request_id": g.request_id})


@api_bp.post("/user-watchlist")
@login_required
def add_watchlist_item():
    data = request.get_json(silent=True) or {}
    symbol = _normalize_symbol(data.get("symbol") or "")
    company = (data.get("company") or "").strip() or None
    buy_price = _to_decimal(data.get("buy_price"))
    shares = _to_decimal(data.get("shares"))

    if not symbol:
        return jsonify({"error": "symbol required", "request_id": g.request_id}), 400
    if buy_price is not None and buy_price <= 0:
        return jsonify({"error": "buy_price must be > 0", "request_id": g.request_id}), 400
    if shares is not None and shares <= 0:
        return jsonify({"error": "shares must be > 0", "request_id": g.request_id}), 400

    existing = WatchlistItem.query.filter_by(user_id=session["user_id"], symbol=symbol).first()
    if existing:
        return jsonify({"error": "symbol already in watchlist", "request_id": g.request_id}), 409

    item = WatchlistItem(
        user_id=session["user_id"], symbol=symbol, company=company, buy_price=buy_price, shares=shares
    )
    db.session.add(item)
    db.session.commit()
    return jsonify({"item": _watchlist_item_payload(item), "request_id": g.request_id}), 201


@api_bp.patch("/user-watchlist/<int:item_id>")
@login_required
def update_watchlist_item(item_id: int):
    data = request.get_json(silent=True) or {}
    item = WatchlistItem.query.filter_by(id=item_id, user_id=session["user_id"]).first()
    if not item:
        return jsonify({"error": "item not found", "request_id": g.request_id}), 404

    if "buy_price" in data:
        buy_price = _to_decimal(data.get("buy_price"))
        if buy_price is not None and buy_price <= 0:
            return jsonify({"error": "buy_price must be > 0", "request_id": g.request_id}), 400
        item.buy_price = buy_price

    if "shares" in data:
        shares = _to_decimal(data.get("shares"))
        if shares is not None and shares <= 0:
            return jsonify({"error": "shares must be > 0", "request_id": g.request_id}), 400
        item.shares = shares

    if "company" in data:
        item.company = (data.get("company") or "").strip() or None

    if "acquired_date" in data:
        acquired_date = (data.get("acquired_date") or "").strip()
        if acquired_date:
            try:
                parsed = datetime.strptime(acquired_date, "%Y-%m-%d")
                item.created_at = parsed.replace(hour=0, minute=0, second=0, microsecond=0)
            except ValueError:
                return jsonify({"error": "acquired_date must be YYYY-MM-DD", "request_id": g.request_id}), 400

    db.session.commit()
    return jsonify({"item": _watchlist_item_payload(item), "request_id": g.request_id})


@api_bp.delete("/user-watchlist/<int:item_id>")
@login_required
def delete_watchlist_item(item_id: int):
    item = WatchlistItem.query.filter_by(id=item_id, user_id=session["user_id"]).first()
    if not item:
        return jsonify({"error": "item not found", "request_id": g.request_id}), 404
    db.session.delete(item)
    db.session.commit()
    return jsonify({"ok": True, "request_id": g.request_id})


@api_bp.post("/user-watchlist/<int:item_id>/sell")
@login_required
def sell_watchlist_item(item_id: int):
    data = request.get_json(silent=True) or {}
    item = WatchlistItem.query.filter_by(id=item_id, user_id=session["user_id"]).first()
    if not item:
        return jsonify({"error": "item not found", "request_id": g.request_id}), 404

    sold_price = _to_decimal(data.get("sold_price"))
    shares_sold = _to_decimal(data.get("shares_sold"))

    if sold_price is None or sold_price <= 0:
        return jsonify({"error": "sold_price must be > 0", "request_id": g.request_id}), 400
    if shares_sold is None or shares_sold <= 0:
        return jsonify({"error": "shares_sold must be > 0", "request_id": g.request_id}), 400
    if item.buy_price is None:
        return jsonify({"error": "entry price is required to calculate gains/losses", "request_id": g.request_id}), 400
    if item.shares is None or item.shares <= 0:
        return jsonify({"error": "shares are required to record a sale", "request_id": g.request_id}), 400
    if shares_sold > item.shares:
        return jsonify({"error": "shares_sold cannot exceed current shares", "request_id": g.request_id}), 400

    realized_amount = (sold_price - item.buy_price) * shares_sold
    sold_trade = SoldTrade(
        user_id=session["user_id"],
        symbol=item.symbol,
        shares_sold=shares_sold,
        sold_price=sold_price,
        entry_price=item.buy_price,
        realized_amount=realized_amount,
    )
    db.session.add(sold_trade)

    remaining_shares = item.shares - shares_sold
    if remaining_shares == 0:
        db.session.delete(item)
        remaining_item = None
    else:
        item.shares = remaining_shares
        remaining_item = _watchlist_item_payload(item)

    db.session.commit()
    return jsonify(
        {
            "sold_trade": _sold_trade_payload(sold_trade),
            "remaining_item": remaining_item,
            "removed": remaining_item is None,
            "request_id": g.request_id,
        }
    )


@api_bp.post("/user-watchlist/<int:item_id>/buy")
@login_required
def buy_watchlist_item(item_id: int):
    data = request.get_json(silent=True) or {}
    item = WatchlistItem.query.filter_by(id=item_id, user_id=session["user_id"]).first()
    if not item:
        return jsonify({"error": "item not found", "request_id": g.request_id}), 404

    bought_price = _to_decimal(data.get("bought_price"))
    shares_bought = _to_decimal(data.get("shares_bought"))

    if bought_price is None or bought_price <= 0:
        return jsonify({"error": "bought_price must be > 0", "request_id": g.request_id}), 400
    if shares_bought is None or shares_bought <= 0:
        return jsonify({"error": "shares_bought must be > 0", "request_id": g.request_id}), 400

    existing_shares = item.shares if item.shares is not None and item.shares > 0 else Decimal("0")
    new_total_shares = existing_shares + shares_bought
    if new_total_shares <= 0:
        return jsonify({"error": "resulting shares must be > 0", "request_id": g.request_id}), 400

    if item.buy_price is None or existing_shares == 0:
        new_entry_price = bought_price
    else:
        prior_cost = item.buy_price * existing_shares
        bought_cost = bought_price * shares_bought
        new_entry_price = (prior_cost + bought_cost) / new_total_shares

    item.shares = new_total_shares
    item.buy_price = new_entry_price
    db.session.commit()

    return jsonify(
        {
            "item": _watchlist_item_payload(item),
            "added": {
                "shares_bought": float(shares_bought),
                "bought_price": float(bought_price),
                "new_entry_price": float(new_entry_price),
            },
            "request_id": g.request_id,
        }
    )


@api_bp.get("/sold-trades")
@login_required
def sold_trades():
    items = (
        SoldTrade.query.filter_by(user_id=session["user_id"])
        .order_by(SoldTrade.sold_at.desc())
        .all()
    )
    payload = [_sold_trade_payload(i) for i in items]
    total_realized = round(sum(i["realized_amount"] for i in payload), 2)
    return jsonify({"items": payload, "total_realized": total_realized, "request_id": g.request_id})


@api_bp.get("/portfolio-summary")
@login_required
def portfolio_summary():
    items = (
        WatchlistItem.query.filter_by(user_id=session["user_id"])
        .order_by(WatchlistItem.created_at.desc())
        .all()
    )

    svc = current_app.extensions.get("market_data_service")
    total_value = 0.0
    score_values: list[float] = []
    sector_totals: Dict[str, float] = defaultdict(float)
    quote_sources: set[str] = set()

    for item in items:
        symbol = item.symbol
        shares = float(item.shares) if item.shares is not None else 1.0

        quote_price = None
        signal_score = None
        sector = "Unknown"

        if svc is not None:
            try:
                quote = svc.get_quote(symbol) or {}
                quote_price = quote.get("price")
                quote_source = quote.get("quote_source")
                if isinstance(quote_source, str) and quote_source:
                    quote_sources.add(quote_source)
            except Exception:  # noqa: BLE001
                quote_price = None

            try:
                signal = svc.get_signal(symbol) or {}
                signal_score = signal.get("score") if signal.get("score") is not None else signal.get("hybrid_score")
            except Exception:  # noqa: BLE001
                signal_score = None

            try:
                sector = svc.get_sector(symbol)
            except Exception:  # noqa: BLE001
                sector = "Unknown"

        if not isinstance(quote_price, (int, float)):
            quote_price = float(item.buy_price) if item.buy_price is not None else 0.0

        position_value = float(quote_price) * shares
        total_value += position_value
        sector_totals[sector or "Unknown"] += position_value

        if isinstance(signal_score, (int, float)):
            score_values.append(float(signal_score))

    avg_score = round(sum(score_values) / len(score_values), 2) if score_values else None
    sector_breakdown = [
        {"sector": sector, "value": round(value, 2)}
        for sector, value in sorted(sector_totals.items(), key=lambda kv: kv[1], reverse=True)
    ]

    return jsonify(
        {
            "total_value": round(total_value, 2),
            "avg_score": avg_score,
            "sector_breakdown": sector_breakdown,
            "positions": len(items),
            "quote_sources": sorted(quote_sources),
            "request_id": g.request_id,
        }
    )


@api_bp.get("/company-details")
def company_details():
    symbol = _normalize_symbol(request.args.get("symbol") or "")
    if not symbol:
        return jsonify({"error": "symbol required", "request_id": g.request_id}), 400

    svc = current_app.extensions.get("market_data_service")
    if svc is None:
        return jsonify({"error": "market data unavailable", "request_id": g.request_id}), 503

    return jsonify({"data": svc.get_company_snapshot(symbol), "request_id": g.request_id})


@api_bp.get("/quote")
def api_quote():
    symbol = _normalize_symbol(request.args.get("symbol") or "")
    if not symbol:
        return jsonify({"error": "symbol required", "request_id": g.request_id}), 400

    svc = current_app.extensions["market_data_service"]
    return jsonify({"data": svc.get_quote(symbol), "request_id": g.request_id})


@api_bp.get("/signal")
def api_signal():
    symbol = _normalize_symbol(request.args.get("symbol") or "")
    if not symbol:
        return jsonify({"error": "symbol required", "request_id": g.request_id}), 400

    svc = current_app.extensions["market_data_service"]
    return jsonify({"data": svc.get_signal(symbol), "request_id": g.request_id})


@api_bp.get("/quick-ask")
def quick_ask():
    symbol = _normalize_symbol(request.args.get("symbol") or "")
    if not symbol:
        return jsonify({"error": "symbol required", "request_id": g.request_id}), 400

    svc = current_app.extensions["market_data_service"]
    ai_svc = current_app.extensions.get("ai_advisor_service")
    deterministic_svc = current_app.extensions.get("deterministic_quick_advisor")
    decision_logger = current_app.extensions.get("decision_logger")

    signal_data = svc.get_signal(symbol)
    quote_data = signal_data.get("quote") or svc.get_quote(symbol)
    decision = None
    if deterministic_svc is not None:
        decision = deterministic_svc.predict_quick_decision(signal_data=signal_data, quote_data=quote_data)
    if decision is None:
        decision = _quick_decision(signal_data, quote_data)

    ai_payload = None
    if ai_svc is not None:
        ai_payload = ai_svc.enhance_quick_decision(
            symbol=symbol,
            quick_decision=decision,
            signal_data=signal_data,
            quote_data=quote_data,
        )

    ai_mode = (ai_payload or {}).get("mode")

    if decision_logger is not None:
        decision_logger.log(
            endpoint="quick_ask",
            symbol=symbol,
            decision_source=decision.get("decision_source"),
            payload={
                "recommendation": decision.get("recommendation"),
                "model_version": decision.get("model_version"),
                "probability_up": decision.get("probability_up"),
                "confidence": decision.get("confidence"),
                "ai_mode": ai_mode,
            },
        )

    return jsonify(
        {
            "data": {
                "symbol": symbol,
                **decision,
                "ai": ai_payload,
                "ai_status": "working" if ai_mode == "ai_enhanced" else "fallback",
                "ai_mode": ai_mode,
            },
            "request_id": g.request_id,
        }
    )


@api_bp.post("/explain-recommendation")
def explain_recommendation():
    data = request.get_json(silent=True) or {}
    recommendation = str(data.get("recommendation") or "").strip()
    reason = str(data.get("reason") or "").strip()
    if not recommendation:
        return jsonify({"error": "recommendation required", "request_id": g.request_id}), 400

    explanation = _plain_english_recommendation(recommendation, reason)
    return jsonify({"data": {"explanation": explanation}, "request_id": g.request_id})


@api_bp.get("/market-overview")
def market_overview():
    svc = current_app.extensions["market_data_service"]
    return jsonify({"items": svc.get_market_indices(), "request_id": g.request_id})


@api_bp.get("/stable-watchlist")
def stable_watchlist():
    svc = current_app.extensions["market_data_service"]
    return jsonify({"items": svc.get_stable_watchlist(), "request_id": g.request_id})


@api_bp.get("/hot-momentum-buys")
def hot_momentum_buys():
    svc = current_app.extensions["market_data_service"]
    decision_logger = current_app.extensions.get("decision_logger")
    items = svc.get_hot_momentum_buys()
    if decision_logger is not None:
        for item in items:
            decision_logger.log(
                endpoint="hot_momentum_buys",
                symbol=item.get("symbol"),
                decision_source=item.get("decision_source") or "rule_based",
                payload={
                    "score": item.get("score"),
                    "model_version": item.get("model_version"),
                    "probability_up": item.get("probability_up"),
                    "confidence": item.get("confidence"),
                },
            )
    return jsonify({"items": items, "request_id": g.request_id})


@api_bp.get("/model-health")
def model_health():
    deterministic_svc = current_app.extensions.get("deterministic_quick_advisor")
    decision_logger = current_app.extensions.get("decision_logger")
    model_path = current_app.config.get("DETERMINISTIC_MODEL_PATH")

    return jsonify(
        {
            "data": {
                "deterministic_quick_enabled": bool(current_app.config.get("DETERMINISTIC_QUICK_ENABLED")),
                "deterministic_momentum_enabled": bool(current_app.config.get("DETERMINISTIC_MOMENTUM_ENABLED")),
                "deterministic_model_path": model_path,
                "model_loaded": bool(getattr(deterministic_svc, "artifact", None) is not None),
                "model_version": getattr(getattr(deterministic_svc, "artifact", None), "version", None),
                "model_load_error": getattr(deterministic_svc, "load_error", None),
                "artifact_metadata": load_artifact_metadata(str(model_path)) if model_path else None,
                "artifact_history": load_artifact_history(str(model_path)) if model_path else [],
                "decision_logging": decision_logger.health() if decision_logger is not None else None,
            },
            "request_id": g.request_id,
        }
    )


@api_bp.get("/decision-log-summary")
def decision_log_summary():
    decision_logger = current_app.extensions.get("decision_logger")
    raw_limit = request.args.get("limit") or "200"
    try:
        limit = max(1, min(int(raw_limit), 1000))
    except ValueError:
        return jsonify({"error": "limit must be an integer", "request_id": g.request_id}), 400

    output_path = (
        getattr(decision_logger, "output_path", None)
        or current_app.config.get("DECISION_LOG_PATH")
        or "data/decision_events.jsonl"
    )
    summary = summarize_decision_events(str(output_path), limit=limit)
    summary["logging_enabled"] = bool(getattr(decision_logger, "enabled", False))

    return jsonify({"data": summary, "request_id": g.request_id})


@api_bp.get("/decision-outcomes")
def decision_outcomes():
    decision_logger = current_app.extensions.get("decision_logger")
    raw_limit = request.args.get("limit") or "20"
    include_skipped = (request.args.get("include_skipped") or "").strip().lower() == "true"
    try:
        limit = max(1, min(int(raw_limit), 100))
    except ValueError:
        return jsonify({"error": "limit must be an integer", "request_id": g.request_id}), 400

    output_path = (
        getattr(decision_logger, "output_path", None)
        or current_app.config.get("DECISION_LOG_PATH")
        or "data/decision_events.jsonl"
    )
    # Read progressively wider windows so the endpoint can still find older evaluated rows
    # when very recent logs are mostly too fresh for 1D / 5D outcomes.
    read_cap = 5000
    read_limit = min(max(limit * 10, 200), read_cap)
    rows: list[dict[str, Any]] = []
    evaluated_rows: list[dict[str, Any]] = []
    while True:
        events = read_decision_events(str(output_path), limit=read_limit)
        rows = evaluate_decision_events(events, future_return_lookup=_future_return_for_outcomes)
        evaluated_rows = [
            row
            for row in rows
            if isinstance(row.get("return_1d"), (int, float)) or isinstance(row.get("return_5d"), (int, float))
        ]
        if include_skipped or len(evaluated_rows) >= limit or read_limit >= read_cap:
            break
        read_limit = min(read_limit * 2, read_cap)
    used_unevaluated_fallback = False
    visible_rows = rows if include_skipped else evaluated_rows
    if not include_skipped and not visible_rows and rows:
        # If nothing is evaluable yet, return the most recent rows so the UI still shows
        # live decision activity instead of an empty panel.
        visible_rows = rows
        used_unevaluated_fallback = True
    visible_rows = visible_rows[-limit:]

    summary_1d = summarize_outcome_rows(visible_rows)
    summary_5d = summarize_outcome_rows([{**row, "return_1d": row.get("return_5d")} for row in visible_rows])
    endpoint_action_rows = [
        {
            **row,
            "endpoint_action": f"{str(row.get('endpoint') or 'unknown')}::{str(row.get('action') or 'unknown')}",
        }
        for row in visible_rows
    ]

    return jsonify(
        {
            "data": {
                "rows": visible_rows,
                "summary_1d": summary_1d,
                "summary_5d": summary_5d,
                "breakdown": {
                    "by_endpoint": summarize_outcome_groups(visible_rows, group_field="endpoint"),
                    "by_action": summarize_outcome_groups(visible_rows, group_field="action"),
                    "by_source": summarize_outcome_groups(visible_rows, group_field="decision_source"),
                    "by_endpoint_action": summarize_outcome_groups(endpoint_action_rows, group_field="endpoint_action"),
                },
                "include_skipped": include_skipped,
                "rows_scanned": len(rows),
                "evaluated_rows_available": len(evaluated_rows),
                "used_unevaluated_fallback": used_unevaluated_fallback,
            },
            "request_id": g.request_id,
        }
    )


@api_bp.get("/wells-picks")
def wells_picks():
    svc = current_app.extensions["market_data_service"]
    return jsonify({"items": svc.get_wells_picks(), "request_id": g.request_id})
def _future_return_for_outcomes(symbol: str, start_ts: int, days: int) -> float | None:
    start_dt = datetime.fromtimestamp(int(start_ts), tz=timezone.utc)
    now_utc = datetime.now(timezone.utc)
    # Skip network calls when event time is too recent (or future) to have realized horizon returns.
    if start_dt >= now_utc:
        return None
    if start_dt + timedelta(days=days) > now_utc:
        return None

    end_dt = start_dt + timedelta(days=max(days + 3, 7))
    safe_end_dt = min(end_dt, now_utc + timedelta(days=1))
    try:
        history = yf.download(
            symbol,
            start=start_dt.strftime("%Y-%m-%d"),
            end=safe_end_dt.strftime("%Y-%m-%d"),
            interval="1d",
            progress=False,
            auto_adjust=False,
        )
    except Exception:  # noqa: BLE001
        return None
    closes = close_values(history)
    if len(closes) <= days:
        return None
    start_price = float(closes[0])
    end_price = float(closes[days])
    if start_price == 0:
        return None
    return round((end_price - start_price) / start_price, 4)
