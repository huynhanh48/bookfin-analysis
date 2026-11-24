"""Seed the market history needed by the chart and screener.

Production starts with an empty PostgreSQL volume. Without history the chart
returns `insufficient_data`, and weekly Ichimoku needs roughly 1200 daily
candles. This one-shot worker:

1. syncs the symbol catalog,
2. resolves either an explicit symbol list or the full active universe,
3. backfills missing history with bounded concurrency,
4. refreshes recent sessions when a symbol already has enough candles.

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

_DEFAULT_LOOKBACK_DAYS = 3650
_DEFAULT_MAX_SYMBOLS = 1000
_DEFAULT_CONCURRENCY = 8
_MIN_HISTORY_CANDLES = 400
_FRESH_WITHIN_DAYS = 3
_HISTORY_TOLERANCE_DAYS = 7


def _env_int(name: str, default: int) -> int:
    """Read an integer environment variable with a safe fallback."""
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _parse_args() -> argparse.Namespace:
    """Read bootstrap symbols and limits from env or command line."""
    parser = argparse.ArgumentParser(description="Seed initial market history.")
    parser.add_argument(
        "--symbols",
        default=(
            os.getenv("BOOTSTRAP_SYMBOLS")
            or os.getenv("REALTIME_WORKER_SYMBOLS")
            or "all"
        ),
        help="Comma-separated symbols, or 'all' for every active symbol.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=_env_int("BOOTSTRAP_MAX_SYMBOLS", _DEFAULT_MAX_SYMBOLS),
        help="Maximum symbols used when --symbols=all.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=_env_int("BOOTSTRAP_LOOKBACK_DAYS", _DEFAULT_LOOKBACK_DAYS),
        help="Initial history window in calendar days.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=_env_int("BOOTSTRAP_CONCURRENCY", _DEFAULT_CONCURRENCY),
        help="Maximum symbols backfilled concurrently.",
    )
    parser.add_argument(
        "--sync-symbols",
        action="store_true",
        help="Sync the FCData symbol catalog before backfilling.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Extend existing symbols backwards to the requested --days window.",
    )
    return parser.parse_args()


def _symbols(value: str) -> list[str]:
    """Normalize a comma-separated symbol list."""
    return sorted({item.strip().upper() for item in value.split(",") if item.strip()})


def _clamp(value: int, low: int, high: int) -> int:
    """Clamp a CLI value into a safe range."""
    return max(low, min(high, value))


async def _target_symbols(
    *,
    settings,
    client: FcDataClient,
    value: str,
    limit: int,
    sync_symbols: bool,
) -> list[str]:
    """Resolve explicit symbols or the full active universe."""
    async with session_scope() as session:
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

        if value.strip().lower() == "all":
            rows, total = await repository.list_symbols(
                active_only=True,
                limit=_clamp(limit, 1, 5000),
            )
            symbols = sorted({row.symbol for row in rows})
            # Seed the realtime watchlist first so the default chart becomes
            # usable while the rest of the universe is still downloading.
            priority = _symbols(os.getenv("REALTIME_WORKER_SYMBOLS", ""))
            if priority:
                selected = set(symbols)
                first = [symbol for symbol in priority if symbol in selected]
                rest = [symbol for symbol in symbols if symbol not in set(priority)]
                symbols = first + rest
            logger.info(
                "bootstrap_universe",
                extra={"selected": len(symbols), "total_active": total},
            )
            return symbols

        return _symbols(value)


async def _seed_symbol(
    *,
    settings,
    client: FcDataClient,
    symbol: str,
    end,
    days: int,
    full: bool,
    semaphore: asyncio.Semaphore,
) -> None:
    """Backfill one symbol, using a dedicated DB session per concurrent task."""
    async with semaphore:
        try:
            async with session_scope() as session:
                repository = MarketRepository(session)
                target_start = end - timedelta(days=days)
                first, last, total = await repository.get_candle_bounds(symbol, "1D")
                # A few recent candles are not enough for Ichimoku/MACD warmup;
                # treat a short series as an incomplete initial seed.
                if total == 0 or total < _MIN_HISTORY_CANDLES:
                    start = target_start
                    mode = "initial"
                elif (
                    full
                    and first is not None
                    and first.date() > target_start + timedelta(days=_HISTORY_TOLERANCE_DAYS)
                ):
                    start = target_start
                    mode = "deepen"
                else:
                    if last is not None and last.date() >= end - timedelta(
                        days=_FRESH_WITHIN_DAYS
                    ):
                        logger.info("bootstrap_symbol_fresh", extra={"symbol": symbol})
                        return
                    latest = last.date() if last is not None else end
                    start = latest - timedelta(days=settings.ingest_incremental_days)
                    start = min(start, end - timedelta(days=1))
                    mode = "incremental"

                service = IngestionService(
                    settings=settings,
                    repository=repository,
                    fcdata=client,
                )
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


async def run(
    symbols_value: str,
    days: int,
    limit: int,
    concurrency: int,
    sync: bool,
    full: bool,
) -> None:
    """Backfill missing history for explicit symbols or the active universe."""
    settings = get_settings()
    configure_logging(settings.log_level)
    end = datetime.now(tz=settings.market_tz).date()
    days = _clamp(days, 30, 3650)
    concurrency = _clamp(concurrency, 1, 32)

    async with FcDataClient(settings) as client:
        symbols = await _target_symbols(
            settings=settings,
            client=client,
            value=symbols_value,
            limit=limit,
            sync_symbols=sync,
        )
        if not symbols:
            logger.warning("bootstrap_no_symbols")
            return

        logger.info(
            "bootstrap_started",
            extra={"symbols": len(symbols), "days": days, "concurrency": concurrency},
        )
        semaphore = asyncio.Semaphore(concurrency)
        await asyncio.gather(
            *(
                _seed_symbol(
                    settings=settings,
                    client=client,
                    symbol=symbol,
                    end=end,
                    days=days,
                    full=full,
                    semaphore=semaphore,
                )
                for symbol in symbols
            )
        )
        logger.info("bootstrap_finished", extra={"symbols": len(symbols)})


async def main() -> None:
    """Entrypoint for the production bootstrap service."""
    args = _parse_args()
    try:
        # A bounded first run keeps a broken upstream from blocking API startup.
        async with asyncio.timeout(3600):
            await run(
                symbols_value=args.symbols,
                days=args.days,
                limit=args.limit,
                concurrency=args.concurrency,
                sync=args.sync_symbols,
                full=args.full,
            )
    except Exception as exc:  # noqa: BLE001 - bootstrap must not block API startup
        logger.warning(
            "bootstrap_failed",
            extra={"error_type": type(exc).__name__},
        )
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
