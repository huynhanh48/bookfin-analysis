"""Add normalized analysis report card to chat turns."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "a1b2c3d4e5f6"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column("chat_turns", sa.Column("report", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_turns", "report")
