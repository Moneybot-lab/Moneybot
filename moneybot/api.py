from __future__ import annotations

import time
import uuid
from datetime import datetime
from collections import defaultdict, deque
from decimal import Decimal
from functools import wraps
from typing import Any, Dict, Tuple

from flask import Blueprint, current_app, g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from advice_engine import compute_user_advice

from .extensions import db
from .models import User, WatchlistItem


api_bp = Blueprint("api", __name__, url_prefix="/api")


def _to_decimal(v: Any) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
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


def _quick_decision(signal_data: Dict[str, Any], quote_data: Dict[str, Any]) -> Dict[str, Any]:
    action = (signal_data.get("action") or "HOLD").upper()
    technical = signal_data.get("technical") or {}
    sentiment = signal_data.get("sentiment") or {}

    rsi = technical.get("rsi")
    macd = technical.get("macd_histogram")
    sentiment_label = (sentiment.get("label") or "neutral").lower()

    if action in {"STRONG BUY", "BUY", "SELL"}:
        recommendation = action
    else:
        bearish = (isinstance(rsi, (int, float)) and rsi >= 68) or (isinstance(macd, (int, float)) and macd < 0)
        recommendation = "SELL" if bearish or sentiment_label in {"negative", "bearish"} else "BUY"

    rationale = signal_data.get("reasons") or signal_data.get("rationale") or []
    short_reason = rationale[0] if rationale else "Derived from momentum and sentiment checks."
    return {
        "recommendation": recommendation,
        "rationale": short_reason,
        "current_price": quote_data.get("price"),
        "change_percent": quote_data.get("change_percent"),
    }


@api_bp.post("/auth/signup")
def signup():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email or not password:
        return jsonify({"error": "email and password required", "request_id": g.request_id}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({"error": "email already exists", "request_id": g.request_id}), 409

    user = User(email=email, password_hash=generate_password_hash(password))
    db.session.add(user)
    db.session.commit()

    session["user_id"] = user.id
    return jsonify({"user": _user_payload(user), "request_id": g.request_id}), 201


@api_bp.post("/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    user = User.query.filter_by(email=email).first()
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({"error": "invalid credentials", "request_id": g.request_id}), 401

    session["user_id"] = user.id
    return jsonify({"user": _user_payload(user), "request_id": g.request_id})


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

        enriched_items.append(
            {
                **item,
                "score": signal.get("score") if signal.get("score") is not None else signal.get("hybrid_score"),
                "sentiment": sentiment,
                "current_price": current_price,
                "performance_percent": round(performance_percent, 2) if performance_percent is not None else None,
                "performance_amount": round(performance_amount, 2) if performance_amount is not None else None,
                "advice": advice,
                "advice_reason": advice_reason,
                "history30": history30,
            }
        )

    return jsonify({"items": base_items, "enriched_items": enriched_items, "request_id": g.request_id})


@api_bp.post("/user-watchlist")
@login_required
def add_watchlist_item():
    data = request.get_json(silent=True) or {}
    symbol = (data.get("symbol") or "").strip().upper()
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
            "request_id": g.request_id,
        }
    )


@api_bp.get("/company-details")
@login_required
def company_details():
    symbol = (request.args.get("symbol") or "").strip().upper()
    if not symbol:
        return jsonify({"error": "symbol required", "request_id": g.request_id}), 400

    svc = current_app.extensions.get("market_data_service")
    if svc is None:
        return jsonify({"error": "market data unavailable", "request_id": g.request_id}), 503

    return jsonify({"data": svc.get_company_snapshot(symbol), "request_id": g.request_id})


@api_bp.get("/quote")
def api_quote():
    symbol = (request.args.get("symbol") or "").strip().upper()
    if not symbol:
        return jsonify({"error": "symbol required", "request_id": g.request_id}), 400

    svc = current_app.extensions["market_data_service"]
    return jsonify({"data": svc.get_quote(symbol), "request_id": g.request_id})


@api_bp.get("/signal")
def api_signal():
    symbol = (request.args.get("symbol") or "").strip().upper()
    if not symbol:
        return jsonify({"error": "symbol required", "request_id": g.request_id}), 400

    svc = current_app.extensions["market_data_service"]
    return jsonify({"data": svc.get_signal(symbol), "request_id": g.request_id})


@api_bp.get("/quick-ask")
def quick_ask():
    symbol = (request.args.get("symbol") or "").strip().upper()
    if not symbol:
        return jsonify({"error": "symbol required", "request_id": g.request_id}), 400

    svc = current_app.extensions["market_data_service"]
    signal_data = svc.get_signal(symbol)
    quote_data = signal_data.get("quote") or svc.get_quote(symbol)
    return jsonify({"data": {"symbol": symbol, **_quick_decision(signal_data, quote_data)}, "request_id": g.request_id})


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
    return jsonify({"items": svc.get_hot_momentum_buys(), "request_id": g.request_id})


@api_bp.get("/wells-picks")
def wells_picks():
    svc = current_app.extensions["market_data_service"]
    return jsonify({"items": svc.get_wells_picks(), "request_id": g.request_id})
