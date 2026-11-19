"""Official async Firecrawl v2 SDK; fallback scraping, optional search capability."""

from datetime import datetime
from typing import Any

from app.core.config import Settings


class FirecrawlExtractionAdapter:
    """Use installed instance-bound search/scrape methods, not legacy scrape_url guesses."""

    def __init__(self, settings: Settings, client: Any = None):
        from firecrawl import AsyncFirecrawl

        self.client = client or AsyncFirecrawl(
            api_key=settings.firecrawl_api_key,
            api_url=settings.firecrawl_base_url,
            timeout=settings.rag_news_timeout_seconds,
            max_retries=2,
        )

    async def search(
        self,
        query: str,
        analysis_at: datetime,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> list[dict]:
        """Provide explicit fallback search; publication filtering remains service-owned.

        Firecrawl groups results by source. News entries expose ``date`` rather than
        Tavily's ``published_date`` and have no relevance score, so normalize both
        shapes here instead of silently returning an unusable list.
        """
        start = start_at or analysis_at.replace(hour=0, minute=0, second=0, microsecond=0)
        end = end_at or analysis_at
        result = await self.client.search(
            query=(f"{query} after:{start.date().isoformat()} before:{end.date().isoformat()}"),
            limit=5,
        )
        data = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        rows = list(data.get("news") or []) + list(data.get("web") or [])
        normalized: list[dict] = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("url"):
                continue
            normalized.append(
                {
                    **row,
                    "title": row.get("title") or "",
                    "content": row.get("description") or row.get("snippet") or "",
                    "published_date": row.get("date") or row.get("published_date"),
                }
            )
        return normalized

    async def extract(self, url: str) -> dict[str, Any]:
        """Return markdown and publication/update metadata without interpreting the article."""
        result = await self.client.scrape(url, formats=["markdown"], only_main_content=True)
        data = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        if not data.get("markdown"):
            raise ValueError("Firecrawl extraction unavailable")
        return {"raw_content": data["markdown"], **(data.get("metadata") or {})}

    async def close(self) -> None:
        """Close pinned SDK transport: 4.57.0 wrapper exposes no public close method.

        This inspected compatibility bridge is intentionally local to the adapter.
        Search/scrape continue using the documented public instance methods.
        """
        v2 = getattr(self.client, "_v2_client", None)
        transport = getattr(v2, "async_http_client", None)
        if transport is not None:
            await transport.close()
