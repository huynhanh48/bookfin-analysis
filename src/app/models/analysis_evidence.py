"""Evidence records extend existing cases/jobs without duplicating market data."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AnalysisEnrichment(Base):
    """Versioned normalized decision-time representation; original case remains intact."""

    __tablename__ = "analysis_enrichments"
    # Expression index used by lexical case retrieval; keep it declared so Alembic
    # does not treat the migration-owned index as drift.
    __table_args__ = (
        Index(
            "ix_enrichment_lexical",
            text("to_tsvector('simple', normalized_text)"),
            postgresql_using="gin",
        ),
    )
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("analysis_cases.id", ondelete="CASCADE"), primary_key=True
    )
    original_input: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    observations: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    resolved_context: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    technical_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    normalized_text: Mapped[str] = mapped_column(Text, default="")
    verification_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    diagnostics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    version: Mapped[str] = mapped_column(String(40), default="evidence-v1")


class AnalysisClaim(Base):
    """Individually verifiable assertion, stored independently from immutable thesis."""

    __tablename__ = "analysis_claims"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("analysis_cases.id", ondelete="CASCADE"), index=True
    )
    claim_type: Mapped[str] = mapped_column(String(50))
    source_type: Mapped[str] = mapped_column(String(20))
    verification_status: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class NewsDocument(Base):
    """Content-addressed article revisions; never overwrite a historical captured revision."""

    __tablename__ = "news_documents"
    __table_args__ = (Index("ix_news_url_hash", "canonical_url", "content_hash", unique=True),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source_url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    publisher: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    relevance: Mapped[float]
    associated_symbol: Mapped[str] = mapped_column(String(20), index=True)
    associated_sector: Mapped[str | None] = mapped_column(String(200))
    source_type: Mapped[str] = mapped_column(String(30), index=True)


class AnalysisEvidence(Base):
    """Case-to-source association and technical/vision provenance references."""

    __tablename__ = "analysis_evidence"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("analysis_cases.id", ondelete="CASCADE"), index=True
    )
    news_id: Mapped[UUID | None] = mapped_column(ForeignKey("news_documents.id"))
    source_type: Mapped[str] = mapped_column(String(30), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ProviderCache(Base):
    """Durable bounded-size provider results; keys include provider/model/input versions."""

    __tablename__ = "evidence_provider_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
