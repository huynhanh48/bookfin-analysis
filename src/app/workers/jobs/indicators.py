"""Các job chỉ báo: recompute confirmed và quét phân kỳ đã xác nhận."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from app.core.config import Settings
from app.core.database import session_scope
from app.core.exceptions import InsufficientDataError
from app.core.logging import get_logger
from app.repositories.indicator_repository import IndicatorRepository
from app.repositories.market_repository import MarketRepository
from app.services.indicator_service import DivergenceScanRow, IndicatorService
from app.workers.jobs.common import dedupe_symbols, load_active_symbols
from app.workers.registry import (
    JobContext,
    JobResult,
    RetryPolicy,
    SymbolSelection,
    register_job,
)
from app.workers.schedule import DailySchedule

logger = get_logger(__name__)


class RecomputeParams(SymbolSelection):
    """Tham số tính lại chỉ báo.

    Attributes:
        include_all: Kết hợp danh sách mã chỉ định với toàn bộ mã đang hoạt động.
        limit: Số mã tối đa khi đọc toàn bộ danh mục.
        timeframe: Khung thời gian.
        end: Cutoff replay có timezone; chỉ nến đóng trước thời điểm này được dùng.
    """

    include_all: bool = False
    limit: int = Field(default=1000, ge=1, le=5000)
    timeframe: Literal["1D", "1W"] = "1D"
    end: datetime | None = None

    @field_validator("end")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        """Từ chối replay thiếu timezone để tránh hiểu sai phiên giao dịch."""
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("end phải có timezone, ví dụ 2026-10-31T15:01:00+07:00")
        return value


class DivergenceScanParams(SymbolSelection):
    """Tham số quét phân kỳ.

    Attributes:
        days: Số phiên gần nhất cần quét.
        limit: Số mã tối đa.
        timeframe: Khung thời gian.
    """

    days: int = Field(default=1, ge=1, le=60)
    limit: int = Field(default=600, ge=1, le=1000)
    timeframe: Literal["1D", "1W"] = "1D"


async def _targets(params: RecomputeParams) -> list[str]:
    """Xác định danh sách mã cần recompute.

    Args:
        params: Tham số job.

    Returns:
        Danh sách mã không trùng; rỗng nghĩa là không có mục tiêu.
    """
    if params.symbols is None:
        return await load_active_symbols(params.limit)
    symbols = dedupe_symbols(params.symbols)
    if params.include_all:
        symbols = dedupe_symbols([*symbols, *await load_active_symbols(params.limit)])
    return symbols


async def recompute_symbols(settings: Settings, params: RecomputeParams) -> JobResult:
    """Tính lại chỉ báo confirmed cho danh sách mã.

    `IndicatorService.recompute` tự tái sử dụng snapshot nếu `input_hash` và
    metadata chưa đổi, nên gọi lại job khi không có nến mới không tính lại
    TA-Lib và không ghi đè kết quả cũ.

    Args:
        settings: Cấu hình ứng dụng.
        params: Tham số job.

    Returns:
        `JobResult` với số mã thành công/bỏ qua/lỗi.
    """
    from app.indicators.native import configure_unstable_periods

    configure_unstable_periods(settings.indicator_unstable_periods)
    symbols = await _targets(params)
    recomputed = 0
    skipped = 0
    failed = 0

    for symbol in symbols:
        try:
            async with session_scope() as session:
                service = IndicatorService(
                    settings=settings,
                    market_repository=MarketRepository(session),
                    indicator_repository=IndicatorRepository(session),
                )
                result = await service.recompute(symbol, params.timeframe, end=params.end)
        except InsufficientDataError as exc:
            skipped += 1
            logger.warning("indicator_skipped", extra={"symbol": symbol, "detail": exc.message})
            continue
        except Exception as exc:  # noqa: BLE001 - một mã lỗi không dừng cả job
            failed += 1
            logger.warning(
                "job_recompute_symbol_failed",
                extra={"symbol": symbol, "error_type": type(exc).__name__},
            )
            continue
        recomputed += 1
        logger.info(
            "indicator_computed",
            extra={
                "symbol": symbol,
                "timeframe": params.timeframe,
                "indicators": len(result.results),
                "candles": result.candles_used,
            },
        )

    logger.info(
        "job_recompute_done",
        extra={
            "symbols": len(symbols),
            "recomputed": recomputed,
            "skipped": skipped,
            "failed": failed,
            "timeframe": params.timeframe,
        },
    )
    return JobResult(
        summary={
            "requested": len(symbols),
            "recomputed": recomputed,
            "skipped": skipped,
            "failed": failed,
            "timeframe": params.timeframe,
        },
        complete=failed == 0,
    )


async def scan_confirmed_divergences(
    settings: Settings, params: DivergenceScanParams
) -> list[DivergenceScanRow]:
    """Quét phân kỳ đã xác nhận, chỉ dùng nến đã đóng.

    Args:
        settings: Cấu hình ứng dụng.
        params: Tham số quét.

    Returns:
        Danh sách `DivergenceScanRow` sắp xếp theo thời điểm xác nhận.
    """
    from app.indicators.native import configure_unstable_periods

    configure_unstable_periods(settings.indicator_unstable_periods)
    async with session_scope() as session:
        service = IndicatorService(
            settings=settings,
            market_repository=MarketRepository(session),
            indicator_repository=IndicatorRepository(session),
        )
        return await service.scan_divergences(
            symbols=params.symbols or None,
            timeframe=params.timeframe,
            days=params.days,
            limit=params.limit,
        )


@register_job(
    name="recompute-indicators",
    params_model=RecomputeParams,
    description="Tính lại chỉ báo confirmed cho toàn bộ mã hoặc danh sách chỉ định.",
    schedule=DailySchedule(15, 20),
    timeout_seconds=1800,
    retry=RetryPolicy(attempts=2, base_delay_seconds=2, max_delay_seconds=30, jitter_seconds=0.5),
    idempotency="day",
)
async def recompute_indicators(ctx: JobContext[RecomputeParams]) -> JobResult:
    """Handler job recompute-indicators.

    Args:
        ctx: Ngữ cảnh job.

    Returns:
        `JobResult` tổng hợp.
    """
    return await recompute_symbols(ctx.settings, ctx.params)


@register_job(
    name="divergence-scan",
    params_model=DivergenceScanParams,
    description="Quét phân kỳ đã xác nhận của các phiên gần nhất.",
    schedule=DailySchedule(15, 30),
    timeout_seconds=900,
    retry=RetryPolicy(attempts=2, base_delay_seconds=2, max_delay_seconds=30),
    idempotency="day",
)
async def divergence_scan(ctx: JobContext[DivergenceScanParams]) -> JobResult:
    """Handler job divergence-scan.

    Args:
        ctx: Ngữ cảnh job.

    Returns:
        `JobResult` với số tín hiệu tìm được.
    """
    rows = await scan_confirmed_divergences(ctx.settings, ctx.params)
    logger.info(
        "job_divergence_scan_done",
        extra={
            "signals": len(rows),
            "symbols": sorted({row.symbol for row in rows})[:50],
        },
    )
    return JobResult(
        summary={
            "signals": len(rows),
            "symbols_with_signals": len({row.symbol for row in rows}),
        }
    )
