"""FastAPI-independent analysis memory operations, ready for LangGraph tools."""

import hashlib
import json
from uuid import UUID, uuid4

from app.adapters.embedding_provider import EmbeddingProvider
from app.adapters.file_storage import FileStorage
from app.adapters.redis_memory_store import RedisMemoryStore
from app.core.exceptions import NotFoundError
from app.core.logging import request_id_var
from app.models.analysis_case import AnalysisCase
from app.repositories.analysis_repository import AnalysisRepository
from app.repositories.rag_repository import RagRepository
from app.schemas.analysis import AnalysisCreate, AnalysisSearch, CaseEvidence, CaseMatch


def decision_text(case: AnalysisCreate) -> str:
    """Stable point-in-time representation; outcomes never enter an embedding or graph."""
    extras = {
        "exchange",
        "price_basis",
        "company_name",
        "company_aliases",
        "sector",
        "custom_metadata",
    }
    data = case.model_dump(mode="json", exclude=extras)
    for name in extras:
        value = getattr(case, name)
        if value and not (name == "price_basis" and value == "RAW"):
            data[name] = value
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


class AnalysisMemoryService:
    """Coordinate source persistence and remote embeddings through injected boundaries."""

    def __init__(
        self,
        repository: AnalysisRepository,
        jobs: RagRepository,
        embedding: EmbeddingProvider,
        storage: FileStorage,
        mirror: RedisMemoryStore | None = None,
        enrichment_enabled: bool = False,
    ):
        self.repository, self.jobs = repository, jobs
        self.embedding, self.storage = embedding, storage
        self.mirror = mirror
        self.enrichment_enabled = enrichment_enabled

    async def ingest_analysis_case(
        self, payload: AnalysisCreate, chart: tuple[bytes, str] | None = None
    ) -> AnalysisCase:
        """Persist original facts and a durable job atomically; deduplicate identical cases."""
        text = decision_text(payload)
        key = await self.storage.save(*chart) if chart else None
        fingerprint = hashlib.sha256(("case:" + text + (key or "")).encode()).hexdigest()
        await self.repository.lock_fingerprint(fingerprint)
        job = await self.jobs.enqueue(
            id=uuid4(),
            fingerprint=fingerprint,
            kind="image" if key else "text",
            namespace="cases",
            case_id=None,
            source=payload.source,
            text=text,
            file_key=key,
            status="PENDING",
            request_id=request_id_var.get(),
        )
        if job.case_id:
            case = await self.get_case(job.case_id)
        else:
            values = payload.model_dump(
                exclude={
                    "exchange",
                    "price_basis",
                    "company_name",
                    "company_aliases",
                    "sector",
                    "custom_metadata",
                }
            )
            case = AnalysisCase(id=uuid4(), **values, chart_path=key, ingest_status="PENDING")
            await self.repository.add(case)
            job.case_id = case.id
        if self.enrichment_enabled:
            from app.repositories.evidence_repository import EvidenceRepository

            await EvidenceRepository(self.repository.session).ensure_state(
                case.id, json.loads(text)
            )
        for name in (
            "exchange",
            "price_basis",
            "company_name",
            "company_aliases",
            "sector",
            "custom_metadata",
        ):
            setattr(case, name, getattr(payload, name))
        await self.repository.session.commit()
        if self.mirror:
            await self.repository.session.refresh(case)
            await self.mirror.case(case)
            if job:
                await self.repository.session.refresh(job)
                await self.mirror.document(job)
        case.job_id = job.id
        return case

    async def get_case(self, case_id: UUID) -> AnalysisCase:
        """Retrieve source detail or raise a safe domain-level not-found error."""
        case = await self.repository.get(case_id)
        if case is None:
            raise NotFoundError("Analysis case not found.")
        from app.repositories.evidence_repository import EvidenceRepository

        evidence = EvidenceRepository(self.repository.session)
        state = await evidence.state(case_id)
        metadata_reader = getattr(self.repository, "embedding_metadata", None)
        embeddings = await metadata_reader(case_id) if callable(metadata_reader) else []
        if state:
            for name in (
                "exchange",
                "price_basis",
                "company_name",
                "company_aliases",
                "sector",
                "custom_metadata",
            ):
                if name in state.original_input:
                    setattr(case, name, state.original_input[name])
            case.enrichment = {
                "vision_observations": state.observations,
                "resolved_context": state.resolved_context,
                "technical_snapshot": state.technical_snapshot,
                "normalized_text": state.normalized_text,
                "verification_summary": state.verification_summary,
                "claims": await evidence.claims(case_id),
                "sources": await evidence.sources(case_id),
                "diagnostics": state.diagnostics,
                "embeddings": embeddings,
            }
        elif embeddings:
            case.enrichment = {"embeddings": embeddings}
        return case

    async def search_similar_analysis_cases(self, query: AnalysisSearch) -> list[CaseMatch]:
        """Embed query in the same case space and return outcome-free historical evidence.

        Historical LangGraph simulations must pass before=decision_timestamp.
        Arbitrary user metadata must itself contain only contemporaneous facts.
        Image-only search requires a multimodal case embedding space; it must not
        silently ignore the image and return text-only matches.
        """
        from app.core.exceptions import InvalidRequestError

        if query.image and not getattr(self.embedding, "multimodal", False):
            raise InvalidRequestError(
                "Image search requires an enrichment-enabled case space or a multimodal "
                "case embedding model."
            )
        vector = await self.embedding.embed(query.query.strip() or "chart similarity", query.image)
        rows = await self.repository.search(vector, self.embedding.space, query)
        return [
            CaseMatch(similarity=float(score), case=CaseEvidence.model_validate(case))
            for case, score in rows
        ]

    async def update_analysis_outcome(self, case_id: UUID, outcome: dict) -> AnalysisCase:
        """Merge realized outcomes without changing reasoning, embeddings or knowledge."""
        case = await self.repository.get(case_id, for_update=True)
        if case is None:
            raise NotFoundError("Analysis case not found.")
        case.outcome = {**(case.outcome or {}), **outcome}
        await self.repository.session.commit()
        await self.repository.session.refresh(case)
        if self.mirror:
            await self.mirror.case(case)
        return case

    async def retry_ingestion(self, case_id: UUID) -> AnalysisCase:
        """Retry a failed case by its public ID without needing the internal job ID."""
        case = await self.get_case(case_id)
        job = await self.jobs.retry_case(case_id)
        if job and job.status == "PENDING":
            case.ingest_status = "PENDING"
        await self.repository.session.commit()
        if self.mirror:
            await self.repository.session.refresh(case)
            await self.mirror.case(case)
            if job:
                await self.repository.session.refresh(job)
                await self.mirror.document(job)
        return case
