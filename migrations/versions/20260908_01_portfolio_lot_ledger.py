"""Preserve acquisition lots and their specific-lot sale ledger.

Revision ID: 20260908_01
Revises: 20260607_01
Create Date: 2026-09-08
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260908_01"
down_revision = "20260607_01"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "original_shares" not in _columns("watchlist_items"):
        with op.batch_alter_table("watchlist_items") as batch:
            batch.add_column(sa.Column("original_shares", sa.Numeric(16, 6), nullable=True))
            # Multiple purchases of one ticker must remain distinct lots.
            batch.drop_constraint("uq_watchlist_user_symbol", type_="unique")
        op.execute("UPDATE watchlist_items SET original_shares = shares WHERE original_shares IS NULL")

    sold_columns = _columns("sold_trades")
    with op.batch_alter_table("sold_trades") as batch:
        if "source_lot_id" not in sold_columns:
            batch.add_column(sa.Column("source_lot_id", sa.Integer(), nullable=True))
            batch.create_foreign_key("fk_sold_trades_source_lot", "watchlist_items", ["source_lot_id"], ["id"])
            batch.create_index("ix_sold_trades_source_lot_id", ["source_lot_id"])
        for name, column in (
            ("gross_proceeds", sa.Column("gross_proceeds", sa.Numeric(22, 6), nullable=True)),
            ("assigned_cost_basis", sa.Column("assigned_cost_basis", sa.Numeric(22, 6), nullable=True)),
            ("acquired_at", sa.Column("acquired_at", sa.DateTime(), nullable=True)),
            ("created_at", sa.Column("created_at", sa.DateTime(), nullable=True)),
            ("updated_at", sa.Column("updated_at", sa.DateTime(), nullable=True)),
        ):
            if name not in sold_columns:
                batch.add_column(column)
    op.execute("UPDATE sold_trades SET gross_proceeds=sold_price*shares_sold, "
               "assigned_cost_basis=entry_price*shares_sold, "
               "created_at=COALESCE(created_at,sold_at), updated_at=COALESCE(updated_at,sold_at)")


def downgrade() -> None:
    with op.batch_alter_table("sold_trades") as batch:
        batch.drop_index("ix_sold_trades_source_lot_id")
        batch.drop_constraint("fk_sold_trades_source_lot", type_="foreignkey")
        for name in ("updated_at", "created_at", "acquired_at", "assigned_cost_basis", "gross_proceeds", "source_lot_id"):
            batch.drop_column(name)
    with op.batch_alter_table("watchlist_items") as batch:
        batch.create_unique_constraint("uq_watchlist_user_symbol", ["user_id", "symbol"])
        batch.drop_column("original_shares")
