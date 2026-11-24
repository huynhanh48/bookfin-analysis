"""Application-owned RAG resources and FastAPI dependency injection."""

from typing import Annotated

from fastapi import Depends, Request

from app.adapters.embedding_provider import EmbeddingSpace
from app.adapters.file_storage import LocalFileStorage
from app.adapters.openrouter_adapter import OpenRouterAdapter, OpenRouterEmbeddingAdapter
from app.adapters.rag_anything_adapter import RagAnythingAdapter
from app.adapters.redis_memory_store import RedisMemoryStore
from app.core.config import Settings, get_settings
from app.core.dependencies import DbSession
from app.core.exceptions import AppError
from app.core.redis import get_redis_client
from app.repositories.analysis_repository import AnalysisRepository
from app.repositories.rag_repository import RagRepository
from app.services.analysis_memory_service import AnalysisMemoryService
from app.services.rag_ingestion_service import RagIngestionService
from app.services.rag_service import RagService


class RagUnavailableError(AppError):
    """Optional module is disabled without affecting market data endpoints."""

    status_code = 503
    code = "rag_unavailable"


class RagRuntime:
    """Own shared clients and two isolated knowledge/case graph namespaces."""

    def __init__(self, settings: Settings):
        import fcntl
        from pathlib import Path

        directory = Path(settings.rag_storage_dir)
        directory.mkdir(parents=True, exist_ok=True)
        self._file_lock = (directory / "runtime.lock").open("a")
        try:
            fcntl.flock(self._file_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._file_lock.close()
            raise RagUnavailableError(
                "RAG requires one owning process per persistent volume."
            ) from exc
        self.worker_task = None
        self.worker_error: str | None = None
        self.settings = settings
        self.provider = OpenRouterAdapter(settings)
        self.text_embedding = OpenRouterEmbeddingAdapter(
            self.provider,
            EmbeddingSpace(
                settings.rag_text_embedding_model,
                settings.rag_text_embedding_dimension,
                "knowledge-v1",
            ),
        )
        multimodal = settings.rag_case_multimodal
        self.case_embedding = OpenRouterEmbeddingAdapter(
            self.provider,
            EmbeddingSpace(
                settings.rag_multimodal_embedding_model
                if multimodal
                else settings.rag_text_embedding_model,
                settings.rag_multimodal_embedding_dimension
                if multimodal
                else settings.rag_text_embedding_dimension,
            ),
            multimodal,
        )
        import asyncio

        from app.adapters.firecrawl_adapter import FirecrawlExtractionAdapter
        from app.adapters.rerank_adapter import OpenRouterReranker
        from app.adapters.rss_news_adapter import RssNewsAdapter
        from app.adapters.tavily_adapter import TavilySearchAdapter
        from app.adapters.technical_evidence_adapter import TechnicalEvidenceAdapter
        from app.adapters.vision_adapter import CachedVisionExtractor, OpenRouterVisionExtractor
        from app.repositories.evidence_repository import PostgresEvidenceCache
        from app.services.news_enrichment_service import NewsEnrichmentService

        self.provider_semaphore = asyncio.Semaphore(settings.rag_provider_concurrency)
        self.evidence_cache = PostgresEvidenceCache(settings.rag_provider_cache_seconds)
        self.vision = CachedVisionExtractor(
            OpenRouterVisionExtractor(self.provider), self.evidence_cache
        )
        self.technical = TechnicalEvidenceAdapter(settings, cache=self.evidence_cache)
        self.reranker = OpenRouterReranker(self.provider)
        self.case_text_embedding = OpenRouterEmbeddingAdapter(
            self.provider,
            EmbeddingSpace(
                settings.rag_text_embedding_model,
                settings.rag_text_embedding_dimension,
                "case-text-v2",
            ),
        )
        self.case_multimodal_embedding = OpenRouterEmbeddingAdapter(
            self.provider,
            EmbeddingSpace(
                settings.rag_multimodal_embedding_model,
                settings.rag_multimodal_embedding_dimension,
                "case-image-v2",
            ),
            True,
        )
        primary = (
            TavilySearchAdapter(settings)
            if settings.rag_news_enabled and settings.tavily_api_key
            else None
        )
        fallback = (
            FirecrawlExtractionAdapter(settings)
            if settings.rag_news_enabled and settings.firecrawl_api_key
            else None
        )
        rss = (
            RssNewsAdapter(settings)
            if settings.rag_news_enabled and settings.rag_news_rss_feeds
            else None
        )
        self.news_clients = [client for client in (primary, fallback, rss) if client is not None]
        self.news = NewsEnrichmentService(settings, primary, fallback, self.evidence_cache, rss=rss)
        redis = get_redis_client() if settings.rag_redis_mirror_enabled else None
        self.mirror = RedisMemoryStore(redis) if redis is not None else None
        self.storage = LocalFileStorage(settings.rag_upload_dir)
        self.knowledge = RagAnythingAdapter(settings, self.provider, self.text_embedding)
        self.cases = RagAnythingAdapter(
            settings,
            self.provider,
            self.text_embedding,
            "cases_evidence_v1" if settings.rag_enrichment_enabled else "cases",
        )
        for adapter in (self.knowledge, self.cases):
            adapter.vision_cache = self.evidence_cache if settings.rag_enrichment_enabled else None
            adapter.reranker = self.reranker

    async def close(self) -> None:
        """Finalize both graphs before closing the HTTP pool, even if one cleanup fails."""
        try:
            try:
                await self.knowledge.close()
            finally:
                await self.cases.close()
        finally:
            try:
                for client in self.news_clients:
                    await client.close()
            finally:
                await self.provider.close()
                self._file_lock.close()


def get_rag_runtime(request: Request) -> RagRuntime:
    """Provide a shared runtime, keeping disabled deployments independent of RAG."""
    if not get_settings().rag_enabled:
        raise RagUnavailableError("Enable RAG_ENABLED to use this module.")
    runtime = getattr(request.app.state, "rag_runtime", None)
    if runtime is None:
        runtime = RagRuntime(get_settings())
        request.app.state.rag_runtime = runtime
    return runtime


RuntimeDep = Annotated[RagRuntime, Depends(get_rag_runtime)]


def get_analysis_service(session: DbSession, runtime: RuntimeDep) -> AnalysisMemoryService:
    """Wire request-scoped repositories to shared remote providers."""
    return AnalysisMemoryService(
        AnalysisRepository(session),
        RagRepository(session),
        runtime.case_embedding,
        runtime.storage,
        runtime.mirror,
        enrichment_enabled=runtime.settings.rag_enrichment_enabled,
    )


def get_rag_ingestion_service(session: DbSession, runtime: RuntimeDep) -> RagIngestionService:
    """Wire durable source ingestion without invoking any provider in the router."""
    return RagIngestionService(
        RagRepository(session), runtime.storage, runtime.mirror, runtime.knowledge
    )


def get_rag_service(runtime: RuntimeDep) -> RagService:
    """Provide knowledge retrieval with its own graph namespace."""
    return RagService(runtime.knowledge)


AnalysisServiceDep = Annotated[AnalysisMemoryService, Depends(get_analysis_service)]
RagIngestionDep = Annotated[RagIngestionService, Depends(get_rag_ingestion_service)]
RagServiceDep = Annotated[RagService, Depends(get_rag_service)]


def get_query_service(session: DbSession, runtime: RuntimeDep):
    """Bind hybrid retrieval to the request SQL session and shared providers."""
    from app.services.rag_query_service import RagQueryService

    return RagQueryService(runtime, session)


RagQueryDep = Annotated[object, Depends(get_query_service)]
