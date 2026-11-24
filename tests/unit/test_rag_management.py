"""Knowledge management must not expose case jobs or leave deleted graph content."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.exceptions import ConflictError, InvalidRequestError, NotFoundError
from app.core.rag_runtime import get_rag_ingestion_service
from app.main import app
from app.schemas.rag import DocumentListResponse, DocumentView
from app.services.rag_ingestion_service import RagIngestionService


def job(status: str = "COMPLETED", namespace: str = "knowledge"):
    return SimpleNamespace(
        id=uuid4(),
        kind="text",
        source="SSI thesis",
        text="RSI divergence",
        namespace=namespace,
        status=status,
        stage=status,
        progress=100,
        error_code=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_list_cursor_and_scope():
    first, second = job(), job()
    repository = SimpleNamespace(list_knowledge=AsyncMock(side_effect=[[first, second], [second]]))
    service = RagIngestionService(repository, AsyncMock())
    page = await service.list(1)
    assert [item.id for item in page.data] == [first.id]
    assert page.next_cursor
    await service.list(1, page.next_cursor)
    before = repository.list_knowledge.await_args.args[1]
    assert before == (first.created_at, first.id)
    with pytest.raises(InvalidRequestError):
        await service.list(1, "not-a-cursor")


@pytest.mark.asyncio
async def test_remove_completed_deletes_graph_before_marking_removed():
    document = job()
    session = SimpleNamespace(commit=AsyncMock())
    repository = SimpleNamespace(get_for_update=AsyncMock(return_value=document), session=session)
    adapter = SimpleNamespace(delete_document=AsyncMock())
    service = RagIngestionService(repository, AsyncMock(), adapter=adapter)
    result = await service.remove(document.id)
    assert result.status == "REMOVED"
    adapter.delete_document.assert_awaited_once_with(str(document.id))
    assert session.commit.await_count == 2


@pytest.mark.asyncio
async def test_remove_rejects_active_or_case_jobs():
    document = job(status="PROCESSING")
    repository = SimpleNamespace(get_for_update=AsyncMock(return_value=document))
    service = RagIngestionService(repository, AsyncMock())
    with pytest.raises(ConflictError):
        await service.remove(document.id)
    document.namespace = "case"
    with pytest.raises(NotFoundError):
        await service.remove(document.id)


@pytest.mark.asyncio
async def test_failed_deletion_remains_retryable():
    document = job()
    session = SimpleNamespace(commit=AsyncMock())
    repository = SimpleNamespace(get_for_update=AsyncMock(return_value=document), session=session)
    adapter = SimpleNamespace(delete_document=AsyncMock(side_effect=TimeoutError()))
    service = RagIngestionService(repository, AsyncMock(), adapter=adapter)
    with pytest.raises(TimeoutError):
        await service.remove(document.id)
    assert document.status == "DELETING"
    assert document.error_code == "knowledge_deletion_failed"


@pytest.mark.asyncio
async def test_original_file_uses_private_storage_reference():
    document = job()
    document.file_key = "opaque.png"
    repository = SimpleNamespace(get=AsyncMock(return_value=document))
    storage = SimpleNamespace(read=AsyncMock(return_value=b"png-data"))
    service = RagIngestionService(repository, storage)
    data, mime, filename = await service.original_file(document.id)
    assert (data, mime) == (b"png-data", "image/png")
    assert filename == f"knowledge-{document.id}.png"
    storage.read.assert_awaited_once_with("opaque.png")


@pytest.mark.asyncio
async def test_management_routes_use_existing_knowledge_service(client):
    document = job()
    service = SimpleNamespace(
        list=AsyncMock(
            return_value=DocumentListResponse(data=[DocumentView.model_validate(document)])
        ),
        status=AsyncMock(return_value=document),
        remove=AsyncMock(return_value=document),
        original_file=AsyncMock(return_value=(b"image", "image/png", "knowledge.png")),
    )
    app.dependency_overrides[get_rag_ingestion_service] = lambda: service
    try:
        listing = await client.get("/api/v1/rag/documents")
        assert listing.status_code == 200
        assert listing.json()["data"][0]["source"] == "SSI thesis"
        detail = await client.get(f"/api/v1/rag/documents/{document.id}/content")
        assert detail.json()["data"]["text"] == "RSI divergence"
        original = await client.get(f"/api/v1/rag/documents/{document.id}/file")
        assert original.content == b"image"
        assert original.headers["content-type"] == "image/png"
        removed = await client.delete(f"/api/v1/rag/documents/{document.id}")
        assert removed.status_code == 200
        service.remove.assert_awaited_once_with(document.id)
    finally:
        app.dependency_overrides.clear()
