"""Scheduler entrypoint tương thích ngược.

Khuyến nghị dùng entrypoint thống nhất::

    python -m app.workers scheduler

Module này giữ nguyên CLI cũ (`--list`, `--run`, `--now`, `--run-on-start`,
`--once`) và các helper `Job`/`is_job_due` để script/test hiện có không bị phá.
Toàn bộ việc thực thi, lock, retry và idempotency do `WorkerEngine` đảm nhiệm.
"""

import argparse
import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.workers.bootstrap import (
    build_engine,
    build_registry,
    close_worker_resources,
    execute_job,
    install_stop_handlers,
    prepare,
)
from app.workers.heartbeat import Heartbeat, heartbeat_path
from app.workers.roles.scheduler import SchedulerRole
from app.workers.schedule import (
    DailySchedule,
    is_job_due,
    parse_holidays,
    scheduled_at,
)

logger = get_logger(__name__)

JobRunner = Callable[[Settings], Awaitable[None]]

__all__ = [
    "JOBS",
    "Job",
    "is_job_due",
    "main",
    "parse_holidays",
    "run_divergence_scan",
    "run_ingest_close",
    "run_recompute_indicators",
    "run_scheduler",
    "run_sync_symbols",
    "scheduled_at",
]


@dataclass(frozen=True, slots=True)
class Job:
    """Một job chạy theo giờ cố định trong ngày (mô hình tương thích cũ).

    Attributes:
        name: Tên job dùng cho CLI và log.
        hour: Giờ chạy (0-23) theo múi giờ scheduler.
        minute: Phút chạy (0-59).
        runner: Coroutine thực thi.
        weekdays_only: Bỏ qua Thứ 7/Chủ nhật khi True.
    """

    name: str
    hour: int
    minute: int
    runner: JobRunner
    weekdays_only: bool = True


def _legacy_runner(name: str) -> JobRunner:
    """Tạo runner tương thích gọi engine theo tên job."""

    async def runner(settings: Settings) -> None:
        await execute_job(settings, name)

    return runner


def _legacy_jobs() -> tuple[Job, ...]:
    """Dẫn xuất `JOBS` từ registry để giữ danh sách/giờ cũ."""
    jobs: list[Job] = []
    for spec in build_registry().scheduled():
        if isinstance(spec.schedule, DailySchedule):
            jobs.append(
                Job(
                    spec.name,
                    spec.schedule.hour,
                    spec.schedule.minute,
                    _legacy_runner(spec.name),
                    spec.schedule.weekdays_only,
                )
            )
    return tuple(jobs)


JOBS: tuple[Job, ...] = _legacy_jobs()


async def run_sync_symbols(settings: Settings) -> None:
    """Đồng bộ danh mục mã từ FCData."""
    await execute_job(settings, "sync-symbols")


async def run_ingest_close(settings: Settings) -> None:
    """Nạp nến đóng của những phiên gần nhất cho toàn bộ mã đang hoạt động."""
    await execute_job(settings, "ingest-close")


async def run_recompute_indicators(settings: Settings) -> None:
    """Tính lại chỉ báo đã xác nhận cho toàn bộ mã đang hoạt động."""
    await execute_job(settings, "recompute-indicators")


async def run_divergence_scan(settings: Settings) -> None:
    """Quét phân kỳ đã xác nhận của phiên gần nhất."""
    await execute_job(settings, "divergence-scan")


def _heartbeat(path: Path) -> None:
    """Ghi heartbeat một lần (tương thích helper cũ)."""
    Heartbeat(path, "scheduler").write()


async def _run_job(job: Job, settings: Settings, timeout: float | None = None) -> None:
    """Chạy một job theo tên; timeout lấy từ JobSpec của registry."""
    del timeout
    await execute_job(settings, job.name, force=True)


async def run_scheduler(settings: Settings, *, run_on_start: bool, once: bool) -> None:
    """Vòng lặp scheduler qua role mới.

    Args:
        settings: Cấu hình ứng dụng.
        run_on_start: Chạy tất cả job ngay khi khởi động.
        once: Chỉ kiểm tra một vòng rồi thoát.
    """
    registry = build_registry()
    role = SchedulerRole(
        settings,
        build_engine(settings, registry=registry),
        registry,
        heartbeat=Heartbeat(
            heartbeat_path(settings, "scheduler"),
            "scheduler",
            settings.worker_heartbeat_seconds,
        ),
    )
    stop = asyncio.Event()
    install_stop_handlers(stop)
    await role.run(run_on_start=run_on_start, once=once, stop_event=stop)


def _parse_args() -> argparse.Namespace:
    """Đọc tham số dòng lệnh."""
    parser = argparse.ArgumentParser(description="Scheduler worker định kỳ.")
    parser.add_argument("--list", action="store_true", help="Liệt kê job và giờ chạy.")
    parser.add_argument("--run", default="", help="Chạy ngay một job theo tên.")
    parser.add_argument("--now", action="store_true", help="Dùng với --run: bỏ qua lịch.")
    parser.add_argument("--run-on-start", action="store_true", help="Chạy mọi job khi khởi động.")
    parser.add_argument("--once", action="store_true", help="Chỉ kiểm tra một vòng rồi thoát.")
    return parser.parse_args()


async def main() -> None:
    """Entrypoint của scheduler."""
    args = _parse_args()
    settings = get_settings()
    prepare(settings)

    if not settings.scheduler_enabled and not args.list and not args.run:
        logger.info("scheduler_disabled")
        return

    try:
        if args.list:
            for job in JOBS:
                print(
                    f"{job.name:<22} {job.hour:02d}:{job.minute:02d}  "
                    f"weekdays_only={job.weekdays_only}"
                )
            return
        if args.run:
            registry = build_registry()
            if args.run not in registry:
                raise SystemExit(f"Không có job '{args.run}'. Dùng --list để xem.")
            outcome = await execute_job(settings, args.run, force=True)
            print(outcome.as_dict())
            return
        await run_scheduler(
            settings,
            run_on_start=args.run_on_start or settings.scheduler_run_on_start,
            once=args.once,
        )
    finally:
        with contextlib.suppress(Exception):
            await close_worker_resources()


if __name__ == "__main__":
    asyncio.run(main())
