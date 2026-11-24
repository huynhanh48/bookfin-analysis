"""Curated RSS feeds use the same as-of and relevance rules as search APIs."""

from datetime import UTC, datetime

import httpx
import pytest

from app.adapters.rss_news_adapter import RssNewsAdapter, _clean_html, _parse_feed
from app.core.config import Settings

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Vinhomes (VHM) báo lãi kỷ lục</title>
    <link>https://example.vn/vinhomes-lai</link>
    <description><![CDATA[<p>Vinhomes và VHM ghi nhận lợi nhuận tăng mạnh.</p>]]></description>
    <pubDate>Mon, 23 Nov 2026 08:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Thị trường chứng khoán tuần qua</title>
    <link>https://example.vn/market-week</link>
    <description>Tổng hợp diễn biến thị trường.</description>
    <pubDate>Mon, 23 Nov 2026 09:00:00 GMT</pubDate>
  </item>
  <item>
    <title>VHM tin tương lai</title>
    <link>https://example.vn/future</link>
    <description>Không được dùng vì sau thời điểm phân tích.</description>
    <pubDate>Wed, 25 Nov 2026 08:00:00 GMT</pubDate>
  </item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>VHM và Vinhomes mở rộng dự án</title>
    <link href="https://example.vn/vinhomes-project"/>
    <summary>Dự án mới của Vinhomes.</summary>
    <published>2026-11-22T10:00:00Z</published>
  </entry>
</feed>"""


def test_parser_handles_rss_and_atom():
    assert len(_parse_feed(RSS)) == 3
    entry = _parse_feed(ATOM)[0]
    assert entry["url"] == "https://example.vn/vinhomes-project"
    assert entry["title"] == "VHM và Vinhomes mở rộng dự án"


def test_clean_html_strips_markup():
    assert _clean_html("<p>VHM <b>tăng</b> &amp; thanh khoản</p>") == "VHM tăng & thanh khoản"


async def test_rss_search_filters_dates_and_ranks_by_query_terms():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=RSS if request.url.path.endswith("rss") else ATOM)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = RssNewsAdapter(
        Settings(
            _env_file=None,
            rag_news_rss_feeds=["https://feed.test/rss", "https://feed.test/atom"],
        ),
        client=client,
    )
    try:
        rows = await adapter.search(
            "VHM Vinhomes",
            datetime(2026, 11, 24, tzinfo=UTC),
            start_at=datetime(2026, 10, 24, tzinfo=UTC),
            end_at=datetime(2026, 11, 24, tzinfo=UTC),
        )
        assert {row["url"] for row in rows} == {
            "https://example.vn/vinhomes-lai",
            "https://example.vn/vinhomes-project",
        }
        assert all(row["score"] == 1.0 for row in rows)
        extracted = await adapter.extract("https://example.vn/vinhomes-lai")
        assert "lợi nhuận" in extracted["raw_content"]
    finally:
        await client.aclose()


async def test_rss_search_requires_matching_term():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=RSS)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = RssNewsAdapter(
        Settings(_env_file=None, rag_news_rss_feeds=["https://feed.test/rss"]), client=client
    )
    try:
        rows = await adapter.search(
            "HPG Hòa Phát",
            datetime(2026, 11, 24, tzinfo=UTC),
            start_at=datetime(2026, 10, 24, tzinfo=UTC),
        )
        assert rows == []
    finally:
        await client.aclose()


@pytest.mark.parametrize("feeds", [[], ["  "]])
async def test_empty_feed_config_is_a_noop(feeds):
    adapter = RssNewsAdapter(Settings(_env_file=None, rag_news_rss_feeds=feeds))
    assert await adapter.search("VHM", datetime.now(UTC)) == []
    await adapter.close()
