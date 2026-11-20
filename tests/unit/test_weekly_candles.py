"""Weekly candle aggregation: OHLCV merge, week boundaries and look-ahead guards."""

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.models.candle import Candle
from app.services.weekly_candles import (
    WEEKLY_SOURCE,
    aggregate_weekly,
    load_timeframe_candles,
    week_close_cutoff,
)

VN = ZoneInfo("Asia/Ho_Chi_Minh")


def _candle(day: date, *, open_: int, high: int, low: int, close: int, volume: int = 100) -> Candle:
    """Nến ngày có giờ mở 00:00 thị trường, lưu theo UTC như dữ liệu thật."""
    return Candle(
        id=None,
        symbol="BSR",
        timeframe="1D",
        candle_open_time=datetime.combine(day, time(0), tzinfo=VN).astimezone(UTC),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=volume,
        turnover=Decimal(volume) * Decimal(close),
        price_basis="RAW",
        source="bookfin-fcdata",
    )


def _week_days(monday: date) -> list[date]:
    return [monday + timedelta(days=offset) for offset in range(5)]


def test_aggregate_merges_ohlcv_of_a_full_week():
    monday = date(2026, 10, 26)
    days = _week_days(monday)
    daily = [
        _candle(days[0], open_=100, high=110, low=95, close=105, volume=10),
        _candle(days[1], open_=105, high=118, low=101, close=112, volume=20),
        _candle(days[2], open_=112, high=115, low=90, close=98, volume=30),
        _candle(days[3], open_=98, high=104, low=97, close=102, volume=40),
        _candle(days[4], open_=102, high=120, low=99, close=118, volume=50),
    ]

    weekly = aggregate_weekly(daily, tz=VN, as_of=datetime(2026, 11, 2, 10, tzinfo=VN))

    assert len(weekly) == 1
    bar = weekly[0]
    # Monday 00:00 VN được lưu theo UTC để khớp quy ước của nến ngày.
    assert bar.candle_open_time == datetime(2026, 10, 25, 17, tzinfo=UTC)
    assert bar.open == Decimal(100)
    assert bar.high == Decimal(120)
    assert bar.low == Decimal(90)
    assert bar.close == Decimal(118)
    assert bar.volume == 150
    assert bar.timeframe == "1W"
    assert bar.source == WEEKLY_SOURCE
    assert bar.id is None


def test_week_is_split_by_market_timezone_not_utc():
    # Thứ Sáu 24/10 và thứ Hai 27/10 (giờ VN): phải nằm ở hai tuần khác nhau.
    daily = [
        _candle(date(2026, 10, 23), open_=10, high=11, low=9, close=10),
        _candle(date(2026, 10, 26), open_=20, high=21, low=19, close=20),
    ]

    weekly = aggregate_weekly(daily, tz=VN, as_of=datetime(2026, 11, 3, 10, tzinfo=VN))

    assert [bar.close for bar in weekly] == [Decimal(10), Decimal(20)]


def test_unfinished_week_is_excluded():
    monday = date(2026, 11, 16)
    daily = [_candle(day, open_=10, high=11, low=9, close=10) for day in _week_days(monday)[:2]]

    # Thứ Ba 17/11 10:00: tuần chưa đóng nên không được trả về.
    weekly = aggregate_weekly(daily, tz=VN, as_of=datetime(2026, 11, 17, 10, tzinfo=VN))

    assert weekly == []


def test_week_closes_after_friday_session_end():
    monday = date(2026, 11, 16)
    daily = [_candle(day, open_=10, high=11, low=9, close=10) for day in _week_days(monday)]

    before = datetime(2026, 11, 20, 14, 59, tzinfo=VN)
    after = datetime(2026, 11, 20, 15, 0, tzinfo=VN)

    assert aggregate_weekly(daily, tz=VN, as_of=before) == []
    assert len(aggregate_weekly(daily, tz=VN, as_of=after)) == 1


def test_replay_cutoff_excludes_weeks_after_as_of():
    daily = [
        _candle(date(2026, 10, 19), open_=10, high=11, low=9, close=10),
        _candle(date(2026, 10, 26), open_=20, high=21, low=19, close=20),
    ]

    weekly = aggregate_weekly(daily, tz=VN, as_of=datetime(2026, 10, 24, 9, tzinfo=VN))

    assert [bar.close for bar in weekly] == [Decimal(10)]


def test_naive_as_of_is_rejected():
    with pytest.raises(ValueError):
        aggregate_weekly([], tz=VN, as_of=datetime(2026, 11, 17, 10))


def test_week_close_cutoff_is_friday_session_end():
    cutoff = week_close_cutoff(date(2026, 11, 16), VN)

    assert cutoff == datetime(2026, 11, 20, 15, 0, tzinfo=VN)


class _FakeRepository:
    """Repository giả trả dữ liệu dựng sẵn theo khung thời gian."""

    def __init__(self, rows: dict[str, list[Candle]]) -> None:
        self._rows = rows
        self.calls: list[tuple[str, int]] = []

    async def get_candles(
        self,
        symbol: str,
        timeframe: str = "1D",
        *,
        limit: int = 250,
        end: datetime | None = None,
        price_basis: str | None = None,
    ) -> list[Candle]:
        self.calls.append((timeframe, limit))
        rows: Sequence[Candle] = self._rows.get(timeframe, [])
        if end is not None:
            rows = [row for row in rows if row.candle_open_time < end]
        return list(rows)[-limit:]


async def test_weekly_is_derived_from_daily_when_not_stored():
    monday = date(2026, 10, 26)
    daily = [_candle(day, open_=10, high=12, low=8, close=11) for day in _week_days(monday)]
    repository = _FakeRepository({"1D": daily})

    candles = await load_timeframe_candles(
        repository,  # type: ignore[arg-type]
        "BSR",
        "1W",
        limit=10,
        tz=VN,
        end=datetime(2026, 11, 3, 10, tzinfo=VN),
    )

    assert len(candles) == 1
    assert candles[0].timeframe == "1W"
    assert ("1D", 60) in repository.calls


async def test_stored_weekly_wins_over_aggregation():
    stored = [_candle(date(2026, 10, 26), open_=1, high=2, low=1, close=2)]
    stored[0].timeframe = "1W"
    repository = _FakeRepository({"1W": stored, "1D": []})

    candles = await load_timeframe_candles(
        repository,  # type: ignore[arg-type]
        "BSR",
        "1W",
        limit=10,
        tz=VN,
    )

    assert candles == stored
    assert repository.calls == [("1W", 10)]


async def test_daily_request_without_data_returns_empty():
    repository = _FakeRepository({"1D": []})

    candles = await load_timeframe_candles(
        repository,  # type: ignore[arg-type]
        "BSR",
        "1D",
        limit=10,
        tz=VN,
    )

    assert candles == []


def test_weekly_divergence_uses_week_scaled_pivots():
    from app.services.indicator_service import (
        WEEKLY_DIVERGENCE_PARAMETERS,
        planned_requests,
        timeframe_parameters,
    )

    assert timeframe_parameters("1D", "divergence", {}) == {}
    assert timeframe_parameters("1W", "divergence", {}) == WEEKLY_DIVERGENCE_PARAMETERS
    # Tham số client truyền vào luôn được ưu tiên.
    assert timeframe_parameters("1W", "divergence", {"pivot_right": 5})["pivot_right"] == 5
    # Chỉ báo khác không bị ảnh hưởng.
    assert timeframe_parameters("1W", "rsi", {"period": 14}) == {"period": 14}

    planned = planned_requests("1W", [("divergence", {}), ("rsi", {"period": 14})])
    assert planned[0][1] == WEEKLY_DIVERGENCE_PARAMETERS
    assert planned[1][1] == {"period": 14}
