"""Smoke test for the health endpoint."""

from httpx import AsyncClient

from app import __version__
from app.config import get_settings


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "app": get_settings().app_name,
        "version": __version__,
    }
