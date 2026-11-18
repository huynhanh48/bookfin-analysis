"""ORM model audit cho các lần chạy nạp dữ liệu và worker."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IngestionRun(Base):
    """Một lần chạy backfill hoặc worker.

    Attributes:
        id: Khoá kỹ thuật.
        source: Nguồn dữ liệu, ví dụ bookfin-fcdata.
        job_type: Loại job, ví dụ backfill.
        started_at: Thời điểm bắt đầu.
        ended_at: Thời điểm kết thúc; None khi job còn chạy.
        status: running, success, partial hoặc failed.
        counts: Số bản ghi đọc/ghi/lỗi dạng JSON.
        error_summary: Mô tả ngắn lỗi gần nhất.
    """

    __tablename__ = "ingestion_runs"
    __table_args__ = (
        Index("ix_ingestion_runs_source_job_type_started_at", "source", "job_type", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="running", server_default="running")
    counts: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    error_summary: Mapped[str | None] = mapped_column(Text)
