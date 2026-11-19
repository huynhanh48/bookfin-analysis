"""Embedding contracts shared by memory services and knowledge adapters."""

import math
from dataclasses import dataclass
from typing import Protocol

from app.core.exceptions import UpstreamError


@dataclass(frozen=True)
class EmbeddingSpace:
    """An immutable retrieval space; changing version requires re-embedding."""

    model: str
    dimension: int
    version: str = "case-v1"

    @property
    def key(self) -> str:
        """Stable identity used to isolate models and representation versions."""
        return f"{self.model}:{self.dimension}:{self.version}"

    def validate(self, vector: list[float]) -> list[float]:
        """Reject malformed, zero or nonfinite provider vectors before persistence."""
        if len(vector) != self.dimension or not all(math.isfinite(v) for v in vector):
            raise UpstreamError("Embedding dimension or numeric values are invalid.")
        if not any(vector):
            raise UpstreamError("Embedding must have nonzero norm.")
        return vector


class EmbeddingProvider(Protocol):
    """Remote embedding provider; image is an optional validated data URL."""

    space: EmbeddingSpace

    async def embed(self, text: str, image: str | None = None) -> list[float]:
        """Return one validated joint vector in this provider's space."""
        ...
