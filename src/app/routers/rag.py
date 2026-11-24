"""Knowledge ingestion, polling/retry and retrieval endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from pydantic import Json

from app.adapters.file_storage import validate_upload
from app.core.dependencies import AppSettings, require_admin_key
from app.core.rag_runtime import RagIngestionDep, RagQueryDep, RagServiceDep
from app.schemas.analysis import MultimodalQuery
from app.schemas.rag import (
    AnswerResponse,
    AnswerView,
    DocumentCreate,
    DocumentDetailResponse,
    DocumentDetailView,
    DocumentListResponse,
    DocumentResponse,
    DocumentView,
    EvidenceAnswerResponse,
    RagQuery,
)

router = APIRouter(prefix="/rag", tags=["knowledge-rag"], dependencies=[Depends(require_admin_key)])


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(
    service: RagIngestionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: str | None = None,
) -> DocumentListResponse:
    """List source uploads and progress without exposing case-ingestion jobs."""
    return await service.list(limit, cursor)


@router.post("/documents", response_model=DocumentResponse, status_code=202)
async def ingest(
    service: RagIngestionDep,
    settings: AppSettings,
    metadata: Annotated[Json[DocumentCreate], Form()],
    file: Annotated[UploadFile | None, File()] = None,
) -> DocumentResponse:
    """Persist multipart JSON metadata and optional image/PDF; return a durable job."""
    from app.core.exceptions import InvalidRequestError

    if metadata.kind == "document" and not settings.rag_remote_parser_url:
        raise InvalidRequestError(
            "PDF requires a remote parser; local model inference is disabled."
        )
    contents = await validate_upload(file, settings) if file else None
    return DocumentResponse(
        data=DocumentView.model_validate(await service.ingest(metadata, contents))
    )


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def status(document_id: UUID, service: RagIngestionDep) -> DocumentResponse:
    """Poll PENDING/PROCESSING/COMPLETED/FAILED without losing source records."""
    return DocumentResponse(data=DocumentView.model_validate(await service.status(document_id)))


@router.get("/documents/{document_id}/content", response_model=DocumentDetailResponse)
async def document_content(document_id: UUID, service: RagIngestionDep) -> DocumentDetailResponse:
    """Read original text and metadata; binary assets stay in private storage."""
    return DocumentDetailResponse(
        data=DocumentDetailView.model_validate(await service.status(document_id))
    )


@router.get("/documents/{document_id}/file")
async def document_file(document_id: UUID, service: RagIngestionDep) -> Response:
    """Read a private original asset without exposing its storage path."""
    data, mime, filename = await service.original_file(document_id)
    return Response(
        content=data,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/documents/{document_id}/retry", response_model=DocumentResponse, status_code=202)
async def retry(document_id: UUID, service: RagIngestionDep) -> DocumentResponse:
    """Explicitly retry a failed ingestion job with the same stable document ID."""
    return DocumentResponse(data=DocumentView.model_validate(await service.retry(document_id)))


@router.delete("/documents/{document_id}", response_model=DocumentResponse)
async def remove(document_id: UUID, service: RagIngestionDep) -> DocumentResponse:
    """Remove one upload from retrieval while retaining its original record."""
    return DocumentResponse(data=DocumentView.model_validate(await service.remove(document_id)))


@router.post("/query", response_model=AnswerResponse)
async def query(payload: RagQuery, service: RagServiceDep) -> AnswerResponse:
    """Query only the financial knowledge namespace, never future case outcomes."""
    answer = await service.retrieve_financial_knowledge(payload.question, payload.mode)
    return AnswerResponse(
        data=AnswerView(
            answer=answer.answer, sources=answer.sources, rerank_metadata=answer.rerank_metadata
        )
    )


@router.post("/query/multimodal", response_model=EvidenceAnswerResponse)
async def query_multimodal(
    service: RagQueryDep,
    settings: AppSettings,
    metadata: Annotated[Json[MultimodalQuery], Form()],
    image: Annotated[UploadFile | None, File()] = None,
):
    """Retrieve with an ephemeral image plus text; never ingest query images."""
    import base64

    query = metadata
    if image:
        data, suffix = await validate_upload(image, settings, image_only=True)
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}[suffix]
        query = query.model_copy(
            update={"image": f"data:{mime};base64," + base64.b64encode(data).decode()}
        )
    elif query.image:
        from app.adapters.file_storage import validate_query_image

        await validate_query_image(query.image, settings)
    return {"data": await service.answer(query)}
