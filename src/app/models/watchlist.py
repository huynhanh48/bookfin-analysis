"""Watchlist do người dùng quản lý.

Watchlist là dữ liệu bền vững trong PostgreSQL, không lưu chỉ ở localStorage hay
Redis. `owner_id` hiện dùng giá trị `"local"` vì hệ thống chưa có authentication,
nhưng đã là khoá truy vấn để sau này gắn principal mà không đổi schema.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Watchlist(Base):
    """Một danh sách mã người dùng quan tâm.

    Attributes:
        id: Khoá kỹ thuật.
        owner_id: Chủ sở hữu; `"local"` khi chưa có auth.
        name: Tên hiển thị.
        sort_order: Thứ tự trong sidebar.
        is_default: True với 10 danh sách mặc định (không được xoá).
        created_at: Thời điểm tạo.
        updated_at: Thời điểm cập nhật gần nhất.
    """

    __tablename__ = "watchlists"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_watchlists_owner_id_name"),
        Index("ix_watchlists_owner_id_sort_order", "owner_id", "sort_order"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default="local", server_default="local"
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class WatchlistItem(Base):
    """Một symbol trong watchlist.

    Attributes:
        id: Khoá kỹ thuật.
        watchlist_id: Watchlist chứa symbol.
        symbol: Mã chứng khoán.
        sort_order: Thứ tự hiển thị trong danh sách.
        created_at: Thời điểm thêm.
    """

    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint("watchlist_id", "symbol", name="uq_watchlist_items_watchlist_id_symbol"),
        Index("ix_watchlist_items_watchlist_id_sort_order", "watchlist_id", "sort_order"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    watchlist_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("watchlists.id", ondelete="CASCADE"),
        nullable=False,
    )
    symbol: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("symbols.symbol", ondelete="CASCADE"),
        nullable=False,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
