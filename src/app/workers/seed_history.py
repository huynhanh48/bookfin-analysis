"""Seed the first-deploy market history required by the chart.

Production starts with an empty PostgreSQL volume. Without history the chart
returns `insufficient_data`, and weekly Ichimoku needs roughly 1200 daily
candles. This one-shot worker:

1. syncs the symbol catalog,
2. backfills the configured bootstrap symbols when they have no candles,
3. refreshes the last few sessions when data already exists.

The worker is idempotent, so Compose can run it on every restart. Upstream
failures are logged per symbol; the API must still start and serve other data.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime, timedelta

from app.core.config import get_settings
from app.core.database import dispose_engine, session_scope
from app.core.logging import configure_logging, get_logger
from app.fcdata.client import FcDataClient
from app.repositories.market_repository import MarketRepository
from app.services.ingestion_service import IngestionService

logger = get_logger(__name__)

_DEFAULT_SYMBOLS = "FPT,SSI,HPG,VNM,VIC"
_DEFAULT_LOOKBACK_DAYS = 1200


def _parse_args() -> argparse.Namespace:
    """Read bootstrap symbols and lookback from env or command line."""
    parser = argparse.ArgumentParser(description="Seed initial market history.")
    parser.add_argument(
        "--symbols",
        default=(
            os.getenv("BOOTSTRAP_SYMBOLS")
            or os.getenv("REALTIME_WORKER_SYMBOLS")
            or _DEFAULT_SYMBOLS
        ),
        help="Comma-separated symbols. Defaults to BOOTSTRAP_SYMBOLS.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=int(os.getenv("BOOTSTRAP_LOOKBACK_DAYS", str(_DEFAULT_LOOKBACK_DAYS))),
        help="Initial history window in calendar days.",
    )
    parser.add_argument(
        "--sync-symbols",
        action="store_true",
        help="Sync the FCData symbol catalog before backfilling.",
    )
    return parser.parse_args()


def _symbols(value: str) -> list[str]:
    """Normalize a comma-separated symbol list."""
    return [item.strip().upper() for item in value.split(",") if item.strip()]


async def run(symbols: list[str], days: int, sync_symbols: bool) -> None:
    """Backfill missing history and refresh recent candles for each symbol."""
    settings = get_settings()
    configure_logging(settings.log_level)
    end = datetime.now(tz=settings.market_tz).date()

    async with FcDataClient(settings) as client, session_scope() as session:
        repository = MarketRepository(session)
        service = IngestionService(
            settings=settings,
            repository=repository,
            fcdata=client,
        )

        if sync_symbols:
            try:
                count = await service.sync_symbols()
                logger.info("bootstrap_symbols_synced", extra={"count": count})
            except Exception as exc:  # noqa: BLE001 - catalog failure must not block API
                logger.warning(
                    "bootstrap_symbols_sync_failed",
                    extra={"error_type": type(exc).__name__},
                )

        for symbol in symbols:
            try:
                existing = await repository.get_candles(symbol, "1D", limit=1)
                if existing:
                    start = end - timedelta(days=settings.ingest_incremental_days)
                    mode = "incremental"
                else:
                    start = end - timedelta(days=days)
                    mode = "initial"

                stats = await service.backfill_symbol(symbol, start=start, end=end)
                logger.info(
                    "bootstrap_symbol_done",
                    extra={
                        "symbol": stats.symbol,
                        "mode": mode,
                        "inserted": stats.inserted,
                        "rejected": stats.rejected,
                        "pages": stats.pages,
                    },
                )
            except Exception as exc:  # noqa: BLE001 - one symbol must not block the rest
                logger.warning(
                    "bootstrap_symbol_failed",
                    extra={"symbol": symbol, "error_type": type(exc).__name__},
                )


async def main() -> None:
    """Entrypoint for the production bootstrap service."""
    args = _parse_args()
    symbols = _symbols(args.symbols)
    if not symbols:
        logger.warning("bootstrap_no_symbols")
        return

    try:
        # A bounded first run keeps a broken upstream from blocking API startup.
        async with asyncio.timeout(300):
            await run(symbols, args.days, args.sync_symbols)
    except Exception as exc:  # noqa: BLE001 - bootstrap must not block API startup
        logger.warning(
            "bootstrap_failed",
            extra={"error_type": type(exc).__name__},
        )
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
