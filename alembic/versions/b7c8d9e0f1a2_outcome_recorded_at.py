"""Record when realized outcomes entered the knowledge base."""

import sqlalchemy as sa
from alembic import op

revision = "b7c8d9e0f1a2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column("analysis_cases", sa.Column("outcome_recorded_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("analysis_cases", "outcome_recorded_at")
