"""HTTP endpoints cho Stock Screener."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query

from app.core.dependencies import ScreenerServiceDep
from app.schemas.screener import (
    ScreenerConditions,
    ScreenerResultsResponse,
    ScreenerScanRequest,
    ScreenerScanResponse,
)

router = APIRouter(prefix="/screener", tags=["screener"])


@router.get(
    "/filters/default",
    response_model=ScreenerConditions,
    summary="Bộ lọc dòng tiền mặc định",
)
async def default_filters() -> ScreenerConditions:
    """Trả cấu hình AFL mặc định, tất cả điều kiện đang bật."""
    return ScreenerConditions()


@router.post(
    "/scan",
    response_model=ScreenerScanResponse,
    summary="Scan cổ phiếu theo bộ lọc dòng tiền",
)
async def scan(payload: ScreenerScanRequest, service: ScreenerServiceDep) -> ScreenerScanResponse:
    """Chạy historical hoặc realtime scan."""
    return await service.scan(payload)


@router.get(
    "/results",
    response_model=ScreenerResultsResponse,
    summary="Snapshot realtime gần nhất",
)
async def results(
    service: ScreenerServiceDep,
    profile_id: str | None = Query(default=None, max_length=32),
) -> ScreenerResultsResponse:
    """Đọc snapshot realtime đã lưu; trả rỗng khi chưa có phiên realtime."""
    resolved = profile_id or await service.active_profile_id() or service.default_profile_id()
    cached = await service.results(resolved)
    if cached is not None:
        return cached
    now = datetime.now(tz=UTC)
    return ScreenerResultsResponse(
        profile_id=resolved,
        mode="realtime",
        as_of=now,
        generated_at=now,
        feed_status="OFFLINE",
        total_universe=0,
        evaluated=0,
        matched=0,
        skipped=0,
        results=[],
    )
