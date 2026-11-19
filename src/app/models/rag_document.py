"""Durable PostgreSQL ingestion queue and source of truth for knowledge uploads."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RagDocument(Base):
    """Content-addressed job with a bounded processing lease and safe error code."""

    __tablename__ = "rag_documents"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(20))
    namespace: Mapped[str] = mapped_column(String(20), default="knowledge")
    case_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("analysis_cases.id", ondelete="CASCADE")
    )
    source: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, default="")
    file_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    error_code: Mapped[str | None] = mapped_column(String(80))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stage: Mapped[str] = mapped_column(String(20), default="PENDING")
    progress: Mapped[int] = mapped_column(default=0)
    diagnostics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(100))
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
