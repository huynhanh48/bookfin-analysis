"""Analysis source records and independently versioned retrieval vectors."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, cast, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AnalysisCase(Base):
    """Original reasoning remains immutable; outcomes are independently updated."""

    __allow_unmapped__ = True
    job_id: UUID | None = None
    enrichment: dict[str, Any]

    __tablename__ = "analysis_cases"
    __table_args__ = (Index("ix_analysis_cases_filters", "symbol", "timeframe", "analyzed_at"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    symbol: Mapped[str | None] = mapped_column(String(20), index=True)
    timeframe: Mapped[str | None] = mapped_column(String(20), index=True)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    thesis: Mapped[str] = mapped_column(Text)
    chart_path: Mapped[str | None] = mapped_column(Text)
    indicator_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    market_context: Mapped[dict[str, Any]] = mapped_column(JSONB)
    conclusion: Mapped[str] = mapped_column(Text)
    outcome_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(Text)
    ingest_status: Mapped[str] = mapped_column(String(20), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CaseEmbedding(Base):
    """Variable-dimensional vector plus explicit space identity prevents mixing models."""

    __tablename__ = "analysis_case_embeddings"
    __table_args__ = (
        CheckConstraint("vector_dims(embedding) = dimension", name="embedding_dimension"),
        Index("ix_case_embeddings_space", "space", "dimension"),
    )
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("analysis_cases.id", ondelete="CASCADE"), primary_key=True
    )
    space: Mapped[str] = mapped_column(String(300), primary_key=True)
    embedding_model: Mapped[str] = mapped_column(String(200))
    dimension: Mapped[int]
    representation_version: Mapped[str] = mapped_column(String(40))
    representation_kind: Mapped[str] = mapped_column(String(20), default="legacy")
    content_hash: Mapped[str | None] = mapped_column(String(64))
    image_hash: Mapped[str | None] = mapped_column(String(64))
    embedding: Mapped[list[float]] = mapped_column(Vector())


# New independent representations have model/dimension/version-specific partial indexes.
# Additional configured models remain searchable by exact SQL scan until an explicit migration adds their index.
for _name, _model, _dimension, _version, _kind in [
    ("ix_case_legacy_openai_1536", "openai/text-embedding-3-small", 1536, "case-v1", "legacy"),
    ("ix_case_legacy_voyage_1024", "mongodb/voyage-multimodal-3.5", 1024, "case-v1", "legacy"),
    ("ix_case_text_v2_openai_1536", "openai/text-embedding-3-small", 1536, "case-text-v2", "text"),
    (
        "ix_case_image_v2_voyage_1024",
        "mongodb/voyage-multimodal-3.5",
        1024,
        "case-image-v2",
        "multimodal",
    ),
]:
    Index(
        _name,
        cast(CaseEmbedding.embedding, Vector(_dimension)).label("embedding"),
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_where=(CaseEmbedding.space == f"{_model}:{_dimension}:{_version}")
        & (CaseEmbedding.dimension == _dimension)
        & (CaseEmbedding.representation_kind == _kind),
    )
