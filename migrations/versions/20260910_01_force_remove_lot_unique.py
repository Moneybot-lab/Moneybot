"""Force removal of every legacy user/symbol uniqueness object.

Revision ID: 20260910_01
Revises: 20260909_01
Create Date: 2026-09-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260910_01"
down_revision = "20260909_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    matching = [
        constraint
        for constraint in inspector.get_unique_constraints("watchlist_items")
        if set(constraint.get("column_names") or ()) == {"user_id", "symbol"}
    ]
    if not matching:
        return

    if bind.dialect.name == "postgresql":
        preparer = bind.dialect.identifier_preparer
        for constraint in matching:
            name = constraint.get("name")
            if name:
                op.execute(
                    "ALTER TABLE watchlist_items DROP CONSTRAINT IF EXISTS "
                    + preparer.quote(name)
                )
        return

    # Batch mode recreates SQLite's table, which is required for auto-indexed
    # UNIQUE constraints. The historical schema uses this stable name.
    with op.batch_alter_table("watchlist_items") as batch:
        for constraint in matching:
            batch.drop_constraint(constraint.get("name") or "uq_watchlist_user_symbol", type_="unique")


def downgrade() -> None:
    with op.batch_alter_table("watchlist_items") as batch:
        batch.create_unique_constraint("uq_watchlist_user_symbol", ["user_id", "symbol"])
