"""Worker engine: retry, timeout, lock, idempotency và graceful cancellation."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.logging import worker_job_var, worker_run_id_var
from app.workers.engine import JobStatus, WorkerEngine
from app.workers.registry import (
    EmptyParams,
    JobContext,
    JobRegistry,
    JobResult,
    JobSpec,
    RetryPolicy,
    SymbolSelection,
    UnknownJobError,
)

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


class FakeRedis:
    """Redis tối thiểu cho SET NX/GET/EVAL của lock và idempotency."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool | None:
        del ex
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    async def eval(self, script: str, numkeys: int, *keys_and_args: object) -> int:
        del numkeys
        key = str(keys_and_args[0])
        token = str(keys_and_args[1])
        if self.data.get(key) != token:
            return 0
        if "pexpire" in script:
            return 1
        self.data.pop(key, None)
        return 1


class FakeLock:
    """Lock ghi nhận acquire/release để kiểm tra lifecycle."""

    def __init__(self, *, acquired: bool = True) -> None:
        self.acquired = acquired
        self.released = False
        self.extended = 0

    @property
    def renewal_interval(self) -> float:
        return 3600

    async def acquire(self) -> bool:
        return self.acquired

    async def release(self) -> None:
        self.released = True

    async def extend(self) -> bool:
        self.extended += 1
        return True


def make_settings(**overrides: object) -> Settings:
    """Cấu hình hermetic cho test engine."""
    values: dict[str, object] = {"_env_file": None, "app_env": "test"}
    values.update(overrides)
    return Settings(**values)


def make_engine(
    handler,
    *,
    registry: JobRegistry | None = None,
    redis: FakeRedis | None = None,
    lock: FakeLock | None = None,
    sleeps: list[float] | None = None,
    now: datetime | None = None,
    spec: JobSpec | None = None,
) -> tuple[WorkerEngine, JobRegistry]:
    """Tạo engine với registry một job và các dependency giả."""

    async def fake_sleep(delay: float) -> None:
        if sleeps is not None:
            sleeps.append(delay)

    job_spec = spec or JobSpec(name="demo", handler=handler, params_model=EmptyParams)
    target_registry = registry or JobRegistry()
    target_registry.register(job_spec)
    engine = WorkerEngine(
        make_settings(),
        target_registry,
        redis=redis,
        lock_factory=lambda *_: lock or FakeLock(),
        sleep=fake_sleep,
        now=lambda: now or datetime(2026, 11, 20, 16, 0, tzinfo=TZ),
        random_source=lambda: 0.0,
    )
    return engine, target_registry


async def test_success_returns_summary_and_sets_log_context():
    seen: dict[str, str | None] = {}

    async def handler(ctx: JobContext[EmptyParams]) -> JobResult:
        seen["job"] = worker_job_var.get()
        seen["run"] = worker_run_id_var.get()
        return JobResult(summary={"count": 2})

    engine, _ = make_engine(handler)
    outcome = await engine.execute("demo")

    assert outcome.status is JobStatus.SUCCEEDED
    assert outcome.summary == {"count": 2}
    assert outcome.attempts == 1
    assert seen["job"] == "demo"
    assert seen["run"] == outcome.run_id
    assert worker_job_var.get() is None
    assert worker_run_id_var.get() is None


async def test_retry_uses_exponential_backoff_until_success():
    calls = 0
    sleeps: list[float] = []

    async def handler(ctx: JobContext[EmptyParams]) -> JobResult:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("transient")
        return JobResult(summary={"calls": calls})

    spec = JobSpec(
        name="demo",
        handler=handler,
        params_model=EmptyParams,
        retry=RetryPolicy(attempts=3, base_delay_seconds=0.5, multiplier=2),
    )
    engine, _ = make_engine(handler, spec=spec, sleeps=sleeps)
    outcome = await engine.execute("demo")

    assert outcome.status is JobStatus.SUCCEEDED
    assert outcome.attempts == 3
    assert sleeps == [0.5, 1.0]


async def test_timeout_is_not_retried():
    calls = 0

    async def handler(ctx: JobContext[EmptyParams]) -> JobResult:
        nonlocal calls
        calls += 1
        await asyncio.sleep(5)
        return JobResult()

    spec = JobSpec(
        name="demo",
        handler=handler,
        params_model=EmptyParams,
        timeout_seconds=0.01,
        retry=RetryPolicy(attempts=3),
    )
    engine, _ = make_engine(handler, spec=spec)
    outcome = await engine.execute("demo")

    assert outcome.status is JobStatus.TIMED_OUT
    assert outcome.attempts == 1
    assert calls == 1
    assert outcome.error_type == "TimeoutError"


async def test_final_failure_is_reported_and_lock_released():
    async def handler(ctx: JobContext[EmptyParams]) -> JobResult:
        raise ValueError("boom")

    lock = FakeLock()
    spec = JobSpec(
        name="demo",
        handler=handler,
        params_model=EmptyParams,
        retry=RetryPolicy(attempts=2, base_delay_seconds=0),
    )
    engine, _ = make_engine(handler, spec=spec, lock=lock)
    outcome = await engine.execute("demo")

    assert outcome.status is JobStatus.FAILED
    assert outcome.attempts == 2
    assert outcome.error_type == "ValueError"
    assert lock.released is True


async def test_lock_contention_skips_handler():
    called = False

    async def handler(ctx: JobContext[EmptyParams]) -> JobResult:
        nonlocal called
        called = True
        return JobResult()

    engine, _ = make_engine(handler, lock=FakeLock(acquired=False))
    outcome = await engine.execute("demo")

    assert outcome.status is JobStatus.SKIPPED_LOCKED
    assert called is False


async def test_scheduled_idempotency_skips_duplicate_but_manual_runs_again():
    calls = 0

    async def handler(ctx: JobContext[EmptyParams]) -> JobResult:
        nonlocal calls
        calls += 1
        return JobResult(summary={"calls": calls})

    spec = JobSpec(
        name="daily",
        handler=handler,
        params_model=EmptyParams,
        idempotency="day",
    )
    redis = FakeRedis()
    engine, _ = make_engine(handler, spec=spec, redis=redis)

    first = await engine.execute("daily", trigger="scheduler")
    second = await engine.execute("daily", trigger="scheduler")
    manual = await engine.execute("daily", trigger="manual")
    third = await engine.execute("daily", trigger="scheduler")

    assert first.status is JobStatus.SUCCEEDED
    assert second.status is JobStatus.SKIPPED_DUPLICATE
    assert manual.status is JobStatus.SUCCEEDED
    assert third.status is JobStatus.SKIPPED_DUPLICATE
    assert calls == 2


async def test_partial_result_is_not_marked_complete():
    calls = 0

    async def handler(ctx: JobContext[EmptyParams]) -> JobResult:
        nonlocal calls
        calls += 1
        return JobResult(summary={"failed": 1}, complete=False)

    spec = JobSpec(
        name="partial",
        handler=handler,
        params_model=EmptyParams,
        idempotency="day",
    )
    redis = FakeRedis()
    engine, _ = make_engine(handler, spec=spec, redis=redis)

    first = await engine.execute("partial", trigger="scheduler")
    second = await engine.execute("partial", trigger="scheduler")

    assert first.status is JobStatus.PARTIAL
    assert first.ok is False
    assert second.status is JobStatus.PARTIAL
    assert calls == 2


async def test_disabled_job_does_not_call_handler():
    async def handler(ctx: JobContext[EmptyParams]) -> JobResult:
        raise AssertionError("không được gọi")

    spec = JobSpec(
        name="off",
        handler=handler,
        params_model=EmptyParams,
        enabled=lambda settings: False,
    )
    engine, _ = make_engine(handler, spec=spec)
    outcome = await engine.execute("off")

    assert outcome.status is JobStatus.SKIPPED_DISABLED


async def test_params_are_validated_and_unknown_job_fails():
    async def handler(ctx: JobContext[SymbolSelection]) -> JobResult:
        return JobResult()

    spec = JobSpec(name="symbols", handler=handler, params_model=SymbolSelection)
    engine, _ = make_engine(handler, spec=spec)

    with pytest.raises(ValidationError):
        await engine.execute("symbols", {"typo": 1})
    with pytest.raises(UnknownJobError):
        await engine.execute("missing")


async def test_cancellation_releases_lock_and_resets_context():
    started = asyncio.Event()

    async def handler(ctx: JobContext[EmptyParams]) -> JobResult:
        started.set()
        await asyncio.Event().wait()
        return JobResult()

    lock = FakeLock()
    engine, _ = make_engine(handler, lock=lock)
    task = asyncio.create_task(engine.execute("demo"))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert lock.released is True
    assert worker_job_var.get() is None
    assert worker_run_id_var.get() is None
