"""Scheduler role: lịch daily/interval, heartbeat và graceful shutdown."""

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import Settings
from app.workers.engine import JobOutcome, JobStatus, WorkerEngine
from app.workers.heartbeat import Heartbeat, read_heartbeat
from app.workers.registry import EmptyParams, JobContext, JobRegistry, JobResult, JobSpec
from app.workers.roles.scheduler import SchedulerRole
from app.workers.schedule import DailySchedule, IntervalSchedule

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


class Clock:
    """Đồng hồ điều khiển được cho test."""

    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class FakeEngine:
    """Engine ghi lại các lần gọi job."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def execute(self, name, params=None, *, trigger="manual", force=False):
        del params, force
        self.calls.append((name, trigger))
        return JobOutcome(job=name, run_id="fake", status=JobStatus.SUCCEEDED)


async def noop(ctx: JobContext[EmptyParams]) -> JobResult:
    return JobResult()


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        scheduler_poll_seconds=5,
        worker_heartbeat_seconds=1,
        worker_shutdown_grace_seconds=1,
    )


def make_registry() -> JobRegistry:
    registry = JobRegistry()
    registry.register(
        JobSpec(
            name="early",
            handler=noop,
            params_model=EmptyParams,
            schedule=DailySchedule(9, 0),
        )
    )
    registry.register(
        JobSpec(
            name="late",
            handler=noop,
            params_model=EmptyParams,
            schedule=DailySchedule(10, 0),
        )
    )
    registry.register(
        JobSpec(
            name="periodic",
            handler=noop,
            params_model=EmptyParams,
            schedule=IntervalSchedule(60),
        )
    )
    return registry


async def test_daily_jobs_run_once_and_interval_runs_after_period(tmp_path):
    clock = Clock(datetime(2026, 11, 20, 11, 0, tzinfo=TZ))
    engine = FakeEngine()
    role = SchedulerRole(
        make_settings(),
        engine,
        make_registry(),
        heartbeat=Heartbeat(tmp_path / "scheduler.heartbeat", "scheduler"),
        clock=clock,
    )

    await role.run(once=True)
    assert engine.calls == [("early", "scheduler"), ("late", "scheduler")]

    await role.run(once=True)
    assert engine.calls == [("early", "scheduler"), ("late", "scheduler")]

    clock.advance(61)
    await role.run(once=True)
    assert engine.calls[-1] == ("periodic", "scheduler")
    assert read_heartbeat(tmp_path / "scheduler.heartbeat") is not None


async def test_run_on_start_executes_all_scheduled_jobs(tmp_path):
    clock = Clock(datetime(2026, 11, 20, 8, 0, tzinfo=TZ))
    engine = FakeEngine()
    role = SchedulerRole(
        make_settings(),
        engine,
        make_registry(),
        heartbeat=Heartbeat(tmp_path / "scheduler.heartbeat", "scheduler"),
        clock=clock,
    )

    await role.run(run_on_start=True, once=True)

    assert [name for name, _trigger in engine.calls] == ["early", "late", "periodic"]


async def test_disabled_job_is_not_executed(tmp_path):
    clock = Clock(datetime(2026, 11, 20, 11, 0, tzinfo=TZ))
    engine = FakeEngine()
    registry = JobRegistry()
    registry.register(
        JobSpec(
            name="off",
            handler=noop,
            params_model=EmptyParams,
            schedule=DailySchedule(9, 0),
            enabled=lambda settings: False,
        )
    )
    role = SchedulerRole(
        make_settings(),
        engine,
        registry,
        heartbeat=Heartbeat(tmp_path / "scheduler.heartbeat", "scheduler"),
        clock=clock,
    )

    await role.run(once=True)

    assert engine.calls == []


async def test_shutdown_waits_then_cancels_running_job(tmp_path):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow(ctx: JobContext[EmptyParams]) -> JobResult:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return JobResult()

    registry = JobRegistry()
    registry.register(
        JobSpec(
            name="slow",
            handler=slow,
            params_model=EmptyParams,
            schedule=DailySchedule(9, 0),
        )
    )
    settings = make_settings()
    engine = WorkerEngine(settings, registry, redis=None)
    role = SchedulerRole(
        settings,
        engine,
        registry,
        heartbeat=Heartbeat(tmp_path / "scheduler.heartbeat", "scheduler"),
        clock=Clock(datetime(2026, 11, 20, 11, 0, tzinfo=TZ)),
    )
    stop = asyncio.Event()
    task = asyncio.create_task(role.run(once=True, stop_event=stop))
    await asyncio.wait_for(started.wait(), timeout=1)
    stop.set()

    await asyncio.wait_for(task, timeout=3)

    assert cancelled.is_set()
    assert task.done()


async def test_crashed_job_does_not_kill_scheduler(tmp_path):
    class CrashEngine:
        async def execute(self, name, params=None, *, trigger="manual", force=False):
            del name, params, trigger, force
            raise ValueError("bad job wiring")

    clock = Clock(datetime(2026, 11, 20, 11, 0, tzinfo=TZ))
    role = SchedulerRole(
        make_settings(),
        CrashEngine(),
        make_registry(),
        heartbeat=Heartbeat(tmp_path / "scheduler.heartbeat", "scheduler"),
        clock=clock,
    )

    await role.run(once=True)

    assert read_heartbeat(tmp_path / "scheduler.heartbeat") is not None


async def test_locked_job_is_retried_on_next_poll(tmp_path):
    class LockedOnceEngine(FakeEngine):
        def __init__(self) -> None:
            super().__init__()
            self.locked = True

        async def execute(self, name, params=None, *, trigger="manual", force=False):
            if self.locked:
                self.locked = False
                return JobOutcome(
                    job=name,
                    run_id="fake",
                    status=JobStatus.SKIPPED_LOCKED,
                )
            return await super().execute(name, params, trigger=trigger, force=force)

    clock = Clock(datetime(2026, 11, 20, 11, 0, tzinfo=TZ))
    engine = LockedOnceEngine()
    role = SchedulerRole(
        make_settings(),
        engine,
        make_registry(),
        heartbeat=Heartbeat(tmp_path / "scheduler.heartbeat", "scheduler"),
        clock=clock,
    )

    await role.run(once=True)
    await role.run(once=True)

    # "early" bị lock ở vòng đầu nên chỉ chạy thành công ở vòng hai; "late" đã xong.
    assert engine.calls == [("late", "scheduler"), ("early", "scheduler")]
