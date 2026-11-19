"""Persistent Redis management mirror; PostgreSQL remains authoritative."""

import json
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.logging import get_logger
from app.models.analysis_case import AnalysisCase
from app.models.rag_document import RagDocument
from app.schemas.analysis import CaseEvidence

logger = get_logger(__name__)


class RedisMemoryStore:
    """Mirror readable text/metadata in hashes without TTL or vector duplication.

    Writes follow the PostgreSQL commit. Redis outages never roll back source data;
    the rebuild command can reconstruct all mirror entries from PostgreSQL.
    """

    def __init__(self, client: Redis):
        self.client = client

    async def _write(self, key: str, fields: dict[str, Any]) -> bool:
        """Replace one hash atomically; preserve Unicode and redact provider errors."""
        values = {
            name: json.dumps(value, ensure_ascii=False, default=str)
            for name, value in fields.items()
        }
        try:
            async with self.client.pipeline(transaction=True) as pipe:
                pipe.delete(key)
                pipe.hset(key, mapping=values)
                await pipe.execute()
            return True
        except RedisError as exc:
            logger.warning("redis_memory_mirror_failed", extra={"error_type": type(exc).__name__})
            return False

    async def document(self, job: RagDocument) -> bool:
        """Mirror original document text, durable state and file references."""
        return await self._write(
            f"stock:rag:documents:{job.id}",
            {
                name: getattr(job, name)
                for name in (
                    "id",
                    "namespace",
                    "case_id",
                    "kind",
                    "source",
                    "text",
                    "file_key",
                    "status",
                    "error_code",
                    "attempts",
                    "created_at",
                    "updated_at",
                )
            },
        )

    async def case(self, case: AnalysisCase) -> bool:
        """Keep decision evidence and realized future outcomes in separate hashes."""
        saved = await self._write(
            f"stock:analysis:cases:{case.id}",
            CaseEvidence.model_validate(case).model_dump(mode="json"),
        )
        outcome_saved = await self._write(
            f"stock:analysis:outcomes:{case.id}", {"outcome": case.outcome}
        )
        return saved and outcome_saved
