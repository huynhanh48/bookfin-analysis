"""Fixture dùng chung cho test.

Biến môi trường được đặt **trước** khi import `app` để `Settings` đọc đúng môi
trường test; nếu đặt sau, cấu hình đã bị cache bởi `get_settings`.
"""

import os
from collections.abc import AsyncIterator

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("REDIS_ENABLED", "false")

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """HTTP client gọi thẳng ASGI app, không cần server thật."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
