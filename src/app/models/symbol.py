"""ORM model cho danh mục mã chứng khoán."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Symbol(Base):
    """Mã chứng khoán và metadata tĩnh.

    Attributes:
        symbol: Mã chứng khoán, khoá chính.
        exchange: Sàn giao dịch, ví dụ HOSE.
        company_name: Tên công ty nếu nguồn có.
        sector: Ngành nếu nguồn có.
        active: False khi mã ngừng giao dịch.
        updated_at: Thời điểm bản ghi được cập nhật gần nhất.
    """

    __tablename__ = "symbols"

    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    exchange: Mapped[str | None] = mapped_column(String(20))
    company_name: Mapped[str | None] = mapped_column(String(255))
    sector: Mapped[str | None] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
