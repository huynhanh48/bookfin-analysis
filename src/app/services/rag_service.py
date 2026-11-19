"""Knowledge retrieval usable by FastAPI or future LangGraph tool wrappers."""

from app.adapters.rag_adapter import RAGAdapter, RagAnswer


class RagService:
    """Keep knowledge retrieval separate from financial indicator calculations."""

    def __init__(self, adapter: RAGAdapter):
        self.adapter = adapter

    async def retrieve_financial_knowledge(self, question: str, mode: str = "hybrid") -> RagAnswer:
        """Return library-backed context and answer; case outcomes are in a separate namespace."""
        return await self.adapter.query(question, mode)
