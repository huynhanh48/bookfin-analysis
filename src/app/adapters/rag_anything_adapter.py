"""Pinned RAG-Anything 1.4.4 / LightRAG 1.4.16 integration."""

import asyncio
import hashlib
import os
import time
from functools import wraps
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

from app.adapters.openrouter_adapter import OpenRouterAdapter, OpenRouterEmbeddingAdapter
from app.adapters.rag_adapter import RagAnswer
from app.core.config import Settings
from app.core.exceptions import ConfigurationError, UpstreamError
from app.core.logging import get_logger


def serialized(operation):
    """Serialize graph reads/writes within the single owning runtime."""

    @wraps(operation)
    async def wrapped(self, *args, **kwargs):
        async with self._operation_lock:
            return await operation(self, *args, **kwargs)

    return wrapped


class RagAnythingAdapter:
    """Lazy, serialized initialization with official PG storage and durable NetworkX.

    The pinned LightRAG registry lacks PGTableGraphStorage. PGGraphStorage needs
    Apache AGE, so this adapter intentionally uses NetworkXStorage on a volume.
    Use one RAG worker process per working directory.
    """

    def __init__(
        self,
        settings: Settings,
        llm: OpenRouterAdapter,
        embedding: OpenRouterEmbeddingAdapter,
        namespace: str = "knowledge",
    ):
        self.settings, self.llm, self.embedding = settings, llm, embedding
        self.namespace = namespace
        self.vision_cache: Any = None
        self.reranker: Any = None
        self._rag: Any = None
        self._lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Prepare one library instance; storage initialization remains library-owned."""
        async with self._lock:
            if self._rag is not None:
                return
            from app.adapters.rag_bootstrap import prepare_tokenizer

            await prepare_tokenizer(Path(self.settings.rag_storage_dir) / "tokenizer")

            def load_library():
                from lightrag import LightRAG
                from lightrag.kg import STORAGE_IMPLEMENTATIONS
                from lightrag.kg.shared_storage import initialize_pipeline_status
                from lightrag.utils import EmbeddingFunc, TiktokenTokenizer
                from raganything import RAGAnything, RAGAnythingConfig

                return (
                    LightRAG,
                    STORAGE_IMPLEMENTATIONS,
                    initialize_pipeline_status,
                    EmbeddingFunc,
                    RAGAnything,
                    RAGAnythingConfig,
                    TiktokenTokenizer(),
                )

            (
                LightRAG,
                STORAGE_IMPLEMENTATIONS,
                initialize_pipeline_status,
                EmbeddingFunc,
                RAGAnything,
                RAGAnythingConfig,
                tokenizer,
            ) = await asyncio.to_thread(load_library)

            if (
                "PGVectorStorage"
                not in STORAGE_IMPLEMENTATIONS["VECTOR_STORAGE"]["implementations"]
            ):
                raise ConfigurationError("Pinned LightRAG lacks required PostgreSQL storage.")
            url = make_url(self.settings.database_url)
            for key, value in {
                "POSTGRES_HOST": url.host or "localhost",
                "POSTGRES_PORT": str(url.port or 5432),
                "POSTGRES_USER": url.username or "",
                "POSTGRES_PASSWORD": url.password or "",
                "POSTGRES_DATABASE": url.database or "",
            }.items():
                os.environ[key] = value
            identity = hashlib.sha256(self.embedding.space.key.encode()).hexdigest()[:16]
            workspace = f"stock_{self.namespace}_{identity}"
            directory = Path(self.settings.rag_storage_dir) / workspace
            directory.mkdir(parents=True, exist_ok=True)

            # LightRAG uses dataclasses.asdict/deepcopy on callback fields.
            # Plain closures retain the shared clients without copying their locks.
            async def llm_callback(*args, **kwargs):
                return await self.llm.complete(*args, **kwargs)

            async def vision_callback(*args, **kwargs):
                from app.repositories.evidence_repository import content_key

                started = time.perf_counter()
                key = content_key(
                    "rag-vision-v1",
                    self.settings.rag_vision_model or self.settings.rag_llm_model,
                    args,
                    kwargs,
                )
                if self.vision_cache:
                    cached = await self.vision_cache.get(key)
                    if cached:
                        get_logger(__name__).info(
                            "rag_vision_callback",
                            extra={
                                "purpose": "vision",
                                "cache_hit": True,
                                "duration_seconds": time.perf_counter() - started,
                            },
                        )
                        return cached["response"]
                response = await self.llm.complete(*args, _vision=True, **kwargs)
                if self.vision_cache:
                    await self.vision_cache.put(key, {"response": response})
                get_logger(__name__).info(
                    "rag_vision_callback",
                    extra={
                        "purpose": "vision",
                        "cache_hit": False,
                        "duration_seconds": time.perf_counter() - started,
                    },
                )
                return response

            async def rerank_callback(*args, **kwargs):
                return await self.reranker.hook(*args, **kwargs)

            async def embedding_callback(*args, **kwargs):
                return await self.embedding.batch(*args, **kwargs)

            light = LightRAG(
                working_dir=str(directory),
                tokenizer=tokenizer,
                workspace=workspace,
                llm_model_func=llm_callback,
                embedding_func=EmbeddingFunc(
                    embedding_dim=self.embedding.space.dimension,
                    max_token_size=8192,
                    func=embedding_callback,
                    model_name=f"emb_{identity}",
                ),
                kv_storage="PGKVStorage",
                vector_storage="PGVectorStorage",
                doc_status_storage="PGDocStatusStorage",
                graph_storage="NetworkXStorage",
                llm_model_max_async=self.settings.rag_llm_max_async,
                embedding_func_max_async=self.settings.rag_embedding_max_async,
                max_parallel_insert=self.settings.rag_max_parallel_insert,
                # OpenRouter can be slower than the library's 30s default while
                # indexing many entity/relation chunks; keep a bounded but generous
                # timeout instead of failing an otherwise valid ingestion.
                default_embedding_timeout=120,
                rerank_model_func=rerank_callback
                if self.settings.rag_rerank_enabled and self.reranker
                else None,
            )
            started = time.perf_counter()
            try:
                await light.initialize_storages()
                await initialize_pipeline_status()
                self._rag = RAGAnything(
                    lightrag=light,
                    config=RAGAnythingConfig(
                        working_dir=str(directory), parser="mineru", display_content_stats=False
                    ),
                    llm_model_func=llm_callback,
                    vision_model_func=vision_callback,
                )
                get_logger(__name__).info(
                    "rag_initialize",
                    extra={
                        "workspace": workspace,
                        "duration_seconds": time.perf_counter() - started,
                    },
                )
                import atexit

                atexit.unregister(self._rag.close)
            except BaseException:
                await light.finalize_storages()
                raise

    @serialized
    async def _insert(self, document_id: str, contents: list[dict], source: str) -> None:
        await self.initialize()
        started = time.perf_counter()
        try:
            await self._rag.insert_content_list(
                content_list=contents, file_path=source, doc_id=document_id
            )
            insert_seconds = time.perf_counter() - started
            status_started = time.perf_counter()
            record = await self._rag.lightrag.doc_status.get_by_id(document_id)
            status_seconds = time.perf_counter() - status_started
            get_logger(__name__).info(
                "rag_insert",
                extra={
                    "document_id": document_id,
                    "content_types": [item.get("type") for item in contents],
                    "insert_seconds": insert_seconds,
                    "doc_status_seconds": status_seconds,
                },
            )
            if not record or record.get("status") != "processed":
                raise UpstreamError("RAG did not confirm document processing.")
        except Exception as exc:
            get_logger(__name__).warning(
                "rag_insert_failed",
                extra={
                    "document_id": document_id,
                    "duration_seconds": time.perf_counter() - started,
                    "error_type": type(exc).__name__,
                },
            )
            raise UpstreamError("Knowledge ingestion failed; the job can be retried.") from exc

    async def ingest_text(self, document_id: str, text: str, source: str) -> None:
        """Insert text using the public content-list API and deterministic ID."""
        await self._insert(document_id, [{"type": "text", "text": text, "page_idx": 0}], source)

    async def ingest_image(self, document_id: str, path: Path, text: str, source: str) -> None:
        """Pass original chart to RAG-Anything's image processor and remote vision."""
        await self._insert(
            document_id,
            [
                {"type": "text", "text": text or source, "page_idx": 0},
                {
                    "type": "image",
                    "img_path": str(path.resolve()),
                    "image_caption": [],
                    "image_footnote": [],
                    "page_idx": 0,
                },
            ],
            source,
        )

    @serialized
    async def ingest_document(self, document_id: str, path: Path, source: str) -> None:
        """Delegate PDF to remote MinerU; fail closed instead of loading local models."""
        if not self.settings.rag_remote_parser_url:
            raise ConfigurationError(
                "PDF requires RAG_REMOTE_PARSER_URL; local inference is disabled."
            )
        await self.initialize()
        try:
            await self._rag.process_document_complete(
                file_path=str(path.resolve()),
                doc_id=document_id,
                backend="vlm-http-client",
                vlm_url=self.settings.rag_remote_parser_url,
                output_dir=str(Path(self.settings.rag_storage_dir) / "parsed"),
            )
            record = await self._rag.lightrag.doc_status.get_by_id(document_id)
            if not record or record.get("status") != "processed":
                raise UpstreamError("RAG did not confirm document processing.")
        except Exception as exc:
            raise UpstreamError("Remote document parsing/indexing failed.") from exc

    async def _with_rerank_diagnostics(self, operation):
        """LightRAG catches reranker exceptions; enforce strict policy after its callback."""
        from app.adapters.rerank_adapter import RerankError, rerank_events

        events: list[dict] = []
        token = rerank_events.set(events)
        try:
            result = await operation
            if not self.settings.rag_rerank_fail_open and any(
                event.get("failed") for event in events
            ):
                raise RerankError("Knowledge reranking failed under strict policy.")
            return result, events
        finally:
            rerank_events.reset(token)

    @serialized
    async def query(self, question: str, mode: str = "hybrid") -> RagAnswer:
        """Use public structured LightRAG query API for answer plus real citations."""
        await self.initialize()
        if self._rag.lightrag is None:
            return RagAnswer("Knowledge index is empty.")
        from lightrag import QueryParam

        try:
            result, rerank_metadata = await self._with_rerank_diagnostics(
                self._rag.lightrag.aquery_llm(
                    question,
                    QueryParam(
                        mode=mode, stream=False, enable_rerank=self.settings.rag_rerank_enabled
                    ),
                )
            )
            if result.get("status") == "failure":
                raise UpstreamError("Knowledge retrieval failed.")
            return RagAnswer(
                result.get("llm_response", {}).get("content", ""),
                result.get("data", {}).get("references", []),
                {"knowledge_hooks": rerank_metadata} if rerank_metadata else {},
            )
        except Exception as exc:
            raise UpstreamError("Knowledge retrieval failed.") from exc

    @serialized
    async def retrieve(self, question: str, limit: int = 20) -> dict:
        """Public structured retrieval, without generating an answer before evidence filtering."""
        await self.initialize()
        from lightrag import QueryParam

        result, rerank_metadata = await self._with_rerank_diagnostics(
            self._rag.lightrag.aquery_data(
                question,
                QueryParam(
                    mode="hybrid",
                    top_k=limit,
                    chunk_top_k=limit,
                    enable_rerank=self.settings.rag_rerank_enabled,
                ),
            )
        )
        if result.get("status") == "failure":
            raise UpstreamError("Knowledge retrieval failed.")
        return {**result.get("data", {}), "_rerank_metadata": rerank_metadata}

    @serialized
    async def query_multimodal(self, question: str, path: Path) -> RagAnswer:
        """Verified public image query API; never inserts the supplied query image."""
        await self.initialize()
        result = await self._rag.aquery_with_multimodal(
            query=question,
            multimodal_content=[{"type": "image", "img_path": str(path.resolve())}],
            mode="hybrid",
        )
        return RagAnswer(result if isinstance(result, str) else str(result))

    @serialized
    async def delete_document(self, document_id: str) -> None:
        """Use official document deletion; callers must serialize with ingestion."""
        await self.initialize()
        if self._rag.lightrag is not None:
            result = await self._rag.lightrag.adelete_by_doc_id(document_id)
            if result.status != "success":
                raise UpstreamError("Knowledge deletion failed.")

    async def health_check(self) -> bool:
        """Return storage lifecycle status without calling a paid provider."""
        return self._rag is not None and self._rag.lightrag is not None

    @serialized
    async def close(self) -> None:
        """Finalize caches, graph and official PostgreSQL storages once."""
        if self._rag is not None:
            await self._rag.finalize_storages()
            self._rag = None
