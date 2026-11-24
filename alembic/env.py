"""Môi trường Alembic cho async SQLAlchemy (asyncpg).

URL database lấy từ `app.core.config.Settings`, nên credential chỉ nằm trong biến
môi trường và không bao giờ xuất hiện trong `alembic.ini`.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app import models  # noqa: F401  (import để autogenerate thấy mọi model)
from app.core.config import get_settings
from app.core.database import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Leave official LightRAG-owned tables out of business-schema autogeneration."""
    return not (
        type_ == "table"
        and reflected
        and (
            name.lower().startswith("lightrag_")
            or name.lower().startswith("checkpoint_")
            or name.lower() == "checkpoints"
        )
    )


def run_migrations_offline() -> None:
    """Chạy migration ở chế độ offline, chỉ sinh SQL, không kết nối database."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Cấu hình migration context trên một connection đã mở.

    Args:
        connection: Connection đồng bộ do `run_sync` cung cấp.
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Chạy migration qua async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Chạy migration ở chế độ online, có thể dùng connection được chia sẻ."""
    connectable = config.attributes.get("connection", None)

    if connectable is None:
        asyncio.run(run_async_migrations())
    else:
        do_run_migrations(connectable)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
