"""Source persistence and idempotent knowledge ingestion, independent of HTTP."""

import hashlib
import json
from uuid import UUID, uuid4

from app.adapters.file_storage import FileStorage
from app.adapters.redis_memory_store import RedisMemoryStore
from app.core.exceptions import InvalidRequestError, NotFoundError
from app.core.logging import request_id_var
from app.models.rag_document import RagDocument
from app.repositories.rag_repository import RagRepository
from app.schemas.rag import DocumentCreate


class RagIngestionService:
    """Store original input before scheduling work; provider failures cannot lose uploads."""

    def __init__(
        self,
        repository: RagRepository,
        storage: FileStorage,
        mirror: RedisMemoryStore | None = None,
    ):
        self.repository, self.storage = repository, storage
        self.mirror = mirror

    async def ingest(
        self, payload: DocumentCreate, file: tuple[bytes, str] | None = None
    ) -> RagDocument:
        """Return the existing job for identical content, kind and source."""
        if payload.kind == "text" and (not payload.text.strip() or file):
            raise InvalidRequestError("Text ingestion requires nonempty text and no file.")
        if payload.kind != "text" and file is None:
            raise InvalidRequestError("Image/document ingestion requires a file.")
        if file and (
            (payload.kind == "image" and file[1] == ".pdf")
            or (payload.kind == "document" and file[1] != ".pdf")
        ):
            raise InvalidRequestError("File type does not match ingestion kind.")
        key = await self.storage.save(*file) if file else None
        fingerprint = hashlib.sha256(
            json.dumps([payload.model_dump(), key], sort_keys=True).encode()
        ).hexdigest()
        job = await self.repository.enqueue(
            id=uuid4(),
            fingerprint=fingerprint,
            kind=payload.kind,
            namespace="knowledge",
            source=payload.source,
            text=payload.text,
            file_key=key,
            status="PENDING",
            request_id=request_id_var.get(),
        )
        await self.repository.session.commit()
        if self.mirror:
            await self.repository.session.refresh(job)
            await self.mirror.document(job)
        return job

    async def status(self, document_id: UUID) -> RagDocument:
        """Expose polling for durable ingestion state."""
        job = await self.repository.get(document_id)
        if job is None:
            raise NotFoundError("Document not found.")
        return job

    async def retry(self, document_id: UUID) -> RagDocument:
        """Schedule only a previously failed job for explicit retry."""
        job = await self.repository.retry(document_id)
        if job is None:
            raise NotFoundError("Document not found.")
        await self.repository.session.commit()
        if self.mirror:
            await self.repository.session.refresh(job)
            await self.mirror.document(job)
        return job
