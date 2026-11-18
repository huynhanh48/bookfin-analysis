"""Worker precompute chỉ báo và sự kiện cấu trúc thị trường.

Chạy độc lập với API::

    uv run python -m app.workers.indicator_worker --symbols FPT,HPG
    uv run python -m app.workers.indicator_worker --all --limit 50
"""

import argparse
import asyncio

from app.core.config import get_settings
from app.core.database import dispose_engine, session_scope
from app.core.exceptions import InsufficientDataError
from app.core.logging import configure_logging, get_logger
from app.repositories.indicator_repository import IndicatorRepository
from app.repositories.market_repository import MarketRepository
from app.services.indicator_service import IndicatorService

logger = get_logger(__name__)


def _parse_args() -> argparse.Namespace:
    """Đọc tham số dòng lệnh của worker."""
    parser = argparse.ArgumentParser(description="Precompute chỉ báo và cấu trúc thị trường.")
    parser.add_argument("--symbols", default="", help="Danh sách mã, phân tách bằng dấu phẩy.")
    parser.add_argument("--all", action="store_true", help="Chạy cho toàn bộ mã đang hoạt động.")
    parser.add_argument("--limit", type=int, default=200, help="Số mã tối đa khi dùng --all.")
    parser.add_argument("--timeframe", default="1D", choices=["1D", "1W"], help="Khung thời gian.")
    return parser.parse_args()


async def run(symbols: list[str], timeframe: str, run_all: bool, limit: int) -> None:
    """Tính lại chỉ báo cho danh sách mã.

    Args:
        symbols: Danh sách mã chỉ định.
        timeframe: Khung thời gian.
        run_all: Chạy cho toàn bộ mã đang hoạt động.
        limit: Số mã tối đa khi chạy toàn bộ.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    async with session_scope() as session:
        market_repository = MarketRepository(session)
        indicator_repository = IndicatorRepository(session)
        service = IndicatorService(
            settings=settings,
            market_repository=market_repository,
            indicator_repository=indicator_repository,
        )

        targets = list(symbols)
        if run_all:
            rows, _ = await market_repository.list_symbols(active_only=True, limit=limit, offset=0)
            targets.extend(row.symbol for row in rows)

        seen: set[str] = set()
        for symbol in targets:
            ticker = symbol.upper()
            if ticker in seen:
                continue
            seen.add(ticker)
            try:
                result = await service.recompute(ticker, timeframe)
            except InsufficientDataError as exc:
                logger.warning("indicator_skipped", extra={"symbol": ticker, "detail": exc.message})
                continue
            logger.info(
                "indicator_computed",
                extra={
                    "symbol": ticker,
                    "timeframe": timeframe,
                    "indicators": len(result.results),
                    "candles": result.candles_used,
                },
            )


async def main() -> None:
    """Entrypoint của worker."""
    args = _parse_args()
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    try:
        await run(symbols, args.timeframe, args.all, args.limit)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
