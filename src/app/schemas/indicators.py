"""Schema cho kết quả chỉ báo kỹ thuật."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

IndicatorStatus = Literal["ok", "insufficient_history"]


class IndicatorResult(BaseModel):
    """Kết quả một chỉ báo tại một thời điểm.

    Attributes:
        symbol: Mã chứng khoán.
        timeframe: Khung thời gian.
        as_of: Thời điểm nến dùng để tính.
        indicator: Tên chỉ báo, ví dụ rsi.
        parameters: Tham số đã dùng.
        values: Giá trị từng dòng; None khi chưa đủ dữ liệu.
        signal: Nhãn tín hiệu nếu có, ví dụ overbought.
        warmup: Số nến tối thiểu cần có.
        status: ok hoặc insufficient_history.
        is_final_candle: True khi nến cuối cùng đã đóng.
        engine_version: Phiên bản logic tính.
    """

    symbol: str
    timeframe: str
    as_of: datetime | None = None
    indicator: str = Field(examples=["rsi"])
    parameters: dict[str, int | float | str] = Field(default_factory=dict)
    values: dict[str, float | None] = Field(default_factory=dict)
    signal: str | None = None
    warmup: int = 0
    status: IndicatorStatus = "ok"
    is_final_candle: bool = True
    engine_version: str = "ind-1.0.0"


class IndicatorSetResponse(BaseModel):
    """Tập chỉ báo của một mã tại nến đã đóng gần nhất.

    Attributes:
        symbol: Mã chứng khoán.
        timeframe: Khung thời gian.
        as_of: Thời điểm nến gần nhất.
        candles_used: Số nến đã dùng để tính.
        computed_on_demand: True khi kết quả được tính ngay trong request.
        results: Danh sách kết quả chỉ báo.
    """

    symbol: str
    timeframe: str
    as_of: datetime | None = None
    candles_used: int = 0
    computed_on_demand: bool = False
    results: list[IndicatorResult] = Field(default_factory=list)
