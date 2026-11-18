"""Schema cho cấu trúc thị trường.

Các model ở đây dùng `from_attributes=True` để dựng trực tiếp từ dataclass của
engine (`app.indicators.market_structure`), nhờ đó không cần lớp chuyển đổi riêng.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PivotKind = Literal["HIGH", "LOW"]
SwingLabel = Literal["HH", "HL", "LH", "LL", "EQH", "EQL"]
BreakBias = Literal["BULLISH", "BEARISH", "NEUTRAL"]
ConfirmedTrend = Literal["UPTREND", "DOWNTREND", "SIDEWAYS", "TRANSITION"]
StructureEventType = Literal[
    "BOS_BULLISH",
    "BOS_BEARISH",
    "CHOCH_BULLISH",
    "CHOCH_BEARISH",
    "MSS_BULLISH",
    "MSS_BEARISH",
    "FAILED_BREAKOUT_BULLISH",
    "FAILED_BREAKOUT_BEARISH",
]


class SwingPointOut(BaseModel):
    """Một pivot đã được xác nhận.

    Attributes:
        kind: HIGH hoặc LOW.
        label: HH/HL/LH/LL hoặc EQH/EQL; None khi chưa có pivot trước.
        price: Giá tại pivot.
        pivot_time: Thời điểm bar tạo pivot.
        confirmed_at: Thời điểm bar xác nhận pivot.
        index: Vị trí bar tạo pivot trong chuỗi đầu vào.
    """

    model_config = ConfigDict(from_attributes=True)

    kind: PivotKind
    label: SwingLabel | None = None
    price: float
    pivot_time: datetime
    confirmed_at: datetime
    index: int


class StructureEventOut(BaseModel):
    """Một sự kiện phá cấu trúc đã xác nhận.

    Attributes:
        event_type: Loại sự kiện.
        pivot_time: Thời điểm bar tạo swing bị phá.
        confirmed_at: Thời điểm swing được xác nhận.
        broken_at: Thời điểm nến đóng phá mức swing.
        price: Giá tại swing bị phá.
        break_level: Mức giá bị phá.
        observed_price: Giá dùng để xác nhận phá.
        bias_before: Bias trước sự kiện.
        bias_after: Bias sau sự kiện.
        is_displacement: True khi nến phá có thân lớn hơn ATR × hệ số.
    """

    model_config = ConfigDict(from_attributes=True)

    event_type: StructureEventType
    pivot_time: datetime
    confirmed_at: datetime
    broken_at: datetime
    price: float
    break_level: float
    observed_price: float
    bias_before: BreakBias
    bias_after: BreakBias
    is_displacement: bool = False


class StructureSnapshotOut(BaseModel):
    """Snapshot cấu trúc thị trường của một mã.

    Attributes:
        symbol: Mã chứng khoán.
        timeframe: Khung thời gian.
        as_of: Thời điểm nến đã đóng gần nhất.
        status: ok hoặc insufficient_history.
        break_bias: Bias phá cấu trúc, tương đương biến trend trong Pine.
        confirmed_trend: Xu hướng chỉ xác nhận khi có HH+HL hoặc LH+LL.
        last_swing_high: Swing high gần nhất.
        last_swing_low: Swing low gần nhất.
        protected_high: Kháng cự chưa bị phá gần nhất.
        protected_low: Hỗ trợ chưa bị phá gần nhất.
        swings: Các swing đã xác nhận.
        events: Các sự kiện đã xác nhận.
        latest_event: Sự kiện gần nhất.
        engine_version: Phiên bản engine.
        config_hash: Hash cấu hình đã dùng.
        candle_finality: closed khi mọi nến đầu vào đã đóng.
        data_quality: Thống kê kiểm tra chất lượng dữ liệu đầu vào.
    """

    model_config = ConfigDict(from_attributes=True)

    symbol: str
    timeframe: str
    as_of: datetime | None = None
    status: Literal["ok", "insufficient_history"] = "ok"
    break_bias: BreakBias = "NEUTRAL"
    confirmed_trend: ConfirmedTrend = "SIDEWAYS"
    last_swing_high: SwingPointOut | None = None
    last_swing_low: SwingPointOut | None = None
    protected_high: SwingPointOut | None = None
    protected_low: SwingPointOut | None = None
    swings: list[SwingPointOut] = Field(default_factory=list)
    events: list[StructureEventOut] = Field(default_factory=list)
    latest_event: StructureEventOut | None = None
    engine_version: str = "ms-1.0.0"
    config_hash: str = ""
    candle_finality: Literal["closed", "forming"] = "closed"
    data_quality: dict[str, Any] = Field(default_factory=dict)
