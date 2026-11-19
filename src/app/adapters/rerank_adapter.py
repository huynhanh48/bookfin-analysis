"""Index-preserving optional OpenRouter reranking for cases and LightRAG's hook."""

import asyncio
import math
from contextvars import ContextVar
from typing import Any, Protocol

from app.adapters.openrouter_adapter import OpenRouterAdapter
from app.core.exceptions import UpstreamError

rerank_events: ContextVar[list[dict] | None] = ContextVar("rerank_events", default=None)


class RerankError(UpstreamError):
    """A strict rerank failure must remain visible even if a library swallows callbacks."""


class Reranker(Protocol):
    """Return source indices and relevance, never reordered source objects without IDs."""

    async def rerank(self, query: str, documents: list[str], top_n: int) -> list[dict]: ...


class OpenRouterReranker:
    """Use official POST /rerank; validate indices, duplicates and relevance values."""

    def __init__(self, provider: OpenRouterAdapter):
        self.provider = provider

    async def rerank(self, query: str, documents: list[str], top_n: int) -> list[dict]:
        """Disabled mode performs no API calls; strict failures are decided by the caller."""
        settings = self.provider.settings
        if not settings.rag_rerank_enabled or not documents:
            return [
                {"index": i, "relevance_score": 1.0 / (i + 1)}
                for i in range(min(top_n, len(documents)))
            ]
        if not settings.openrouter_api_key:
            raise UpstreamError("OPENROUTER_API_KEY is required for reranking.")
        try:
            async with asyncio.timeout(settings.rag_rerank_timeout_seconds):
                response = await self.provider.client.post(
                    "/rerank",
                    cast_to=dict[str, Any],
                    body={
                        "model": settings.rag_rerank_model,
                        "query": query,
                        "documents": documents,
                        "top_n": min(top_n, len(documents)),
                        "return_documents": False,
                    },
                )
            results, seen = [], set()
            for row in response["results"]:
                index, score = row["index"], float(row["relevance_score"])
                if (
                    type(index) is not int
                    or not 0 <= index < len(documents)
                    or index in seen
                    or not math.isfinite(score)
                ):
                    raise ValueError("Invalid rerank result")
                seen.add(index)
                results.append({"index": index, "relevance_score": score})
            if not results:
                raise ValueError("Empty rerank results")
            return results[:top_n]
        except Exception as exc:
            raise RerankError("Reranking provider failed.") from exc

    async def hook(self, query: str, documents: list[str], top_n: int, **kwargs) -> list[dict]:
        """Installed LightRAG expects index/relevance_score dictionaries."""
        try:
            result = await self.rerank(query, documents, top_n)
            if (events := rerank_events.get()) is not None:
                events.append(
                    {
                        "enabled": self.provider.settings.rag_rerank_enabled,
                        "applied": self.provider.settings.rag_rerank_enabled,
                        "failed": False,
                        "model": self.provider.settings.rag_rerank_model,
                    }
                )
            return result
        except UpstreamError:
            if (events := rerank_events.get()) is not None:
                events.append(
                    {"failed": True, "fail_open": self.provider.settings.rag_rerank_fail_open}
                )
            if not self.provider.settings.rag_rerank_fail_open:
                raise
            return [
                {"index": i, "relevance_score": 1.0 / (i + 1)}
                for i in range(min(top_n, len(documents)))
            ]
