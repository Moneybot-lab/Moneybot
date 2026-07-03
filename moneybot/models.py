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
    investor_profile = db.relationship(
        "InvestorProfile",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy=True,
        uselist=False,
    )
    investor_profile_revisions = db.relationship(
        "InvestorProfileRevision",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy=True,
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
    fresh_breakouts = db.Column(db.Boolean, nullable=False, default=True)
    whale_top_investor_added = db.Column(db.Boolean, nullable=False, default=True)
    whales_top_stock_list_changes = db.Column(db.Boolean, nullable=False, default=True)
    clearview_hold_off_to_buy = db.Column(db.Boolean, nullable=False, default=True)
    push_notifications_enabled = db.Column(db.Boolean, nullable=False, default=False)
    clearview_symbols_csv = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    user = db.relationship("User", back_populates="notification_trigger_preferences")


class InvestorProfile(db.Model):
    __tablename__ = "investor_profiles"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True, index=True)
    profile_version = db.Column(db.Integer, nullable=False, default=1)
    primary_goal = db.Column(db.String(32), nullable=True)
    time_horizon_years = db.Column(db.Integer, nullable=True)
    risk_tolerance = db.Column(db.String(32), nullable=True)
    loss_capacity_percent = db.Column(db.Numeric(5, 2), nullable=True)
    liquidity_need = db.Column(db.String(32), nullable=True)
    experience_level = db.Column(db.String(32), nullable=True)
    account_type = db.Column(db.String(32), nullable=True)
    position_size_limit_percent = db.Column(db.Numeric(5, 2), nullable=True)
    sector_limit_percent = db.Column(db.Numeric(5, 2), nullable=True)
    excluded_sectors_csv = db.Column(db.Text, nullable=False, default="")
    penny_stocks_allowed = db.Column(db.Boolean, nullable=True)
    after_hours_alerts = db.Column(db.Boolean, nullable=True)
    recommendation_style = db.Column(db.String(32), nullable=True)
    questionnaire_completed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    user = db.relationship("User", back_populates="investor_profile")

    __mapper_args__ = {
        "version_id_col": profile_version,
        "version_id_generator": lambda version: (version or 0) + 1,
    }


class InvestorProfileRevision(db.Model):
    __tablename__ = "investor_profile_revisions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    profile_version = db.Column(db.Integer, nullable=False, index=True)
    previous_profile_json = db.Column(db.Text, nullable=False)
    new_profile_json = db.Column(db.Text, nullable=False)
    change_reason = db.Column(db.String(255), nullable=True)
    source = db.Column(db.String(32), nullable=False, default="settings")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship("User", back_populates="investor_profile_revisions")

    __table_args__ = (
        db.UniqueConstraint("user_id", "profile_version", name="uq_profile_revision_user_version"),
    )


class WaitlistSignup(db.Model):
    __tablename__ = "waitlist_signups"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    source = db.Column(db.String(80), nullable=False, default="landing")
    welcome_email_sent = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
