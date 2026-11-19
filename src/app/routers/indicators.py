"""Endpoint chỉ báo kỹ thuật và cấu trúc thị trường.

Router đọc kết quả đã precompute qua service; chỉ tính tại chỗ khi chưa có
snapshot và service đánh dấu `computed_on_demand`.
"""

from typing import Annotated

from fastapi import APIRouter, Query

from app.core.dependencies import IndicatorServiceDep
from app.core.exceptions import InvalidRequestError
from app.indicators.registry import parse_request
from app.routers.market import to_candle_out
from app.schemas.divergence import DivergenceQuery
from app.schemas.indicators import IndicatorSeriesResponse, IndicatorSetResponse
from app.schemas.market import Timeframe
from app.schemas.structure import StructureSnapshotOut

router = APIRouter(prefix="/indicators", tags=["indicators"])


@router.get(
    "/{symbol}",
    response_model=IndicatorSetResponse,
    summary="Bộ chỉ báo của một mã",
)
async def get_indicators(
    symbol: str,
    service: IndicatorServiceDep,
    timeframe: Timeframe = "1D",
) -> IndicatorSetResponse:
    """Đọc bộ chỉ báo đã precompute của nến mới nhất.

    Args:
        symbol: Mã chứng khoán.
        service: Service chỉ báo.
        timeframe: Khung thời gian.

    Returns:
        `IndicatorSetResponse` kèm cờ `computed_on_demand`.
    """
    result = await service.get_indicators(symbol, timeframe)
    return IndicatorSetResponse(
        symbol=result.symbol,
        timeframe=result.timeframe,
        as_of=result.as_of,
        candles_used=result.candles_used,
        computed_on_demand=result.computed_on_demand,
        results=result.results,
    )


@router.get(
    "/{symbol}/series",
    response_model=IndicatorSeriesResponse,
    summary="Nến và chuỗi chỉ báo để vẽ biểu đồ",
)
async def get_series(
    symbol: str,
    service: IndicatorServiceDep,
    timeframe: Timeframe = "1D",
    limit: Annotated[int, Query(ge=20, le=1000, description="Số nến tối đa")] = 250,
    indicator: Annotated[
        list[str] | None,
        Query(
            description=(
                "Lặp lại tham số cho từng chỉ báo: `name` hoặc `name:v1:v2`. "
                "Tham số theo thứ tự khai báo, ví dụ `sma:20` hoặc `macd:12:26:9`."
            )
        ),
    ] = None,
) -> IndicatorSeriesResponse:
    """Trả nến và chuỗi chỉ báo căn cùng trục thời gian cho biểu đồ.

    Args:
        symbol: Mã chứng khoán.
        service: Service chỉ báo.
        timeframe: Khung thời gian.
        limit: Số nến tối đa.
        indicator: Danh sách chỉ báo cần tính; bỏ trống để dùng bộ mặc định.

    Returns:
        `IndicatorSeriesResponse` gồm nến và các chuỗi chỉ báo.

    Raises:
        InvalidRequestError: Khi chuỗi chỉ báo hoặc tham số không hợp lệ.
    """
    planned = None
    if indicator:
        try:
            planned = [parse_request(item) for item in indicator]
        except (KeyError, ValueError) as exc:
            message = exc.args[0] if isinstance(exc, KeyError) else str(exc)
            raise InvalidRequestError(str(message)) from exc

    result = await service.get_series(symbol, timeframe, requests=planned, limit=limit)
    return IndicatorSeriesResponse(
        symbol=result.symbol,
        timeframe=result.timeframe,
        as_of=result.as_of,
        count=len(result.candles),
        candles=[to_candle_out(candle) for candle in result.candles],
        series=result.series,
    )


@router.get(
    "/{symbol}/structure",
    response_model=StructureSnapshotOut,
    summary="Cấu trúc thị trường của một mã",
)
async def get_structure(
    symbol: str,
    service: IndicatorServiceDep,
    timeframe: Timeframe = "1D",
) -> StructureSnapshotOut:
    """Đọc snapshot cấu trúc thị trường: swing, BOS, CHoCH và bias.

    Args:
        symbol: Mã chứng khoán.
        service: Service chỉ báo.
        timeframe: Khung thời gian.

    Returns:
        `StructureSnapshotOut` của nến đã đóng mới nhất.
    """
    snapshot = await service.get_structure(symbol, timeframe)
    return StructureSnapshotOut.model_validate(snapshot)


@router.get(
    "/{symbol}/divergence",
    response_model=IndicatorSeriesResponse,
    summary="Swing-based MACD/RSI divergence with confirmation evidence",
)
async def get_divergence(
    symbol: str, service: IndicatorServiceDep, options: Annotated[DivergenceQuery, Query()]
) -> IndicatorSeriesResponse:
    """Return existing chart response with named, validated divergence configuration."""
    parameters = options.model_dump(exclude={"timeframe", "limit"})
    result = await service.get_series(
        symbol, options.timeframe, requests=[("divergence", parameters)], limit=options.limit
    )
    return IndicatorSeriesResponse(
        symbol=result.symbol,
        timeframe=result.timeframe,
        as_of=result.as_of,
        count=len(result.candles),
        candles=[to_candle_out(c) for c in result.candles],
        series=result.series,
    )
