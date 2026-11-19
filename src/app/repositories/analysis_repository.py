"""SQL-only persistence and pgvector search; no provider calls."""

from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import Float, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.embedding_provider import EmbeddingSpace
from app.models.analysis_case import AnalysisCase, CaseEmbedding
from app.models.analysis_evidence import AnalysisEnrichment
from app.schemas.analysis import AnalysisSearch


class AnalysisRepository:
    """Session-scoped source records and strictly isolated embedding spaces."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, case_id: UUID, for_update: bool = False) -> AnalysisCase | None:
        """Fetch original metadata, chart reference and separately stored outcome."""
        return await self.session.get(AnalysisCase, case_id, with_for_update=for_update)

    async def lock_fingerprint(self, fingerprint: str) -> None:
        """Serialize duplicate case creation transactionally across processes."""
        from sqlalchemy import text

        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(fingerprint[:15], 16)}
        )

    async def add(self, case: AnalysisCase) -> None:
        """Stage source facts in the same transaction as the ingestion job."""
        self.session.add(case)
        await self.session.flush()

    async def embedding_metadata(self, case_id: UUID) -> list[dict]:
        """Expose model/dimension/hash provenance without returning raw vectors."""
        statement = (
            select(
                CaseEmbedding.representation_kind,
                CaseEmbedding.embedding_model,
                CaseEmbedding.dimension,
                CaseEmbedding.content_hash,
                CaseEmbedding.image_hash,
            )
            .where(CaseEmbedding.case_id == case_id)
            .order_by(CaseEmbedding.representation_kind)
        )
        rows = await self.session.execute(statement)
        return [
            {
                "kind": row.representation_kind,
                "model": row.embedding_model,
                "dimension": row.dimension,
                "content_hash": row.content_hash,
                "image_hash": row.image_hash,
            }
            for row in rows
        ]

    async def save_embedding(
        self,
        case_id: UUID,
        space: EmbeddingSpace,
        vector: list[float],
        *,
        kind: str = "legacy",
        content_hash: str | None = None,
        image_hash: str | None = None,
    ) -> None:
        """Upsert only the requested model/dimension/version, retaining older spaces."""
        from sqlalchemy.dialects.postgresql import insert

        statement = insert(CaseEmbedding).values(
            case_id=case_id,
            space=space.key,
            embedding_model=space.model,
            dimension=space.dimension,
            representation_version=space.version,
            embedding=space.validate(vector),
            representation_kind=kind,
            content_hash=content_hash,
            image_hash=image_hash,
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[CaseEmbedding.case_id, CaseEmbedding.space],
                set_={
                    "embedding": statement.excluded.embedding,
                    "content_hash": content_hash,
                    "image_hash": image_hash,
                    "representation_kind": kind,
                },
            )
        )

    async def search(
        self, vector: list[float], space: EmbeddingSpace, query: AnalysisSearch
    ) -> list[tuple[AnalysisCase, float]]:
        """Compute cosine top-k in PostgreSQL; filter before ranking, exclude future cases."""
        distance = cast(CaseEmbedding.embedding, Vector(space.dimension)).cosine_distance(
            space.validate(vector)
        )
        statement = (
            select(AnalysisCase, (1 - distance).label("similarity"))
            .join(CaseEmbedding)
            .where(
                CaseEmbedding.space == space.key,
                CaseEmbedding.embedding_model == space.model,
                CaseEmbedding.dimension == space.dimension,
                AnalysisCase.ingest_status.in_(["COMPLETED", "PARTIAL"]),
            )
        )
        kind = {"case-text-v2": "text", "case-image-v2": "multimodal"}.get(space.version, "legacy")
        if kind:
            statement = statement.where(CaseEmbedding.representation_kind == kind)
        statement = self.apply_filters(statement, query)
        rows = await self.session.execute(statement.order_by(distance).limit(query.top_k))
        return [(row[0], float(row[1])) for row in rows]

    @staticmethod
    def apply_filters(statement, query):
        """Exact relational filters execute before ranking; derived values never trust user snapshots."""
        if query.conditions:
            statement = statement.join(
                AnalysisEnrichment, AnalysisEnrichment.case_id == AnalysisCase.id
            )
            for condition in query.conditions:
                value = cast(
                    AnalysisEnrichment.technical_snapshot["filter_values"][condition.field].astext,
                    Float,
                )
                predicate = (
                    value >= condition.value
                    if condition.operator == "gte"
                    else value <= condition.value
                    if condition.operator == "lte"
                    else value == condition.value
                )
                statement = statement.where(
                    predicate, AnalysisEnrichment.technical_snapshot["status"].astext == "ok"
                )
        if query.symbol:
            statement = statement.where(AnalysisCase.symbol == query.symbol)
        if query.timeframe:
            statement = statement.where(
                AnalysisCase.timeframe.in_(
                    {
                        "D1": ["1D", "D1"],
                        "1D": ["1D", "D1"],
                        "W1": ["1W", "W1"],
                        "1W": ["1W", "W1"],
                    }.get(query.timeframe, [query.timeframe])
                )
            )
        if query.before:
            statement = statement.where(AnalysisCase.analyzed_at < query.before)
        if query.after:
            statement = statement.where(AnalysisCase.analyzed_at >= query.after)
        return statement

    async def structured_search(self, query: AnalysisSearch) -> list[AnalysisCase]:
        """Lexical ranking complements vector similarity; filters also work without text."""
        document = func.to_tsvector("simple", AnalysisEnrichment.normalized_text)
        search = func.plainto_tsquery("simple", query.query) if query.query.strip() else None
        statement = (
            select(AnalysisCase)
            .join(AnalysisEnrichment)
            .where(AnalysisCase.ingest_status.in_(["COMPLETED", "PARTIAL"]))
        )
        # apply_filters joins sidecar for indicator filters; avoid joining twice.
        if query.conditions:
            statement = select(AnalysisCase).where(
                AnalysisCase.ingest_status.in_(["COMPLETED", "PARTIAL"])
            )
        statement = self.apply_filters(statement, query)
        if search is not None:
            statement = statement.where(document.op("@@")(search)).order_by(
                func.ts_rank_cd(document, search).desc()
            )
        else:
            statement = statement.order_by(AnalysisCase.analyzed_at.desc())
        return list(await self.session.scalars(statement.limit(query.top_k)))

    async def eligible(self, case_id: UUID, query: AnalysisSearch) -> AnalysisCase | None:
        """Recheck every graph-linked candidate against the exact SQL filters."""
        statement = self.apply_filters(
            select(AnalysisCase).where(
                AnalysisCase.id == case_id, AnalysisCase.ingest_status.in_(["COMPLETED", "PARTIAL"])
            ),
            query,
        )
        return (await self.session.scalars(statement)).first()
