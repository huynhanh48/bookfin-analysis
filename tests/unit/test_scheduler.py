"""Scheduler: lịch chạy, bỏ qua cuối tuần/ngày lễ và chống chạy trùng trong ngày."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.workers.scheduler import Job, is_job_due, parse_holidays, scheduled_at

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def job(hour: int = 15, minute: int = 5) -> Job:
    """Job rỗng dùng cho test lịch."""
    return Job("test", hour, minute, lambda settings: None)  # type: ignore[arg-type]


def at(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Tạo thời điểm theo múi giờ thị trường."""
    return datetime(year, month, day, hour, minute, tzinfo=TZ)


def test_scheduled_at_uses_market_timezone():
    stamp = scheduled_at(job(15, 5), date(2026, 11, 20), TZ)
    assert stamp.hour == 15 and stamp.minute == 5
    assert stamp.tzinfo is not None


def test_job_is_not_due_before_its_time():
    assert not is_job_due(job(15, 5), at(2026, 11, 20, 15, 4), last_run=None, holidays=frozenset())


def test_job_is_due_at_its_time():
    assert is_job_due(job(15, 5), at(2026, 11, 20, 15, 5), last_run=None, holidays=frozenset())


def test_job_runs_once_per_day():
    assert not is_job_due(
        job(15, 5),
        at(2026, 11, 20, 16, 0),
        last_run=date(2026, 11, 20),
        holidays=frozenset(),
    )
    assert is_job_due(
        job(15, 5),
        at(2026, 11, 20, 16, 0),
        last_run=date(2026, 11, 19),
        holidays=frozenset(),
    )


def test_weekend_is_skipped():
    # 21/11/2026 là Thứ 7.
    assert not is_job_due(job(9, 0), at(2026, 11, 21, 10, 0), last_run=None, holidays=frozenset())


def test_holiday_is_skipped():
    holidays = parse_holidays("02/09/2026")
    assert holidays == frozenset({date(2026, 9, 2)})
    assert not is_job_due(job(9, 0), at(2026, 9, 2, 10, 0), last_run=None, holidays=holidays)


def test_parse_holidays_ignores_invalid_values():
    assert parse_holidays("02/09/2026, bad-date, , 01/01/2027") == frozenset(
        {date(2026, 9, 2), date(2027, 1, 1)}
    )


def test_job_without_weekday_restriction_runs_on_weekend():
    weekend_job = Job("weekly", 9, 0, lambda settings: None, weekdays_only=False)  # type: ignore[arg-type]
    assert is_job_due(weekend_job, at(2026, 11, 21, 9, 0), last_run=None, holidays=frozenset())
