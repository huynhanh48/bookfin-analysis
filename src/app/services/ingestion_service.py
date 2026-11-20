"""Nghiệp vụ nạp dữ liệu: đồng bộ danh mục và backfill nến lịch sử.

Backfill phân trang qua proxy `/DailyOhlc`, chỉ upsert nến đã đóng và ghi lại một
bản ghi `ingestion_runs` để audit. Khi upstream lỗi, dữ liệu đã có trong
PostgreSQL không bị xoá hay ghi đè.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.core.config import Settings
from app.core.exceptions import InvalidRequestError, UpstreamError
from app.core.logging import get_logger
from app.fcdata.client import FcDataClient
from app.fcdata.mapper import format_vn_date, map_catalog_payload, map_daily_ohlc_payload
from app.repositories.market_repository import SOURCE, MarketRepository
from app.schemas.market import BackfillResult
from app.services.weekly_candles import DAILY_TIMEFRAME, WEEKLY_TIMEFRAME

logger = get_logger(__name__)

_PAGE_SIZE = 1000
_MAX_PAGES = 20


@dataclass(frozen=True, slots=True)
class BackfillStats:
    """Kết quả backfill của một mã.

    Attributes:
        symbol: Mã chứng khoán.
        inserted: Số nến đã ghi hoặc cập nhật.
        rejected: Số bản ghi bị loại vì không đạt kiểm tra chất lượng.
        pages: Số trang đã đọc từ gateway.
    """

    symbol: str
    inserted: int
    rejected: int
    pages: int


class IngestionService:
    """Nghiệp vụ nạp dữ liệu từ BookFin FCData Gateway."""

    def __init__(
        self,
        *,
        settings: Settings,
        repository: MarketRepository,
        fcdata: FcDataClient,
    ) -> None:
        """Khởi tạo service.

        Args:
            settings: Cấu hình ứng dụng.
            repository: Repository thị trường.
            fcdata: Client tới gateway.
        """
        self._settings = settings
        self._repository = repository
        self._fcdata = fcdata

    async def sync_symbols(self) -> int:
        """Đồng bộ danh mục mã chứng khoán từ catalog của gateway.

        Returns:
            Số mã đã ghi hoặc cập nhật.
        """
        payload = await self._fcdata.fetch_catalog()
        records = map_catalog_payload(payload)
        count = await self._repository.upsert_symbols(records)
        logger.info("symbols_synced", extra={"count": count})
        return count

    async def backfill_symbol(
        self,
        symbol: str,
        *,
        timeframe: str = "1D",
        start: date | None = None,
        end: date | None = None,
    ) -> BackfillStats:
        """Nạp lịch sử nến của một mã theo từng trang.

        Args:
            symbol: Mã chứng khoán.
            timeframe: Khung thời gian.
            start: Ngày bắt đầu; mặc định lùi theo `backfill_lookback_days`.
            end: Ngày kết thúc; mặc định hôm nay theo múi giờ thị trường.

        Returns:
            `BackfillStats` với số nến ghi được và số bản ghi bị loại.

        Raises:
            InvalidRequestError: Khi yêu cầu backfill khung khác `1D`.
        """
        if timeframe != DAILY_TIMEFRAME:
            raise InvalidRequestError(
                f"Backfill chỉ hỗ trợ {DAILY_TIMEFRAME}; khung {WEEKLY_TIMEFRAME} "
                "được dựng từ nến ngày khi đọc."
            )
        ticker = symbol.upper()
        end_date = end or datetime.now(tz=self._settings.market_tz).date()
        start_date = start or end_date - timedelta(days=self._settings.backfill_lookback_days)
        if start_date > end_date:
            start_date, end_date = end_date, start_date

        await self._repository.ensure_symbol(ticker)

        inserted = 0
        rejected = 0
        pages = 0

        for page_index in range(1, _MAX_PAGES + 1):
            payload = await self._fcdata.fetch_daily_ohlc(
                ticker,
                start=format_vn_date(start_date),
                end=format_vn_date(end_date),
                page_index=page_index,
                page_size=_PAGE_SIZE,
                ascending=True,
            )
            rows = payload.get("data") or []
            if not rows:
                break

            batch = map_daily_ohlc_payload(payload, symbol=ticker, timeframe=timeframe)
            inserted += await self._repository.upsert_candles(batch.candles)
            rejected += len(batch.rejected)
            pages += 1

            if len(rows) < _PAGE_SIZE:
                break

        logger.info(
            "backfill_symbol_done",
            extra={"symbol": ticker, "inserted": inserted, "rejected": rejected, "pages": pages},
        )
        return BackfillStats(symbol=ticker, inserted=inserted, rejected=rejected, pages=pages)

    async def backfill(
        self,
        symbols: list[str],
        *,
        timeframe: str = "1D",
        start: date | None = None,
        end: date | None = None,
    ) -> BackfillResult:
        """Nạp lịch sử cho nhiều mã và ghi audit một lần chạy.

        Args:
            symbols: Danh sách mã cần nạp.
            timeframe: Khung thời gian.
            start: Ngày bắt đầu.
            end: Ngày kết thúc.

        Returns:
            `BackfillResult` tổng hợp cho cả lô.
        """
        run = await self._repository.create_ingestion_run(SOURCE, "backfill")
        requested = len(symbols)
        succeeded = 0
        failed = 0
        inserted = 0
        rejected = 0
        errors: dict[str, str] = {}

        for symbol in symbols:
            ticker = symbol.upper()
            try:
                stats = await self.backfill_symbol(
                    ticker, timeframe=timeframe, start=start, end=end
                )
            except UpstreamError as exc:
                failed += 1
                errors[ticker] = exc.message
                logger.warning("backfill_symbol_failed", extra={"symbol": ticker, "code": exc.code})
                continue

            succeeded += 1
            inserted += stats.inserted
            rejected += stats.rejected

        status = "success" if failed == 0 else ("partial" if succeeded else "failed")
        counts = {
            "requested": requested,
            "succeeded": succeeded,
            "failed": failed,
            "inserted": inserted,
            "rejected": rejected,
        }
        await self._repository.finish_ingestion_run(
            run.id,
            status=status,
            counts=counts,
            error_summary="; ".join(f"{key}: {value}" for key, value in errors.items()) or None,
        )

        return BackfillResult(
            requested=requested,
            succeeded=succeeded,
            failed=failed,
            inserted=inserted,
            rejected=rejected,
            errors=errors,
        )
