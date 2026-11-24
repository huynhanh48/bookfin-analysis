"""HTTP surface for starting and inspecting stock-research runs."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.config import Settings, get_settings
from app.core.dependencies import OwnerId, require_admin_key
from app.schemas.agent import AnalysisResponse, AnalysisStep, AnalyzeRequest
from app.services.agent_service import AgentService

router = APIRouter(prefix="/agent", tags=["agent"], dependencies=[Depends(require_admin_key)])


def get_agent_service(request: Request) -> AgentService:
    service = getattr(request.app.state, "agent_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Agent runtime unavailable")
    return service


AgentServiceDep = Annotated[AgentService, Depends(get_agent_service)]
AgentSettings = Annotated[Settings, Depends(get_settings)]


@router.post("/analyze", response_model=AnalysisResponse, status_code=status.HTTP_202_ACCEPTED)
async def analyze(
    payload: AnalyzeRequest, service: AgentServiceDep, settings: AgentSettings, user_id: OwnerId
) -> AnalysisResponse:
    """Schedule a checkpointed graph run and return its opaque ID immediately."""
    if not settings.agent_enabled:
        raise HTTPException(status_code=503, detail="Agent disabled")
    if not settings.agent_checkpoint_enabled:
        raise HTTPException(status_code=503, detail="Agent checkpoint is required for async runs")
    try:
        return await service.start(payload, user_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail="Agent queue is full") from exc


@router.get("/analyses/{analysis_id}", response_model=AnalysisResponse)
async def get_analysis(
    analysis_id: UUID, service: AgentServiceDep, user_id: OwnerId
) -> AnalysisResponse:
    """Read the latest checkpoint, including a completed structured report."""
    result = await service.get(analysis_id, user_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return result


@router.get("/analyses/{analysis_id}/steps", response_model=list[AnalysisStep])
async def get_steps(
    analysis_id: UUID, service: AgentServiceDep, user_id: OwnerId
) -> list[AnalysisStep]:
    """List persisted graph super-steps in reverse chronological order."""
    result = await service.steps(analysis_id, user_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return result


@router.post("/analyses/{analysis_id}/resume", response_model=AnalysisResponse)
async def resume(analysis_id: UUID, service: AgentServiceDep, user_id: OwnerId) -> AnalysisResponse:
    """Continue a failed or interrupted checkpoint with its original thread ID."""
    try:
        result = await service.resume(analysis_id, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Agent model is not configured") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail="Agent queue is full") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return result
