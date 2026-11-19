"""SQL persistence for enriched representations, source links and durable provider cache."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.core.database import SessionFactory
from app.models.analysis_evidence import (
    AnalysisClaim,
    AnalysisEnrichment,
    AnalysisEvidence,
    NewsDocument,
    ProviderCache,
)


def content_key(*parts: Any) -> str:
    """Stable version-aware cache identity; never includes credentials."""
    return hashlib.sha256(
        json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False).encode()
    ).hexdigest()


class EvidenceCache(Protocol):
    """Cache boundary usable with PostgreSQL or a future Redis acceleration layer."""

    async def get(self, key: str) -> dict | None: ...
    async def put(self, key: str, payload: dict) -> None: ...


class PostgresEvidenceCache:
    """Independent short sessions keep concurrent worker branches SQLAlchemy-safe."""

    def __init__(self, ttl_seconds: int, session_factory=SessionFactory):
        self.ttl_seconds, self.session_factory = ttl_seconds, session_factory

    async def get(self, key: str) -> dict | None:
        """Expired entries never masquerade as fresh provider evidence."""
        async with self.session_factory() as session:
            row = await session.get(ProviderCache, key)
            return row.payload if row and row.expires_at > datetime.now(UTC) else None

    async def put(self, key: str, payload: dict) -> None:
        """Upsert validated small JSON results; raw article revisions have their own table."""
        async with self.session_factory() as session:
            statement = insert(ProviderCache).values(
                key=key,
                payload=payload,
                expires_at=datetime.now(UTC) + timedelta(seconds=self.ttl_seconds),
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[ProviderCache.key],
                    set_={
                        "payload": statement.excluded.payload,
                        "expires_at": statement.excluded.expires_at,
                    },
                )
            )
            await session.commit()


class EvidenceRepository:
    """One worker/request session; source text, claims and linked article revisions are separate."""

    def __init__(self, session):
        self.session = session

    async def state(self, case_id: UUID) -> AnalysisEnrichment | None:
        """Fetch enrichment alongside original case without altering user metadata."""
        if case_id in getattr(self, "_states", {}):
            return self._states[case_id]
        return await self.session.get(AnalysisEnrichment, case_id)

    async def ensure_state(self, case_id: UUID, original_input: dict) -> AnalysisEnrichment:
        """Create the sidecar atomically, preserving exact original metadata for future review."""
        await self.session.execute(
            insert(AnalysisEnrichment)
            .values(
                case_id=case_id,
                original_input=original_input,
                observations={},
                resolved_context={},
                technical_snapshot={},
                normalized_text="",
                verification_summary={},
                diagnostics={},
                version="evidence-v1",
            )
            .on_conflict_do_nothing()
        )
        state = await self.state(case_id)
        if state is None:
            raise RuntimeError("Enrichment sidecar was not persisted")
        return state

    async def save_claims(self, case_id: UUID, claims: list) -> None:
        """Retry replaces derived claims only; original notes and source bytes are untouched."""
        await self.session.execute(delete(AnalysisClaim).where(AnalysisClaim.case_id == case_id))
        for claim in claims:
            self.session.add(
                AnalysisClaim(
                    id=claim.claim_id,
                    case_id=case_id,
                    claim_type=claim.claim_type,
                    source_type=claim.source_type,
                    verification_status=claim.verification_status,
                    payload=claim.model_dump(mode="json"),
                )
            )

    async def claims(self, case_id: UUID) -> list[dict]:
        """Return original claim text with verification reason and evidence links."""
        rows = await self.session.scalars(
            select(AnalysisClaim).where(AnalysisClaim.case_id == case_id)
        )
        return [row.payload for row in rows]

    async def link_news(self, case_id: UUID, news: list) -> None:
        """Retain article revisions; retry refreshes case links without duplicating documents."""
        await self.session.execute(
            delete(AnalysisEvidence).where(
                AnalysisEvidence.case_id == case_id, AnalysisEvidence.news_id.is_not(None)
            )
        )
        for article in news:
            values = article.model_dump()
            statement = (
                insert(NewsDocument)
                .values(id=uuid4(), **values)
                .on_conflict_do_nothing(
                    index_elements=[NewsDocument.canonical_url, NewsDocument.content_hash]
                )
            )
            await self.session.execute(statement)
            row = (
                await self.session.scalars(
                    select(NewsDocument).where(
                        NewsDocument.canonical_url == article.canonical_url,
                        NewsDocument.content_hash == article.content_hash,
                    )
                )
            ).one()
            self.session.add(
                AnalysisEvidence(
                    id=uuid4(),
                    case_id=case_id,
                    news_id=row.id,
                    source_type=article.source_type,
                    payload={
                        "source_url": article.source_url,
                        "published_at": article.published_at.isoformat(),
                        "retrieved_at": article.retrieved_at.isoformat(),
                        "content_hash": article.content_hash,
                    },
                )
            )

    async def prefetch(self, case_ids: list[UUID]) -> None:
        """Load bounded candidate evidence in three queries before hydration."""
        states = await self.session.scalars(
            select(AnalysisEnrichment).where(AnalysisEnrichment.case_id.in_(case_ids))
        )
        self._states = {row.case_id: row for row in states}
        rows = await self.session.execute(
            select(AnalysisEvidence.case_id, NewsDocument)
            .join(NewsDocument, AnalysisEvidence.news_id == NewsDocument.id)
            .where(AnalysisEvidence.case_id.in_(case_ids))
        )
        self._news: dict[UUID, list[NewsDocument]] = {case_id: [] for case_id in case_ids}
        for case_id, article in rows:
            self._news[case_id].append(article)
        technical = await self.session.scalars(
            select(AnalysisEvidence).where(
                AnalysisEvidence.case_id.in_(case_ids), AnalysisEvidence.news_id.is_(None)
            )
        )
        self._technical: dict[UUID, list[AnalysisEvidence]] = {case_id: [] for case_id in case_ids}
        for row in technical:
            self._technical[row.case_id].append(row)

    async def sources(self, case_id: UUID) -> list[dict]:
        """Return bounded provenance, excluding full article bodies from case responses."""
        if case_id in getattr(self, "_news", {}):
            articles = self._news[case_id]
        else:
            rows = await self.session.execute(
                select(NewsDocument)
                .join(AnalysisEvidence, AnalysisEvidence.news_id == NewsDocument.id)
                .where(AnalysisEvidence.case_id == case_id)
            )
            articles = rows.scalars()
        sources = [
            {
                "evidence_id": str(row.id),
                "source_url": row.source_url,
                "canonical_url": row.canonical_url,
                "title": row.title,
                "publisher": row.publisher,
                "published_at": row.published_at.isoformat(),
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                "retrieved_at": row.retrieved_at.isoformat(),
                "content_hash": row.content_hash,
                "source_type": row.source_type,
            }
            for row in articles
        ]
        if case_id in getattr(self, "_technical", {}):
            technical = self._technical[case_id]
        else:
            technical = await self.session.scalars(
                select(AnalysisEvidence).where(
                    AnalysisEvidence.case_id == case_id, AnalysisEvidence.news_id.is_(None)
                )
            )
        sources.extend(
            {
                **row.payload,
                "source_type": row.source_type,
                "case_id": str(case_id),
                "source_url": f"/api/v1/analysis/cases/{case_id}",
            }
            for row in technical
        )
        return sources
