"""Job Registry: nguồn duy nhất mô tả các job mà worker engine có thể chạy.

Mỗi job là một `JobSpec` bất biến gồm handler async, Pydantic params model và
metadata vận hành (lịch, timeout, retry, idempotency). Job module chỉ cần gọi
`register_job(...)` một lần; scheduler, CLI và realtime đều tra cứu qua registry
thay vì import trực tiếp từng worker.

Thiết kế này cho phép thêm job mới bằng cách thêm một module trong
`app.workers.jobs` và đăng ký spec, không cần tạo process/container mới.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from app.core.config import Settings
from app.workers.schedule import DailySchedule, IntervalSchedule, Schedule

Trigger = Literal["manual", "scheduler", "realtime"]
IdempotencyMode = Literal["none", "day"]

_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class WorkerError(Exception):
    """Lỗi gốc của worker engine."""


class UnknownJobError(WorkerError):
    """Tên job không tồn tại trong registry."""


class DuplicateJobError(WorkerError):
    """Job đã được đăng ký trước đó."""


class JobParams(BaseModel):
    """Base cho params của mọi job.

    `extra="forbid"` biến lỗi gõ sai tên tham số thành lỗi rõ ràng thay vì âm
    thầm bỏ qua, và giữ contract CLI/API ổn định.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EmptyParams(JobParams):
    """Params rỗng cho job không nhận tham số."""


class SymbolSelection(JobParams):
    """Params chung cho job nhận danh sách mã.

    Attributes:
        symbols: Danh sách mã; `None` nghĩa là dùng mặc định của job, còn danh
            sách rỗng nghĩa là không chọn mã nào.
    """

    symbols: list[str] | None = None

    @field_validator("symbols", mode="before")
    @classmethod
    def _split_symbols(cls, value: object) -> object:
        """Chấp nhận cả list và chuỗi phân tách bằng dấu phẩy từ CLI.

        Args:
            value: Giá trị thô từ JSON hoặc `--param symbols=FPT,HPG`.

        Returns:
            List mã đã tách, hoặc nguyên giá trị để Pydantic báo lỗi kiểu.
        """
        if isinstance(value, str):
            items = [item.strip().upper() for item in value.split(",") if item.strip()]
            return items
        if isinstance(value, list):
            return [str(item).strip().upper() for item in value if str(item).strip()]
        return value


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Chính sách retry với exponential backoff.

    Attributes:
        attempts: Tổng số lần thử, tối thiểu 1 (1 nghĩa là không retry).
        base_delay_seconds: Thời gian chờ sau lần lỗi đầu tiên.
        max_delay_seconds: Trần thời gian chờ giữa hai lần thử.
        multiplier: Hệ số nhân sau mỗi lần lỗi.
        jitter_seconds: Nhiễu ngẫu nhiên tối đa cộng thêm để tránh đồng bộ.
    """

    attempts: int = 1
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    multiplier: float = 2.0
    jitter_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("RetryPolicy.attempts phải >= 1")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("RetryPolicy delay không được âm")
        if self.multiplier < 1:
            raise ValueError("RetryPolicy.multiplier phải >= 1")
        if self.jitter_seconds < 0:
            raise ValueError("RetryPolicy.jitter_seconds không được âm")

    def delay_for(self, failed_attempt: int) -> float:
        """Thời gian chờ trước lần thử kế tiếp.

        Args:
            failed_attempt: Số thứ tự lần vừa lỗi (bắt đầu từ 1).

        Returns:
            Delay chưa gồm jitter, đã bị chặn bởi `max_delay_seconds`.
        """
        exponent = max(0, failed_attempt - 1)
        return min(self.base_delay_seconds * (self.multiplier**exponent), self.max_delay_seconds)


@dataclass(frozen=True, slots=True)
class JobContext[P: BaseModel]:
    """Ngữ cảnh một lần thực thi job.

    Attributes:
        job: Tên job trong registry.
        run_id: Mã ngắn định danh lần chạy, dùng cho log và lock.
        trigger: Nguồn kích hoạt (`manual`, `scheduler`, `realtime`).
        params: Params đã được Pydantic validate theo `JobSpec.params_model`.
        settings: Cấu hình ứng dụng dùng chung.
        attempt: Số thứ tự lần thử hiện tại (bắt đầu từ 1).
    """

    job: str
    run_id: str
    trigger: Trigger
    params: P
    settings: Settings
    attempt: int = 1


JobHandler = Callable[[JobContext[Any]], Awaitable["JobResult"]]


@dataclass(frozen=True, slots=True)
class JobResult:
    """Kết quả nghiệp vụ của một job.

    Attributes:
        summary: Số liệu tổng hợp để log/CLI hiển thị; phải JSON-serializable.
        complete: False khi job chạy xong nhưng còn mã lỗi. Khi False, engine
            không ghi idempotency marker để lần chạy sau có thể bổ sung.
        skipped: True khi job chủ động bỏ qua (ví dụ RAG đang tắt).
        reason: Lý do bỏ qua, dùng cho log.
    """

    summary: dict[str, Any] = field(default_factory=dict)
    complete: bool = True
    skipped: bool = False
    reason: str | None = None


def _always_enabled(settings: Settings) -> bool:
    """Điều kiện bật mặc định: job luôn được phép chạy."""
    del settings
    return True


@dataclass(frozen=True, slots=True)
class JobSpec[P: BaseModel]:
    """Mô tả đầy đủ một job.

    Attributes:
        name: Tên duy nhất, dùng cho CLI và log (kebab-case).
        handler: Coroutine nhận `JobContext` và trả `JobResult`.
        params_model: Pydantic model validate tham số.
        description: Mô tả ngắn cho `--list`/tài liệu.
        schedule: Lịch chạy; None nghĩa là job chỉ chạy thủ công.
        timeout_seconds: Timeout một lần thử; None dùng
            `settings.scheduler_job_timeout_seconds`.
        lock_ttl_seconds: TTL khoá Redis; None suy ra từ timeout + 60 giây.
        retry: Chính sách retry/backoff.
        idempotency: `day` ghi marker theo ngày cho trigger không thủ công để
            instance khởi động lại không chạy trùng; `none` tắt marker.
        enabled: Hàm quyết định job có chạy trong cấu hình hiện tại hay không.
    """

    name: str
    handler: JobHandler
    params_model: type[P] = EmptyParams  # type: ignore[assignment]
    description: str = ""
    schedule: Schedule | None = None
    timeout_seconds: float | None = None
    lock_ttl_seconds: int | None = None
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    idempotency: IdempotencyMode = "none"
    enabled: Callable[[Settings], bool] = _always_enabled

    def __post_init__(self) -> None:
        if not _NAME_PATTERN.fullmatch(self.name):
            raise ValueError(f"Tên job không hợp lệ: {self.name!r} (dùng kebab-case)")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds phải > 0")
        if self.lock_ttl_seconds is not None and self.lock_ttl_seconds < 1:
            raise ValueError("lock_ttl_seconds phải >= 1")


class JobRegistry:
    """Bộ chứa job bất biến theo tên, giữ thứ tự đăng ký."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobSpec[Any]] = {}

    def register(self, spec: JobSpec[Any], *, replace: bool = False) -> JobSpec[Any]:
        """Đăng ký một job.

        Args:
            spec: Mô tả job.
            replace: Cho phép ghi đè job cùng tên (dùng cho test/plugin).

        Returns:
            Spec vừa đăng ký.

        Raises:
            DuplicateJobError: Khi tên đã tồn tại và `replace=False`.
        """
        if spec.name in self._jobs and not replace:
            raise DuplicateJobError(f"Job {spec.name!r} đã được đăng ký")
        self._jobs[spec.name] = spec
        return spec

    def get(self, name: str) -> JobSpec[Any]:
        """Tra cứu job theo tên.

        Args:
            name: Tên job.

        Returns:
            Spec tương ứng.

        Raises:
            UnknownJobError: Khi tên không tồn tại.
        """
        try:
            return self._jobs[name]
        except KeyError as exc:
            raise UnknownJobError(f"Không có job {name!r}") from exc

    def all(self) -> tuple[JobSpec[Any], ...]:
        """Toàn bộ job theo thứ tự đăng ký."""
        return tuple(self._jobs.values())

    def scheduled(self) -> tuple[JobSpec[Any], ...]:
        """Các job có lịch chạy, daily sắp theo giờ rồi tới interval.

        Thứ tự này bảo đảm khi scheduler khởi động muộn sau giờ đóng cửa,
        `ingest-close` (15:05) luôn chạy trước `recompute-indicators` (15:20)
        bất kể thứ tự import module.
        """
        daily: list[tuple[int, int, JobSpec[Any]]] = []
        interval: list[JobSpec[Any]] = []
        for spec in self._jobs.values():
            if isinstance(spec.schedule, DailySchedule):
                daily.append((spec.schedule.hour, spec.schedule.minute, spec))
            elif isinstance(spec.schedule, IntervalSchedule):
                interval.append(spec)
        daily.sort(key=lambda item: (item[0], item[1]))
        return tuple([spec for _hour, _minute, spec in daily] + interval)

    def names(self) -> tuple[str, ...]:
        """Tên các job đã đăng ký."""
        return tuple(self._jobs)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._jobs

    def __len__(self) -> int:
        return len(self._jobs)


_DEFAULT_REGISTRY = JobRegistry()


def get_registry() -> JobRegistry:
    """Registry mặc định của process."""
    return _DEFAULT_REGISTRY


def discover_jobs() -> JobRegistry:
    """Import các job module để decorator đăng ký chạy một lần.

    Returns:
        Registry mặc định sau khi tất cả job đã được nạp.
    """
    import app.workers.jobs  # noqa: F401  # side-effect registration

    return _DEFAULT_REGISTRY


def register_job(
    *,
    name: str,
    params_model: type[BaseModel] = EmptyParams,
    description: str = "",
    schedule: Schedule | None = None,
    timeout_seconds: float | None = None,
    lock_ttl_seconds: int | None = None,
    retry: RetryPolicy | None = None,
    idempotency: IdempotencyMode = "none",
    enabled: Callable[[Settings], bool] = _always_enabled,
    registry: JobRegistry | None = None,
) -> Callable[[JobHandler], JobHandler]:
    """Decorator đăng ký job vào registry.

    Args:
        name: Tên job kebab-case.
        params_model: Pydantic model validate params.
        description: Mô tả cho CLI/tài liệu.
        schedule: Lịch chạy, None nếu chỉ chạy thủ công.
        timeout_seconds: Timeout một lần thử.
        lock_ttl_seconds: TTL khoá Redis.
        retry: Chính sách retry; None dùng mặc định không retry.
        idempotency: Chế độ idempotency (`none`/`day`).
        enabled: Điều kiện bật job.
        registry: Registry đích; mặc định registry của process.

    Returns:
        Decorator giữ nguyên hàm gốc để test gọi trực tiếp.
    """

    def decorate(handler: JobHandler) -> JobHandler:
        target = registry or _DEFAULT_REGISTRY
        target.register(
            JobSpec(
                name=name,
                handler=handler,
                params_model=params_model,
                description=description,
                schedule=schedule,
                timeout_seconds=timeout_seconds,
                lock_ttl_seconds=lock_ttl_seconds,
                retry=retry or RetryPolicy(),
                idempotency=idempotency,
                enabled=enabled,
            )
        )
        return handler

    return decorate
