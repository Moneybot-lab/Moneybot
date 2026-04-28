from datetime import datetime

from .extensions import db


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    profile_image_url = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    watchlist_items = db.relationship(
        "WatchlistItem",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy=True,
    )
    sold_trades = db.relationship(
        "SoldTrade",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy=True,
    )
    fcm_device_tokens = db.relationship(
        "FcmDeviceToken",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy=True,
    )
    notification_trigger_preferences = db.relationship(
        "NotificationTriggerPreference",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy=True,
        uselist=False,
    )


class WatchlistItem(db.Model):
    __tablename__ = "watchlist_items"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    symbol = db.Column(db.String(16), nullable=False, index=True)
    company = db.Column(db.String(255), nullable=True)
    buy_price = db.Column(db.Numeric(16, 4), nullable=True)
    shares = db.Column(db.Numeric(16, 6), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship("User", back_populates="watchlist_items")

    __table_args__ = (
        db.UniqueConstraint("user_id", "symbol", name="uq_watchlist_user_symbol"),
    )


class SoldTrade(db.Model):
    __tablename__ = "sold_trades"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    symbol = db.Column(db.String(16), nullable=False, index=True)
    shares_sold = db.Column(db.Numeric(16, 6), nullable=False)
    sold_price = db.Column(db.Numeric(16, 4), nullable=False)
    entry_price = db.Column(db.Numeric(16, 4), nullable=False)
    realized_amount = db.Column(db.Numeric(16, 4), nullable=False)
    sold_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship("User", back_populates="sold_trades")


class FcmDeviceToken(db.Model):
    __tablename__ = "fcm_device_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    token = db.Column(db.String(255), nullable=False, unique=True, index=True)
    user_agent = db.Column(db.String(512), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    user = db.relationship("User", back_populates="fcm_device_tokens")


class NotificationTriggerPreference(db.Model):
    __tablename__ = "notification_trigger_preferences"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True, index=True)
    portfolio_sell_advice_change = db.Column(db.Boolean, nullable=False, default=True)
    portfolio_buy_advice_change = db.Column(db.Boolean, nullable=False, default=True)
    hot_momentum_score_crosses_8 = db.Column(db.Boolean, nullable=False, default=True)
    whale_top_investor_added = db.Column(db.Boolean, nullable=False, default=True)
    whales_top_stock_list_changes = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    user = db.relationship("User", back_populates="notification_trigger_preferences")
