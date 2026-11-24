"""HTTP endpoints quản lý watchlist.

Router chỉ validate request, gọi service và map response; mọi quy tắc nghiệp vụ
nằm trong `WatchlistService`.
"""


from fastapi import APIRouter, Response, status

from app.core.dependencies import OwnerId, WatchlistServiceDep
from app.schemas.watchlist import (
    WatchlistBulkAddRequest,
    WatchlistBulkAddResult,
    WatchlistCreate,
    WatchlistItemsResponse,
    WatchlistListResponse,
    WatchlistOut,
    WatchlistSymbolRequest,
    WatchlistUpdate,
)

router = APIRouter(prefix="/watchlists", tags=["watchlists"])


@router.get("", response_model=WatchlistListResponse, summary="Danh sách watchlist")
async def list_watchlists(
    service: WatchlistServiceDep, owner_id: OwnerId
) -> WatchlistListResponse:
    """Trả 10 watchlist mặc định (tự seed) và các list người dùng đã tạo."""
    return await service.list_watchlists(owner_id)


@router.post(
    "",
    response_model=WatchlistOut,
    status_code=status.HTTP_201_CREATED,
    summary="Tạo watchlist",
)
async def create_watchlist(
    payload: WatchlistCreate, service: WatchlistServiceDep, owner_id: OwnerId
) -> WatchlistOut:
    """Tạo một watchlist người dùng mới."""
    return await service.create(payload, owner_id)


@router.patch(
    "/{watchlist_id}",
    response_model=WatchlistOut,
    summary="Đổi tên hoặc thứ tự watchlist",
)
async def update_watchlist(
    watchlist_id: int,
    payload: WatchlistUpdate,
    service: WatchlistServiceDep,
    owner_id: OwnerId,
) -> WatchlistOut:
    """Rename/sort watchlist; áp dụng cho cả default list."""
    return await service.update(watchlist_id, payload, owner_id)


@router.delete(
    "/{watchlist_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Xoá watchlist người dùng",
)
async def delete_watchlist(
    watchlist_id: int, service: WatchlistServiceDep, owner_id: OwnerId
) -> Response:
    """Xoá watchlist không phải default."""
    await service.delete(watchlist_id, owner_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{watchlist_id}/items",
    response_model=WatchlistItemsResponse,
    summary="Symbol trong watchlist",
)
async def list_items(
    watchlist_id: int, service: WatchlistServiceDep, owner_id: OwnerId
) -> WatchlistItemsResponse:
    """Đọc toàn bộ symbol của một watchlist."""
    return await service.items(watchlist_id, owner_id)


@router.post(
    "/{watchlist_id}/items",
    response_model=WatchlistBulkAddResult,
    summary="Thêm một symbol",
)
async def add_item(
    watchlist_id: int,
    payload: WatchlistSymbolRequest,
    service: WatchlistServiceDep,
    owner_id: OwnerId,
) -> WatchlistBulkAddResult:
    """Thêm một symbol; trùng trong cùng list sẽ được bỏ qua."""
    return await service.add_symbols(watchlist_id, [payload.symbol], owner_id)


@router.post(
    "/{watchlist_id}/items/bulk",
    response_model=WatchlistBulkAddResult,
    summary="Thêm nhiều symbol",
)
async def add_items_bulk(
    watchlist_id: int,
    payload: WatchlistBulkAddRequest,
    service: WatchlistServiceDep,
    owner_id: OwnerId,
) -> WatchlistBulkAddResult:
    """Thêm selected/all trong một transaction, bỏ qua symbol đã có."""
    return await service.add_symbols(watchlist_id, payload.symbols, owner_id)


@router.delete(
    "/{watchlist_id}/items/{symbol}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Xoá một symbol",
)
async def remove_item(
    watchlist_id: int, symbol: str, service: WatchlistServiceDep, owner_id: OwnerId
) -> Response:
    """Xoá symbol khỏi watchlist."""
    await service.remove_symbol(watchlist_id, symbol, owner_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/{watchlist_id}/items",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Làm rỗng watchlist",
)
async def clear_items(
    watchlist_id: int, service: WatchlistServiceDep, owner_id: OwnerId
) -> Response:
    """Xoá toàn bộ symbol nhưng giữ watchlist."""
    await service.clear_items(watchlist_id, owner_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
