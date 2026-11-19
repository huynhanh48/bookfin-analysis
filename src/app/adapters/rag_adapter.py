"""Knowledge-index contract independent of RAG-Anything."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class RagAnswer:
    """Answer and backend references, without inventing unsupported citations."""

    answer: str
    sources: list[dict] = field(default_factory=list)
    rerank_metadata: dict = field(default_factory=dict)


class RAGAdapter(Protocol):
    """Lifecycle and indexing operations used by business services."""

    async def ingest_text(self, document_id: str, text: str, source: str) -> None:
        """Index text under a stable application document ID."""
        ...

    async def ingest_image(self, document_id: str, path: Path, text: str, source: str) -> None:
        """Index an original chart through remote vision."""
        ...

    async def ingest_document(self, document_id: str, path: Path, source: str) -> None:
        """Delegate document parsing to the library's remote parser."""
        ...

    async def query(self, question: str, mode: str = "hybrid") -> RagAnswer:
        """Retrieve knowledge and answer using the selected library retrieval mode."""
        ...

    async def retrieve(self, question: str, limit: int = 20) -> dict:
        """Retrieve source-bearing chunks without generating an unfiltered answer."""
        ...

    async def query_multimodal(self, question: str, path: Path) -> RagAnswer:
        """Query with an ephemeral original image through the library public API."""
        ...

    async def delete_document(self, document_id: str) -> None:
        """Delete a document using the library's supported deletion API."""
        ...

    async def health_check(self) -> bool:
        """Report whether the adapter was initialized successfully."""
        ...

    async def close(self) -> None:
        """Flush and finalize adapter storage."""
        ...
