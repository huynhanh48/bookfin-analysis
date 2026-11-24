"""Schema API cho watchlist."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

_SYMBOL_PATTERN = r"^[A-Z0-9._-]{1,20}$"


class WatchlistCreate(BaseModel):
    """Tạo watchlist mới.

    Attributes:
        name: Tên hiển thị, không rỗng.
    """

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)


class WatchlistUpdate(BaseModel):
    """Đổi tên hoặc thứ tự watchlist."""

    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    sort_order: int | None = Field(default=None, ge=0, le=100_000)


class WatchlistOut(BaseModel):
    """Một watchlist kèm số symbol."""

    model_config = ConfigDict(from_attributes=True)
    id: int
    owner_id: str
    name: str
    sort_order: int
    is_default: bool
    item_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WatchlistListResponse(BaseModel):
    """Danh sách watchlist của owner."""

    items: list[WatchlistOut]
    total: int


class WatchlistItemOut(BaseModel):
    """Một symbol trong watchlist."""

    model_config = ConfigDict(from_attributes=True)
    symbol: str
    sort_order: int
    created_at: datetime | None = None


class WatchlistItemsResponse(BaseModel):
    """Toàn bộ symbol của một watchlist."""

    watchlist_id: int
    name: str
    items: list[WatchlistItemOut]
    count: int


class WatchlistBulkAddRequest(BaseModel):
    """Thêm nhiều symbol trong một transaction."""

    model_config = ConfigDict(extra="forbid")
    symbols: list[str] = Field(min_length=1, max_length=1000)

    @field_validator("symbols")
    @classmethod
    def _normalize(cls, values: list[str]) -> list[str]:
        """Chuẩn hoá uppercase, bỏ rỗng và giữ thứ tự."""
        result = [value.strip().upper() for value in values if value.strip()]
        if not result:
            raise ValueError("symbols phải có ít nhất một mã")
        return result


class WatchlistBulkAddResult(BaseModel):
    """Kết quả thêm nhiều symbol."""

    watchlist_id: int
    added: int
    skipped: int
    symbols: list[str]


class WatchlistSymbolRequest(BaseModel):
    """Thêm một symbol."""

    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(pattern=_SYMBOL_PATTERN, max_length=20)

    @field_validator("symbol")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()
