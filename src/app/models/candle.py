"""ORM model cho nến OHLCV đã đóng."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Candle(Base):
    """Một nến OHLCV đã đóng.

    Bảng chỉ chứa nến đã xác nhận đóng. Nến đang hình thành được giữ ở Redis
    hoặc bộ nhớ với trạng thái FORMING và không bao giờ ghi đè bản ghi đã đóng.

    Attributes:
        id: Khoá kỹ thuật tăng dần.
        symbol: Mã chứng khoán.
        timeframe: Khung thời gian, ví dụ 1D.
        candle_open_time: Thời điểm mở nến, lưu theo UTC.
        open: Giá mở cửa.
        high: Giá cao nhất.
        low: Giá thấp nhất.
        close: Giá đóng cửa.
        volume: Khối lượng khớp.
        turnover: Giá trị khớp nếu nguồn trả về.
        price_basis: RAW hoặc ADJUSTED.
        source: Nguồn dữ liệu.
        source_updated_at: Thời điểm nguồn cập nhật bản ghi.
        created_at: Thời điểm ghi bản ghi.
    """

    __tablename__ = "candles"
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "timeframe",
            "candle_open_time",
            "price_basis",
            name="uq_candles_symbol_timeframe_candle_open_time_price_basis",
        ),
        Index(
            "ix_candles_symbol_timeframe_candle_open_time",
            "symbol",
            "timeframe",
            "candle_open_time",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(
        String(20), ForeignKey("symbols.symbol", ondelete="RESTRICT"), nullable=False
    )
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    candle_open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    turnover: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    price_basis: Mapped[str] = mapped_column(String(16), default="RAW", server_default="RAW")
    source: Mapped[str] = mapped_column(String(32), default="bookfin-fcdata")
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
