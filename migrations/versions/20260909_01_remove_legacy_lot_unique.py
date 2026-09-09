"""Remove the legacy one-ticker-per-user constraint after startup backfills.

Revision ID: 20260909_01
Revises: 20260908_01
Create Date: 2026-09-09

The prior revision coupled constraint removal to adding ``original_shares``.
MoneyBot's startup compatibility hook adds that column before Alembic runs, so
an existing database could skip constraint removal and reject every additional
lot for a ticker.  This repair revision checks the constraint independently.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260909_01"
down_revision = "20260908_01"
branch_labels = None
depends_on = None


def _has_legacy_unique() -> bool:
    uniques = sa.inspect(op.get_bind()).get_unique_constraints("watchlist_items")
    return any(
        constraint.get("name") == "uq_watchlist_user_symbol"
        or set(constraint.get("column_names") or ()) == {"user_id", "symbol"}
        for constraint in uniques
    )


def upgrade() -> None:
    if _has_legacy_unique():
        with op.batch_alter_table("watchlist_items") as batch:
            batch.drop_constraint("uq_watchlist_user_symbol", type_="unique")


def downgrade() -> None:
    if not _has_legacy_unique():
        with op.batch_alter_table("watchlist_items") as batch:
            batch.create_unique_constraint("uq_watchlist_user_symbol", ["user_id", "symbol"])
