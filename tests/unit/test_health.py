"""Test cho endpoint vận hành và envelope lỗi."""

from httpx import AsyncClient

from app import __version__
from app.core.config import get_settings


async def test_health_tra_ve_ok(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["app"] == get_settings().app_name
    assert payload["version"] == __version__


async def test_health_gan_request_id(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.headers["X-Request-ID"]


async def test_request_id_duoc_giu_lai_tu_client(client: AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-ID": "abc-123"})

    assert response.headers["X-Request-ID"] == "abc-123"


async def test_route_khong_ton_tai_tra_envelope_loi(client: AsyncClient) -> None:
    response = await client.get("/khong-ton-tai")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "http_404"
    assert body["error"]["request_id"]
