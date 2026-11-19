"""Fixture riêng cho test tích hợp.

Engine SQLAlchemy được tạo ở cấp module, còn pytest-asyncio tạo event loop mới cho
mỗi test. Vì vậy phải giải phóng pool sau mỗi test, nếu không connection tạo ở
loop trước sẽ bị dùng lại ở loop sau và asyncpg báo lỗi "attached to a different loop".
"""

from collections.abc import AsyncIterator

import pytest

from app.core.database import dispose_engine

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _dispose_engine_after_test() -> AsyncIterator[None]:
    """Đóng pool sau mỗi test tích hợp."""
    yield
    await dispose_engine()


@pytest.fixture(autouse=True)
def _seed_tokenizer_cache(monkeypatch) -> None:
    """Reuse the local tokenizer cache when available; unit tests still cover download."""
    from pathlib import Path

    cache = Path("data/rag/tokenizer")
    if cache.exists():
        monkeypatch.setenv("RAG_TOKENIZER_CACHE_DIR", str(cache))
