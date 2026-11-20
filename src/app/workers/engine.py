"""Worker Engine: executor thống nhất cho mọi job.

Engine chịu trách nhiệm duy nhất các cơ chế vận hành, để handler chỉ tập trung
vào nghiệp vụ:

- tra cứu `JobSpec` trong registry và validate params bằng Pydantic;
- Redis distributed lock theo tên job (release an toàn bằng token);
- timeout cho từng lần thử;
- retry exponential backoff có jitter;
- idempotency marker theo ngày cho trigger scheduler/realtime;
- structured logging với `worker_job`/`worker_run_id`, không log params thô;
- nhả lock và reset context khi bị cancel (graceful shutdown).

Engine không tự mở process hay kết nối DB; nó được scheduler, CLI executor và
realtime role dùng chung.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel
from redis.asyncio import Redis

from app.core.config import Settings
from app.core.logging import (
    get_logger,
    worker_job_var,
    worker_run_id_var,
)
from app.core.redis import get_redis_client
from app.workers.locks import DistributedLock, build_lock
from app.workers.registry import (
    JobContext,
    JobRegistry,
    JobResult,
    JobSpec,
    Trigger,
)

logger = get_logger(__name__)

LockFactory = Callable[[str, int, str], DistributedLock]
SleepFunc = Callable[[float], Awaitable[None]]
NowFunc = Callable[[], datetime]

_KEY_PREFIX = "worker"
_UNSET = object()


class JobStatus(StrEnum):
    """Trạng thái cuối cùng của một lần chạy job."""

    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    SKIPPED_LOCKED = "skipped_locked"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    SKIPPED_DISABLED = "skipped_disabled"


@dataclass(frozen=True, slots=True)
class JobOutcome:
    """Kết quả một lần gọi engine.

    Attributes:
        job: Tên job.
        run_id: Mã lần chạy.
        status: Trạng thái cuối cùng.
        attempts: Số lần handler đã được gọi.
        elapsed_ms: Thời gian chạy tính bằng mili giây.
        summary: Số liệu tổng hợp từ handler.
        error_type: Tên lớp exception khi thất bại.
        reason: Lý do khi bỏ qua.
    """

    job: str
    run_id: str
    status: JobStatus
    attempts: int = 0
    elapsed_ms: int = 0
    summary: dict[str, Any] = field(default_factory=dict)
    error_type: str | None = None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        """True khi job hoàn tất toàn bộ hoặc bỏ qua an toàn."""
        return self.status in {
            JobStatus.SUCCEEDED,
            JobStatus.SKIPPED_LOCKED,
            JobStatus.SKIPPED_DUPLICATE,
            JobStatus.SKIPPED_DISABLED,
        }

    def as_dict(self) -> dict[str, Any]:
        """Chuyển outcome thành dict JSON-friendly cho CLI/healthcheck."""
        return {
            "job": self.job,
            "run_id": self.run_id,
            "status": str(self.status),
            "ok": self.ok,
            "attempts": self.attempts,
            "elapsed_ms": self.elapsed_ms,
            "summary": self.summary,
            "error_type": self.error_type,
            "reason": self.reason,
        }


class WorkerEngine:
    """Executor dùng chung cho scheduler, realtime trigger và CLI."""

    def __init__(
        self,
        settings: Settings,
        registry: JobRegistry,
        *,
        redis: Redis | None | object = _UNSET,
        lock_factory: LockFactory | None = None,
        sleep: SleepFunc = asyncio.sleep,
        now: NowFunc | None = None,
        random_source: Callable[[], float] = random.random,
    ) -> None:
        """Khởi tạo engine.

        Args:
            settings: Cấu hình ứng dụng.
            registry: Registry chứa job.
            redis: Redis client; mặc định lấy client dùng chung của process.
                Truyền `None` để tắt lock/idempotency trong test.
            lock_factory: Factory lock tuỳ chọn cho test.
            sleep: Hàm sleep, inject để test backoff không chờ thật.
            now: Hàm lấy thời điểm hiện tại, inject để test idempotency.
            random_source: Nguồn jitter, inject để test deterministic.
        """
        self._settings = settings
        self._registry = registry
        self._redis: Redis | None = (
            get_redis_client() if redis is _UNSET else redis  # type: ignore[assignment]
        )
        self._lock_factory = lock_factory or self._default_lock
        self._sleep = sleep
        self._now = now or (lambda: datetime.now(tz=settings.market_tz))
        self._random = random_source

    async def execute(
        self,
        name: str,
        params: Mapping[str, Any] | BaseModel | None = None,
        *,
        trigger: Trigger = "manual",
        force: bool = False,
    ) -> JobOutcome:
        """Thực thi một job theo tên.

        Args:
            name: Tên job trong registry.
            params: Dict hoặc Pydantic model; được validate theo spec.
            trigger: Nguồn kích hoạt, quyết định idempotency marker.
            force: Bỏ qua kiểm tra idempotency của ngày.

        Returns:
            `JobOutcome` mô tả kết quả; không raise lỗi nghiệp vụ.

        Raises:
            UnknownJobError: Khi job không tồn tại.
            pydantic.ValidationError: Khi params không hợp lệ.
            asyncio.CancelledError: Khi process/role bị dừng giữa chừng.
        """
        spec = self._registry.get(name)
        validated = spec.params_model.model_validate(
            params.model_dump() if isinstance(params, BaseModel) else (params or {})
        )
        run_id = uuid4().hex[:12]
        started = time.monotonic()
        params_hash = _params_hash(validated)
        job_token = worker_job_var.set(spec.name)
        run_token = worker_run_id_var.set(run_id)
        lock: DistributedLock | None = None
        renewal: asyncio.Task[None] | None = None
        outcome = JobOutcome(
            job=spec.name,
            run_id=run_id,
            status=JobStatus.SKIPPED_DISABLED,
            reason="disabled",
        )

        logger.info(
            "job_started",
            extra={
                "job": spec.name,
                "run_id": run_id,
                "trigger": trigger,
                "params_hash": params_hash,
            },
        )
        try:
            if not spec.enabled(self._settings):
                logger.info("job_disabled", extra={"job": spec.name, "run_id": run_id})
                return outcome

            if not force and spec.idempotency == "day" and trigger != "manual":
                marker = self._marker_key(spec, trigger, params_hash)
                if await self._is_marked(marker):
                    logger.info(
                        "job_skipped_duplicate",
                        extra={"job": spec.name, "run_id": run_id, "trigger": trigger},
                    )
                    outcome = JobOutcome(
                        job=spec.name,
                        run_id=run_id,
                        status=JobStatus.SKIPPED_DUPLICATE,
                        elapsed_ms=_elapsed_ms(started),
                        reason="already_completed_today",
                    )
                    return outcome

            lock = self._lock_factory(
                self._lock_key(spec),
                self._lock_ttl(spec),
                run_id,
            )
            if not await lock.acquire():
                logger.info(
                    "job_skipped_locked",
                    extra={"job": spec.name, "run_id": run_id, "trigger": trigger},
                )
                outcome = JobOutcome(
                    job=spec.name,
                    run_id=run_id,
                    status=JobStatus.SKIPPED_LOCKED,
                    elapsed_ms=_elapsed_ms(started),
                    reason="locked",
                )
                return outcome

            renewal = self._start_renewal(lock, spec.name, run_id)
            try:
                result, status, attempts, error_type = await self._run_attempts(
                    spec, validated, run_id=run_id, trigger=trigger
                )
            except asyncio.CancelledError:
                outcome = JobOutcome(
                    job=spec.name,
                    run_id=run_id,
                    status=JobStatus.CANCELLED,
                    elapsed_ms=_elapsed_ms(started),
                    reason="cancelled",
                )
                raise
            outcome = JobOutcome(
                job=spec.name,
                run_id=run_id,
                status=status,
                attempts=attempts,
                elapsed_ms=_elapsed_ms(started),
                summary=result.summary if result else {},
                error_type=error_type,
                reason=result.reason if result else None,
            )
            if (
                result is not None
                and result.complete
                and not result.skipped
                and status is JobStatus.SUCCEEDED
                and not force
                and spec.idempotency == "day"
                and trigger != "manual"
            ):
                await self._mark(self._marker_key(spec, trigger, params_hash), run_id)
            return outcome
        finally:
            if renewal is not None:
                renewal.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await renewal
            if lock is not None:
                await lock.release()
            logger.info(
                "job_finished",
                extra={
                    "job": spec.name,
                    "run_id": run_id,
                    "trigger": trigger,
                    "status": str(outcome.status),
                    "attempts": outcome.attempts,
                    "elapsed_ms": _elapsed_ms(started),
                    "summary": outcome.summary,
                },
            )
            worker_run_id_var.reset(run_token)
            worker_job_var.reset(job_token)

    async def _run_attempts(
        self,
        spec: JobSpec[Any],
        params: BaseModel,
        *,
        run_id: str,
        trigger: Trigger,
    ) -> tuple[JobResult | None, JobStatus, int, str | None]:
        """Chạy handler với retry/backoff và timeout.

        Args:
            spec: Mô tả job.
            params: Params đã validate.
            run_id: Mã lần chạy.
            trigger: Nguồn kích hoạt.

        Returns:
            Tuple `(result, status, attempts, error_type)`.
        """
        timeout = spec.timeout_seconds or self._settings.scheduler_job_timeout_seconds
        attempts = spec.retry.attempts
        for attempt in range(1, attempts + 1):
            context = JobContext(
                job=spec.name,
                run_id=run_id,
                trigger=trigger,
                params=params,
                settings=self._settings,
                attempt=attempt,
            )
            try:
                async with asyncio.timeout(timeout):
                    result = await spec.handler(context)
                status = JobStatus.SUCCEEDED if result.complete else JobStatus.PARTIAL
                return result, status, attempt, None
            except TimeoutError:
                logger.error(
                    "job_timeout",
                    extra={
                        "job": spec.name,
                        "run_id": run_id,
                        "attempt": attempt,
                        "timeout_seconds": timeout,
                    },
                )
                return None, JobStatus.TIMED_OUT, attempt, "TimeoutError"
            except asyncio.CancelledError:
                logger.info(
                    "job_cancelled",
                    extra={"job": spec.name, "run_id": run_id, "attempt": attempt},
                )
                raise
            except Exception as exc:  # noqa: BLE001 - engine phải báo cáo, không làm chết role
                error_type = type(exc).__name__
                if attempt >= attempts:
                    logger.exception(
                        "job_failed",
                        extra={
                            "job": spec.name,
                            "run_id": run_id,
                            "attempt": attempt,
                            "error_type": error_type,
                        },
                    )
                    return None, JobStatus.FAILED, attempt, error_type
                delay = spec.retry.delay_for(attempt)
                if spec.retry.jitter_seconds:
                    delay += self._random() * spec.retry.jitter_seconds
                logger.warning(
                    "job_retry",
                    extra={
                        "job": spec.name,
                        "run_id": run_id,
                        "attempt": attempt,
                        "delay_seconds": round(delay, 3),
                        "error_type": error_type,
                    },
                )
                await self._sleep(delay)
        raise AssertionError("retry loop phải return trước khi kết thúc")

    def _start_renewal(
        self, lock: DistributedLock, job: str, run_id: str
    ) -> asyncio.Task[None] | None:
        """Khởi động task gia hạn TTL nếu lock cần gia hạn.

        Args:
            lock: Lock vừa acquire.
            job: Tên job.
            run_id: Mã lần chạy.

        Returns:
            Task gia hạn hoặc None với NullLock.
        """
        interval = lock.renewal_interval
        if interval <= 0:
            return None

        async def renew() -> None:
            while True:
                await asyncio.sleep(interval)
                if not await lock.extend():
                    logger.warning("job_lock_lost", extra={"job": job, "run_id": run_id})
                    return

        return asyncio.create_task(renew(), name=f"worker-lock-{job}")

    def _default_lock(self, key: str, ttl_seconds: int, token: str) -> DistributedLock:
        """Tạo lock Redis mặc định."""
        return build_lock(
            self._redis,
            key,
            ttl_seconds,
            token=token,
            fail_open=self._settings.worker_lock_fail_open,
        )

    def _lock_ttl(self, spec: JobSpec[Any]) -> int:
        """TTL lock đủ dài cho timeout của job cộng buffer."""
        if spec.lock_ttl_seconds is not None:
            return spec.lock_ttl_seconds
        timeout = spec.timeout_seconds or self._settings.scheduler_job_timeout_seconds
        return max(60, int(timeout) + 60)

    def _lock_key(self, spec: JobSpec[Any]) -> str:
        """Khoá theo tên job để mọi biến thể params của cùng job chạy tuần tự.

        Nhờ vậy recompute toàn bộ của scheduler và recompute watchlist của
        realtime không tính trùng cùng lúc; service vẫn tái sử dụng snapshot
        theo `input_hash` nên lần chạy sau không tính lại TA-Lib.
        """
        return f"{_KEY_PREFIX}:lock:{spec.name}"

    def _marker_key(self, spec: JobSpec[Any], trigger: Trigger, params_hash: str) -> str:
        """Khoá idempotency theo job + trigger + params + ngày thị trường."""
        day = self._now().date().isoformat()
        return f"{_KEY_PREFIX}:done:{spec.name}:{trigger}:{params_hash}:{day}"

    async def _is_marked(self, key: str) -> bool:
        """Kiểm tra marker đã tồn tại; lỗi Redis coi như chưa có."""
        if self._redis is None:
            return False
        try:
            return bool(await self._redis.get(key))
        except Exception as exc:  # noqa: BLE001 - marker là tối ưu, không phải chốt an toàn
            logger.warning(
                "worker_idempotency_read_failed",
                extra={"key": key, "error_type": type(exc).__name__},
            )
            return False

    async def _mark(self, key: str, run_id: str) -> None:
        """Ghi marker idempotency với TTL cấu hình."""
        if self._redis is None:
            return
        try:
            await self._redis.set(key, run_id, ex=self._settings.worker_idempotency_ttl_seconds)
        except Exception as exc:  # noqa: BLE001 - job đã thành công, chỉ mất tối ưu
            logger.warning(
                "worker_idempotency_write_failed",
                extra={"key": key, "error_type": type(exc).__name__},
            )


def _params_hash(params: BaseModel) -> str:
    """Hash params đã chuẩn hoá để đặt tên lock/marker.

    Không log giá trị params thô; hash đủ để phân biệt các biến thể job.
    """
    payload = params.model_dump(mode="json", exclude_none=True)
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def _elapsed_ms(started: float) -> int:
    """Thời gian đã trôi qua tính bằng mili giây."""
    return int((time.monotonic() - started) * 1000)
