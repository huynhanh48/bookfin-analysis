"""Bootstrap dùng chung cho mọi entrypoint worker.

Gom việc cấu hình logging, TA-Lib unstable periods, registry, engine và dọn tài
nguyên vào một chỗ để CLI, scheduler cũ và các worker legacy có cùng hành vi.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from app.core.config import Settings
from app.core.database import dispose_engine
from app.core.logging import configure_logging
from app.core.redis import close_redis_client
from app.workers.engine import JobOutcome, WorkerEngine
from app.workers.registry import JobRegistry, Trigger, discover_jobs


def prepare(settings: Settings) -> None:
    """Cấu hình process trước khi chạy job.

    Args:
        settings: Cấu hình ứng dụng.
    """
    configure_logging(settings.log_level)
    from app.indicators.native import configure_unstable_periods

    configure_unstable_periods(settings.indicator_unstable_periods)


def build_registry() -> JobRegistry:
    """Nạp toàn bộ job và trả registry mặc định."""
    return discover_jobs()


def build_engine(settings: Settings, *, registry: JobRegistry | None = None) -> WorkerEngine:
    """Tạo worker engine cho process hiện tại.

    Args:
        settings: Cấu hình ứng dụng.
        registry: Registry tuỳ chọn; mặc định registry đã discover.

    Returns:
        Engine dùng Redis client chung của process.
    """
    return WorkerEngine(settings, registry or build_registry())


async def execute_job(
    settings: Settings,
    name: str,
    params: Mapping[str, Any] | BaseModel | None = None,
    *,
    trigger: Trigger = "manual",
    force: bool = False,
) -> JobOutcome:
    """Chạy một job một lần (executor).

    Args:
        settings: Cấu hình ứng dụng.
        name: Tên job.
        params: Tham số job.
        trigger: Nguồn kích hoạt.
        force: Bỏ qua idempotency marker của ngày.

    Returns:
        Kết quả job.
    """
    engine = build_engine(settings)
    return await engine.execute(name, params, trigger=trigger, force=force)


async def close_worker_resources() -> None:
    """Đóng Redis và pool PostgreSQL của process worker."""
    with contextlib.suppress(Exception):
        await close_redis_client()
    await dispose_engine()


def install_stop_handlers(stop_event: asyncio.Event) -> None:
    """Gắn SIGTERM/SIGINT để dừng role một cách graceful.

    Args:
        stop_event: Sự kiện được set khi nhận signal.
    """
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop_event.set)
