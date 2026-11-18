"""Endpoint chỉ báo kỹ thuật và cấu trúc thị trường.

Router đọc kết quả đã precompute qua service; chỉ tính tại chỗ khi chưa có
snapshot và service đánh dấu `computed_on_demand`.
"""

from fastapi import APIRouter

from app.core.dependencies import IndicatorServiceDep
from app.schemas.indicators import IndicatorSetResponse
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
