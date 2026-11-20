"""Helper dùng chung cho các job module."""

from __future__ import annotations

from app.core.database import session_scope
from app.repositories.market_repository import MarketRepository


async def load_active_symbols(limit: int = 1000, *, offset: int = 0) -> list[str]:
    """Đọc danh sách mã đang hoạt động từ PostgreSQL.

    Args:
        limit: Số mã tối đa.
        offset: Vị trí bắt đầu (dùng cho job phân trang trong tương lai).

    Returns:
        Danh sách mã đã chuẩn hoá theo thứ tự repository trả về.
    """
    async with session_scope() as session:
        rows, _total = await MarketRepository(session).list_symbols(
            limit=limit, offset=offset, active_only=True
        )
    return [row.symbol for row in rows]


def dedupe_symbols(symbols: list[str]) -> list[str]:
    """Chuẩn hoá và loại trùng mã nhưng giữ nguyên thứ tự.

    Args:
        symbols: Danh sách mã thô.

    Returns:
        Danh sách mã uppercase, không trùng.
    """
    return list(dict.fromkeys(item.strip().upper() for item in symbols if item.strip()))
