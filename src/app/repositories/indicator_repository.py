"""Truy vấn indicator snapshot và sự kiện cấu trúc thị trường.

Upsert theo khoá nghiệp vụ để worker chạy lại không tạo bản ghi trùng; sự kiện
cấu trúc dùng `idempotency_key` làm khoá chống lặp.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.indicator import IndicatorSnapshot, StructureEvent


class IndicatorRepository:
    """Repository cho snapshot chỉ báo, sự kiện cấu trúc và audit job."""

    def __init__(self, session: AsyncSession) -> None:
        """Khởi tạo repository.

        Args:
            session: Session database.
        """
        self._session = session

    async def upsert_snapshot(
        self,
        *,
        symbol: str,
        timeframe: str,
        candle_time: datetime,
        indicator: str,
        params_hash: str,
        engine_version: str,
        payload: Mapping[str, Any],
    ) -> None:
        """Ghi hoặc cập nhật một snapshot chỉ báo.

        Args:
            symbol: Mã chứng khoán.
            timeframe: Khung thời gian.
            candle_time: Thời điểm nến được tính.
            indicator: Tên chỉ báo.
            params_hash: Hash tham số.
            engine_version: Phiên bản logic tính.
            payload: Kết quả dạng JSON.
        """
        statement = insert(IndicatorSnapshot).values(
            symbol=symbol.upper(),
            timeframe=timeframe,
            candle_time=candle_time,
            indicator=indicator,
            params_hash=params_hash,
            engine_version=engine_version,
            payload=dict(payload),
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_indicator_snapshots_identity",
            set_={"payload": statement.excluded.payload, "computed_at": datetime.now(tz=UTC)},
        )
        await self._session.execute(statement)
        await self._session.commit()

    async def get_snapshots(
        self,
        symbol: str,
        timeframe: str,
        *,
        engine_version: str,
    ) -> list[IndicatorSnapshot]:
        """Đọc snapshot mới nhất của từng chỉ báo.

        Args:
            symbol: Mã chứng khoán.
            timeframe: Khung thời gian.
            engine_version: Phiên bản engine đang dùng.

        Returns:
            Danh sách snapshot tại nến mới nhất đã lưu.
        """
        latest = (
            select(IndicatorSnapshot)
            .where(
                IndicatorSnapshot.symbol == symbol.upper(),
                IndicatorSnapshot.timeframe == timeframe,
                IndicatorSnapshot.engine_version == engine_version,
            )
            .order_by(IndicatorSnapshot.candle_time.desc())
            .limit(1)
        )
        latest_row = (await self._session.execute(latest)).scalars().first()
        if latest_row is None:
            return []

        statement = select(IndicatorSnapshot).where(
            IndicatorSnapshot.symbol == symbol.upper(),
            IndicatorSnapshot.timeframe == timeframe,
            IndicatorSnapshot.engine_version == engine_version,
            IndicatorSnapshot.candle_time == latest_row.candle_time,
        )
        return list((await self._session.execute(statement)).scalars())

    async def get_latest_snapshot(
        self,
        symbol: str,
        timeframe: str,
        *,
        indicator: str,
        engine_version: str,
    ) -> IndicatorSnapshot | None:
        """Đọc snapshot mới nhất của một chỉ báo cụ thể.

        Args:
            symbol: Mã chứng khoán.
            timeframe: Khung thời gian.
            indicator: Tên chỉ báo, ví dụ market_structure.
            engine_version: Phiên bản engine.

        Returns:
            `IndicatorSnapshot` mới nhất hoặc None khi chưa có.
        """
        statement = (
            select(IndicatorSnapshot)
            .where(
                IndicatorSnapshot.symbol == symbol.upper(),
                IndicatorSnapshot.timeframe == timeframe,
                IndicatorSnapshot.indicator == indicator,
                IndicatorSnapshot.engine_version == engine_version,
            )
            .order_by(IndicatorSnapshot.candle_time.desc())
            .limit(1)
        )
        return (await self._session.execute(statement)).scalars().first()

    async def upsert_structure_events(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """Ghi sự kiện cấu trúc, bỏ qua sự kiện đã tồn tại.

        Args:
            rows: Danh sách sự kiện đã chuẩn hoá cho database.

        Returns:
            Số sự kiện đã gửi lên database.
        """
        if not rows:
            return 0
        statement = insert(StructureEvent).values([dict(row) for row in rows])
        statement = statement.on_conflict_do_nothing(index_elements=["idempotency_key"])
        await self._session.execute(statement)
        await self._session.commit()
        return len(rows)

    async def get_structure_events(
        self,
        symbol: str,
        timeframe: str,
        *,
        limit: int = 50,
    ) -> list[StructureEvent]:
        """Đọc sự kiện cấu trúc mới nhất của một mã.

        Args:
            symbol: Mã chứng khoán.
            timeframe: Khung thời gian.
            limit: Số sự kiện tối đa.

        Returns:
            Danh sách sự kiện giảm dần theo thời điểm phá mức.
        """
        statement = (
            select(StructureEvent)
            .where(
                StructureEvent.symbol == symbol.upper(),
                StructureEvent.timeframe == timeframe,
            )
            .order_by(StructureEvent.event_time.desc())
            .limit(limit)
        )
        return list((await self._session.execute(statement)).scalars())
