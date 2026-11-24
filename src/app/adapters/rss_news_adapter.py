"""RSS/Atom news provider for curated market and company feeds.

Feeds are configured by an administrator through ``RAG_NEWS_RSS_FEEDS``. The
adapter keeps the same search/extract protocol as Tavily and Firecrawl so the
news enrichment service applies one set of as-of, relevance and dedup rules.
Article bodies are not scraped here; the feed summary is returned as content and
is explicitly treated as a snippet by the caller.
"""

import asyncio
import html
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any

import httpx

from app.adapters.news_search_adapter import canonical_url, publication_time
from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_MAX_FEED_BYTES = 2_000_000
_MAX_ITEMS_PER_FEED = 50


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _clean_html(value: str) -> str:
    """Strip tags and decode entities from feed summaries."""
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", value or "", flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _child_text(node: ET.Element, names: set[str]) -> str:
    for child in node:
        if _local(child.tag) in names and (child.text or "").strip():
            return (child.text or "").strip()
    return ""


def _entry_link(node: ET.Element) -> str:
    for child in node:
        if _local(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        if href:
            return href
        if (child.text or "").strip():
            return (child.text or "").strip()
    return _child_text(node, {"guid", "id"})


def _parse_feed(text: str) -> list[dict[str, Any]]:
    """Parse RSS 2.0, RSS/RDF and Atom without external dependencies."""
    root = ET.fromstring(text)
    items: list[dict[str, Any]] = []
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        title = _clean_html(_child_text(node, {"title"}))
        url = _entry_link(node)
        summary = _clean_html(_child_text(node, {"description", "summary", "content", "encoded"}))
        published = _child_text(node, {"pubdate", "published", "updated", "date"})
        if not url or not title:
            continue
        items.append(
            {
                "url": url,
                "title": title,
                "content": summary,
                "published_date": published,
            }
        )
        if len(items) >= _MAX_ITEMS_PER_FEED:
            break
    return items


def _query_tokens(query: str) -> list[str]:
    tokens = []
    for token in re.findall(r"\w+", (query or "").casefold()):
        if len(token) >= 2 and token not in tokens:
            tokens.append(token)
    return tokens


class RssNewsAdapter:
    """Fetch configured feeds concurrently and score items against the query."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.feeds = [url.strip() for url in settings.rag_news_rss_feeds if url.strip()]
        self._client = client or httpx.AsyncClient(
            timeout=settings.rag_news_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "BookFinAnalysis/0.1 (+news-rss)"},
        )
        self._last_items: dict[str, dict[str, Any]] = {}

    async def _fetch(self, feed_url: str) -> list[dict[str, Any]]:
        response = await self._client.get(feed_url)
        response.raise_for_status()
        if len(response.content) > _MAX_FEED_BYTES:
            raise ValueError("Feed exceeds size limit")
        return _parse_feed(response.text)

    async def search(
        self,
        query: str,
        analysis_at: datetime,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Return feed items inside the window, ranked by query-term coverage."""
        if not self.feeds:
            return []
        if len(self._last_items) > 2_000:
            self._last_items.clear()
        end = end_at or analysis_at
        start = start_at or analysis_at.replace(hour=0, minute=0, second=0, microsecond=0)
        results = await asyncio.gather(
            *(self._fetch(feed) for feed in self.feeds), return_exceptions=True
        )
        tokens = _query_tokens(query)
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for feed, result in zip(self.feeds, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning("rss_feed_failed", extra={"error_type": type(result).__name__})
                continue
            for item in result:
                try:
                    url = canonical_url(str(item.get("url") or ""))
                except ValueError:
                    continue
                if not url or url in seen:
                    continue
                published = publication_time(item.get("published_date"))
                if not published or published > end or published < start:
                    continue
                text = f"{item.get('title', '')} {item.get('content', '')}".casefold()
                matched = sum(1 for token in tokens if token in text)
                score = matched / len(tokens) if tokens else 0.0
                if score <= 0:
                    continue
                seen.add(url)
                row = {
                    **item,
                    "url": url,
                    "published_date": published.isoformat(),
                    "score": round(score, 4),
                    "feed_url": feed,
                }
                self._last_items[url] = row
                rows.append(row)
        rows.sort(key=lambda item: (item["score"], item["published_date"]), reverse=True)
        return rows[:_MAX_ITEMS_PER_FEED]

    async def extract(self, url: str) -> dict[str, Any]:
        """Return the feed summary as content; never fabricate an article body."""
        item = self._last_items.get(url)
        if item is None:
            raise ValueError("RSS summary unavailable")
        return {
            "raw_content": item.get("content") or item.get("title") or "",
            "published_at": item.get("published_date"),
            "retrieved_at": datetime.now(UTC).isoformat(),
        }

    async def close(self) -> None:
        await self._client.aclose()
