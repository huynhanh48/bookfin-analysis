"""Worker backfill lịch sử nến.

Chạy độc lập với API::

    uv run python -m app.workers.backfill --symbols FPT,HPG --days 400
    uv run python -m app.workers.backfill --symbols FPT --days 1200
    uv run python -m app.workers.backfill --sync-symbols

Khung 1W không nạp riêng: nến tuần được dựng từ nến ngày đã đóng khi đọc.
Dùng `--days` lớn (khoảng 1200 ngày) khi cần đủ lịch sử cho Ichimoku tuần.
"""

import argparse
import asyncio
from datetime import datetime, timedelta

from app.core.config import get_settings
from app.core.database import dispose_engine, session_scope
from app.core.logging import configure_logging, get_logger
from app.fcdata.client import FcDataClient
from app.repositories.market_repository import MarketRepository
from app.services.ingestion_service import IngestionService

logger = get_logger(__name__)


def _parse_args() -> argparse.Namespace:
    """Đọc tham số dòng lệnh của worker."""
    parser = argparse.ArgumentParser(description="Backfill nến lịch sử từ FCData Gateway.")
    parser.add_argument("--symbols", default="", help="Danh sách mã, phân tách bằng dấu phẩy.")
    parser.add_argument(
        "--timeframe",
        default="1D",
        choices=["1D"],
        help="Khung thời gian nạp. Chỉ 1D: khung 1W được dựng từ nến ngày khi đọc.",
    )
    parser.add_argument("--days", type=int, default=None, help="Số ngày lịch cần nạp.")
    parser.add_argument(
        "--sync-symbols",
        action="store_true",
        help="Đồng bộ danh mục mã trước khi backfill.",
    )
    return parser.parse_args()


async def run(symbols: list[str], timeframe: str, days: int | None, sync_symbols: bool) -> None:
    """Chạy backfill cho danh sách mã.

    Args:
        symbols: Danh sách mã cần nạp.
        timeframe: Khung thời gian.
        days: Số ngày lịch cần nạp; None dùng giá trị mặc định trong cấu hình.
        sync_symbols: Đồng bộ danh mục mã trước khi nạp nến.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    end = datetime.now(tz=settings.market_tz).date()
    start = end - timedelta(days=days) if days else None

    async with FcDataClient(settings) as client, session_scope() as session:
        service = IngestionService(
            settings=settings, repository=MarketRepository(session), fcdata=client
        )
        if sync_symbols:
            count = await service.sync_symbols()
            logger.info("symbols_synced", extra={"count": count})

        if not symbols:
            logger.info("backfill_no_symbols")
            return

        result = await service.backfill(symbols, timeframe=timeframe, start=start, end=end)
        logger.info(
            "backfill_finished",
            extra={
                "requested": result.requested,
                "succeeded": result.succeeded,
                "failed": result.failed,
                "inserted": result.inserted,
                "rejected": result.rejected,
            },
        )


async def main() -> None:
    """Entrypoint của worker."""
    args = _parse_args()
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    try:
        await run(symbols, args.timeframe, args.days, args.sync_symbols)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
