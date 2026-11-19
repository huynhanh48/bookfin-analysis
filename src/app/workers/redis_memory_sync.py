"""Rebuild Redis text/metadata mirror: uv run python -m app.workers.redis_memory_sync."""

import asyncio

from sqlalchemy import select

from app.adapters.redis_memory_store import RedisMemoryStore
from app.core.database import SessionFactory, dispose_engine
from app.core.redis import close_redis_client, get_redis_client
from app.models.analysis_case import AnalysisCase
from app.models.rag_document import RagDocument


async def sync() -> None:
    """Stream source rows in bounded batches, never reading or duplicating embeddings."""
    client = get_redis_client()
    if client is None:
        raise SystemExit("Set REDIS_ENABLED=true")
    store = RedisMemoryStore(client)
    count = 0
    try:
        async with SessionFactory() as session:
            for model, write in ((RagDocument, store.document), (AnalysisCase, store.case)):
                rows = await session.stream_scalars(select(model).execution_options(yield_per=100))
                async for record in rows:
                    if not await write(record):
                        raise SystemExit("Redis mirror failed; source data remains in PostgreSQL")
                    count += 1
        print(f"Mirrored {count} records to Redis")
    finally:
        await close_redis_client()
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(sync())
