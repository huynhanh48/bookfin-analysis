"""Dependency dùng chung, gắn vào router qua `Depends`.

Đây là điểm duy nhất định nghĩa cách lấy session database, Redis client, FCData
client và các service. Test có thể thay bằng `app.dependency_overrides`.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import SessionFactory
from app.core.exceptions import AdminAuthError
from app.core.redis import get_redis_client
from app.fcdata.client import FcDataClient
from app.repositories.indicator_repository import IndicatorRepository
from app.repositories.market_repository import MarketRepository
from app.services.indicator_service import IndicatorService
from app.services.ingestion_service import IngestionService
from app.services.market_service import MarketService


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Cấp một `AsyncSession` theo từng request.

    Yields:
        Session được đóng tự động sau khi response hoàn tất.
    """
    async with SessionFactory() as session:
        yield session


def get_redis() -> Redis | None:
    """Cấp Redis client, hoặc `None` khi tính năng cache đang tắt.

    Returns:
        Client `redis.asyncio` hoặc `None`.
    """
    return get_redis_client()


def get_fcdata_client(request: Request) -> FcDataClient:
    """Lấy FCData client dùng chung, tạo mới nếu chưa có.

    Lifespan tạo client ngay khi ứng dụng khởi động để giữ connection pool. Hàm
    này vẫn tạo bù khi client chưa tồn tại (ví dụ khi test gọi thẳng ASGI app mà
    không chạy lifespan).

    Args:
        request: Request hiện tại, dùng để truy cập `app.state`.

    Returns:
        Client dùng chung tới BookFin FCData Gateway.
    """
    client = getattr(request.app.state, "fcdata_client", None)
    if client is None:
        client = FcDataClient(get_settings())
        request.app.state.fcdata_client = client
    return client


def require_admin_key(
    settings: Annotated[Settings, Depends(get_settings)],
    x_admin_key: Annotated[str | None, Header(alias="X-Admin-Key")] = None,
) -> None:
    """Chặn endpoint thao tác nếu không có admin key hợp lệ.

    Args:
        settings: Cấu hình hiện tại.
        x_admin_key: Giá trị header `X-Admin-Key` do client gửi.

    Raises:
        AdminAuthError: Khi môi trường không phải development và key sai/thiếu.
    """
    if settings.is_development and settings.admin_api_key is None:
        return
    if not x_admin_key or x_admin_key != settings.admin_api_key:
        raise AdminAuthError("Endpoint này yêu cầu X-Admin-Key hợp lệ.")


AppSettings = Annotated[Settings, Depends(get_settings)]
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
RedisClient = Annotated[Redis | None, Depends(get_redis)]
FcData = Annotated[FcDataClient, Depends(get_fcdata_client)]


def get_market_repository(session: DbSession) -> MarketRepository:
    """Tạo repository thị trường gắn với session hiện tại.

    Args:
        session: Session database của request.

    Returns:
        `MarketRepository` dùng session này.
    """
    return MarketRepository(session)


def get_indicator_repository(session: DbSession) -> IndicatorRepository:
    """Tạo repository chỉ báo gắn với session hiện tại.

    Args:
        session: Session database của request.

    Returns:
        `IndicatorRepository` dùng session này.
    """
    return IndicatorRepository(session)


MarketRepositoryDep = Annotated[MarketRepository, Depends(get_market_repository)]
IndicatorRepositoryDep = Annotated[IndicatorRepository, Depends(get_indicator_repository)]


def get_market_service(
    settings: AppSettings,
    repository: MarketRepositoryDep,
    redis: RedisClient,
    fcdata: FcData,
) -> MarketService:
    """Tạo service dữ liệu thị trường cho một request.

    Args:
        settings: Cấu hình ứng dụng.
        repository: Repository thị trường.
        redis: Redis client hoặc `None`.
        fcdata: FCData client.

    Returns:
        `MarketService` đã gắn đủ phụ thuộc.
    """
    return MarketService(settings=settings, repository=repository, redis=redis, fcdata=fcdata)


def get_ingestion_service(
    settings: AppSettings,
    repository: MarketRepositoryDep,
    fcdata: FcData,
) -> IngestionService:
    """Tạo service nạp dữ liệu cho một request.

    Args:
        settings: Cấu hình ứng dụng.
        repository: Repository thị trường.
        fcdata: FCData client.

    Returns:
        `IngestionService` đã gắn đủ phụ thuộc.
    """
    return IngestionService(settings=settings, repository=repository, fcdata=fcdata)


def get_indicator_service(
    settings: AppSettings,
    market_repository: MarketRepositoryDep,
    indicator_repository: IndicatorRepositoryDep,
) -> IndicatorService:
    """Tạo service chỉ báo cho một request.

    Args:
        settings: Cấu hình ứng dụng.
        market_repository: Repository thị trường để đọc nến.
        indicator_repository: Repository chỉ báo và sự kiện cấu trúc.

    Returns:
        `IndicatorService` đã gắn đủ phụ thuộc.
    """
    return IndicatorService(
        settings=settings,
        market_repository=market_repository,
        indicator_repository=indicator_repository,
    )


MarketServiceDep = Annotated[MarketService, Depends(get_market_service)]
IngestionServiceDep = Annotated[IngestionService, Depends(get_ingestion_service)]
IndicatorServiceDep = Annotated[IndicatorService, Depends(get_indicator_service)]
