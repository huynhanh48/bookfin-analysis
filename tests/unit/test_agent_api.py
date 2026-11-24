"""Agent router contract with an injected service, without provider calls."""

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from app.core.config import Settings, get_settings
from app.core.dependencies import require_admin_key
from app.main import create_app
from app.routers.agent import get_agent_service
from app.schemas.agent import AnalysisResponse


@pytest.mark.asyncio
async def test_analyze_returns_202_and_owner_scoped_result():
    analysis_id = uuid4()
    calls = []

    class FakeService:
        async def start(self, request, owner_id):
            calls.append((request.symbol, owner_id))
            return AnalysisResponse(
                analysis_id=analysis_id,
                symbol=request.symbol,
                timeframe=request.timeframe,
                status="running",
                analyzed_at=datetime(2026, 11, 20, tzinfo=UTC),
            )

        async def get(self, requested_id, owner_id):
            if requested_id != analysis_id or owner_id != "local":
                return None
            return await self.start(
                type("Request", (), {"symbol": "SSI", "timeframe": "1D"})(), owner_id
            )

    app = create_app()
    app.dependency_overrides[get_agent_service] = lambda: FakeService()
    app.dependency_overrides[get_settings] = lambda: Settings(
        agent_enabled=True, openrouter_api_key="test-key"
    )
    app.dependency_overrides[require_admin_key] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/agent/analyze", json={"symbol": "ssi", "question": "Phân tích SSI"}
        )
        assert response.status_code == 202
        assert response.json()["analysis_id"] == str(analysis_id)
        assert calls == [("SSI", "local")]
        found = await client.get(f"/api/v1/agent/analyses/{analysis_id}")
        assert found.status_code == 200
        missing = await client.get(f"/api/v1/agent/analyses/{uuid4()}")
        assert missing.status_code == 404
