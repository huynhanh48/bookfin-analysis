"""ORM model cho indicator snapshot và sự kiện cấu trúc thị trường."""

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IndicatorSnapshot(Base):
    """Kết quả chỉ báo đã precompute cho một nến.

    Khoá nghiệp vụ gồm symbol, timeframe, thời điểm nến, tên chỉ báo, hash tham
    số và phiên bản engine. Khi logic hoặc tham số đổi, hash/version đổi theo nên
    bản ghi cũ vẫn được giữ để đối chiếu.

    Attributes:
        id: Khoá kỹ thuật.
        symbol: Mã chứng khoán.
        timeframe: Khung thời gian.
        candle_time: Thời điểm nến mà chỉ báo được tính.
        indicator: Tên chỉ báo, ví dụ rsi.
        params_hash: Hash canonical của tham số.
        engine_version: Phiên bản logic tính.
        computed_at: Thời điểm tính.
        payload: Toàn bộ kết quả dạng JSON.
    """

    __tablename__ = "indicator_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "timeframe",
            "candle_time",
            "indicator",
            "params_hash",
            "engine_version",
            name="uq_indicator_snapshots_identity",
        ),
        Index(
            "ix_indicator_snapshots_symbol_timeframe_indicator_candle_time",
            "symbol",
            "timeframe",
            "indicator",
            "candle_time",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(
        String(20), ForeignKey("symbols.symbol", ondelete="RESTRICT"), nullable=False
    )
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    candle_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    indicator: Mapped[str] = mapped_column(String(32), nullable=False)
    params_hash: Mapped[str] = mapped_column(String(32), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)


class StructureEvent(Base):
    """Sự kiện cấu trúc thị trường đã xác nhận.

    `idempotency_key` chống ghi trùng khi worker chạy lại cùng một đoạn dữ liệu.

    Attributes:
        id: Khoá kỹ thuật.
        symbol: Mã chứng khoán.
        timeframe: Khung thời gian.
        event_type: Loại sự kiện, ví dụ BOS_BULLISH.
        pivot_time: Thời điểm bar tạo swing bị phá.
        confirmed_at: Thời điểm swing được xác nhận.
        event_time: Thời điểm nến đóng phá mức swing.
        break_level: Mức giá bị phá.
        observed_price: Giá dùng để xác nhận phá.
        bias_before: Break bias trước sự kiện.
        bias_after: Break bias sau sự kiện.
        config_hash: Hash cấu hình engine.
        engine_version: Phiên bản engine.
        payload: Dữ liệu bổ sung dạng JSON.
        idempotency_key: Khoá chống trùng sự kiện.
    """

    __tablename__ = "structure_events"
    __table_args__ = (
        Index(
            "ix_structure_events_symbol_timeframe_event_time", "symbol", "timeframe", "event_time"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(
        String(20), ForeignKey("symbols.symbol", ondelete="RESTRICT"), nullable=False
    )
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    pivot_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    break_level: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    observed_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    bias_before: Mapped[str] = mapped_column(String(16), nullable=False)
    bias_after: Mapped[str] = mapped_column(String(16), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(32), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
