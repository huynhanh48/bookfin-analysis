"""Nghiệp vụ watchlist: default list, rename, bulk add và bảo vệ dữ liệu."""

from __future__ import annotations

import re
from collections.abc import Sequence

from sqlalchemy.exc import IntegrityError

from app.core.exceptions import ConflictError, InvalidRequestError, NotFoundError
from app.core.logging import get_logger
from app.repositories.watchlist_repository import WatchlistRepository
from app.schemas.watchlist import (
    WatchlistBulkAddResult,
    WatchlistCreate,
    WatchlistItemOut,
    WatchlistItemsResponse,
    WatchlistListResponse,
    WatchlistOut,
    WatchlistUpdate,
)

logger = get_logger(__name__)

DEFAULT_OWNER_ID = "local"
DEFAULT_WATCHLIST_COUNT = 10
_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9._-]{1,20}$")


class WatchlistService:
    """Quản lý watchlist của một owner."""

    def __init__(self, repository: WatchlistRepository) -> None:
        """Khởi tạo service.

        Args:
            repository: Repository watchlist.
        """
        self._repository = repository

    async def list_watchlists(self, owner_id: str = DEFAULT_OWNER_ID) -> WatchlistListResponse:
        """Đọc watchlist, tự seed 10 list mặc định cho owner mới."""
        await self._repository.ensure_default_lists(owner_id)
        await self._repository.commit()
        rows = await self._repository.list_for_owner(owner_id)
        counts = await self._repository.item_counts([row.id for row in rows])
        items = [
            WatchlistOut.model_validate(row).model_copy(
                update={"item_count": counts.get(row.id, 0)}
            )
            for row in rows
        ]
        return WatchlistListResponse(items=items, total=len(items))

    async def create(
        self, payload: WatchlistCreate, owner_id: str = DEFAULT_OWNER_ID
    ) -> WatchlistOut:
        """Tạo watchlist người dùng; không phải default."""
        await self._repository.ensure_default_lists(owner_id)
        name = payload.name.strip()
        if not name:
            raise InvalidRequestError("Tên watchlist không được để trống.")
        sort_order = await self._repository.max_sort_order(owner_id) + 1
        try:
            row = await self._repository.create(
                owner_id, name, sort_order=sort_order, is_default=False
            )
            await self._repository.commit()
        except IntegrityError as exc:
            await self._repository.rollback()
            raise ConflictError(f"Watchlist {name!r} đã tồn tại.") from exc
        return WatchlistOut.model_validate(row)

    async def update(
        self, watchlist_id: int, payload: WatchlistUpdate, owner_id: str = DEFAULT_OWNER_ID
    ) -> WatchlistOut:
        """Đổi tên hoặc thứ tự; default list vẫn được rename."""
        row = await self._owned(owner_id, watchlist_id)
        if payload.name is not None:
            name = payload.name.strip()
            if not name:
                raise InvalidRequestError("Tên watchlist không được để trống.")
            row.name = name
        if payload.sort_order is not None:
            row.sort_order = payload.sort_order
        try:
            await self._repository.commit()
        except IntegrityError as exc:
            await self._repository.rollback()
            raise ConflictError("Tên watchlist đã tồn tại.") from exc
        await self._repository.refresh(row)
        return WatchlistOut.model_validate(row)

    async def delete(self, watchlist_id: int, owner_id: str = DEFAULT_OWNER_ID) -> None:
        """Xoá watchlist người dùng; từ chối xoá 10 list mặc định."""
        row = await self._owned(owner_id, watchlist_id)
        if row.is_default:
            raise InvalidRequestError("Không thể xoá watchlist mặc định.")
        await self._repository.delete(row)
        await self._repository.commit()

    async def items(
        self, watchlist_id: int, owner_id: str = DEFAULT_OWNER_ID
    ) -> WatchlistItemsResponse:
        """Đọc toàn bộ symbol của watchlist."""
        row = await self._owned(owner_id, watchlist_id)
        items = await self._repository.list_items(watchlist_id)
        return WatchlistItemsResponse(
            watchlist_id=row.id,
            name=row.name,
            items=[WatchlistItemOut.model_validate(item) for item in items],
            count=len(items),
        )

    async def add_symbols(
        self,
        watchlist_id: int,
        symbols: Sequence[str],
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> WatchlistBulkAddResult:
        """Thêm nhiều symbol trong một transaction, bỏ qua symbol trùng."""
        await self._owned(owner_id, watchlist_id)
        normalized = self._normalize_symbols(symbols)
        if not normalized:
            raise InvalidRequestError("Danh sách symbol rỗng hoặc không hợp lệ.")
        known = await self._repository.known_symbols(normalized)
        unknown = [symbol for symbol in normalized if symbol not in known]
        if unknown:
            raise InvalidRequestError(
                "Một số mã không có trong danh mục.",
                details={"symbols": unknown[:50]},
            )
        added = await self._repository.add_symbols(watchlist_id, normalized)
        await self._repository.commit()
        logger.info(
            "watchlist_symbols_added",
            extra={"watchlist_id": watchlist_id, "added": added, "requested": len(normalized)},
        )
        return WatchlistBulkAddResult(
            watchlist_id=watchlist_id,
            added=added,
            skipped=len(normalized) - added,
            symbols=normalized,
        )

    async def remove_symbol(
        self, watchlist_id: int, symbol: str, owner_id: str = DEFAULT_OWNER_ID
    ) -> None:
        """Xoá một symbol khỏi watchlist."""
        await self._owned(owner_id, watchlist_id)
        removed = await self._repository.remove_symbol(watchlist_id, symbol)
        await self._repository.commit()
        if not removed:
            raise NotFoundError(f"Không có {symbol.upper()} trong watchlist.")

    async def clear_items(self, watchlist_id: int, owner_id: str = DEFAULT_OWNER_ID) -> int:
        """Làm rỗng watchlist, giữ lại bản thân watchlist."""
        await self._owned(owner_id, watchlist_id)
        removed = await self._repository.clear_items(watchlist_id)
        await self._repository.commit()
        return removed

    async def _owned(self, owner_id: str, watchlist_id: int):
        """Đảm bảo watchlist tồn tại và thuộc owner."""
        row = await self._repository.get(owner_id, watchlist_id)
        if row is None:
            raise NotFoundError("Không tìm thấy watchlist.")
        return row

    @staticmethod
    def _normalize_symbols(symbols: Sequence[str]) -> list[str]:
        """Uppercase, loại trùng và giữ thứ tự; bỏ symbol sai format."""
        result: list[str] = []
        seen: set[str] = set()
        for value in symbols:
            symbol = str(value).strip().upper()
            if not symbol or symbol in seen:
                continue
            if not _SYMBOL_PATTERN.fullmatch(symbol):
                raise InvalidRequestError(f"Mã {symbol!r} không hợp lệ.")
            seen.add(symbol)
            result.append(symbol)
        return result
