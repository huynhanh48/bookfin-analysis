"""Truy vấn symbol và nến OHLCV đã đóng.

Repository không chứa business logic và không gọi mạng. Mọi ghi nến đều dùng
upsert idempotent theo khoá (symbol, timeframe, candle_open_time, price_basis).
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.fcdata.mapper import CandleRecord, SymbolRecord
from app.models.candle import Candle
from app.models.ingestion import IngestionRun
from app.models.symbol import Symbol

SOURCE = "bookfin-fcdata"


class MarketRepository:
    """Repository cho danh mục mã và nến đã đóng."""

    def __init__(self, session: AsyncSession) -> None:
        """Khởi tạo repository.

        Args:
            session: Session database của request hoặc worker.
        """
        self._session = session

    async def upsert_symbols(self, records: Sequence[SymbolRecord]) -> int:
        """Ghi hoặc cập nhật danh mục mã chứng khoán.

        Args:
            records: Danh sách mã đã chuẩn hoá.

        Returns:
            Số bản ghi đã gửi lên database.
        """
        if not records:
            return 0

        # Catalog có thể chứa cùng một mã ở nhiều dòng; giữ bản ghi cuối cùng để
        # câu INSERT ... ON CONFLICT không cập nhật hai lần trên một dòng.
        by_symbol: dict[str, SymbolRecord] = {record.symbol: record for record in records}
        rows = [
            {
                "symbol": record.symbol,
                "exchange": record.exchange,
                "company_name": record.company_name,
                "sector": record.sector,
                "active": record.active,
            }
            for record in by_symbol.values()
        ]
        statement = insert(Symbol).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[Symbol.symbol],
            set_={
                "exchange": statement.excluded.exchange,
                "company_name": statement.excluded.company_name,
                "sector": statement.excluded.sector,
                "active": statement.excluded.active,
                "updated_at": func.now(),
            },
        )
        await self._session.execute(statement)
        await self._session.commit()
        return len(rows)

    async def ensure_symbol(self, symbol: str) -> None:
        """Bảo đảm mã tồn tại để khoá ngoại của bảng nến không lỗi.

        Args:
            symbol: Mã chứng khoán.
        """
        statement = insert(Symbol).values(symbol=symbol.upper(), active=True)
        statement = statement.on_conflict_do_nothing(index_elements=[Symbol.symbol])
        await self._session.execute(statement)
        await self._session.commit()

    async def list_symbols(
        self,
        *,
        active_only: bool = True,
        limit: int = 200,
        offset: int = 0,
        query: str | None = None,
    ) -> tuple[list[Symbol], int]:
        """Đọc danh mục mã có phân trang và tìm kiếm.

        Args:
            active_only: Chỉ lấy mã còn giao dịch.
            limit: Số bản ghi tối đa.
            offset: Vị trí bắt đầu.
            query: Chuỗi tìm kiếm; khớp mã hoặc tên công ty, không phân biệt hoa thường.

        Returns:
            Tuple (danh sách mã, tổng số mã khớp điều kiện).
        """
        filters = [Symbol.active.is_(True)] if active_only else []
        if query:
            pattern = f"%{query.strip().upper()}%"
            filters.append(
                or_(
                    func.upper(Symbol.symbol).like(pattern),
                    func.upper(Symbol.company_name).like(pattern),
                )
            )
        count_statement = select(func.count()).select_from(Symbol).where(*filters)
        total = int((await self._session.execute(count_statement)).scalar_one())

        statement = (
            select(Symbol).where(*filters).order_by(Symbol.symbol).limit(limit).offset(offset)
        )
        rows = list((await self._session.execute(statement)).scalars())
        return rows, total

    async def get_symbol(self, symbol: str) -> Symbol | None:
        """Đọc một mã theo ticker.

        Args:
            symbol: Mã chứng khoán.

        Returns:
            `Symbol` hoặc None khi chưa đồng bộ.
        """
        return await self._session.get(Symbol, symbol.upper())

    async def upsert_candles(self, records: Sequence[CandleRecord]) -> int:
        """Ghi hoặc cập nhật nến đã đóng.

        Nến đã có sẽ được cập nhật giá và khối lượng, không tạo bản ghi trùng.

        Args:
            records: Danh sách nến đã chuẩn hoá.

        Returns:
            Số bản ghi đã gửi lên database.
        """
        if not records:
            return 0

        rows = [
            {
                "symbol": record.symbol,
                "timeframe": record.timeframe,
                "candle_open_time": record.open_time,
                "open": Decimal(str(record.open)),
                "high": Decimal(str(record.high)),
                "low": Decimal(str(record.low)),
                "close": Decimal(str(record.close)),
                "volume": record.volume,
                "turnover": None if record.turnover is None else Decimal(str(record.turnover)),
                "price_basis": record.price_basis,
                "source": record.source,
                "source_updated_at": record.source_updated_at,
            }
            for record in records
        ]
        statement = insert(Candle).values(rows)
        statement = statement.on_conflict_do_update(
            constraint="uq_candles_symbol_timeframe_candle_open_time_price_basis",
            set_={
                "open": statement.excluded.open,
                "high": statement.excluded.high,
                "low": statement.excluded.low,
                "close": statement.excluded.close,
                "volume": statement.excluded.volume,
                "turnover": statement.excluded.turnover,
                "source": statement.excluded.source,
                "source_updated_at": statement.excluded.source_updated_at,
            },
        )
        await self._session.execute(statement)
        await self._session.commit()
        return len(rows)

    async def get_candles(
        self,
        symbol: str,
        timeframe: str = "1D",
        *,
        limit: int = 250,
        end: datetime | None = None,
        price_basis: str | None = None,
    ) -> list[Candle]:
        """Đọc nến đã đóng, sắp xếp tăng dần theo thời gian.

        Args:
            symbol: Mã chứng khoán.
            timeframe: Khung thời gian.
            limit: Số nến tối đa.
            end: Chỉ lấy nến mở trước mốc thời gian này.

        Returns:
            Danh sách nến tăng dần theo `candle_open_time`.
        """
        filters = [Candle.symbol == symbol.upper(), Candle.timeframe == timeframe]
        if price_basis is not None:
            filters.append(Candle.price_basis == price_basis)
        if end is not None:
            filters.append(Candle.candle_open_time < end)

        statement = (
            select(Candle).where(*filters).order_by(Candle.candle_open_time.desc()).limit(limit)
        )
        rows = list((await self._session.execute(statement)).scalars())
        rows.reverse()
        return rows

    async def latest_candle_time(self, symbol: str, timeframe: str = "1D") -> datetime | None:
        """Thời điểm nến mới nhất đã lưu.

        Args:
            symbol: Mã chứng khoán.
            timeframe: Khung thời gian.

        Returns:
            Thời điểm mở nến mới nhất, hoặc None khi chưa có dữ liệu.
        """
        statement = select(func.max(Candle.candle_open_time)).where(
            Candle.symbol == symbol.upper(), Candle.timeframe == timeframe
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def count_candles(self, symbol: str, timeframe: str = "1D") -> int:
        """Đếm số nến đã lưu của một mã.

        Args:
            symbol: Mã chứng khoán.
            timeframe: Khung thời gian.

        Returns:
            Số nến đã có trong database.
        """
        statement = (
            select(func.count())
            .select_from(Candle)
            .where(Candle.symbol == symbol.upper(), Candle.timeframe == timeframe)
        )
        return int((await self._session.execute(statement)).scalar_one())

    async def create_ingestion_run(self, source: str, job_type: str) -> IngestionRun:
        """Tạo bản ghi audit cho một lần chạy nạp dữ liệu.

        Args:
            source: Nguồn dữ liệu, ví dụ bookfin-fcdata.
            job_type: Loại job, ví dụ backfill.

        Returns:
            Bản ghi `IngestionRun` ở trạng thái running.
        """
        run = IngestionRun(source=source, job_type=job_type, status="running", counts={})
        self._session.add(run)
        await self._session.commit()
        await self._session.refresh(run)
        return run

    async def finish_ingestion_run(
        self,
        run_id: int,
        *,
        status: str,
        counts: Mapping[str, Any],
        error_summary: str | None = None,
    ) -> None:
        """Cập nhật kết quả cuối của một lần chạy nạp dữ liệu.

        Args:
            run_id: Id bản ghi `ingestion_runs`.
            status: success, partial hoặc failed.
            counts: Số liệu tổng hợp.
            error_summary: Mô tả lỗi ngắn nếu có.
        """
        statement = (
            update(IngestionRun)
            .where(IngestionRun.id == run_id)
            .values(
                status=status,
                counts=dict(counts),
                error_summary=error_summary,
                ended_at=datetime.now(tz=UTC),
            )
        )
        await self._session.execute(statement)
        await self._session.commit()
