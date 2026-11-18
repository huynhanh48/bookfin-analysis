"""Endpoint vận hành: liveness và readiness."""

from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from app import __version__
from app.core.config import get_settings
from app.core.database import engine
from app.core.logging import get_logger
from app.core.redis import get_redis_client

logger = get_logger(__name__)

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    """Trạng thái liveness của process."""

    status: Literal["ok"]
    app: str
    version: str
    env: str


class CheckResult(BaseModel):
    """Kết quả một phép kiểm tra phụ thuộc."""

    ok: bool
    detail: str | None = None


class ReadyResponse(BaseModel):
    """Trạng thái readiness kèm chi tiết từng phụ thuộc."""

    status: Literal["ready", "degraded"]
    checks: dict[str, CheckResult]


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health() -> HealthResponse:
    """Báo process còn sống.

    Không truy cập database, Redis hay upstream nên luôn nhanh và độc lập, dùng
    được cho healthcheck của container.

    Returns:
        Thông tin định danh service kèm trạng thái `ok`.
    """
    settings = get_settings()
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        version=__version__,
        env=settings.app_env,
    )


async def _check_database() -> CheckResult:
    """Kiểm tra PostgreSQL bằng một câu `SELECT 1`.

    Returns:
        `CheckResult` với `ok=False` khi không kết nối được database.
    """
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("ready_database_failed", extra={"error_type": type(exc).__name__})
        return CheckResult(ok=False, detail="database unavailable")
    return CheckResult(ok=True)


async def _check_redis() -> CheckResult:
    """Kiểm tra Redis khi tính năng cache được bật.

    Returns:
        `CheckResult` với `detail="disabled"` khi `REDIS_ENABLED=false`, và
        `ok=False` khi Redis được bật nhưng không phản hồi.
    """
    client = get_redis_client()
    if client is None:
        return CheckResult(ok=True, detail="disabled")
    try:
        await client.ping()
    except Exception as exc:
        logger.warning("ready_redis_failed", extra={"error_type": type(exc).__name__})
        return CheckResult(ok=False, detail="redis unavailable")
    return CheckResult(ok=True)


@router.get("/ready", response_model=ReadyResponse, summary="Readiness probe")
async def ready(response: Response) -> ReadyResponse:
    """Kiểm tra các phụ thuộc bắt buộc trước khi nhận traffic.

    Args:
        response: Response được inject để có thể hạ status xuống 503.

    Returns:
        Trạng thái `ready` hoặc `degraded` kèm chi tiết từng phụ thuộc.
    """
    checks = {
        "database": await _check_database(),
        "redis": await _check_redis(),
    }
    is_ready = all(check.ok for check in checks.values())
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(status="ready" if is_ready else "degraded", checks=checks)
