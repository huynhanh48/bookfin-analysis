"""Dựng nến tuần (1W) từ nến ngày (1D) đã đóng.

FCData Gateway chỉ phục vụ nến ngày và intraday, nên khung `1W` được tạo ra khi
đọc dữ liệu thay vì lưu sẵn trong PostgreSQL. Quy tắc:

- Tuần bắt đầu thứ Hai theo múi giờ thị trường (`Asia/Ho_Chi_Minh`).
- open = open của nến ngày đầu tuần, close = close của nến ngày cuối tuần.
- high/low = max/min trong tuần, volume/turnover = tổng.
- Chỉ trả tuần đã đóng: mốc đóng là thứ Sáu 15:00 giờ thị trường. Tuần đang
  hình thành bị loại để không tạo look-ahead bias.

Nến trả về là bản ghi ORM không gắn session (`id=None`) nên chỉ dùng để đọc,
không bao giờ được ghi đè lên nến 1D đã lưu.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from decimal import Decimal
from typing import Final

from app.models.candle import Candle
from app.repositories.market_repository import MarketRepository

DAILY_TIMEFRAME: Final = "1D"
WEEKLY_TIMEFRAME: Final = "1W"
WEEKLY_SOURCE: Final = "aggregated-1d"
SESSION_CLOSE: Final = time(15, 0)
FRIDAY_OFFSET: Final = 4
# Cần khoảng 5 nến ngày cho mỗi nến tuần; lấy dư để bù tuần lễ và ngày nghỉ.
_DAILY_PER_WEEK: Final = 6


def _week_start(moment: datetime, tz: tzinfo) -> date:
    """Trả về thứ Hai của tuần chứa `moment` theo giờ thị trường."""
    local_date = moment.astimezone(tz).date()
    return local_date - timedelta(days=local_date.weekday())


def week_close_cutoff(week_start: date, tz: tzinfo) -> datetime:
    """Mốc đóng tuần: thứ Sáu 15:00 giờ thị trường của tuần bắt đầu `week_start`."""
    friday = week_start + timedelta(days=FRIDAY_OFFSET)
    return datetime.combine(friday, SESSION_CLOSE, tzinfo=tz)


def aggregate_weekly(
    daily: Sequence[Candle],
    *,
    tz: tzinfo,
    as_of: datetime | None = None,
) -> list[Candle]:
    """Gộp nến ngày thành nến tuần đã đóng.

    Args:
        daily: Nến ngày tăng dần theo `candle_open_time`.
        tz: Múi giờ thị trường dùng để chia tuần và xác định mốc đóng.
        as_of: Thời điểm đánh giá; mặc định là hiện tại. Khi replay lịch sử,
            truyền mốc cutoff để không dùng dữ liệu tương lai.

    Returns:
        Danh sách nến tuần tăng dần, chỉ gồm các tuần đã đóng.

    Raises:
        ValueError: Khi `as_of` không có thông tin múi giờ.
    """
    if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() is None):
        raise ValueError("as_of phải là datetime có múi giờ")
    now = as_of or datetime.now(tz=UTC)

    buckets: dict[date, list[Candle]] = {}
    for candle in daily:
        buckets.setdefault(_week_start(candle.candle_open_time, tz), []).append(candle)

    weekly: list[Candle] = []
    for start in sorted(buckets):
        if week_close_cutoff(start, tz) > now:
            continue
        weekly.append(_merge_week(start, buckets[start], tz))
    return weekly


def _merge_week(start: date, rows: Sequence[Candle], tz: tzinfo) -> Candle:
    """Gộp các nến ngày của một tuần thành một nến tuần."""
    rows = sorted(rows, key=lambda row: row.candle_open_time)
    first, last = rows[0], rows[-1]
    turnover = sum((row.turnover for row in rows if row.turnover is not None), Decimal(0))
    return Candle(
        id=None,
        symbol=first.symbol,
        timeframe=WEEKLY_TIMEFRAME,
        # Nến ngày được lưu theo UTC tại 00:00 giờ thị trường; nến tuần giữ quy ước đó.
        candle_open_time=datetime.combine(start, time(0), tzinfo=tz).astimezone(UTC),
        open=first.open,
        high=max(row.high for row in rows),
        low=min(row.low for row in rows),
        close=last.close,
        volume=sum(row.volume for row in rows),
        turnover=turnover if any(row.turnover is not None for row in rows) else None,
        price_basis=first.price_basis,
        source=WEEKLY_SOURCE,
        source_updated_at=last.source_updated_at,
    )


async def load_timeframe_candles(
    repository: MarketRepository,
    symbol: str,
    timeframe: str,
    *,
    limit: int,
    tz: tzinfo,
    end: datetime | None = None,
    price_basis: str | None = None,
) -> list[Candle]:
    """Đọc nến của một khung thời gian, dựng 1W từ 1D khi chưa được lưu.

    Nến 1W có sẵn trong PostgreSQL luôn được ưu tiên; chỉ khi khung đó rỗng mới
    gộp từ 1D. Nhờ vậy sau này nếu có job lưu 1W thì API không phải đổi.

    Args:
        repository: Repository thị trường.
        symbol: Mã chứng khoán.
        timeframe: `1D` hoặc `1W`.
        limit: Số nến tối đa cần trả về.
        tz: Múi giờ thị trường.
        end: Chỉ dùng nến mở trước mốc này (replay lịch sử).
        price_basis: Lọc theo cơ sở giá nếu cần.

    Returns:
        Nến tăng dần theo thời gian, tối đa `limit` phần tử.
    """
    stored = await repository.get_candles(
        symbol, timeframe, limit=limit, end=end, price_basis=price_basis
    )
    if stored or timeframe != WEEKLY_TIMEFRAME:
        return stored

    daily = await repository.get_candles(
        symbol,
        DAILY_TIMEFRAME,
        limit=limit * _DAILY_PER_WEEK,
        end=end,
        price_basis=price_basis,
    )
    if not daily:
        return []
    return aggregate_weekly(daily, tz=tz, as_of=end)[-limit:]
