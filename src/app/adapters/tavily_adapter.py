"""Verified AsyncTavilyClient 0.8.5 Search/Extract adapter."""

from datetime import datetime, timedelta
from typing import Any

from app.core.config import Settings


class TavilySearchAdapter:
    """Use bounded historical search and fetch raw content only for shortlisted URLs."""

    def __init__(self, settings: Settings, client: Any = None):
        from tavily import AsyncTavilyClient

        self.settings = settings
        self.client = client or AsyncTavilyClient(api_key=settings.tavily_api_key)

    async def search(
        self,
        query: str,
        analysis_at: datetime,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Date limits narrow retrieval; the service still enforces exact as-of timestamps."""
        start = start_at or analysis_at - timedelta(days=self.settings.rag_news_lookback_days)
        end = end_at or analysis_at
        result = await self.client.search(
            query=query,
            topic="news",
            search_depth="advanced",
            start_date=start.date().isoformat(),
            end_date=end.date().isoformat(),
            max_results=min(20, self.settings.rag_news_max_sources * 2),
            include_domains=[
                "hsx.vn",
                "hnx.vn",
                "ssc.gov.vn",
                *self.settings.rag_news_company_domains,
            ],
            include_domains_mode="prefer",
            include_answer=False,
            include_raw_content=False,
            timeout=self.settings.rag_news_timeout_seconds,
            include_usage=True,
        )
        return result.get("results", [])

    async def extract(self, url: str) -> dict[str, Any]:
        """Tavily Extract is the first content extraction strategy."""
        result = await self.client.extract(
            urls=[url],
            format="markdown",
            timeout=self.settings.rag_news_timeout_seconds,
            include_usage=True,
        )
        results = result.get("results", [])
        if not results or not results[0].get("raw_content"):
            raise ValueError("Tavily extraction unavailable")
        return results[0]

    async def close(self) -> None:
        """Release the official SDK async connection pool when runtime shuts down."""
        await self.client.close()
