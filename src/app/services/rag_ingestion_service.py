"""Source persistence and idempotent knowledge ingestion, independent of HTTP."""

import base64
import binascii
import hashlib
import json
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from app.adapters.file_storage import FileStorage
from app.adapters.rag_adapter import RAGAdapter
from app.adapters.redis_memory_store import RedisMemoryStore
from app.core.exceptions import ConflictError, InvalidRequestError, NotFoundError
from app.core.logging import request_id_var
from app.models.rag_document import RagDocument
from app.repositories.rag_repository import RagRepository
from app.schemas.rag import DocumentCreate, DocumentListResponse, DocumentView

_ACTIVE_STATUSES = frozenset(
    {"PROCESSING", "EXTRACTING", "ENRICHING", "VERIFYING", "NORMALIZING", "INDEXING"}
)


def _decode_cursor(value: str | None) -> tuple[datetime, UUID] | None:
    if value is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
        timestamp, identifier = raw.rsplit("|", 1)
        when = datetime.fromisoformat(timestamp)
        if when.tzinfo is None or when.utcoffset() is None:
            raise ValueError("Cursor timestamp must be aware")
        return when, UUID(identifier)
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise InvalidRequestError("Invalid document cursor.") from exc


def _encode_cursor(job: RagDocument) -> str:
    value = f"{job.created_at.isoformat()}|{job.id}".encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


class RagIngestionService:
    """Store original input before scheduling work; provider failures cannot lose uploads."""

    def __init__(
        self,
        repository: RagRepository,
        storage: FileStorage,
        mirror: RedisMemoryStore | None = None,
        adapter: RAGAdapter | None = None,
    ):
        self.repository, self.storage = repository, storage
        self.mirror = mirror
        self.adapter = adapter

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
        if job is None or job.namespace != "knowledge":
            raise NotFoundError("Document not found.")
        return job

    async def list(self, limit: int, cursor: str | None = None) -> DocumentListResponse:
        """Return a bounded keyset page of uploaded knowledge, including failed jobs."""
        rows = await self.repository.list_knowledge(limit, _decode_cursor(cursor))
        return DocumentListResponse(
            data=[DocumentView.model_validate(row) for row in rows[:limit]],
            next_cursor=_encode_cursor(rows[limit - 1]) if len(rows) > limit else None,
        )

    async def original_file(self, document_id: UUID) -> tuple[bytes, str, str]:
        """Return a validated original binary only to the authenticated admin route."""
        job = await self.status(document_id)
        if job.file_key is None:
            raise NotFoundError("This document has no file.")
        suffix = Path(job.file_key).suffix.lower()
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".webp": "image/webp",
            ".pdf": "application/pdf",
        }.get(suffix)
        if mime is None:
            raise NotFoundError("Unsupported stored file type.")
        try:
            data = await self.storage.read(job.file_key)
        except FileNotFoundError as exc:
            raise NotFoundError("Original file is unavailable.") from exc
        return data, mime, f"knowledge-{document_id}{suffix}"

    async def retry(self, document_id: UUID) -> RagDocument:
        """Schedule only a previously failed job for explicit retry."""
        await self.status(document_id)
        job = await self.repository.retry(document_id)
        if job is None:
            raise NotFoundError("Document not found.")
        await self.repository.session.commit()
        if self.mirror:
            await self.repository.session.refresh(job)
            await self.mirror.document(job)
        return job

    async def remove(self, document_id: UUID) -> RagDocument:
        """Remove indexed knowledge while retaining the original audit record and asset."""
        job = await self.repository.get_for_update(document_id)
        if job is None or job.namespace != "knowledge":
            raise NotFoundError("Document not found.")
        if job.status in _ACTIVE_STATUSES:
            raise ConflictError("Wait for ingestion to finish before removing this document.")
        if job.status == "REMOVED":
            return job
        if job.status == "PENDING":
            job.status, job.stage, job.progress = "REMOVED", "REMOVED", 100
            await self.repository.session.commit()
            return job
        if self.adapter is None:
            raise RuntimeError("Knowledge adapter is unavailable")
        job.status, job.stage = "DELETING", "DELETING"
        job.error_code = None
        await self.repository.session.commit()
        try:
            await self.adapter.delete_document(str(document_id))
        except Exception:
            job.error_code = "knowledge_deletion_failed"
            await self.repository.session.commit()
            raise
        job.status, job.stage, job.progress = "REMOVED", "REMOVED", 100
        await self.repository.session.commit()
        return job
