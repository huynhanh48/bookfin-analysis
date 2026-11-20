"""Tính chỉ báo cho nến đang chạy (provisional) bằng đúng engine của BE.

Bar realtime không được persist và không dùng cho claim verification; mục đích
duy nhất là hiển thị chỉ báo mượt trên chart với cùng công thức TA-Lib như chuỗi
lịch sử, tránh lệch số giữa nến đã đóng và nến đang chạy.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from time import monotonic
from typing import Protocol

import numpy as np

from app.core.database import session_scope
from app.core.logging import get_logger
from app.indicators import Ohlcv
from app.indicators.registry import compute_indicator
from app.repositories.market_repository import MarketRepository

logger = get_logger(__name__)

# Đủ warmup cho Ichimoku (52+26) và các chỉ báo EMA/ADX.
CLOSED_LOOKBACK = 400
CACHE_TTL_SECONDS = 60.0

# Bộ chỉ báo chart cần cho nến đang chạy; divergence bỏ qua vì cần pivot đã xác nhận.
REALTIME_INDICATORS: tuple[tuple[str, dict[str, int | float]], ...] = (
    ("ichimoku", {}),
    ("macd", {"fast_period": 12, "slow_period": 26, "signal_period": 9}),
    ("rsi", {"period": 14}),
    ("adx", {"period": 14}),
)


@dataclass(frozen=True, slots=True)
class OhlcvRow:
    """Một nến OHLCV tối thiểu để dựng mảng tính chỉ báo."""

    open: float
    high: float
    low: float
    close: float
    volume: float
    day: date | None = None
    opened_at: datetime | None = None


class ClosedCandleLoader(Protocol):
    """Nguồn nến đã đóng cho việc tính chỉ báo realtime."""

    async def __call__(self, symbol: str, limit: int) -> Sequence[OhlcvRow]: ...


async def load_closed_daily(symbol: str, limit: int = CLOSED_LOOKBACK) -> list[OhlcvRow]:
    """Đọc nến ngày đã đóng từ PostgreSQL.

    Args:
        symbol: Mã chứng khoán.
        limit: Số nến tối đa.

    Returns:
        Danh sách nến tăng dần; rỗng khi chưa có dữ liệu.
    """
    async with session_scope() as session:
        rows = await MarketRepository(session).get_candles(symbol, "1D", limit=limit)
    return [
        OhlcvRow(
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
            day=row.candle_open_time.date(),
            opened_at=row.candle_open_time,
        )
        for row in rows
    ]


def compute_realtime_indicators(rows: Sequence[OhlcvRow]) -> dict[str, dict[str, float | None]]:
    """Tính giá trị chỉ báo tại nến cuối của chuỗi (đã gồm nến provisional).

    Args:
        rows: Nến đã đóng, phần tử cuối là nến đang chạy.

    Returns:
        Mapping tên chỉ báo → giá trị tại bar cuối. Rỗng khi không đủ dữ liệu.
    """
    if len(rows) < 30:
        return {}
    ohlcv = Ohlcv(
        open=np.asarray([row.open for row in rows], dtype=np.float64),
        high=np.asarray([row.high for row in rows], dtype=np.float64),
        low=np.asarray([row.low for row in rows], dtype=np.float64),
        close=np.asarray([row.close for row in rows], dtype=np.float64),
        volume=np.asarray([row.volume for row in rows], dtype=np.float64),
    )
    result: dict[str, dict[str, float | None]] = {}
    for name, parameters in REALTIME_INDICATORS:
        try:
            output = compute_indicator(name, ohlcv, parameters)
        except (ValueError, KeyError) as exc:
            logger.warning(
                "realtime_indicator_failed",
                extra={"indicator": name, "error_type": type(exc).__name__},
            )
            continue
        result[name] = {key: value for key, value in output.values.items()}
    return result


Loader = Callable[[str, int], Awaitable[Sequence[OhlcvRow]]]


class ClosedCandleCache:
    """Cache nến đã đóng theo mã để vòng realtime không đọc DB mỗi giây."""

    def __init__(self, loader: Loader, ttl_seconds: float = CACHE_TTL_SECONDS) -> None:
        self._loader = loader
        self._ttl = ttl_seconds
        self._rows: dict[str, list[OhlcvRow]] = {}
        self._expiry: dict[str, float] = {}

    async def get(self, symbol: str, limit: int = CLOSED_LOOKBACK) -> list[OhlcvRow]:
        """Trả nến đã đóng, tự nạp lại khi hết hạn TTL."""
        now = monotonic()
        cached = self._rows.get(symbol)
        if cached is not None and self._expiry.get(symbol, 0.0) > now:
            return cached
        rows = list(await self._loader(symbol, limit))
        self._rows[symbol] = rows
        self._expiry[symbol] = now + self._ttl
        return rows

    def invalidate(self, symbol: str) -> None:
        """Bỏ cache của một mã (dùng khi có nến mới đóng)."""
        self._rows.pop(symbol, None)
        self._expiry.pop(symbol, None)


def build_default_cache() -> ClosedCandleCache:
    """Tạo cache đọc trực tiếp từ PostgreSQL của service hiện tại."""

    async def loader(symbol: str, limit: int) -> Sequence[OhlcvRow]:
        return await load_closed_daily(symbol, limit)

    return ClosedCandleCache(loader)
