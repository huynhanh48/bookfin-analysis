"""Thin analysis-memory HTTP routes; business operations remain tool-ready services."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import Json

from app.adapters.file_storage import validate_upload
from app.core.dependencies import AppSettings, require_admin_key
from app.core.rag_runtime import AnalysisServiceDep, RuntimeDep
from app.schemas.analysis import (
    AnalysisCreate,
    AnalysisSearch,
    CaseDetail,
    CaseResponse,
    OutcomeUpdate,
    SearchResponse,
)
from app.schemas.evidence import JobResponse

router = APIRouter(
    prefix="/analysis", tags=["analysis-memory"], dependencies=[Depends(require_admin_key)]
)


@router.post("/cases", response_model=CaseResponse, status_code=202)
async def create_case(
    service: AnalysisServiceDep,
    settings: AppSettings,
    metadata: Annotated[Json[AnalysisCreate], Form()],
    chart: Annotated[UploadFile | None, File()] = None,
) -> CaseResponse:
    """Accept metadata fields as multipart form data plus an optional original chart."""
    file = await validate_upload(chart, settings, image_only=True) if chart else None
    case = await service.ingest_analysis_case(metadata, file)
    return CaseResponse(data=CaseDetail.model_validate(case), job_id=getattr(case, "job_id", None))


@router.get("/cases/{case_id}", response_model=CaseResponse)
async def get_case(case_id: UUID, service: AnalysisServiceDep) -> CaseResponse:
    """Get immutable reasoning and explicitly separated outcomes."""
    return CaseResponse(data=CaseDetail.model_validate(await service.get_case(case_id)))


@router.get("/cases/{case_id}/chart", response_class=FileResponse)
async def get_chart(
    case_id: UUID, service: AnalysisServiceDep, runtime: RuntimeDep
) -> FileResponse:
    """Serve the original chart by case ID; JSON responses only contain opaque keys."""
    from app.core.exceptions import NotFoundError

    case = await service.get_case(case_id)
    if not case.chart_path:
        raise NotFoundError("Case has no chart.")
    return FileResponse(runtime.storage.path(case.chart_path))


@router.post("/search", response_model=SearchResponse)
async def search(
    query: AnalysisSearch,
    service: AnalysisServiceDep,
    settings: AppSettings,
    request: Request,
) -> SearchResponse:
    """Retrieve cosine-ranked cases, with outcome excluded from every hit."""
    if query.image:
        from app.adapters.file_storage import validate_query_image

        await validate_query_image(query.image, settings)
    if settings.rag_enrichment_enabled:
        from app.core.rag_runtime import get_rag_runtime
        from app.services.rag_query_service import RagQueryService

        return await RagQueryService(get_rag_runtime(request), service.repository.session).search(
            query
        )
    rows = await service.search_similar_analysis_cases(query)
    return SearchResponse(data=rows, case_ids=[match.case.id for match in rows])


@router.patch("/cases/{case_id}/outcome", response_model=CaseResponse)
async def update_outcome(
    case_id: UUID, payload: OutcomeUpdate, service: AnalysisServiceDep
) -> CaseResponse:
    """Merge observed outcomes without reindexing or changing the original decision."""
    return CaseResponse(
        data=CaseDetail.model_validate(
            await service.update_analysis_outcome(case_id, payload.outcome)
        )
    )


@router.post("/cases/{case_id}/retry", response_model=CaseResponse, status_code=202)
async def retry_case(case_id: UUID, service: AnalysisServiceDep) -> CaseResponse:
    """Reschedule a failed analysis ingestion while preserving the original source."""
    return CaseResponse(data=CaseDetail.model_validate(await service.retry_ingestion(case_id)))


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: UUID, service: AnalysisServiceDep):
    """Poll a durable analysis job without running enrichment in the request."""
    from app.core.exceptions import NotFoundError
    from app.schemas.evidence import JobDetail

    job = await service.jobs.get(job_id)
    if job is None or job.case_id is None:
        raise NotFoundError("Analysis job not found.")
    return {"data": JobDetail.model_validate(job)}
