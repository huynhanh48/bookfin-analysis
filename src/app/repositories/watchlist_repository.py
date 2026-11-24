"""Truy vấn PostgreSQL cho watchlist.

Repository không chứa business rule; service quyết định quyền, validate symbol và
transaction. Các thao tác ghi dùng `flush` để service commit một lần.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.symbol import Symbol
from app.models.watchlist import Watchlist, WatchlistItem


class WatchlistRepository:
    """Repository cho watchlist và các symbol bên trong."""

    def __init__(self, session: AsyncSession) -> None:
        """Khởi tạo repository.

        Args:
            session: Session database của request.
        """
        self._session = session

    async def list_for_owner(self, owner_id: str) -> list[Watchlist]:
        """Đọc toàn bộ watchlist của owner theo thứ tự hiển thị."""
        statement = (
            select(Watchlist)
            .where(Watchlist.owner_id == owner_id)
            .order_by(Watchlist.sort_order, Watchlist.id)
        )
        return list((await self._session.execute(statement)).scalars())

    async def item_counts(self, watchlist_ids: Sequence[int]) -> dict[int, int]:
        """Đếm symbol cho từng watchlist trong một query."""
        if not watchlist_ids:
            return {}
        statement = (
            select(WatchlistItem.watchlist_id, func.count(WatchlistItem.id))
            .where(WatchlistItem.watchlist_id.in_(watchlist_ids))
            .group_by(WatchlistItem.watchlist_id)
        )
        rows = (await self._session.execute(statement)).all()
        return {int(watchlist_id): int(count) for watchlist_id, count in rows}

    async def get(self, owner_id: str, watchlist_id: int) -> Watchlist | None:
        """Đọc một watchlist thuộc owner."""
        statement = select(Watchlist).where(
            Watchlist.id == watchlist_id, Watchlist.owner_id == owner_id
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_by_name(self, owner_id: str, name: str) -> Watchlist | None:
        """Đọc watchlist theo tên trong cùng owner."""
        statement = select(Watchlist).where(
            Watchlist.owner_id == owner_id, Watchlist.name == name
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def ensure_default_lists(self, owner_id: str) -> None:
        """Seed 10 watchlist mặc định nếu owner chưa có watchlist nào."""
        count = int(
            (
                await self._session.execute(
                    select(func.count()).select_from(Watchlist).where(
                        Watchlist.owner_id == owner_id
                    )
                )
            ).scalar_one()
        )
        if count > 0:
            return
        rows = [
            {
                "owner_id": owner_id,
                "name": f"List {index}",
                "sort_order": index - 1,
                "is_default": True,
            }
            for index in range(1, 11)
        ]
        statement = insert(Watchlist).values(rows)
        statement = statement.on_conflict_do_nothing(
            index_elements=["owner_id", "name"]
        )
        await self._session.execute(statement)
        await self._session.flush()

    async def max_sort_order(self, owner_id: str) -> int:
        """Thứ tự lớn nhất hiện tại của owner."""
        value = (
            await self._session.execute(
                select(func.max(Watchlist.sort_order)).where(Watchlist.owner_id == owner_id)
            )
        ).scalar_one_or_none()
        return int(value) if value is not None else -1

    async def create(
        self, owner_id: str, name: str, *, sort_order: int, is_default: bool = False
    ) -> Watchlist:
        """Tạo watchlist và flush để lấy id."""
        watchlist = Watchlist(
            owner_id=owner_id,
            name=name,
            sort_order=sort_order,
            is_default=is_default,
        )
        self._session.add(watchlist)
        await self._session.flush()
        return watchlist

    async def delete(self, watchlist: Watchlist) -> None:
        """Xoá watchlist; item cascade ở database."""
        await self._session.delete(watchlist)
        await self._session.flush()

    async def list_items(self, watchlist_id: int) -> list[WatchlistItem]:
        """Đọc symbol của watchlist theo thứ tự."""
        statement = (
            select(WatchlistItem)
            .where(WatchlistItem.watchlist_id == watchlist_id)
            .order_by(WatchlistItem.sort_order, WatchlistItem.id)
        )
        return list((await self._session.execute(statement)).scalars())

    async def max_item_sort_order(self, watchlist_id: int) -> int:
        """Thứ tự symbol lớn nhất trong watchlist."""
        value = (
            await self._session.execute(
                select(func.max(WatchlistItem.sort_order)).where(
                    WatchlistItem.watchlist_id == watchlist_id
                )
            )
        ).scalar_one_or_none()
        return int(value) if value is not None else -1

    async def add_symbols(self, watchlist_id: int, symbols: Sequence[str]) -> int:
        """Thêm symbol idempotent; trả số dòng thực sự được thêm."""
        if not symbols:
            return 0
        start = await self.max_item_sort_order(watchlist_id) + 1
        rows = [
            {"watchlist_id": watchlist_id, "symbol": symbol, "sort_order": start + index}
            for index, symbol in enumerate(symbols)
        ]
        statement = insert(WatchlistItem).values(rows)
        statement = statement.on_conflict_do_nothing(
            index_elements=["watchlist_id", "symbol"]
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        return int(getattr(result, "rowcount", 0) or 0)

    async def remove_symbol(self, watchlist_id: int, symbol: str) -> bool:
        """Xoá một symbol; trả True nếu có dòng bị xoá."""
        result = await self._session.execute(
            delete(WatchlistItem).where(
                WatchlistItem.watchlist_id == watchlist_id,
                WatchlistItem.symbol == symbol.upper(),
            )
        )
        await self._session.flush()
        return bool(getattr(result, "rowcount", 0))

    async def clear_items(self, watchlist_id: int) -> int:
        """Xoá toàn bộ symbol của watchlist."""
        result = await self._session.execute(
            delete(WatchlistItem).where(WatchlistItem.watchlist_id == watchlist_id)
        )
        await self._session.flush()
        return int(getattr(result, "rowcount", 0) or 0)

    async def known_symbols(self, symbols: Sequence[str]) -> set[str]:
        """Trả các symbol tồn tại trong catalog."""
        if not symbols:
            return set()
        statement = select(Symbol.symbol).where(Symbol.symbol.in_(symbols))
        return set((await self._session.execute(statement)).scalars())

    async def refresh(self, instance: Watchlist) -> None:
        """Refresh server-generated timestamps sau commit."""
        await self._session.refresh(instance)

    async def commit(self) -> None:
        """Commit transaction do service sở hữu."""
        await self._session.commit()

    async def rollback(self) -> None:
        """Rollback transaction do service sở hữu."""
        await self._session.rollback()
