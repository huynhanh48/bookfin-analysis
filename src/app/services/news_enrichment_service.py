"""Bounded historical search/extraction; optional provider failures stay explicit."""

import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from app.adapters.news_search_adapter import NewsEvidenceProvider, canonical_url, publication_time
from app.core.config import Settings
from app.repositories.evidence_repository import EvidenceCache, content_key
from app.schemas.evidence import NewsEvidence


class NewsEnrichmentService:
    """Reject future/updated/undated evidence rather than fabricate historical availability."""

    def __init__(
        self,
        settings: Settings,
        primary: NewsEvidenceProvider | None,
        fallback: NewsEvidenceProvider | None,
        cache: EvidenceCache,
    ):
        self.settings, self.primary, self.fallback, self.cache = settings, primary, fallback, cache
        self.semaphore = asyncio.Semaphore(settings.rag_provider_concurrency)

    async def _search(
        self,
        primary: NewsEvidenceProvider,
        query: str,
        analysis_at: datetime,
        start_at: datetime,
        end_at: datetime,
    ) -> tuple[list[dict], list[str]]:
        """Try Tavily first, then Firecrawl search only when the primary fails or is empty."""
        warnings: list[str] = []
        providers: list[NewsEvidenceProvider] = [primary]
        if self.fallback is not None and callable(getattr(self.fallback, "search", None)):
            providers.append(self.fallback)
        for index, provider in enumerate(providers):
            try:
                async with asyncio.timeout(self.settings.rag_news_timeout_seconds):
                    rows = await provider.search(
                        query, analysis_at, start_at=start_at, end_at=end_at
                    )
            except Exception as exc:
                warnings.append(f"news_search_failed:{type(exc).__name__}")
                continue
            if rows:
                if index:
                    warnings.append("news_fallback_search_used")
                return rows, warnings
            warnings.append("news_search_empty")
        return [], warnings

    async def enrich(
        self,
        symbol: str,
        analysis_at: datetime,
        company: str = "",
        sector: str = "",
        *,
        aliases: list[str] | None = None,
        window_start: datetime | None = None,
    ) -> tuple[list[NewsEvidence], list[str]]:
        """Search a signal-aware window, deduplicate, rank and extract a shortlist."""
        if not self.settings.rag_news_enabled:
            return [], ["news_disabled"]
        primary = self.primary
        if primary is None:
            return [], ["tavily_not_configured"]
        lookback_start = analysis_at - timedelta(days=self.settings.rag_news_lookback_days)
        # Signal-aware mode searches the divergence formation window (+ buffer) and
        # ends at analyzed_at; the normal lookback is the fallback when no signal exists.
        search_start = min(window_start, analysis_at) if window_start else lookback_start
        key = content_key(
            "news-v3",
            symbol,
            company,
            aliases or [],
            sector,
            analysis_at,
            search_start,
            self.settings.rag_news_lookback_days,
            self.settings.rag_news_signal_window_enabled,
            self.settings.rag_news_signal_buffer_days,
            self.settings.rag_news_max_sources,
            self.settings.rag_news_min_relevance,
            self.settings.rag_news_company_domains,
            self.settings.rag_news_allow_unknown_revision,
        )
        if cached := await self.cache.get(key):
            return [NewsEvidence.model_validate(row) for row in cached["articles"]], cached[
                "warnings"
            ]
        query = " ".join(
            [
                symbol,
                company,
                *(aliases or []),
                sector,
                "công bố thông tin sự kiện doanh nghiệp sở giao dịch",
            ]
        )
        candidates, warnings = await self._search(
            primary, query, analysis_at, search_start, analysis_at
        )
        if not candidates:
            return [], warnings
        selected, seen = [], set()
        for row in candidates:
            try:
                url = canonical_url(row["url"])
                published = publication_time(row.get("published_date") or row.get("published_at"))
                if url in seen or not published or published > analysis_at:
                    continue
                if published < search_start:
                    continue
                evidence_text = f"{row.get('title', '')} {row.get('content', '')}".casefold()
                terms = [
                    term.casefold() for term in (symbol, company, sector, *(aliases or [])) if term
                ]
                if not any(term in evidence_text for term in terms):
                    continue
                if (
                    row.get("score") is not None
                    and float(row["score"]) < self.settings.rag_news_min_relevance
                ):
                    continue
                seen.add(url)
                selected.append((row, url, published))
            except (ValueError, KeyError, TypeError):
                continue

        def priority(item):
            host = urlsplit(item[1]).hostname or ""
            official = any(
                host == d or host.endswith("." + d)
                for d in ("hsx.vn", "hnx.vn", "ssc.gov.vn", *self.settings.rag_news_company_domains)
            ) or host.endswith(".gov.vn")
            # A supplied company domain may be used by callers in the search query;
            # never label an arbitrary publisher official based on ticker text alone.
            return (
                not official,
                -float(item[0].get("score", 0)),
                abs((analysis_at - item[2]).days),
            )

        selected.sort(key=priority)

        async def extract(item):
            row, url, published = item
            async with self.semaphore:
                try:
                    extract_key = content_key("article-v1", url)
                    extracted = await self.cache.get(extract_key)
                    if extracted is None:
                        try:
                            extracted = await primary.extract(url)
                        except Exception:
                            if self.fallback is None:
                                raise
                            extracted = await self.fallback.extract(url)
                        extracted = {
                            **extracted,
                            "raw_content": str(extracted.get("raw_content", ""))[:100_000],
                            "retrieved_at": datetime.now(UTC).isoformat(),
                        }
                        await self.cache.put(extract_key, extracted)
                    # If extraction supplies conflicting dates, use the later time (conservative).
                    pub = publication_time(
                        extracted.get("published_at")
                        or extracted.get("published_time")
                        or extracted.get("article:published_time")
                    )
                    published = max(published, pub) if pub else published
                    updated_raw = (
                        extracted.get("modified_time")
                        or extracted.get("updated_at")
                        or extracted.get("article:modified_time")
                        or row.get("updated_at")
                    )
                    updated = publication_time(updated_raw)
                    archived = publication_time(extracted.get("archived_at"))
                    if (
                        updated is None
                        and analysis_at < datetime.now(UTC)
                        and not (archived and archived <= analysis_at)
                    ):
                        if not self.settings.rag_news_allow_unknown_revision:
                            if "historical_content_revision_unconfirmed" not in warnings:
                                warnings.append("historical_content_revision_unconfirmed")
                            return None
                        if "news_revision_unknown" not in warnings:
                            warnings.append("news_revision_unknown")
                    if (
                        published > analysis_at
                        or (updated and updated > analysis_at)
                        or (updated_raw and not updated)
                    ):
                        return None
                    content = str(extracted.get("raw_content", ""))[:100_000]
                    if not content.strip():
                        return None
                    host = urlsplit(url).hostname or ""
                    official = (
                        host.endswith(".gov.vn")
                        or any(
                            host == d or host.endswith("." + d)
                            for d in self.settings.rag_news_company_domains
                        )
                        or host in ("hsx.vn", "hnx.vn", "ssc.gov.vn")
                        or host.endswith((".hsx.vn", ".hnx.vn"))
                    )
                    return NewsEvidence(
                        source_url=row["url"],
                        canonical_url=url,
                        title=row.get("title", ""),
                        publisher=host,
                        published_at=published,
                        updated_at=updated,
                        retrieved_at=publication_time(extracted.get("retrieved_at"))
                        or datetime.now(UTC),
                        content=content,
                        content_hash=content_key(content),
                        relevance=float(row.get("score", 0)),
                        associated_symbol=symbol,
                        associated_sector=sector or None,
                        source_type="official" if official else "news",
                    )
                except Exception as exc:
                    warnings.append(f"news_extract_failed:{type(exc).__name__}")
                    return None

        results = await asyncio.gather(
            *(extract(item) for item in selected[: self.settings.rag_news_max_sources])
        )
        articles = [article for article in results if article is not None]
        if not articles:
            warnings.append("no_dated_historical_sources")
        # Cache successful searches, including exclusions; never cache failed provider requests.
        await self.cache.put(
            key, {"articles": [a.model_dump(mode="json") for a in articles], "warnings": warnings}
        )
        return articles, warnings
