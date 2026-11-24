"""Watchlists and ordered watchlist items.

Revision: e5f6a7b8c9d0
Revises: d3e4f5a6b7c8
"""

import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create durable user watchlists; default lists are seeded lazily by the service."""
    op.create_table(
        "watchlists",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.String(64), server_default="local", nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_watchlists"),
        sa.UniqueConstraint("owner_id", "name", name="uq_watchlists_owner_id_name"),
    )
    op.create_index(
        "ix_watchlists_owner_id_sort_order", "watchlists", ["owner_id", "sort_order"]
    )

    op.create_table(
        "watchlist_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("watchlist_id", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["watchlist_id"],
            ["watchlists.id"],
            name="fk_watchlist_items_watchlist_id_watchlists",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["symbol"],
            ["symbols.symbol"],
            name="fk_watchlist_items_symbol_symbols",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_watchlist_items"),
        sa.UniqueConstraint(
            "watchlist_id", "symbol", name="uq_watchlist_items_watchlist_id_symbol"
        ),
    )
    op.create_index(
        "ix_watchlist_items_watchlist_id_sort_order",
        "watchlist_items",
        ["watchlist_id", "sort_order"],
    )


def downgrade() -> None:
    """Drop watchlist tables; no market data is affected."""
    op.drop_index("ix_watchlist_items_watchlist_id_sort_order", table_name="watchlist_items")
    op.drop_table("watchlist_items")
    op.drop_index("ix_watchlists_owner_id_sort_order", table_name="watchlists")
    op.drop_table("watchlists")
