"""API validation, body bounds, storage references and point-in-time representation."""

import io
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from starlette.datastructures import Headers, UploadFile

from app.adapters.file_storage import LocalFileStorage, validate_upload
from app.core.config import Settings
from app.core.exceptions import InvalidRequestError, UpstreamError
from app.core.rag_runtime import get_analysis_service, get_rag_ingestion_service, get_rag_service
from app.main import app
from app.schemas.analysis import AnalysisCreate, CaseEvidence
from app.services.analysis_memory_service import decision_text


@pytest.fixture
def chart():
    stream = io.BytesIO()
    Image.new("RGB", (20, 20)).save(stream, "PNG")
    return stream.getvalue()


async def test_chart_storage_roundtrip_and_dedup(tmp_path, chart):
    storage = LocalFileStorage(str(tmp_path))
    key = await storage.save(chart, ".png")
    assert await storage.save(chart, ".png") == key
    assert await storage.read(key) == chart
    with pytest.raises(InvalidRequestError):
        storage.path("../secret")


async def test_upload_mime_size_pixels(chart):
    upload = UploadFile(io.BytesIO(chart), headers=Headers({"content-type": "image/png"}))
    data, suffix = await validate_upload(upload, Settings(_env_file=None), True)
    assert (data, suffix) == (chart, ".png")
    with pytest.raises(InvalidRequestError):
        await validate_upload(
            UploadFile(io.BytesIO(chart), headers=Headers({"content-type": "image/jpeg"})),
            Settings(_env_file=None),
            True,
        )
    with pytest.raises(InvalidRequestError, match="pixel"):
        await validate_upload(
            UploadFile(io.BytesIO(chart), headers=Headers({"content-type": "image/png"})),
            Settings(_env_file=None, rag_max_image_pixels=5),
            True,
        )
    with pytest.raises(InvalidRequestError, match="limit"):
        await validate_upload(
            UploadFile(io.BytesIO(b"x" * 2048)), Settings(_env_file=None, rag_max_upload_bytes=1024)
        )


async def test_point_in_time_text_excludes_outcome():
    payload = AnalysisCreate(
        symbol="FPT", timeframe="D1", analyzed_at=datetime.now(UTC), thesis="HH HL breakout"
    )
    assert "outcome" not in decision_text(payload)
    assert "outcome" not in CaseEvidence.model_fields


async def test_api_form_validation_and_provider_error(client):
    service = AsyncMock()
    app.dependency_overrides[get_analysis_service] = lambda: service
    app.dependency_overrides[get_rag_service] = lambda: service
    try:
        result = await client.post(
            "/api/v1/analysis/cases", data={"metadata": '{"symbol":"invalid"}'}
        )
        assert result.status_code == 422
        service.ingest_analysis_case.assert_not_awaited()
        service.retrieve_financial_knowledge.side_effect = UpstreamError("Provider unavailable.")
        result = await client.post("/api/v1/rag/query", json={"question": "RSI?"})
        assert result.status_code == 502
        assert result.json()["error"]["request_id"]
        assert (
            await client.post("/api/v1/analysis/search", json={"query": "q", "top_k": 100})
        ).status_code == 422
    finally:
        app.dependency_overrides.clear()


async def test_bad_uploaded_chart_rejected(client):
    app.dependency_overrides[get_analysis_service] = lambda: AsyncMock()
    try:
        result = await client.post(
            "/api/v1/analysis/cases",
            data={
                "metadata": AnalysisCreate(
                    symbol="FPT", timeframe="D1", analyzed_at=datetime.now(UTC), thesis="breakout"
                ).model_dump_json()
            },
            files={"chart": ("chart.png", b"not image", "image/png")},
        )
        assert result.status_code == 400
    finally:
        app.dependency_overrides.clear()


async def test_content_length_limit(client):
    response = await client.post(
        "/api/v1/rag/documents", content=b"", headers={"Content-Length": "999999999"}
    )
    assert response.status_code == 413
    assert response.json()["error"]["request_id"]


async def test_ingestion_requires_payload_contract(client):
    app.dependency_overrides[get_rag_ingestion_service] = lambda: AsyncMock()
    try:
        response = await client.post(
            "/api/v1/rag/documents", data={"metadata": '{"kind":"wrong","source":"s"}'}
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()
