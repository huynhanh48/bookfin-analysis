"""Worker job cho Stock Screener confirmed sau phiên đóng."""

from __future__ import annotations

from app.core.database import session_scope
from app.core.logging import get_logger
from app.fcdata.client import FcDataClient
from app.repositories.market_repository import MarketRepository
from app.schemas.screener import ScreenerScanRequest
from app.services.screener_service import ScreenerService
from app.workers.registry import JobContext, JobResult, RetryPolicy, register_job
from app.workers.schedule import DailySchedule

logger = get_logger(__name__)


@register_job(
    name="screener-scan",
    params_model=ScreenerScanRequest,
    description="Quét chiến lược lọc dòng tiền AFL trên nến đã đóng.",
    schedule=DailySchedule(15, 35),
    timeout_seconds=900,
    retry=RetryPolicy(attempts=2, base_delay_seconds=3, max_delay_seconds=60),
    idempotency="day",
)
async def screener_scan(ctx: JobContext[ScreenerScanRequest]) -> JobResult:
    """Chạy historical scan mặc định sau khi nến đã được ingest.

    Args:
        ctx: Ngữ cảnh job.

    Returns:
        `JobResult` với số mã đánh giá/khớp.
    """
    settings = ctx.settings
    request = ctx.params
    if request.mode == "realtime":
        # Job chạy theo lịch nên luôn dùng dữ liệu confirmed, tránh phát tín hiệu
        # dựa trên quote cũ sau giờ đóng.
        request = request.model_copy(update={"mode": "historical"})
    async with FcDataClient(settings) as client, session_scope() as session:
        service = ScreenerService(
            settings=settings,
            market_repository=MarketRepository(session),
            fcdata=client,
        )
        response = await service.scan(request)
    logger.info(
        "job_screener_scan_done",
        extra={
            "evaluated": response.evaluated,
            "matched": response.matched,
            "skipped": response.skipped,
        },
    )
    return JobResult(
        summary={
            "evaluated": response.evaluated,
            "matched": response.matched,
            "skipped": response.skipped,
            "profile_id": response.profile_id,
        }
    )
