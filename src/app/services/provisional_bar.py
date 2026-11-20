"""Nến đang chạy (provisional) lấy từ FCData cho endpoint chart.

Chỉ dùng để hiển thị: bar này không được persist, không tham gia tính chỉ báo đã
xác nhận và không tạo tín hiệu phân kỳ/cấu trúc.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from time import monotonic
from typing import Protocol
from zoneinfo import ZoneInfo

from app.core.config import Settings
from app.core.logging import get_logger
from app.fcdata.mapper import CandleRecord, format_vn_date, map_candles
from app.services.realtime_indicators import OhlcvRow

logger = get_logger(__name__)

CACHE_TTL_SECONDS = 2.0
_SHARED_CACHE: dict[str, tuple[float, "ProvisionalBar | None"]] = {}


class DailyOhlcProvider(Protocol):
    """Phần client FCData mà provider cần."""

    async def fetch_daily_ohlc(
        self, symbol: str, *, start: str, end: str, page_index: int = 1, page_size: int = 100
    ) -> dict: ...


@dataclass(frozen=True, slots=True)
class ProvisionalBar:
    """Bar đang chạy của phiên hiện tại.

    Attributes:
        bar: OHLCV đã chuẩn hoá.
        open_time: Thời điểm mở bar theo UTC (00:00 giờ thị trường).
        trading_day: Ngày giao dịch theo giờ thị trường.
    """

    bar: OhlcvRow
    open_time: datetime
    trading_day: date


class ProvisionalBarProvider:
    """Đọc snapshot phiên hiện tại với cache ngắn để không gọi FCData mỗi request."""

    def __init__(
        self,
        settings: Settings,
        provider: DailyOhlcProvider,
        ttl_seconds: float = CACHE_TTL_SECONDS,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._ttl = ttl_seconds
        # Cache dùng chung giữa các request (provider được tạo theo request).
        self._cache = _SHARED_CACHE
        self._tz: ZoneInfo = settings.market_tz

    async def fetch(self, symbol: str) -> ProvisionalBar | None:
        """Trả bar đang chạy của hôm nay, hoặc None khi không có dữ liệu.

        Args:
            symbol: Mã chứng khoán.

        Returns:
            `ProvisionalBar` hoặc None nếu FCData không trả dữ liệu hôm nay.
        """
        ticker = symbol.upper()
        now = monotonic()
        cached = self._cache.get(ticker)
        if cached is not None and now - cached[0] < self._ttl:
            return cached[1]

        today = datetime.now(self._tz).date()
        bar: ProvisionalBar | None = None
        try:
            payload = await self._provider.fetch_daily_ohlc(
                ticker,
                start=format_vn_date(today),
                end=format_vn_date(today),
                page_index=1,
                page_size=10,
            )
            rows = [
                row
                for row in map_candles(
                    payload.get("data") or [],
                    symbol=ticker,
                    timeframe="1D",
                    price_basis="RAW",
                    tz=self._tz,
                ).candles
                if row.open_time.astimezone(self._tz).date() == today
            ]
            bar = self._to_bar(ticker, today, rows)
        except Exception as exc:  # noqa: BLE001 - provider lỗi không được làm hỏng chart
            logger.warning(
                "provisional_bar_failed",
                extra={"symbol": ticker, "error_type": type(exc).__name__},
            )

        self._cache[ticker] = (now, bar)
        return bar

    def _to_bar(self, symbol: str, today: date, rows: list[CandleRecord]) -> ProvisionalBar | None:
        """Gộp các bản ghi trong ngày thành một bar provisional."""
        del symbol
        if not rows:
            return None
        ordered = sorted(rows, key=lambda row: row.open_time)
        first, last = ordered[0], ordered[-1]
        return ProvisionalBar(
            bar=OhlcvRow(
                open=float(first.open),
                high=max(float(row.high) for row in ordered),
                low=min(float(row.low) for row in ordered),
                close=float(last.close),
                volume=float(sum(row.volume for row in ordered)),
                day=today,
                opened_at=datetime.combine(today, time(0), tzinfo=self._tz).astimezone(UTC),
            ),
            open_time=datetime.combine(today, time(0), tzinfo=self._tz).astimezone(UTC),
            trading_day=today,
        )


def market_day_bounds(settings: Settings, day: date) -> tuple[datetime, datetime]:
    """Biên UTC của một ngày giao dịch (dùng cho test và kiểm tra chéo)."""
    tz = settings.market_tz
    start = datetime.combine(day, time(0), tzinfo=tz).astimezone(UTC)
    return start, start + timedelta(days=1)
