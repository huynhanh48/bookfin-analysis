"""PostgreSQL durable queue, deduplication and bounded leases."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag_document import RagDocument


class RagRepository:
    """Own all job SQL; a future Redis broker must still refer to these records."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def enqueue(self, **values) -> RagDocument:
        """Deduplicate atomically across requests with a unique fingerprint."""
        await self.session.execute(
            insert(RagDocument)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["fingerprint"])
        )
        return (
            await self.session.execute(
                select(RagDocument).where(RagDocument.fingerprint == values["fingerprint"])
            )
        ).scalar_one()

    async def get(self, document_id: UUID) -> RagDocument | None:
        """Read persisted processing status."""
        return await self.session.get(RagDocument, document_id)

    async def claim(self, timeout: int) -> RagDocument | None:
        """Skip locked jobs and recover crashed work after a lease longer than timeout."""
        now = datetime.now(UTC)
        query = (
            select(RagDocument)
            .where(
                or_(
                    and_(
                        RagDocument.status == "PENDING",
                        or_(RagDocument.lease_until.is_(None), RagDocument.lease_until < now),
                    ),
                    and_(
                        RagDocument.status.in_(
                            [
                                "PROCESSING",
                                "EXTRACTING",
                                "ENRICHING",
                                "VERIFYING",
                                "NORMALIZING",
                                "INDEXING",
                            ]
                        ),
                        RagDocument.lease_until < now,
                    ),
                )
            )
            .order_by(RagDocument.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        job = (await self.session.execute(query)).scalar_one_or_none()
        if job:
            job.status = "PROCESSING"
            job.stage = "PENDING"
            job.lease_until = now + timedelta(seconds=timeout + 120)
            job.attempts += 1
            job.error_code = None
        await self.session.commit()
        return job

    async def retry(self, document_id: UUID) -> RagDocument | None:
        """Explicitly retry failures; never reset a live processing job."""
        job = await self.session.get(RagDocument, document_id, with_for_update=True)
        if job and job.status in ("FAILED", "PARTIAL"):
            job.status, job.error_code, job.lease_until, job.attempts = "PENDING", None, None, 0
            job.stage, job.progress = "PENDING", 0
        return job

    async def retry_case(self, case_id: UUID) -> RagDocument | None:
        """Find and retry the durable job owned by an analysis case."""
        job = (
            await self.session.execute(
                select(RagDocument).where(RagDocument.case_id == case_id).with_for_update()
            )
        ).scalar_one_or_none()
        if job and job.status in ("FAILED", "PARTIAL"):
            job.status, job.error_code, job.lease_until, job.attempts = "PENDING", None, None, 0
            job.stage, job.progress = "PENDING", 0
        return job

    async def requeue_completed(self) -> int:
        """Explicit maintenance reindex for a new model; retain all old vector rows."""
        from sqlalchemy import update

        result = await self.session.execute(
            update(RagDocument)
            .where(RagDocument.status.in_(["COMPLETED", "FAILED", "PARTIAL"]))
            .values(
                status="PENDING",
                stage="PENDING",
                progress=0,
                error_code=None,
                lease_until=None,
                attempts=0,
            )
        )
        await self.session.commit()
        return result.rowcount

    async def for_case(self, case_id: UUID) -> RagDocument | None:
        """Read the stable source/job link for response envelopes and polling."""
        return (
            await self.session.scalars(select(RagDocument).where(RagDocument.case_id == case_id))
        ).first()
