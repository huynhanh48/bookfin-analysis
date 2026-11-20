"""Scheduler role: kích hoạt job theo lịch qua WorkerEngine."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import Settings
from app.core.logging import get_logger
from app.workers.engine import JobOutcome, JobStatus, WorkerEngine
from app.workers.heartbeat import Heartbeat, heartbeat_path
from app.workers.registry import JobRegistry, JobSpec
from app.workers.schedule import (
    DailySchedule,
    IntervalSchedule,
    is_daily_due,
    is_interval_due,
    parse_holidays,
)

logger = get_logger(__name__)

Clock = Callable[[], datetime]


class SchedulerRole:
    """Vòng lặp scheduler, chỉ quyết định *khi nào* chạy job.

    Việc thực thi, lock, retry và idempotency nằm ở `WorkerEngine`. Role này giữ
    trạng thái `last_run` theo ngày và `next_run` theo chu kỳ, ghi heartbeat, và
    chờ job đang chạy hoàn tất trong thời gian graceful shutdown trước khi cancel.
    """

    def __init__(
        self,
        settings: Settings,
        engine: WorkerEngine,
        registry: JobRegistry,
        *,
        heartbeat: Heartbeat | None = None,
        holidays: frozenset[date] | None = None,
        clock: Clock | None = None,
    ) -> None:
        """Khởi tạo role.

        Args:
            settings: Cấu hình ứng dụng.
            engine: Executor dùng chung.
            registry: Registry chứa job.
            heartbeat: Bộ ghi heartbeat; mặc định dùng file scheduler cũ.
            holidays: Tập ngày lễ; mặc định parse từ cấu hình.
            clock: Hàm lấy thời điểm hiện tại, inject cho test.
        """
        self._settings = settings
        self._engine = engine
        self._registry = registry
        self._heartbeat = heartbeat or Heartbeat(
            heartbeat_path(settings, "scheduler"),
            "scheduler",
            settings.worker_heartbeat_seconds,
        )
        self._holidays = (
            holidays if holidays is not None else parse_holidays(settings.scheduler_holidays)
        )
        self._tz: ZoneInfo = ZoneInfo(settings.scheduler_timezone)
        self._clock = clock or (lambda: datetime.now(tz=self._tz))
        self._last_run: dict[str, date] = {}
        self._next_run: dict[str, datetime] = {}
        self._last_state: dict[str, object] = {}

    async def run(
        self,
        *,
        run_on_start: bool = False,
        once: bool = False,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """Chạy vòng lặp scheduler.

        Args:
            run_on_start: Chạy toàn bộ job có lịch ngay khi khởi động.
            once: Chỉ kiểm tra một vòng rồi thoát (test/CI).
            stop_event: Sự kiện dừng; role tự tạo nếu không truyền.
        """
        stop = stop_event or asyncio.Event()
        now = self._clock()
        self._initialize(now)
        logger.info(
            "scheduler_started",
            extra={
                "jobs": [spec.name for spec in self._registry.scheduled()],
                "run_on_start": run_on_start,
            },
        )
        self._heartbeat.write(status="ok", detail=self._health_detail())
        heartbeat_task = asyncio.create_task(
            self._heartbeat.run(stop, status_provider=self._health_detail),
            name="scheduler-heartbeat",
        )

        try:
            if run_on_start:
                for spec in self._registry.scheduled():
                    if stop.is_set():
                        break
                    if not spec.enabled(self._settings):
                        continue
                    outcome = await self._execute(spec, stop)
                    self._mark_if_finished(spec, outcome)

            while not stop.is_set():
                now = self._clock()
                for spec in self._registry.scheduled():
                    if stop.is_set():
                        break
                    if not spec.enabled(self._settings):
                        continue
                    if not self._is_due(spec, now):
                        continue
                    outcome = await self._execute(spec, stop)
                    self._mark_if_finished(spec, outcome)
                if once:
                    break
                try:
                    await asyncio.wait_for(
                        stop.wait(), timeout=self._settings.scheduler_poll_seconds
                    )
                except TimeoutError:
                    continue
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
            self._heartbeat.write(status="stopping", detail=self._health_detail())
            logger.info("scheduler_stopped")

    def _initialize(self, now: datetime) -> None:
        """Khởi tạo thời điểm chạy kế tiếp cho lịch chu kỳ."""
        for spec in self._registry.scheduled():
            if isinstance(spec.schedule, IntervalSchedule):
                self._next_run.setdefault(spec.name, now + timedelta(seconds=spec.schedule.seconds))

    def _is_due(self, spec: JobSpec[Any], now: datetime) -> bool:
        """Kiểm tra một job đã tới lịch hay chưa."""
        schedule = spec.schedule
        if isinstance(schedule, DailySchedule):
            return is_daily_due(
                schedule,
                now,
                last_run=self._last_run.get(spec.name),
                holidays=self._holidays,
            )
        if isinstance(schedule, IntervalSchedule):
            next_run = self._next_run.setdefault(
                spec.name, now + timedelta(seconds=schedule.seconds)
            )
            return is_interval_due(schedule, now, next_run_at=next_run)
        return False

    def _mark(self, spec: JobSpec[Any], now: datetime) -> None:
        """Ghi nhận đã chạy để vòng lặp sau không lặp lại."""
        schedule = spec.schedule
        if isinstance(schedule, DailySchedule):
            self._last_run[spec.name] = now.date()
        elif isinstance(schedule, IntervalSchedule):
            self._next_run[spec.name] = now + timedelta(seconds=schedule.seconds)

    def _mark_if_finished(self, spec: JobSpec[Any], outcome: JobOutcome | None) -> None:
        """Chỉ đánh dấu đã chạy khi job thực sự kết thúc.

        Job bị `skipped_locked` chưa chạy, nên phải để lần poll sau thử lại;
        nếu đánh dấu ngay, scheduler sẽ bỏ qua phần dữ liệu còn thiếu trong ngày.
        """
        if outcome is None or outcome.status is JobStatus.SKIPPED_LOCKED:
            return
        self._mark(spec, self._clock())

    async def _execute(self, spec: JobSpec[Any], stop: asyncio.Event) -> JobOutcome | None:
        """Chạy job và xử lý graceful shutdown.

        Khi stop được set giữa lúc job đang chạy, role chờ tối đa
        `worker_shutdown_grace_seconds` để job hoàn tất rồi mới cancel. Engine
        vẫn nhả lock trong `finally` khi bị cancel.
        """
        task = asyncio.create_task(
            self._engine.execute(spec.name, trigger="scheduler"),
            name=f"scheduler-job-{spec.name}",
        )
        stopper = asyncio.create_task(stop.wait(), name=f"scheduler-stop-{spec.name}")
        try:
            done, _pending = await asyncio.wait(
                {task, stopper}, return_when=asyncio.FIRST_COMPLETED
            )
            if task in done:
                try:
                    outcome = task.result()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - scheduler không được chết vì job
                    logger.exception(
                        "scheduler_job_crashed",
                        extra={"job": spec.name, "error_type": type(exc).__name__},
                    )
                    return JobOutcome(
                        job=spec.name,
                        run_id="crashed",
                        status=JobStatus.FAILED,
                        error_type=type(exc).__name__,
                    )
            else:
                logger.info("scheduler_job_shutdown_wait", extra={"job": spec.name})
                try:
                    outcome = await asyncio.wait_for(
                        asyncio.shield(task),
                        timeout=self._settings.worker_shutdown_grace_seconds,
                    )
                except TimeoutError:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                    logger.warning("scheduler_job_cancelled", extra={"job": spec.name})
                    return None
        except asyncio.CancelledError:
            logger.info("scheduler_job_cancelled", extra={"job": spec.name})
            raise
        finally:
            stopper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stopper

        self._last_state = {
            "last_job": spec.name,
            "last_status": str(outcome.status),
            "last_finished_at": self._clock().isoformat(),
        }
        return outcome

    def _health_detail(self) -> dict[str, object]:
        """Detail ghi vào heartbeat."""
        return {
            **self._last_state,
            "scheduled_jobs": [spec.name for spec in self._registry.scheduled()],
        }
