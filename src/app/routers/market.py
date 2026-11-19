"""Endpoint dữ liệu thị trường.

Router chỉ validate tham số, gọi service và map sang schema; không chứa business
logic và không truy cập SQLAlchemy trực tiếp.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import (
    IngestionServiceDep,
    MarketServiceDep,
    require_admin_key,
)
from app.models.candle import Candle
from app.schemas.market import (
    BackfillRequest,
    BackfillResult,
    CandleListResponse,
    CandleOut,
    QuoteOut,
    SymbolListResponse,
    SymbolOut,
    Timeframe,
)

router = APIRouter(prefix="/market", tags=["market"])


def to_candle_out(candle: Candle) -> CandleOut:
    """Chuyển nến ORM sang schema trả cho client.

    Args:
        candle: Bản ghi nến trong database.

    Returns:
        `CandleOut` với giá dạng float và thời gian mở nến.
    """
    return CandleOut(
        time=candle.candle_open_time,
        open=float(candle.open),
        high=float(candle.high),
        low=float(candle.low),
        close=float(candle.close),
        volume=int(candle.volume),
        value=None if candle.turnover is None else float(candle.turnover),
        price_basis=candle.price_basis,
        source=candle.source,
    )


@router.get(
    "/symbols",
    response_model=SymbolListResponse,
    summary="Danh mục mã chứng khoán",
)
async def list_symbols(
    service: MarketServiceDep,
    limit: Annotated[int, Query(ge=1, le=1000, description="Số mã mỗi trang")] = 200,
    offset: Annotated[int, Query(ge=0, description="Vị trí bắt đầu")] = 0,
) -> SymbolListResponse:
    """Đọc danh mục mã đã đồng bộ từ gateway.

    Args:
        service: Service dữ liệu thị trường.
        limit: Số bản ghi tối đa.
        offset: Vị trí bắt đầu.

    Returns:
        Danh sách mã kèm tổng số bản ghi.
    """
    rows, total = await service.list_symbols(limit=limit, offset=offset)
    return SymbolListResponse(
        items=[SymbolOut.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/candles/{symbol}",
    response_model=CandleListResponse,
    summary="Nến đã đóng của một mã",
)
async def get_candles(
    symbol: str,
    service: MarketServiceDep,
    timeframe: Timeframe = "1D",
    limit: Annotated[int, Query(ge=1, le=1000, description="Số nến tối đa")] = 250,
) -> CandleListResponse:
    """Đọc nến đã đóng từ PostgreSQL.

    Args:
        symbol: Mã chứng khoán.
        service: Service dữ liệu thị trường.
        timeframe: Khung thời gian.
        limit: Số nến tối đa.

    Returns:
        Danh sách nến tăng dần theo thời gian.
    """
    candles = await service.get_candles(symbol, timeframe, limit=limit)
    return CandleListResponse(
        symbol=symbol.upper(),
        timeframe=timeframe,
        count=len(candles),
        candles=[to_candle_out(candle) for candle in candles],
    )


@router.get(
    "/quotes/{symbol}",
    response_model=QuoteOut,
    summary="Snapshot giá mới nhất",
)
async def get_quote(symbol: str, service: MarketServiceDep) -> QuoteOut:
    """Đọc snapshot giá mới nhất, có ghi rõ nguồn và độ mới.

    Args:
        symbol: Mã chứng khoán.
        service: Service dữ liệu thị trường.

    Returns:
        `QuoteOut` kèm `source`, `as_of` và `is_stale`.
    """
    return await service.get_quote(symbol)


@router.post(
    "/backfill",
    response_model=BackfillResult,
    summary="Nạp lịch sử nến",
    dependencies=[Depends(require_admin_key)],
)
async def backfill(payload: BackfillRequest, service: IngestionServiceDep) -> BackfillResult:
    """Nạp lịch sử nến cho một danh sách mã.

    Endpoint yêu cầu header `X-Admin-Key` khi service được cấu hình admin key.

    Args:
        payload: Danh sách mã, khung thời gian và khoảng ngày.
        service: Service nạp dữ liệu.

    Returns:
        `BackfillResult` tổng hợp cho cả lô.
    """
    return await service.backfill(
        payload.symbols,
        timeframe=payload.timeframe,
        start=payload.start,
        end=payload.end,
    )
