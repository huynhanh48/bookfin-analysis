"""Knowledge document pages use real PostgreSQL ordering and namespace isolation."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.adapters.file_storage import LocalFileStorage
from app.core.database import SessionFactory
from app.models.rag_document import RagDocument
from app.repositories.rag_repository import RagRepository
from app.services.rag_ingestion_service import RagIngestionService

pytestmark = pytest.mark.integration


async def test_knowledge_list_paginates_without_case_jobs(tmp_path):
    async with SessionFactory() as session:
        source = f"agent-test-{uuid4()}"
        newest = datetime.now(UTC) + timedelta(days=1)
        documents = [
            RagDocument(
                fingerprint=uuid4().hex,
                kind="text",
                namespace=namespace,
                source=f"{source}-{index}",
                text="known evidence",
                status="COMPLETED",
                created_at=newest + timedelta(seconds=index),
            )
            for index, namespace in enumerate(("knowledge", "case", "knowledge"))
        ]
        session.add_all(documents)
        await session.flush()
        repository = RagRepository(session)
        service = RagIngestionService(repository, LocalFileStorage(str(tmp_path)))
        first = await service.list(1)
        assert len(first.data) == 1 and first.next_cursor
        second = await service.list(1, first.next_cursor)
        assert len(second.data) == 1
        assert {first.data[0].id, second.data[0].id} == {documents[0].id, documents[2].id}
        assert documents[1].id not in {first.data[0].id, second.data[0].id}
        await session.rollback()
