"""Add versioned investor profiles.

Revision ID: 20260607_01
Revises:
Create Date: 2026-06-07
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260607_01"
down_revision = None
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    table_names = _table_names()
    if "investor_profiles" not in table_names:
        op.create_table(
            "investor_profiles",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("profile_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("primary_goal", sa.String(length=32), nullable=True),
            sa.Column("time_horizon_years", sa.Integer(), nullable=True),
            sa.Column("risk_tolerance", sa.String(length=32), nullable=True),
            sa.Column("loss_capacity_percent", sa.Numeric(precision=5, scale=2), nullable=True),
            sa.Column("liquidity_need", sa.String(length=32), nullable=True),
            sa.Column("experience_level", sa.String(length=32), nullable=True),
            sa.Column("account_type", sa.String(length=32), nullable=True),
            sa.Column("position_size_limit_percent", sa.Numeric(precision=5, scale=2), nullable=True),
            sa.Column("sector_limit_percent", sa.Numeric(precision=5, scale=2), nullable=True),
            sa.Column("excluded_sectors_csv", sa.Text(), nullable=False, server_default=""),
            sa.Column("penny_stocks_allowed", sa.Boolean(), nullable=True),
            sa.Column("after_hours_alerts", sa.Boolean(), nullable=True),
            sa.Column("recommendation_style", sa.String(length=32), nullable=True),
            sa.Column("questionnaire_completed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id"),
        )
        op.create_index("ix_investor_profiles_user_id", "investor_profiles", ["user_id"], unique=True)

    if "investor_profile_revisions" not in table_names:
        op.create_table(
            "investor_profile_revisions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("profile_version", sa.Integer(), nullable=False),
            sa.Column("previous_profile_json", sa.Text(), nullable=False),
            sa.Column("new_profile_json", sa.Text(), nullable=False),
            sa.Column("change_reason", sa.String(length=255), nullable=True),
            sa.Column("source", sa.String(length=32), nullable=False, server_default="settings"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "profile_version", name="uq_profile_revision_user_version"),
        )
        op.create_index(
            "ix_investor_profile_revisions_profile_version",
            "investor_profile_revisions",
            ["profile_version"],
            unique=False,
        )
        op.create_index(
            "ix_investor_profile_revisions_user_id",
            "investor_profile_revisions",
            ["user_id"],
            unique=False,
        )


def downgrade() -> None:
    table_names = _table_names()
    if "investor_profile_revisions" in table_names:
        op.drop_index("ix_investor_profile_revisions_user_id", table_name="investor_profile_revisions")
        op.drop_index("ix_investor_profile_revisions_profile_version", table_name="investor_profile_revisions")
        op.drop_table("investor_profile_revisions")
    if "investor_profiles" in table_names:
        op.drop_index("ix_investor_profiles_user_id", table_name="investor_profiles")
        op.drop_table("investor_profiles")
