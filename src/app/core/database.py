"""Kết nối PostgreSQL: async engine, session factory và ORM base.

Mọi truy vấn đi qua `AsyncSession`; repository là nơi duy nhất được phép viết
SQLAlchemy. Router và service chỉ nhận session qua `Depends`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import Settings, get_settings

# Tên constraint ổn định để Alembic so sánh schema không bị nhiễu.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base khai báo cho mọi ORM model.

    Alembic autogenerate đọc `Base.metadata`, vì vậy mọi module model phải được
    import trước khi sinh migration (xem `app.models`).
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def create_engine(settings: Settings | None = None) -> AsyncEngine:
    """Tạo async engine từ cấu hình.

    Args:
        settings: Cấu hình tuỳ chọn; mặc định lấy từ môi trường.

    Returns:
        `AsyncEngine` dùng driver asyncpg, có pool và `pool_pre_ping`.
    """
    settings = settings or get_settings()
    return create_async_engine(
        settings.database_url,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
    )


engine: AsyncEngine = create_engine()

SessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Cấp một session cho worker hoặc script chạy ngoài request.

    Yields:
        `AsyncSession` tự đóng khi thoát khỏi ngữ cảnh.
    """
    async with SessionFactory() as session:
        yield session


async def dispose_engine() -> None:
    """Đóng toàn bộ connection trong pool khi ứng dụng dừng."""
    await engine.dispose()
