"""Schema cho API dữ liệu thị trường."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Timeframe = Literal["1D", "1W"]


class SymbolOut(BaseModel):
    """Một mã chứng khoán trong danh mục.

    Attributes:
        symbol: Mã chứng khoán.
        exchange: Sàn giao dịch.
        company_name: Tên công ty.
        sector: Ngành.
        active: Mã còn giao dịch hay không.
        updated_at: Thời điểm đồng bộ gần nhất.
    """

    model_config = ConfigDict(from_attributes=True)

    symbol: str = Field(examples=["FPT"])
    exchange: str | None = None
    company_name: str | None = None
    sector: str | None = None
    active: bool = True
    updated_at: datetime | None = None


class SymbolListResponse(BaseModel):
    """Danh sách mã chứng khoán có phân trang."""

    items: list[SymbolOut]
    total: int
    limit: int
    offset: int


class CandleOut(BaseModel):
    """Một nến OHLCV đã đóng.

    Attributes:
        time: Thời điểm mở nến theo UTC.
        open: Giá mở cửa.
        high: Giá cao nhất.
        low: Giá thấp nhất.
        close: Giá đóng cửa.
        volume: Khối lượng khớp.
        value: Giá trị khớp.
        price_basis: RAW hoặc ADJUSTED.
        source: Nguồn dữ liệu.
    """

    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    value: float | None = None
    price_basis: str = "RAW"
    source: str = "bookfin-fcdata"


class CandleListResponse(BaseModel):
    """Nến đã đóng của một mã, sắp xếp tăng dần theo thời gian."""

    symbol: str
    timeframe: str
    count: int
    candles: list[CandleOut]


class QuoteOut(BaseModel):
    """Snapshot giá gần nhất của một mã.

    Attributes:
        symbol: Mã chứng khoán.
        price: Giá khớp gần nhất.
        change_percent: Phần trăm thay đổi so với giá tham chiếu.
        volume: Khối lượng khớp trong phiên.
        as_of: Thời điểm dữ liệu được ghi nhận.
        is_stale: True khi dữ liệu cũ hơn ngưỡng cấu hình.
        source: Nguồn của snapshot: gateway, cache hay database.
        note: Ghi chú khi phải dùng bản ghi cũ.
    """

    symbol: str
    price: float | None = None
    change_percent: float | None = None
    volume: int | None = None
    as_of: datetime | None = None
    is_stale: bool = True
    source: Literal["gateway", "cache", "database", "unavailable"] = "unavailable"
    note: str | None = None


class BackfillRequest(BaseModel):
    """Yêu cầu nạp lịch sử nến.

    Attributes:
        symbols: Danh sách mã cần nạp.
        timeframe: Khung thời gian.
        start: Ngày bắt đầu, mặc định lùi theo cấu hình.
        end: Ngày kết thúc, mặc định hôm nay theo múi giờ thị trường.
    """

    symbols: list[str] = Field(min_length=1, max_length=50, examples=[["FPT", "HPG"]])
    timeframe: Timeframe = "1D"
    start: date | None = None
    end: date | None = None


class BackfillResult(BaseModel):
    """Kết quả nạp lịch sử cho một hoặc nhiều mã."""

    requested: int
    succeeded: int
    failed: int
    inserted: int
    rejected: int
    errors: dict[str, str] = Field(default_factory=dict)
