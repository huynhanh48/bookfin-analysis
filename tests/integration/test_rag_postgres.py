"""Real PostgreSQL/pgvector tests, opt-in with RAG_TEST_DATABASE_URL.

Use a disposable migrated database only. No OpenRouter calls are made.
"""

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.adapters.embedding_provider import EmbeddingSpace
from app.adapters.file_storage import LocalFileStorage
from app.models.analysis_case import AnalysisCase
from app.repositories.analysis_repository import AnalysisRepository
from app.repositories.rag_repository import RagRepository
from app.schemas.analysis import AnalysisCreate, AnalysisSearch
from app.schemas.rag import DocumentCreate
from app.services.analysis_memory_service import AnalysisMemoryService
from app.services.rag_ingestion_service import RagIngestionService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("RAG_TEST_DATABASE_URL"), reason="Set disposable RAG_TEST_DATABASE_URL"
    ),
]


@pytest.fixture
async def sessions():
    """Use a separate test database with committed transactions and module-only cleanup."""
    from sqlalchemy.engine import make_url

    database = make_url(os.environ["RAG_TEST_DATABASE_URL"]).database or ""
    if not database.endswith("_test"):
        pytest.skip("Use an explicitly disposable database whose name ends with _test")
    engine = create_async_engine(os.environ["RAG_TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE rag_documents, analysis_case_embeddings, analysis_cases, news_documents, evidence_provider_cache CASCADE"
            )
        )
    await engine.dispose()


async def test_migration_and_cosine_indexes(sessions):
    async with sessions() as session:
        assert await session.scalar(
            text("SELECT extversion FROM pg_extension WHERE extname='vector'")
        )
        assert (
            await session.scalar(text("SELECT version_num FROM alembic_version")) == "d3e4f5a6b7c8"
        )
        indexes = list(
            (
                await session.execute(
                    text(
                        "SELECT indexdef FROM pg_indexes WHERE tablename='analysis_case_embeddings'"
                    )
                )
            ).scalars()
        )
        assert any(
            "hnsw" in definition and "vector_cosine_ops" in definition for definition in indexes
        )


async def test_case_roundtrip_idempotency_outcome_and_model_isolation(sessions, tmp_path):
    space = EmbeddingSpace("mock/model", 3)
    embedding = SimpleNamespace(space=space, embed=AsyncMock(return_value=[1.0, 0.0, 0.0]))
    storage = LocalFileStorage(str(tmp_path))
    payload = AnalysisCreate(
        symbol="FPT",
        timeframe="D1",
        analyzed_at=datetime.now(UTC) - timedelta(days=30),
        thesis="HH HL volume breakout",
        indicator_snapshot={"RSI": 58, "ADX": 27, "volume_ratio": 1.8},
        market_context={"structure": ["HH", "HL"]},
        source="TradingView example",
    )
    async with sessions() as session:
        repo = AnalysisRepository(session)
        service = AnalysisMemoryService(repo, RagRepository(session), embedding, storage)
        case = await service.ingest_analysis_case(payload, (b"original-chart-fixture", ".png"))
        duplicate = await service.ingest_analysis_case(payload, (b"original-chart-fixture", ".png"))
        assert duplicate.id == case.id
        assert await storage.read(case.chart_path) == b"original-chart-fixture"
        case.ingest_status = "COMPLETED"
        await repo.save_embedding(case.id, space, [1.0, 0.0, 0.0])
        await repo.save_embedding(case.id, EmbeddingSpace("different/model", 3), [0.0, 1.0, 0.0])
        await session.commit()
        before_text = case.thesis
        await service.update_analysis_outcome(case.id, {"5_sessions": {"return_pct": 2.1}})
        await service.update_analysis_outcome(case.id, {"10_sessions": {"return_pct": 3.0}})
        assert case.thesis == before_text
        assert set(case.outcome) == {"5_sessions", "10_sessions"}
        matches = await service.search_similar_analysis_cases(
            AnalysisSearch(query="breakout", symbol="FPT", timeframe="D1", before=datetime.now(UTC))
        )
        assert matches[0].similarity == pytest.approx(1.0)
        assert "outcome" not in matches[0].model_dump()["case"]
        assert not await repo.search(
            [1.0, 0.0, 0.0], EmbeddingSpace("absent", 3), AnalysisSearch(query="q")
        )
        assert not await repo.search(
            [1.0, 0.0, 0.0], space, AnalysisSearch(query="q", before=payload.analyzed_at)
        )
        assert not await repo.search(
            [1.0, 0.0, 0.0], space, AnalysisSearch(query="q", symbol="VCB")
        )


async def test_job_idempotency_claim_recovery_retry(sessions, tmp_path):
    async with sessions() as session:
        repo = RagRepository(session)
        service = RagIngestionService(repo, LocalFileStorage(str(tmp_path)))
        payload = DocumentCreate(kind="text", source="unit-source", text="stock research knowledge")
        first = await service.ingest(payload)
        second = await service.ingest(payload)
        assert first.id == second.id
        claimed = await repo.claim(30)
        assert claimed.status == "PROCESSING"
        assert await repo.claim(30) is None
        claimed.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
        recovered = await repo.claim(30)
        assert recovered.id == first.id
        assert recovered.attempts == 2
        recovered.status = "FAILED"
        await session.commit()
        assert (await service.retry(first.id)).status == "PENDING"


async def test_worker_persists_vector_status_and_recovers_provider_failure(
    sessions, tmp_path, monkeypatch
):
    from app.workers import rag_worker

    monkeypatch.setattr(rag_worker, "SessionFactory", sessions)
    space = EmbeddingSpace("test/model", 3)
    embedding = SimpleNamespace(
        space=space, multimodal=False, embed=AsyncMock(return_value=[1.0, 0.0, 0.0])
    )
    storage = LocalFileStorage(str(tmp_path))
    adapter = SimpleNamespace(
        ingest_text=AsyncMock(), ingest_image=AsyncMock(), ingest_document=AsyncMock()
    )
    runtime = SimpleNamespace(
        settings=SimpleNamespace(rag_job_timeout_seconds=30),
        case_embedding=embedding,
        storage=storage,
        cases=adapter,
        knowledge=adapter,
    )
    async with sessions() as session:
        service = AnalysisMemoryService(
            AnalysisRepository(session), RagRepository(session), embedding, storage
        )
        case = await service.ingest_analysis_case(
            AnalysisCreate(
                symbol="FPT", timeframe="D1", analyzed_at=datetime.now(UTC), thesis="breakout"
            )
        )
        case_id = case.id
    embedding.embed.side_effect = RuntimeError("provider unavailable")
    assert await rag_worker.process_one(runtime)
    async with sessions() as session:
        case = await session.get(AnalysisCase, case_id)
        assert case.ingest_status == "FAILED"
        job_id = await session.scalar(
            text("SELECT id FROM rag_documents WHERE case_id=:id"), {"id": case_id}
        )
        await RagRepository(session).retry(job_id)
        await session.commit()
    embedding.embed.side_effect = None
    assert await rag_worker.process_one(runtime)
    async with sessions() as session:
        assert (await session.get(AnalysisCase, case_id)).ingest_status == "COMPLETED"
        assert await session.scalar(text("SELECT count(*) FROM analysis_case_embeddings")) == 1
        assert (await RagRepository(session).get(job_id)).status == "COMPLETED"
    adapter.ingest_text.assert_awaited_once()


async def test_api_upload_search_and_outcome(sessions, tmp_path, client, monkeypatch):
    import io

    from PIL import Image

    from app.core.config import get_settings
    from app.core.dependencies import get_db_session
    from app.core.rag_runtime import get_analysis_service, get_rag_runtime
    from app.main import app

    # This test exercises the legacy pgvector search path; the host .env may enable
    # enrichment for real usage, so pin the setting for deterministic integration runs.
    monkeypatch.setattr(get_settings(), "rag_enrichment_enabled", False)

    storage = LocalFileStorage(str(tmp_path))
    space = EmbeddingSpace("api/model", 3)
    embedding = SimpleNamespace(space=space, embed=AsyncMock(return_value=[1.0, 0.0, 0.0]))

    async def session_dep():
        async with sessions() as session:
            yield session

    def service_dep(session):
        return AnalysisMemoryService(
            AnalysisRepository(session), RagRepository(session), embedding, storage
        )

    from fastapi import Depends

    session_dependency = Depends(session_dep)

    def injected_service(session=session_dependency):
        return service_dep(session)

    app.dependency_overrides[get_analysis_service] = injected_service
    app.dependency_overrides[get_rag_runtime] = lambda: SimpleNamespace(storage=storage)
    app.dependency_overrides[get_db_session] = session_dep
    try:
        image = io.BytesIO()
        Image.new("RGB", (20, 20)).save(image, "PNG")
        payload = AnalysisCreate(
            symbol="FPT", timeframe="D1", analyzed_at=datetime.now(UTC), thesis="HH HL breakout"
        )
        response = await client.post(
            "/api/v1/analysis/cases",
            data={"metadata": payload.model_dump_json()},
            files={"chart": ("chart.png", image.getvalue(), "image/png")},
        )
        assert response.status_code == 202, response.text
        case_id = response.json()["data"]["id"]
        assert (
            await client.get(f"/api/v1/analysis/cases/{case_id}/chart")
        ).content == image.getvalue()
        async with sessions() as session:
            case = await session.get(AnalysisCase, __import__("uuid").UUID(case_id))
            case.ingest_status = "COMPLETED"
            await AnalysisRepository(session).save_embedding(case.id, space, [1.0, 0.0, 0.0])
            await session.commit()
        response = await client.patch(
            f"/api/v1/analysis/cases/{case_id}/outcome", json={"outcome": {"5_sessions": 2.0}}
        )
        assert response.status_code == 200
        assert response.json()["data"]["thesis"] == payload.thesis
        result = await client.post("/api/v1/analysis/search", json={"query": "HH HL"})
        assert result.status_code == 200
        assert result.json()["data"][0]["similarity"] == pytest.approx(1.0)
        assert "outcome" not in result.json()["data"][0]["case"]
    finally:
        app.dependency_overrides.clear()


async def test_real_library_pg_storage_and_text_ingestion(sessions, tmp_path, monkeypatch):
    """Exercise installed library/storage APIs with deterministic provider doubles."""
    import sys
    from pathlib import Path

    monkeypatch.setenv(
        "PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    )
    import numpy as np

    from app.adapters.rag_anything_adapter import RagAnythingAdapter
    from app.core.config import Settings

    async def complete(*args, **kwargs):
        return "entity<|#|>FPT<|#|>organization<|#|>Example company\n<|COMPLETE|>"

    async def batch(texts, **kwargs):
        return np.asarray([[1.0, 0.0, 0.0] for _ in texts], dtype=np.float32)

    embedding = SimpleNamespace(space=EmbeddingSpace("test/library", 3), batch=batch)
    settings = Settings(
        _env_file=None,
        database_url=os.environ["RAG_TEST_DATABASE_URL"],
        rag_storage_dir=str(tmp_path),
    )
    adapter = RagAnythingAdapter(
        settings, SimpleNamespace(complete=complete), embedding, namespace="test"
    )
    document_id = str(uuid4())
    try:
        await adapter.initialize()
        first = adapter._rag
        await adapter.initialize()
        assert adapter._rag is first
        assert await adapter.health_check()
        await adapter.ingest_text(
            document_id,
            "FPT is an example company used for a historical breakout case.",
            "example-source",
        )
        assert (await adapter._rag.lightrag.doc_status.get_by_id(document_id))[
            "status"
        ] == "processed"
    finally:
        await adapter.close()


async def test_alembic_roundtrip(sessions):
    """Apply real down/up migrations on an explicitly disposable test database."""
    import asyncio
    import sys

    env = {
        **os.environ,
        "DATABASE_URL": os.environ["RAG_TEST_DATABASE_URL"],
        "RAG_ENABLED": "false",
    }
    for arguments in (("downgrade", "bb14e84dd2de"), ("upgrade", "head")):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "alembic",
            *arguments,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        assert process.returncode == 0, (stdout + stderr).decode()
    async with sessions() as session:
        assert (
            await session.scalar(text("SELECT version_num FROM alembic_version")) == "d3e4f5a6b7c8"
        )


async def test_evidence_dual_vectors_exact_filters_and_provenance(sessions):
    from sqlalchemy import func, select

    from app.models.analysis_evidence import NewsDocument
    from app.repositories.evidence_repository import EvidenceRepository
    from app.schemas.evidence import NewsEvidence, TechnicalClaim

    case_id = uuid4()
    async with sessions() as session:
        case = AnalysisCase(
            id=case_id,
            symbol="KDH",
            timeframe="1D",
            analyzed_at=datetime(2026, 1, 15, tzinfo=UTC),
            thesis="Original unmodified thesis",
            chart_path="original.png",
            indicator_snapshot={"user_claimed_rsi": 99},
            market_context={},
            conclusion="",
            outcome={"future_return": 999},
            source="synthetic integration",
            ingest_status="COMPLETED",
        )
        repo = AnalysisRepository(session)
        await repo.add(case)
        evidence = EvidenceRepository(session)
        state = await evidence.ensure_state(case.id, {"thesis": case.thesis})
        state.normalized_text = "KDH RSI bullish divergence verified on confirmed pivots"
        state.technical_snapshot = {"status": "ok", "filter_values": {"rsi": 58, "rvol": 1.8}}
        c = TechnicalClaim(
            claim_type="volume_increase",
            original_text="volume increased",
            verification_status="VERIFIED",
            evidence_ids=["technical:test"],
        )
        await evidence.save_claims(case.id, [c])
        text_space = EmbeddingSpace("mock/text", 3, "case-text-v2")
        image_space = EmbeddingSpace("mock/image", 4, "case-image-v2")
        await repo.save_embedding(
            case.id, text_space, [1, 0, 0], kind="text", content_hash="a" * 64
        )
        await repo.save_embedding(
            case.id, image_space, [0, 1, 0, 0], kind="multimodal", image_hash="b" * 64
        )
        article = NewsEvidence(
            source_url="https://hnx.vn/synthetic",
            canonical_url="https://hnx.vn/synthetic",
            title="Synthetic KDH",
            publisher="hnx.vn",
            published_at=datetime(2026, 1, 14, tzinfo=UTC),
            updated_at=datetime(2026, 1, 14, tzinfo=UTC),
            retrieved_at=datetime.now(UTC),
            content="Synthetic fixture, not financial advice",
            content_hash="c" * 64,
            relevance=0.9,
            associated_symbol="KDH",
            source_type="official",
        )
        await evidence.link_news(case.id, [article])
        await session.commit()
        await evidence.link_news(case.id, [article])
        await session.commit()
        assert await session.scalar(select(func.count()).select_from(NewsDocument)) == 1
        assert len(await evidence.sources(case.id)) == 1
        assert (await evidence.claims(case.id))[0]["verification_status"] == "VERIFIED"
        query = AnalysisSearch(
            query="bullish",
            symbol="KDH",
            timeframe="D1",
            conditions=[{"field": "rsi", "operator": "gte", "value": 58}],
        )
        text_hits = await repo.search([1, 0, 0], text_space, query)
        image_hits = await repo.search([0, 1, 0, 0], image_space, query)
        assert text_hits[0][0].id == image_hits[0][0].id == case.id
        assert text_hits[0][1] == pytest.approx(1)
        assert not await repo.search(
            [1, 0, 0],
            text_space,
            query.model_copy(update={"before": datetime(2025, 1, 1, tzinfo=UTC)}),
        )
        impossible = AnalysisSearch(
            query="bullish", conditions=[{"field": "rsi", "operator": "gte", "value": 90}]
        )
        assert not await repo.search([1, 0, 0], text_space, impossible)
        assert "future_return" not in state.normalized_text
        assert case.thesis == "Original unmodified thesis"


async def test_provider_cache_and_model_specific_indexes(sessions):
    from app.repositories.evidence_repository import PostgresEvidenceCache

    cache = PostgresEvidenceCache(60, sessions)
    key = "d" * 64
    await cache.put(key, {"observations": "typed-cache"})
    assert await cache.get(key) == {"observations": "typed-cache"}
    async with sessions() as session:
        indexes = (
            (
                await session.execute(
                    text(
                        "SELECT indexdef FROM pg_indexes WHERE tablename='analysis_case_embeddings'"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert sum("hnsw" in index for index in indexes) == 4
        assert all(
            "space" in index and "representation_kind" in index
            for index in indexes
            if "hnsw" in index
        )
        await session.execute(
            text(
                "UPDATE evidence_provider_cache SET expires_at = now() - interval '1 second' WHERE key = :key"
            ),
            {"key": key},
        )
        await session.commit()
    assert await cache.get(key) is None
