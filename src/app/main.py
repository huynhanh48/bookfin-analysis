"""Điểm vào của FastAPI application."""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.core.config import get_settings
from app.core.database import dispose_engine
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.rag_runtime import RagRuntime
from app.core.redis import close_redis_client
from app.fcdata.client import FcDataClient
from app.middleware.access_logging import AccessLoggingMiddleware
from app.middleware.request_id import RequestIdMiddleware
from app.routers import (
    agent,
    analysis,
    chat,
    health,
    indicators,
    market,
    rag,
    realtime,
    screener,
    watchlists,
)
from app.services.agent_service import AgentService
from app.services.realtime_service import MarketChannelAuthorizer, RealtimeService
from app.services.screener_realtime_service import ScreenerRealtimeBridge

logger = get_logger(__name__)


async def _shutdown_step(name: str, operation, timeout: float) -> None:
    """Bound one cleanup step so a stuck provider cannot block process exit.

    ``asyncio.wait`` is used instead of ``wait_for`` because a cancelled
    operation may itself ignore cancellation (for example while waiting on a
    thread that is still finishing a provider call).
    """
    task = asyncio.ensure_future(operation)
    started = time.perf_counter()
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if not done:
        task.cancel()
        logger.warning(
            "shutdown_step_timeout", extra={"step": name, "timeout_seconds": timeout}
        )
        return
    if task.cancelled():
        logger.warning("shutdown_step_cancelled", extra={"step": name})
        return
    error = task.exception()
    if error is not None:
        logger.warning(
            "shutdown_step_failed", extra={"step": name, "error_type": type(error).__name__}
        )
        return
    logger.info(
        "shutdown_step",
        extra={"step": name, "duration_seconds": time.perf_counter() - started},
    )


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
    from app.indicators.native import configure_unstable_periods

    configure_unstable_periods(settings.indicator_unstable_periods)
    app.state.realtime = RealtimeService(
        MarketChannelAuthorizer(),
        queue_size=settings.websocket_queue_size,
        max_connections=settings.websocket_max_connections,
        max_subscriptions=settings.websocket_max_subscriptions,
    )
    app.state.fcdata_client = FcDataClient(settings)
    app.state.agent_service = AgentService(settings, app.state.realtime, app.state.fcdata_client)
    from app.services.chart_realtime_service import ChartRealtimeService
    from app.services.realtime_indicators import build_default_cache

    screener_bridge_task = None
    screener_stop = asyncio.Event()
    app.state.screener_bridge = None
    if settings.screener_realtime_enabled:
        from app.core.redis import get_redis_client

        redis = get_redis_client()
        if redis is not None:
            bridge = ScreenerRealtimeBridge(redis, app.state.realtime)
            app.state.screener_bridge = bridge
            screener_bridge_task = asyncio.create_task(
                bridge.run(screener_stop), name="screener-bridge"
            )
        else:
            logger.warning("screener_bridge_disabled_no_redis")

    chart_task = None
    if settings.chart_realtime_enabled:
        chart_task = asyncio.create_task(
            ChartRealtimeService(
                settings,
                app.state.realtime,
                app.state.fcdata_client,
                build_default_cache(),
            ).run(),
            name="chart-realtime",
        )
    app.state.rag_runtime = None
    if settings.rag_enabled:
        try:
            from app.workers.rag_worker import consume

            runtime = RagRuntime(settings)
            app.state.rag_runtime = runtime
            app.state.agent_service.rag_runtime = runtime
            runtime.worker_task = asyncio.create_task(consume(runtime), name="rag-consumer")
            # Retrieve exception immediately to avoid unobserved-task logs with provider details.
            runtime.worker_task.add_done_callback(
                lambda task: task.exception() if not task.cancelled() else None
            )
        except Exception as exc:
            logger.warning("rag_start_failed", extra={"error_type": type(exc).__name__})
    logger.info(
        "app_started",
        extra={"app": settings.app_name, "env": settings.app_env, "version": __version__},
    )
    try:
        yield
    finally:
        if chart_task:
            chart_task.cancel()
            await _shutdown_step("chart_realtime", chart_task, 5)
        screener_stop.set()
        if screener_bridge_task:
            screener_bridge_task.cancel()
            await _shutdown_step("screener_bridge", screener_bridge_task, 5)
        await _shutdown_step("realtime", app.state.realtime.close(), 5)
        await _shutdown_step("agent", app.state.agent_service.close(), 5)
        runtime = app.state.rag_runtime
        if runtime:
            if runtime.worker_task:
                runtime.worker_task.cancel()
                await _shutdown_step("rag_worker", runtime.worker_task, 5)
            await _shutdown_step("rag_runtime", runtime.close(), 8)
        await _shutdown_step("fcdata", app.state.fcdata_client.close(), 5)
        await _shutdown_step("redis", close_redis_client(), 5)
        await _shutdown_step("database", dispose_engine(), 5)
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
    from app.middleware.rag_body_limit import RagBodyLimitMiddleware

    app.add_middleware(
        RagBodyLimitMiddleware,
        limit=settings.rag_max_upload_bytes + 262144,
        prefix=settings.api_prefix,
    )
    app.add_middleware(AccessLoggingMiddleware)
    app.add_middleware(RequestIdMiddleware)

    register_exception_handlers(app)

    app.include_router(realtime.router, prefix=settings.api_prefix)
    app.include_router(agent.router, prefix=settings.api_prefix)
    app.include_router(chat.router, prefix=settings.api_prefix)
    app.include_router(health.router)
    app.include_router(market.router, prefix=settings.api_prefix)
    app.include_router(indicators.router, prefix=settings.api_prefix)
    app.include_router(analysis.router, prefix=settings.api_prefix)
    app.include_router(rag.router, prefix=settings.api_prefix)
    app.include_router(screener.router, prefix=settings.api_prefix)
    app.include_router(watchlists.router, prefix=settings.api_prefix)

    return app


app = create_app()
