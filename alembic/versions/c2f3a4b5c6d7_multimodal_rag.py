"""Durable multimodal sources, isolated case vectors and cosine indexes.

Revision ID: c2f3a4b5c6d7
Revises: bb14e84dd2de
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "c2f3a4b5c6d7"
down_revision = "bb14e84dd2de"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create extension, source records and model/version-isolated embeddings."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "analysis_cases",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("timeframe", sa.String(20), nullable=False),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("thesis", sa.Text(), nullable=False),
        sa.Column("chart_path", sa.Text()),
        sa.Column("indicator_snapshot", JSONB(), nullable=False),
        sa.Column("market_context", JSONB(), nullable=False),
        sa.Column("conclusion", sa.Text(), nullable=False),
        sa.Column("outcome", JSONB()),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("ingest_status", sa.String(20), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    for field in ("symbol", "timeframe", "analyzed_at"):
        op.create_index(f"ix_analysis_cases_{field}", "analysis_cases", [field])
    op.create_index(
        "ix_analysis_cases_filters", "analysis_cases", ["symbol", "timeframe", "analyzed_at"]
    )
    op.create_table(
        "analysis_case_embeddings",
        sa.Column(
            "case_id",
            UUID(),
            sa.ForeignKey("analysis_cases.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("space", sa.String(300), primary_key=True),
        sa.Column("embedding_model", sa.String(200), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("representation_version", sa.String(40), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.CheckConstraint("vector_dims(embedding) = dimension", name="embedding_dimension"),
    )
    op.create_index("ix_case_embeddings_space", "analysis_case_embeddings", ["space", "dimension"])
    # Casted expression + partial dimension predicate matches repository search exactly.
    # Models remain separated by space predicate, even when dimensions are identical.
    for dimension in (1024, 1536):
        op.execute(
            f"CREATE INDEX ix_case_embedding_cosine_{dimension} ON analysis_case_embeddings "
            f"USING hnsw ((embedding::vector({dimension})) vector_cosine_ops) WHERE dimension = {dimension}"
        )
    op.create_table(
        "rag_documents",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("fingerprint", sa.String(64), unique=True, nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("namespace", sa.String(20), nullable=False),
        sa.Column(
            "case_id", UUID(), sa.ForeignKey("analysis_cases.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("file_key", sa.Text()),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_rag_documents_status", "rag_documents", ["status"])


def downgrade() -> None:
    """Remove only this module; retain vector extension and all market tables."""
    op.drop_table("rag_documents")
    op.drop_table("analysis_case_embeddings")
    op.drop_table("analysis_cases")
