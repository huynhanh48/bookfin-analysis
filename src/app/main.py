"""Điểm vào của FastAPI application."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.core.config import get_settings
from app.core.database import dispose_engine
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.redis import close_redis_client
from app.fcdata.client import FcDataClient
from app.middleware.access_logging import AccessLoggingMiddleware
from app.middleware.request_id import RequestIdMiddleware
from app.routers import health, indicators, market

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Quản lý tài nguyên dùng chung theo vòng đời ứng dụng.

    FCData client được tạo một lần cho cả process để dùng chung connection pool.

    Args:
        app: FastAPI application đang khởi động.

    Yields:
        None; phần sau `yield` là bước shutdown mềm.
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    app.state.fcdata_client = FcDataClient(settings)
    logger.info(
        "app_started",
        extra={"app": settings.app_name, "env": settings.app_env, "version": __version__},
    )
    try:
        yield
    finally:
        await app.state.fcdata_client.close()
        await close_redis_client()
        await dispose_engine()
        logger.info("app_stopped")


def create_app() -> FastAPI:
    """Tạo và cấu hình FastAPI application.

    Returns:
        Ứng dụng đã gắn middleware, exception handler và các router.
    """
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="Backend phân tích chứng khoán Việt Nam, dữ liệu từ BookFin FCData Gateway.",
        lifespan=lifespan,
    )

    # Thứ tự add_middleware: cái thêm sau nằm ngoài cùng, nên request id được
    # thiết lập trước khi access log chạy.
    app.add_middleware(AccessLoggingMiddleware)
    app.add_middleware(RequestIdMiddleware)

    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(market.router, prefix=settings.api_prefix)
    app.include_router(indicators.router, prefix=settings.api_prefix)

    return app


app = create_app()
