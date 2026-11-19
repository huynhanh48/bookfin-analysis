"""Durable ingestion worker: python -m app.workers.rag_worker [--once]."""

import argparse
import asyncio
import contextlib

from app.adapters.file_storage import image_data_url
from app.core.config import get_settings
from app.core.database import SessionFactory, dispose_engine
from app.core.logging import case_id_var, configure_logging, get_logger, job_id_var, request_id_var
from app.core.rag_runtime import RagRuntime
from app.repositories.analysis_repository import AnalysisRepository
from app.repositories.rag_repository import RagRepository

logger = get_logger(__name__)


async def process_one(runtime: RagRuntime) -> bool:
    """Claim one leased job, isolate provider errors, and durably record its result."""
    async with SessionFactory() as session:
        jobs = RagRepository(session)
        job = await jobs.claim(runtime.settings.rag_job_timeout_seconds)
        if job is None:
            return False
        tokens = (
            request_id_var.set(getattr(job, "request_id", None)),
            job_id_var.set(str(job.id)),
            case_id_var.set(str(job.case_id) if job.case_id else None),
        )
        try:
            return await _process_claimed(runtime, session, jobs, job)
        finally:
            request_id_var.reset(tokens[0])
            job_id_var.reset(tokens[1])
            case_id_var.reset(tokens[2])


async def _process_claimed(runtime, session, jobs, job) -> bool:
    """Run a leased job with trace context; SQL failures can safely roll back before reload."""
    case_id = job.case_id
    job_id = job.id
    try:
        async with asyncio.timeout(runtime.settings.rag_job_timeout_seconds):
            adapter = runtime.cases if job.namespace == "cases" else runtime.knowledge
            enriched = bool(case_id and getattr(runtime.settings, "rag_enrichment_enabled", False))
            if enriched:
                from app.services.rag_enrichment_service import RagEnrichmentService

                case = await AnalysisRepository(session).get(case_id)
                if case is None:
                    raise ValueError("Missing case source")
                await RagEnrichmentService(runtime, session).process(job, case)
            elif case_id:
                repository = AnalysisRepository(session)
                case = await repository.get(case_id)
                if case is None:
                    raise ValueError("Missing case source")
                case.ingest_status = "PROCESSING"
                await session.commit()
                image = (
                    await image_data_url(runtime.storage, job.file_key)
                    if job.file_key and runtime.case_embedding.multimodal
                    else None
                )
                vector = await runtime.case_embedding.embed(job.text, image)
                await repository.save_embedding(case_id, runtime.case_embedding.space, vector)
            if not enriched:
                if job.kind == "image":
                    await adapter.ingest_image(
                        str(job.id), runtime.storage.path(job.file_key), job.text, job.source
                    )
                elif job.kind == "document":
                    await adapter.ingest_document(
                        str(job.id), runtime.storage.path(job.file_key), job.source
                    )
                else:
                    await adapter.ingest_text(str(job.id), job.text, job.source)
                if case_id and case is not None:
                    case.ingest_status = "COMPLETED"
                job.status, job.lease_until = "COMPLETED", None
                await session.commit()
    except Exception as exc:
        await session.rollback()
        # Never log provider messages/prompts, API keys or SQL parameter values.
        logger.warning(
            "rag_ingest_failed", extra={"job_id": str(job_id), "error_type": type(exc).__name__}
        )
        job = await jobs.get(job_id)
        from datetime import UTC, datetime, timedelta

        settings = runtime.settings
        retry = bool(
            case_id
            and job is not None
            and getattr(settings, "rag_enrichment_enabled", False)
            and job.attempts < getattr(settings, "rag_enrichment_max_attempts", 3)
        )
        if job is not None:
            job.status = "PENDING" if retry else "FAILED"
            job.stage = "FAILED"
            job.error_code = type(exc).__name__
            job.lease_until = (
                datetime.now(UTC) + timedelta(seconds=min(60, 2**job.attempts)) if retry else None
            )
        if case_id:
            case = await AnalysisRepository(session).get(case_id)
            if case:
                case.ingest_status = job.status if job is not None else "FAILED"
        await session.commit()
    mirror = getattr(runtime, "mirror", None)
    if mirror and job is not None:
        await session.refresh(job)
        await mirror.document(job)
        if case_id:
            case = await AnalysisRepository(session).get(case_id)
            if case:
                await session.refresh(case)
                await mirror.case(case)
    return True


async def consume(runtime: RagRuntime, once: bool = False) -> None:
    """Consume durable jobs in the app process or in a standalone maintenance worker."""
    try:
        await runtime.text_embedding.validate_model()
        if runtime.settings.rag_enrichment_enabled:
            await runtime.case_text_embedding.validate_model()
            await runtime.case_multimodal_embedding.validate_model()
        else:
            await runtime.case_embedding.validate_model()
        while True:
            worked = await process_one(runtime)
            if once:
                break
            if not worked:
                await asyncio.sleep(2)
    except Exception as exc:
        runtime.worker_error = type(exc).__name__
        logger.warning("rag_worker_stopped", extra={"error_type": type(exc).__name__})
        raise


async def run(once: bool = False, reindex: bool = False) -> None:
    """Standalone maintenance consumer; stop the RAG-enabled API before using it."""
    settings = get_settings()
    if not settings.rag_enabled:
        raise SystemExit("Set RAG_ENABLED=true to start the ingestion worker")
    configure_logging(settings.log_level)
    from app.indicators.native import configure_unstable_periods

    configure_unstable_periods(settings.indicator_unstable_periods)
    runtime = RagRuntime(settings)
    try:
        if reindex:
            async with SessionFactory() as session:
                await RagRepository(session).requeue_completed()
        await consume(runtime, once)
    finally:
        with contextlib.suppress(Exception):
            await runtime.close()
        await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="Requeue completed/failed sources after a model change",
    )
    args = parser.parse_args()
    asyncio.run(run(args.once, args.reindex))
