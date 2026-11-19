"""OpenAI compatible async OpenRouter clients; no local model inference."""

import time
from typing import Any

import numpy as np
from openai import APIError, AsyncOpenAI

from app.adapters.embedding_provider import EmbeddingSpace
from app.core.config import Settings
from app.core.exceptions import ConfigurationError, UpstreamError
from app.core.logging import get_logger
from app.core.provider_metrics import record_provider_event


class OpenRouterAdapter:
    """Own the HTTP pool, bounded SDK retry/backoff, chat and vision callbacks."""

    def __init__(self, settings: Settings, client: AsyncOpenAI | None = None):
        self.settings = settings
        self.client = client or AsyncOpenAI(
            api_key=settings.openrouter_api_key or "not-configured",
            base_url=settings.openrouter_base_url,
            timeout=settings.openrouter_timeout_seconds,
            max_retries=settings.openrouter_max_retries,
        )

    async def complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        history_messages: list[dict] | None = None,
        image_data: str | None = None,
        messages: list[dict] | None = None,
        **kwargs: Any,
    ) -> str:
        """LightRAG/RAG-Anything callback; accept their vision message contract."""
        if not self.settings.openrouter_api_key:
            raise ConfigurationError("OPENROUTER_API_KEY is required when RAG is enabled.")
        if messages is None:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.extend(history_messages or [])
            content: Any = prompt
            if image_data:
                url = (
                    image_data
                    if image_data.startswith("data:")
                    else f"data:image/png;base64,{image_data}"
                )
                content = [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": url}},
                ]
            messages.append({"role": "user", "content": content})
        purpose = kwargs.get("purpose")
        if not purpose:
            purpose = (
                "vision"
                if kwargs.get("_vision")
                else "entity_extraction"
                if kwargs.get("keyword_extraction")
                else "llm"
            )
        model = (
            self.settings.rag_vision_model or self.settings.rag_llm_model
            if kwargs.get("_vision")
            else self.settings.rag_llm_model
        )
        started = time.perf_counter()
        try:
            result = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                **(
                    {"response_format": {"type": "json_object"}}
                    if kwargs.get("keyword_extraction") or kwargs.get("json_output")
                    else {}
                ),
            )
            usage = result.usage.model_dump() if result.usage else {}
            record_provider_event(
                provider="openrouter",
                model=result.model,
                purpose=purpose,
                duration_seconds=time.perf_counter() - started,
                status="ok",
                prompt_chars=sum(len(str(message.get("content", ""))) for message in messages),
                token_usage=usage,
                estimated_cost=usage.get("cost"),
            )
            get_logger(__name__).info(
                "openrouter_usage",
                extra={
                    "model": result.model,
                    "purpose": purpose,
                    "duration_seconds": time.perf_counter() - started,
                    "token_usage": usage,
                    "estimated_cost": usage.get("cost"),
                },
            )
            content = result.choices[0].message.content
            if content is None:
                raise UpstreamError("Provider returned an empty answer.")
            return content
        except APIError as exc:
            record_provider_event(
                provider="openrouter",
                model=model,
                purpose=purpose,
                duration_seconds=time.perf_counter() - started,
                status="error",
                prompt_chars=sum(len(str(message.get("content", ""))) for message in messages),
                error_type=type(exc).__name__,
            )
            raise UpstreamError("OpenRouter chat request failed.") from exc

    async def close(self) -> None:
        """Release the shared asynchronous HTTP client."""
        await self.client.close()


class OpenRouterEmbeddingAdapter:
    """One configured vector space; never silently fall back to another model."""

    def __init__(
        self, provider: OpenRouterAdapter, space: EmbeddingSpace, multimodal: bool = False
    ):
        self.provider, self.space, self.multimodal = provider, space, multimodal

    async def validate_model(self) -> None:
        """Verify model availability and declared input modalities from live catalog."""
        try:
            catalog = await self.provider.client.get("/embeddings/models", cast_to=dict[str, Any])
            model = next((m for m in catalog["data"] if m["id"] == self.space.model), None)
            if model is None or (
                self.multimodal and "image" not in model["architecture"]["input_modalities"]
            ):
                raise ConfigurationError("Embedding model unavailable or missing image support.")
        except APIError as exc:
            raise UpstreamError("Embedding catalog unavailable.") from exc

    async def embed(self, text: str, image: str | None = None) -> list[float]:
        """Use documented content-array format for joint text/image embeddings."""
        if not self.provider.settings.openrouter_api_key:
            raise ConfigurationError("OPENROUTER_API_KEY is required.")
        if image and not self.multimodal:
            raise ConfigurationError("This embedding space does not accept images.")
        inputs: Any = [text]
        if image:
            inputs = [
                {
                    "content": [
                        {"type": "text", "text": text},
                        {"type": "image_url", "image_url": {"url": image}},
                    ]
                }
            ]
        started = time.perf_counter()
        try:
            result = await self.provider.client.post(
                "/embeddings",
                cast_to=dict[str, Any],
                body={
                    "model": self.space.model,
                    "input": inputs,
                    "encoding_format": "float",
                },
            )
            usage = result.get("usage", {})
            record_provider_event(
                provider="openrouter",
                model=self.space.model,
                purpose="multimodal_embedding" if image else "embedding",
                duration_seconds=time.perf_counter() - started,
                status="ok",
                batch_size=1,
                token_usage=usage,
                estimated_cost=usage.get("cost"),
            )
            get_logger(__name__).info(
                "embedding_usage",
                extra={
                    "model": self.space.model,
                    "purpose": "multimodal_embedding" if image else "embedding",
                    "duration_seconds": time.perf_counter() - started,
                    "token_usage": usage,
                    "estimated_cost": usage.get("cost"),
                },
            )
            return self.space.validate([float(v) for v in result["data"][0]["embedding"]])
        except (APIError, KeyError, TypeError, ValueError, IndexError) as exc:
            record_provider_event(
                provider="openrouter",
                model=self.space.model,
                purpose="multimodal_embedding" if image else "embedding",
                duration_seconds=time.perf_counter() - started,
                status="error",
                batch_size=1,
                error_type=type(exc).__name__,
            )
            raise UpstreamError(
                "OpenRouter embedding request failed or returned invalid data."
            ) from exc

    async def batch(self, texts: list[str], **kwargs: Any) -> np.ndarray:
        """LightRAG embedding callback using one request per bounded text batch.

        LightRAG applies a worker timeout around this callback. Sending one text per
        request (the previous behaviour) can exceed that timeout while indexing an
        image/PDF with many chunks, so send an input array and validate every vector.
        """
        if not self.provider.settings.openrouter_api_key:
            raise ConfigurationError("OPENROUTER_API_KEY is required.")
        if self.multimodal:
            raise ConfigurationError("Multimodal spaces do not support text batch indexing.")
        items = [str(item) for item in texts]
        if not items:
            return np.empty((0, self.space.dimension), dtype=np.float32)

        vectors: list[list[float]] = []
        # LightRAG defaults to 10 texts per batch; keep a larger bound for other callers.
        for start in range(0, len(items), 32):
            chunk = items[start : start + 32]
            started = time.perf_counter()
            try:
                result = await self.provider.client.post(
                    "/embeddings",
                    cast_to=dict[str, Any],
                    body={
                        "model": self.space.model,
                        "input": chunk,
                        "encoding_format": "float",
                    },
                )
                rows = result["data"]
                if len(rows) != len(chunk):
                    raise ValueError("Embedding count does not match input count")
                indexed = []
                for position, row in enumerate(rows):
                    index = row.get("index", position)
                    if not isinstance(index, int) or not 0 <= index < len(chunk):
                        raise ValueError("Invalid embedding index")
                    indexed.append((index, row))
                indexed.sort(key=lambda item: item[0])
                if [index for index, _ in indexed] != list(range(len(chunk))):
                    raise ValueError("Duplicate or missing embedding index")
                vectors.extend(
                    self.space.validate([float(value) for value in row["embedding"]])
                    for _, row in indexed
                )
                usage = result.get("usage", {})
                record_provider_event(
                    provider="openrouter",
                    model=self.space.model,
                    purpose="embedding_batch",
                    duration_seconds=time.perf_counter() - started,
                    status="ok",
                    batch_size=len(chunk),
                    token_usage=usage,
                    estimated_cost=usage.get("cost"),
                )
                get_logger(__name__).info(
                    "embedding_usage",
                    extra={
                        "model": self.space.model,
                        "purpose": "embedding_batch",
                        "duration_seconds": time.perf_counter() - started,
                        "token_usage": usage,
                        "estimated_cost": usage.get("cost"),
                        "batch_size": len(chunk),
                    },
                )
            except (APIError, KeyError, TypeError, ValueError, IndexError) as exc:
                record_provider_event(
                    provider="openrouter",
                    model=self.space.model,
                    purpose="embedding_batch",
                    duration_seconds=time.perf_counter() - started,
                    status="error",
                    batch_size=len(chunk),
                    error_type=type(exc).__name__,
                )
                raise UpstreamError(
                    "OpenRouter batch embedding request failed or returned invalid data."
                ) from exc
        return np.asarray(vectors, dtype=np.float32)
