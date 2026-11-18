"""FastAPI dependencies shared by routers."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionFactory


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Provide a request-scoped database session."""
    async with SessionFactory() as session:
        yield session
