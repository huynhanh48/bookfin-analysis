"""Nghiệp vụ dữ liệu thị trường: danh mục mã, nến đã đóng và snapshot giá.

Snapshot giá đọc theo thứ tự: cache Redis, gateway, rồi nến đã đóng trong
PostgreSQL. Khi gateway lỗi, service trả bản ghi cũ kèm `as_of`/`is_stale` và
`note` thay vì giả vờ thành công.
"""

import json
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.exceptions import NotFoundError, UpstreamError
from app.core.logging import get_logger
from app.fcdata.client import FcDataClient
from app.fcdata.mapper import format_vn_date, map_quote_row
from app.models.candle import Candle
from app.models.symbol import Symbol
from app.repositories.market_repository import MarketRepository
from app.schemas.market import QuoteOut

logger = get_logger(__name__)

_QUOTE_CACHE_PREFIX = "quote:"
_QUOTE_WINDOW_DAYS = 7


class MarketService:
    """Nghiệp vụ đọc dữ liệu thị trường đã lưu và snapshot giá."""

    def __init__(
        self,
        *,
        settings: Settings,
        repository: MarketRepository,
        redis: Redis | None,
        fcdata: FcDataClient,
    ) -> None:
        """Khởi tạo service.

        Args:
            settings: Cấu hình ứng dụng.
            repository: Repository thị trường.
            redis: Redis client hoặc None khi cache tắt.
            fcdata: Client tới BookFin FCData Gateway.
        """
        self._settings = settings
        self._repository = repository
        self._redis = redis
        self._fcdata = fcdata

    async def list_symbols(
        self,
        *,
        limit: int = 200,
        offset: int = 0,
        active_only: bool = True,
    ) -> tuple[list[Symbol], int]:
        """Đọc danh mục mã chứng khoán có phân trang.

        Args:
            limit: Số bản ghi tối đa.
            offset: Vị trí bắt đầu.
            active_only: Chỉ lấy mã còn giao dịch.

        Returns:
            Tuple (danh sách mã, tổng số mã).
        """
        return await self._repository.list_symbols(
            active_only=active_only, limit=limit, offset=offset
        )

    async def get_candles(
        self,
        symbol: str,
        timeframe: str = "1D",
        *,
        limit: int = 250,
    ) -> list[Candle]:
        """Đọc nến đã đóng của một mã.

        Args:
            symbol: Mã chứng khoán.
            timeframe: Khung thời gian.
            limit: Số nến tối đa.

        Returns:
            Danh sách nến tăng dần theo thời gian.

        Raises:
            NotFoundError: Khi mã chưa có trong danh mục hoặc chưa có nến.
        """
        symbol_row = await self._repository.get_symbol(symbol)
        if symbol_row is None:
            raise NotFoundError(f"Chưa có mã {symbol.upper()} trong danh mục.")
        candles = await self._repository.get_candles(symbol, timeframe, limit=limit)
        if not candles:
            raise NotFoundError(f"Mã {symbol.upper()} chưa có nến {timeframe}.")
        return candles

    async def get_quote(self, symbol: str) -> QuoteOut:
        """Lấy snapshot giá mới nhất, có ghi rõ độ mới của dữ liệu.

        Args:
            symbol: Mã chứng khoán.

        Returns:
            `QuoteOut` với `source`, `as_of` và `is_stale`. Khi không lấy được dữ
            liệu từ bất kỳ nguồn nào, trả `source="unavailable"` kèm ghi chú.
        """
        ticker = symbol.upper()
        cached = await self._read_cached_quote(ticker)
        if cached is not None:
            return cached

        try:
            rows = await self._fetch_live_quote_rows(ticker)
        except UpstreamError as exc:
            logger.warning("quote_upstream_failed", extra={"symbol": ticker, "code": exc.code})
            fallback = await self._quote_from_database(ticker, note=f"gateway lỗi: {exc.message}")
            if fallback is not None:
                return fallback
            return QuoteOut(
                symbol=ticker,
                source="unavailable",
                is_stale=True,
                note="Chưa có nến đã đóng và gateway đang lỗi.",
            )

        if not rows:
            fallback = await self._quote_from_database(ticker, note="Gateway không trả dữ liệu.")
            if fallback is not None:
                return fallback
            return QuoteOut(
                symbol=ticker,
                source="unavailable",
                is_stale=True,
                note="Gateway không trả dữ liệu giá cho mã này.",
            )

        record = map_quote_row(rows[0], symbol=ticker)
        fetched_at = datetime.now(tz=UTC)
        await self._write_cached_quote(record, fetched_at)
        return QuoteOut(
            symbol=ticker,
            price=record.price,
            change_percent=record.change_percent,
            volume=record.volume,
            as_of=record.as_of or fetched_at,
            is_stale=False,
            source="gateway",
        )

    async def _fetch_live_quote_rows(self, ticker: str) -> list[dict]:
        """Gọi gateway lấy một row giá mới nhất.

        Gateway bắt buộc có `fromDate`/`toDate`, nếu thiếu sẽ trả rỗng.
        """
        today = datetime.now(tz=self._settings.market_tz).date()
        start = today - timedelta(days=_QUOTE_WINDOW_DAYS)
        return await self._fcdata.fetch_batch_stock_prices(
            [ticker], start=format_vn_date(start), end=format_vn_date(today)
        )

    async def _read_cached_quote(self, ticker: str) -> QuoteOut | None:
        """Đọc snapshot giá từ Redis nếu còn trong TTL."""
        if self._redis is None:
            return None
        raw = await self._redis.get(f"{_QUOTE_CACHE_PREFIX}{ticker}")
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except ValueError:
            logger.warning("quote_cache_corrupted", extra={"symbol": ticker})
            return None

        fetched_at = datetime.fromisoformat(payload["fetched_at"])
        as_of_raw = payload.get("as_of")
        as_of = datetime.fromisoformat(as_of_raw) if as_of_raw else fetched_at
        age_seconds = (datetime.now(tz=UTC) - fetched_at).total_seconds()
        return QuoteOut(
            symbol=ticker,
            price=payload.get("price"),
            change_percent=payload.get("change_percent"),
            volume=payload.get("volume"),
            as_of=as_of,
            is_stale=age_seconds > self._settings.quote_stale_after_seconds,
            source="cache",
        )

    async def _write_cached_quote(self, record, fetched_at: datetime) -> None:
        """Ghi snapshot giá vào Redis với TTL cấu hình."""
        if self._redis is None:
            return
        payload = {
            "price": record.price,
            "change_percent": record.change_percent,
            "volume": record.volume,
            "as_of": record.as_of.isoformat() if record.as_of else None,
            "fetched_at": fetched_at.isoformat(),
        }
        await self._redis.set(
            f"{_QUOTE_CACHE_PREFIX}{record.symbol}",
            json.dumps(payload),
            ex=self._settings.redis_quote_ttl_seconds,
        )

    async def _quote_from_database(self, ticker: str, *, note: str) -> QuoteOut | None:
        """Dựng snapshot giá từ nến đã đóng cuối cùng."""
        candles = await self._repository.get_candles(ticker, "1D", limit=1)
        if not candles:
            return None
        candle = candles[-1]
        return QuoteOut(
            symbol=ticker,
            price=float(candle.close),
            change_percent=None,
            volume=int(candle.volume),
            as_of=candle.candle_open_time,
            is_stale=True,
            source="database",
            note=note,
        )
