"""Các job nạp dữ liệu thị trường: danh mục mã và nến đóng."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field

from app.core.database import session_scope
from app.core.logging import get_logger
from app.fcdata.client import FcDataClient
from app.repositories.market_repository import MarketRepository
from app.services.ingestion_service import BackfillStats, IngestionService
from app.workers.jobs.common import dedupe_symbols, load_active_symbols
from app.workers.registry import (
    EmptyParams,
    JobContext,
    JobResult,
    RetryPolicy,
    SymbolSelection,
    register_job,
)
from app.workers.schedule import DailySchedule

logger = get_logger(__name__)


class SyncSymbolsParams(EmptyParams):
    """Job đồng bộ catalog không nhận tham số."""


class IngestCloseParams(SymbolSelection):
    """Tham số nạp nến đóng.

    Attributes:
        days: Số ngày lịch cần nạp lại; None dùng `INGEST_INCREMENTAL_DAYS`.
        timeframe: Chỉ hỗ trợ `1D`; `1W` được dựng từ nến ngày khi đọc.
        limit: Số mã tối đa khi không truyền danh sách mã cụ thể.
    """

    days: int | None = Field(default=None, ge=1, le=60)
    timeframe: Literal["1D"] = "1D"
    limit: int = Field(default=1000, ge=1, le=5000)


@register_job(
    name="sync-symbols",
    params_model=SyncSymbolsParams,
    description="Đồng bộ danh mục mã chứng khoán từ FCData.",
    schedule=DailySchedule(8, 0),
    timeout_seconds=300,
    retry=RetryPolicy(attempts=3, base_delay_seconds=2, max_delay_seconds=30, jitter_seconds=1),
    idempotency="day",
)
async def sync_symbols(ctx: JobContext[SyncSymbolsParams]) -> JobResult:
    """Đồng bộ danh mục mã từ FCData Gateway.

    Args:
        ctx: Ngữ cảnh job.

    Returns:
        `JobResult` với số mã đã ghi/cập nhật.
    """
    settings = ctx.settings
    async with FcDataClient(settings) as client, session_scope() as session:
        service = IngestionService(
            settings=settings,
            repository=MarketRepository(session),
            fcdata=client,
        )
        count = await service.sync_symbols()
    logger.info("job_sync_symbols_done", extra={"count": count})
    return JobResult(summary={"count": count})


@register_job(
    name="ingest-close",
    params_model=IngestCloseParams,
    description="Nạp nến đóng của các phiên gần nhất cho toàn bộ mã đang hoạt động.",
    schedule=DailySchedule(15, 5),
    timeout_seconds=1800,
    retry=RetryPolicy(attempts=2, base_delay_seconds=5, max_delay_seconds=60, jitter_seconds=1),
    idempotency="day",
)
async def ingest_close(ctx: JobContext[IngestCloseParams]) -> JobResult:
    """Nạp nến đóng cho danh sách mã hoặc toàn bộ mã đang hoạt động.

    Mỗi mã dùng session riêng để một mã lỗi không làm hỏng transaction của mã
    khác. Kết quả trả về `complete=False` khi còn mã lỗi, nhờ đó engine không ghi
    idempotency marker và lần chạy sau vẫn có thể bổ sung.

    Args:
        ctx: Ngữ cảnh job.

    Returns:
        `JobResult` tổng hợp số nến đã ghi và số mã lỗi.
    """
    settings = ctx.settings
    params = ctx.params
    symbols = (
        dedupe_symbols(params.symbols)
        if params.symbols is not None
        else await load_active_symbols(params.limit)
    )
    if not symbols:
        logger.warning("job_ingest_no_symbols")
        return JobResult(summary={"requested": 0, "inserted": 0, "failed": 0})

    end = datetime.now(tz=settings.market_tz).date()
    start = end - timedelta(days=params.days or settings.ingest_incremental_days)
    semaphore = asyncio.Semaphore(settings.scheduler_ingest_concurrency)
    inserted = 0
    rejected = 0
    failed = 0

    async with FcDataClient(settings) as client:

        async def ingest(symbol: str) -> BackfillStats:
            async with semaphore, session_scope() as session:
                service = IngestionService(
                    settings=settings,
                    repository=MarketRepository(session),
                    fcdata=client,
                )
                return await service.backfill_symbol(
                    symbol, timeframe=params.timeframe, start=start, end=end
                )

        results = await asyncio.gather(
            *(ingest(symbol) for symbol in symbols), return_exceptions=True
        )

    for symbol, result in zip(symbols, results, strict=True):
        if isinstance(result, BaseException):
            failed += 1
            logger.warning(
                "job_ingest_symbol_failed",
                extra={"symbol": symbol, "error_type": type(result).__name__},
            )
            continue
        inserted += result.inserted
        rejected += result.rejected

    logger.info(
        "job_ingest_close_done",
        extra={
            "symbols": len(symbols),
            "inserted": inserted,
            "rejected": rejected,
            "failed": failed,
            "from": start.isoformat(),
            "to": end.isoformat(),
        },
    )
    return JobResult(
        summary={
            "requested": len(symbols),
            "inserted": inserted,
            "rejected": rejected,
            "failed": failed,
            "from": start.isoformat(),
            "to": end.isoformat(),
        },
        complete=failed == 0,
    )
