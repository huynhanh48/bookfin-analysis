"""Health and readiness endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel

from app import __version__
from app.config import get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Payload returned by the liveness probe."""

    status: str
    app: str
    version: str


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health() -> HealthResponse:
    """Report that the API process is running.

    The check is deliberately dependency-free: it does not touch PostgreSQL,
    Redis or SSI FCData.
    """
    settings = get_settings()
    return HealthResponse(status="ok", app=settings.app_name, version=__version__)
