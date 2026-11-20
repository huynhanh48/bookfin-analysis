"""Lịch chạy job dùng chung cho scheduler và các bài test.

Hai kiểu lịch được hỗ trợ:

- `DailySchedule`: chạy một lần mỗi ngày tại giờ/phút cố định, có thể bỏ qua
  cuối tuần và ngày lễ.
- `IntervalSchedule`: chạy lặp theo chu kỳ giây, phù hợp với job hàng đợi.

Mọi phép tính dùng múi giờ của thời điểm truyền vào, nên caller phải cung cấp
`datetime` có timezone (scheduler luôn dùng múi giờ thị trường).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DailySchedule:
    """Lịch chạy một lần mỗi ngày.

    Attributes:
        hour: Giờ chạy (0-23) theo múi giờ của scheduler.
        minute: Phút chạy (0-59).
        weekdays_only: Bỏ qua Thứ 7/Chủ nhật khi True.
    """

    hour: int
    minute: int
    weekdays_only: bool = True

    def __post_init__(self) -> None:
        if not 0 <= self.hour <= 23:
            raise ValueError("DailySchedule.hour phải nằm trong 0..23")
        if not 0 <= self.minute <= 59:
            raise ValueError("DailySchedule.minute phải nằm trong 0..59")


@dataclass(frozen=True, slots=True)
class IntervalSchedule:
    """Lịch chạy lặp theo chu kỳ.

    Attributes:
        seconds: Khoảng cách tối thiểu giữa hai lần chạy.
    """

    seconds: int

    def __post_init__(self) -> None:
        if self.seconds < 5:
            raise ValueError("IntervalSchedule.seconds phải >= 5")


Schedule = DailySchedule | IntervalSchedule


def parse_holidays(value: str) -> frozenset[date]:
    """Đọc danh sách ngày lễ dạng `dd/mm/yyyy,dd/mm/yyyy`.

    Args:
        value: Chuỗi cấu hình; phần tử không hợp lệ bị bỏ qua kèm log warning.

    Returns:
        Tập ngày lễ đã chuẩn hoá.
    """
    holidays: set[date] = set()
    for item in value.split(","):
        text = item.strip()
        if not text:
            continue
        try:
            holidays.add(datetime.strptime(text, "%d/%m/%Y").date())
        except ValueError:
            logger.warning("scheduler_bad_holiday", extra={"value": text})
    return frozenset(holidays)


def scheduled_at(schedule: DailySchedule, day: date, tz: ZoneInfo) -> datetime:
    """Thời điểm chạy của lịch ngày trong một ngày.

    Args:
        schedule: Lịch ngày cần tính.
        day: Ngày cần tính.
        tz: Múi giờ của scheduler.

    Returns:
        `datetime` có timezone tại đúng giờ/phút cấu hình.
    """
    return datetime.combine(day, time(schedule.hour, schedule.minute), tzinfo=tz)


def is_daily_due(
    schedule: DailySchedule,
    now: datetime,
    *,
    last_run: date | None,
    holidays: frozenset[date],
) -> bool:
    """Lịch ngày đã tới giờ và chưa chạy trong ngày hay chưa.

    Args:
        schedule: Lịch cần kiểm tra.
        now: Thời điểm hiện tại có timezone.
        last_run: Ngày chạy gần nhất.
        holidays: Tập ngày lễ cần bỏ qua.

    Returns:
        True khi job cần chạy ngay.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("is_daily_due yêu cầu datetime có timezone")
    today = now.date()
    if schedule.weekdays_only and today.weekday() >= 5:
        return False
    if today in holidays:
        return False
    if last_run == today:
        return False
    return now >= scheduled_at(schedule, today, now.tzinfo)  # type: ignore[arg-type]


def is_interval_due(schedule: IntervalSchedule, now: datetime, *, next_run_at: datetime) -> bool:
    """Lịch chu kỳ đã tới hạn hay chưa.

    Args:
        schedule: Lịch chu kỳ (giữ tham số để API đồng nhất với `is_daily_due`).
        now: Thời điểm hiện tại có timezone.
        next_run_at: Thời điểm chạy kế tiếp đã tính trước đó.

    Returns:
        True khi `now >= next_run_at`.
    """
    del schedule
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("is_interval_due yêu cầu datetime có timezone")
    return now >= next_run_at


def is_job_due(
    job: object,
    now: datetime,
    *,
    last_run: date | None,
    holidays: frozenset[date],
) -> bool:
    """Tương thích ngược: kiểm tra một object có `hour`, `minute`, `weekdays_only`.

    Được giữ lại cho `app.workers.scheduler` cũ và các test hiện có.

    Args:
        job: Object mô tả lịch ngày.
        now: Thời điểm hiện tại có timezone.
        last_run: Ngày chạy gần nhất.
        holidays: Tập ngày lễ cần bỏ qua.

    Returns:
        True khi job cần chạy ngay.
    """
    schedule = DailySchedule(
        hour=job.hour,  # type: ignore[attr-defined]
        minute=job.minute,  # type: ignore[attr-defined]
        weekdays_only=job.weekdays_only,  # type: ignore[attr-defined]
    )
    return is_daily_due(schedule, now, last_run=last_run, holidays=holidays)


def describe_schedule(schedule: Schedule | None) -> str:
    """Mô tả lịch chạy cho CLI và log.

    Args:
        schedule: Lịch cần mô tả; None nghĩa là job chỉ chạy thủ công.

    Returns:
        Chuỗi ngắn gọn, ví dụ `weekdays 15:05` hoặc `every 60s`.
    """
    if schedule is None:
        return "manual"
    if isinstance(schedule, DailySchedule):
        scope = "weekdays" if schedule.weekdays_only else "daily"
        return f"{scope} {schedule.hour:02d}:{schedule.minute:02d}"
    return f"every {schedule.seconds}s"
