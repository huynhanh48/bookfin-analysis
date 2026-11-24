"""Schema cho Stock Screener.

Cấu trúc điều kiện được tách thành từng nhóm có `enabled` để có thể thêm RSI,
MACD, ADX, Breakout, Divergence sau này mà không đổi contract scan.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ScreenerMode = Literal["historical", "realtime"]
ScreenerStatus = Literal["LIVE", "CONFIRMED", "STALE"]
ScreenerFeedStatus = Literal["LIVE", "STALE", "OFFLINE"]
ScreenerUniverse = Literal["all", "symbols"]


class ToggleCondition(BaseModel):
    """Điều kiện có công tắc bật/tắt."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = True


class MinVolumeCondition(ToggleCondition):
    """DK1: volume tối thiểu."""

    value: int = Field(default=100_000, ge=0, le=10_000_000_000)


class MinPriceCondition(ToggleCondition):
    """DK2: giá tối thiểu, đơn vị VND."""

    value: float = Field(default=10_000, ge=0, le=10_000_000)


class RelativeVolumeCondition(ToggleCondition):
    """DK3: volume hiện tại so với SMA volume các phiên trước."""

    value: float = Field(default=0.8, ge=0, le=100)
    lookback: int = Field(default=10, ge=1, le=60)


class PriceChangeCondition(ToggleCondition):
    """DK4: phần trăm tăng so với close phiên trước."""

    percent: float = Field(default=2.0, ge=-100, le=1000)


class PriceVsMA5Condition(ToggleCondition):
    """DK6: close cao hơn MA5 theo phần trăm."""

    percent: float = Field(default=2.0, ge=-100, le=1000)
    period: int = Field(default=5, ge=1, le=60)


class DivergenceCondition(ToggleCondition):
    """Bộ lọc phân kỳ đã xác nhận trên nến đóng.

    Mặc định tắt để giữ nguyên chiến lược AFL gốc.

    Attributes:
        window: `latest` (N phiên gần nhất; 1 = phiên đóng gần nhất) hoặc
            `range` (khoảng ngày tùy chỉnh).
        date_from: Ngày bắt đầu inclusive khi `window=range`.
        date_to: Ngày kết thúc inclusive khi `window=range`.
        days: Số phiên gần nhất khi `window=latest`; 1 = phiên gần nhất.
        basis: `pivot` lọc theo đỉnh/đáy thứ hai của phân kỳ (ngày nhìn thấy
            trên chart), `confirmed` lọc theo nến xác nhận tín hiệu.
    """

    enabled: bool = False
    direction: Literal["bullish", "bearish", "any"] = "bullish"
    kind: Literal["regular", "hidden", "any"] = "regular"
    window: Literal["latest", "today", "recent", "range"] = "latest"
    days: int = Field(default=1, ge=1, le=60)
    basis: Literal["pivot", "confirmed"] = "pivot"
    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def _validate_window(self) -> DivergenceCondition:
        """Chuẩn hoá alias cũ và validate range."""
        if self.window in ("today", "recent"):
            self.window = "latest"
        if self.window == "range":
            if self.date_from is None or self.date_to is None:
                raise ValueError("Divergence range yêu cầu date_from và date_to")
            if self.date_from > self.date_to:
                raise ValueError("date_from phải <= date_to")
        return self


class ScreenerConditions(BaseModel):
    """Toàn bộ điều kiện của chiến lược lọc dòng tiền AFL."""

    model_config = ConfigDict(extra="forbid")
    min_volume: MinVolumeCondition = Field(default_factory=MinVolumeCondition)
    min_price: MinPriceCondition = Field(default_factory=MinPriceCondition)
    relative_volume: RelativeVolumeCondition = Field(
        default_factory=RelativeVolumeCondition
    )
    price_change: PriceChangeCondition = Field(default_factory=PriceChangeCondition)
    price_vs_ma5: PriceVsMA5Condition = Field(default_factory=PriceVsMA5Condition)
    divergence: DivergenceCondition = Field(default_factory=DivergenceCondition)


class ScreenerScanRequest(BaseModel):
    """Yêu cầu scan historical hoặc realtime."""

    model_config = ConfigDict(extra="forbid")
    mode: ScreenerMode = "historical"
    universe: ScreenerUniverse = "all"
    symbols: list[str] | None = Field(default=None, max_length=1000)
    conditions: ScreenerConditions = Field(default_factory=ScreenerConditions)
    as_of: datetime | None = None
    limit: int = Field(default=1000, ge=1, le=2000)

    @field_validator("symbols")
    @classmethod
    def _normalize_symbols(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        result: list[str] = []
        seen: set[str] = set()
        for item in value:
            symbol = str(item).strip().upper()
            if symbol and symbol not in seen:
                seen.add(symbol)
                result.append(symbol)
        return result

    @field_validator("as_of")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("as_of phải có timezone")
        return value

    @model_validator(mode="after")
    def _validate_universe(self) -> ScreenerScanRequest:
        if self.universe == "symbols" and not self.symbols:
            raise ValueError("universe=symbols yêu cầu danh sách symbols")
        return self


class ScreenerRow(BaseModel):
    """Một symbol thỏa toàn bộ điều kiện đang bật."""

    symbol: str
    price: float
    change_percent: float
    volume: int
    volume_sma: float
    relative_volume: float
    ma5: float
    price_vs_ma5_percent: float
    status: ScreenerStatus
    updated_at: datetime
    matched_conditions: list[str] = Field(default_factory=list)
    divergence: str | None = None


class ScreenerScanResponse(BaseModel):
    """Kết quả một lần scan."""

    profile_id: str
    mode: ScreenerMode
    as_of: datetime
    generated_at: datetime
    feed_status: ScreenerFeedStatus = "OFFLINE"
    total_universe: int
    evaluated: int
    matched: int
    skipped: int
    results: list[ScreenerRow]


class ScreenerResultsResponse(ScreenerScanResponse):
    """Snapshot realtime đã lưu trong Redis."""


class ScreenerRealtimeEvent(BaseModel):
    """Payload event realtime gửi qua WebSocket."""

    profile_id: str
    rows: list[ScreenerRow] = Field(default_factory=list)
    entered: list[ScreenerRow] = Field(default_factory=list)
    exited: list[str] = Field(default_factory=list)
    feed_status: ScreenerFeedStatus = "OFFLINE"
    generated_at: datetime
    scan_seed_at: datetime | None = None
